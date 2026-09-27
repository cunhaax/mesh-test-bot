#!/usr/bin/env python3
# mesh-test-bot: range-test bot for Meshtastic radios (unofficial).
# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this
# program. If not, see <https://www.gnu.org/licenses/>.
"""
Range-test bot for Meshtastic radios (unofficial, not affiliated with the Meshtastic project).

Runs a short, automatic on-air test on a Meshtastic channel:

1. Sends `count` messages on the configured channel at random moments of the
   emission window (never two closer than `min_gap_seconds`), so that everybody
   running this does not transmit at once. `random_schedule = false` gives fixed
   times instead. A message looks like

       MTBOT NARROW_FAST | Lisboa | 2/3

   i.e. keyword, the radio's LoRa mode, the place, and "n of total". The mode is
   read from the radio's own LoRa configuration, never typed by hand.
2. Listens on the same channel the whole time and records every message of that
   form heard: who (the radio node), over how many hops, with what SNR/RSSI, and
   how many of the sender's messages arrived (delivery rate, duplicates).
3. At a random moment after the emission window writes the report: a readable
   text file (fields separated by " | ") and a JSON line, one per session.

One TCP connection to the radio (which accepts a single client) stays open for
the whole session, using the meshtastic Python library: it listens and sends on
the same connection, so nothing is missed between messages. A supervisor thread
recreates the connection if it drops; the report says how long the radio was
unreachable, if it ever was.

Times are wall-clock in the configured `timezone` (default Europe/Lisbon),
whatever the machine's own zone is: someone in the Azores keeps the same
`start_time` and the script converts.

`--schedule` keeps the process running and repeats the session every `weekday`
at `start_time` (this is what the Docker image does).

Config: bot.ini next to this script (or --config). Every option can also come from
an environment variable MTBOT_<NAME> (e.g. MTBOT_PLACE) and from a command-line flag
(see --help); flags win over the environment, which wins over the file, which wins
over the defaults. Needs the `meshtastic` package (pinned in requirements.txt),
Python 3.9+.
"""

import argparse
import configparser
import contextlib
import json
import logging
import os
import random
import re
import socket
import sys
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("bot")

ENV_PREFIX = "MTBOT_"  # environment variables: MTBOT_HOST, MTBOT_PLACE...
SCHEMA = 1  # version of the JSON report
MAX_TEXT_BYTES = 200  # a Meshtastic text message holds about this much
MIN_EPOCH = 1_000_000_000  # a packet time below this (2001) is not a real clock reading
MAX_RANDOM_COUNT = 5  # with random_schedule, more than this needs --fixed-schedule
MIN_RANDOM_LISTEN_MINUTES = 120.0  # with random_schedule, less than this needs --fixed-schedule
# Both exist because LoRa has no real collision avoidance: with many participants spread
# across random_schedule's shared window, a high count or a short window risks flooding
# the channel with everyone's messages at once -- exactly the congestion a range test is
# meant to measure. --fixed-schedule is for a single operator's own controlled testing
# (see the README) and is exempt from both.

DEFAULTS = {
    "host": "meshtastic.local",
    "port": "4403",  # TCP port of the radio, or of whatever else speaks its TCP protocol
    "channel": "",  # required: index of the channel, as shown in the app (0 = primary)
    "channel_name": "",  # recommended: checked on connect, the session aborts if it differs
    "place": "",  # required: where you are (city level), goes in every message
    "keyword": "MTBOT",  # what our messages start with
    "mode": "",  # empty = read from the radio's LoRa configuration
    "mode_aliases": "BW62-SF7-CR6=NARROW_FAST",  # names for manual LoRa settings (BW-SF-CR)
    "count": "3",  # messages per session
    "interval_minutes": "5",  # only with random_schedule = false
    "random_schedule": "true",  # random moments, so that everyone does not transmit at once
    "min_gap_seconds": "60",  # never two of our messages closer than this
    "session_tolerance_seconds": "60",  # messages the radio got this long before the session started still count
    "report_window_minutes": "30",  # report at a random moment in the N min after the emission window
    "timezone": "Europe/Lisbon",  # zone start_time refers to
    "weekday": "saturday",  # monday..sunday, or "daily"
    "start_time": "21:00",
    "listen_minutes": "120",  # with random_schedule, below MIN_RANDOM_LISTEN_MINUTES is refused
    "wake_before_minutes": "2",  # --schedule: connect this early to check the radio
    "report_file": "report.txt",
    "report_json_file": "report.jsonl",
    "rx_file": "rx.log",
}

WEEKDAYS = {name: i for i, names in enumerate([
    ("monday", "mon"), ("tuesday", "tue"), ("wednesday", "wed"), ("thursday", "thu"),
    ("friday", "fri"), ("saturday", "sat"), ("sunday", "sun")]) for name in names}

MODE_RE = re.compile(r"[A-Za-z0-9_.@-]+")


def message_regex(keyword):
    """`<keyword> <mode> | <place> [| n/total]`. Strict on purpose: these messages
    are written by machines, and a strict form keeps chat out."""
    return re.compile(
        r"^\W*" + re.escape(keyword) + r"\s+(?P<mode>[A-Za-z0-9_.@-]+)\s*\|\s*(?P<place>[^|]*?)\s*"
        r"(?:\|\s*(?P<seq>\d+)\s*/\s*(?P<total>\d+))?\s*$", re.I)


def build_message(cfg, mode, seq, total):
    return "%s %s | %s | %d/%d" % (cfg["keyword"], mode, cfg["place"], seq, total)


def parse_aliases(text):
    aliases = {}
    for part in text.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            sys.exit("Invalid mode_aliases (expected A=B,C=D): %r" % text)
        key, value = (x.strip() for x in part.split("=", 1))
        aliases[key] = value
    return aliases


def load_config(argv):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Every option can also be set with an environment variable %s<NAME>, e.g. %sPLACE "
               "(an empty variable counts as unset). Precedence: flags, environment, file, defaults." % (
                   ENV_PREFIX, ENV_PREFIX))
    ap.add_argument("--config", default=os.path.join(HERE, "bot.ini"))
    ap.add_argument("--host")
    ap.add_argument("--port", help="TCP port of the radio (default 4403)")
    ap.add_argument("--channel", help="channel index used to send and listen")
    ap.add_argument("--channel-name", dest="channel_name",
                    help="expected name of that channel (empty = skip check)")
    ap.add_argument("--place", help="where you are, goes in every message")
    ap.add_argument("--keyword", help="what our messages start with")
    ap.add_argument("--mode", help="LoRa mode label to use instead of reading it from the radio")
    ap.add_argument("--count", help="number of messages to send")
    ap.add_argument("--interval", dest="interval_minutes", help="minutes between messages")
    ap.add_argument("--start-time", dest="start_time", help="HH:MM in --timezone")
    ap.add_argument("--timezone", help="IANA zone start-time refers to, e.g. Europe/Lisbon")
    ap.add_argument("--weekday", help="day of the session (--schedule), or 'daily'")
    ap.add_argument("--min-gap", dest="min_gap_seconds", help="seconds between two of our messages, at least")
    ap.add_argument("--report-window-minutes", dest="report_window_minutes",
                    help="report at a random moment this many minutes after the emission window")
    ap.add_argument("--fixed-schedule", action="store_true",
                    help="no randomness: messages every --interval minutes from start-time, report right at the end")
    ap.add_argument("--listen-minutes", dest="listen_minutes",
                    help="emission and listening window length, from start-time")
    ap.add_argument("--report-file", dest="report_file")
    ap.add_argument("--schedule", action="store_true",
                    help="stay running and repeat the session every weekday at start-time")
    ap.add_argument("--now", action="store_true",
                    help="start immediately instead of waiting for start-time")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the schedule, do not connect to the radio")
    args = ap.parse_args(argv)

    cp = configparser.ConfigParser(interpolation=None)
    cp["bot"] = DEFAULTS
    cp.read(args.config)
    cfg = dict(cp["bot"])
    for key in DEFAULTS:
        env = os.environ.get(ENV_PREFIX + key.upper(), "").strip()  # empty = unset, so that a
        if env:  # compose file can pass ${VAR} through without overriding the file by accident
            cfg[key] = env
        if getattr(args, key, None) is not None:
            cfg[key] = getattr(args, key)
    base = os.path.dirname(os.path.abspath(args.config))
    for key in ("report_file", "report_json_file", "rx_file"):
        if not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(base, cfg[key])
    for key in ("channel", "place"):
        if not cfg[key].strip():
            sys.exit("Set `%s`: in file %s, in variable %s%s, or with --%s (see bot.example.ini)" % (
                key, args.config, ENV_PREFIX, key.upper(), key))
    try:
        cfg["channel"] = int(cfg["channel"])
        cfg["port"] = int(cfg["port"])
    except ValueError:
        sys.exit("`channel` and `port` must be numbers: channel=%r port=%r" % (cfg["channel"], cfg["port"]))
    if not 0 < cfg["port"] < 65536:
        sys.exit("Invalid `port`: %d" % cfg["port"])
    cfg["place"] = cfg["place"].strip()
    if "|" in cfg["place"] or "\n" in cfg["place"]:
        sys.exit("`place` cannot contain '|' or line breaks: %r" % cfg["place"])
    cfg["keyword"] = cfg["keyword"].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cfg["keyword"]):
        sys.exit("`keyword` may only contain letters, digits, '_' and '-': %r" % cfg["keyword"])
    cfg["mode"] = cfg["mode"].strip()
    if cfg["mode"] and not MODE_RE.fullmatch(cfg["mode"]):
        sys.exit("`mode` may only contain letters, digits, '_', '.', '@' and '-': %r" % cfg["mode"])
    cfg["mode_aliases"] = parse_aliases(cfg["mode_aliases"])
    worst = build_message(cfg, "X" * 24, 99, 99)
    if len(worst.encode("utf-8")) > MAX_TEXT_BYTES:
        sys.exit("The message does not fit in %d bytes (`place` too long): %r" % (MAX_TEXT_BYTES, worst))
    cfg["msg_re"] = message_regex(cfg["keyword"])
    cfg["count"] = int(cfg["count"])
    cfg["interval_minutes"] = float(cfg["interval_minutes"])
    cfg["listen_minutes"] = float(cfg["listen_minutes"])
    cfg["wake_before_minutes"] = float(cfg["wake_before_minutes"])
    if args.fixed_schedule:
        cfg["random_schedule"], cfg["report_window_minutes"] = "false", "0"
    cfg["random_schedule"] = cfg["random_schedule"].strip().lower() in ("1", "true", "yes", "sim")
    if cfg["random_schedule"]:
        if cfg["count"] > MAX_RANDOM_COUNT:
            sys.exit("`count` above %d needs --fixed-schedule: with random_schedule, many "
                     "participants each sending that many messages risks flooding the channel: %r"
                     % (MAX_RANDOM_COUNT, cfg["count"]))
        if cfg["listen_minutes"] < MIN_RANDOM_LISTEN_MINUTES:
            sys.exit("`listen_minutes` below %g needs --fixed-schedule: with random_schedule, many "
                     "participants in a short window risks flooding the channel: %r"
                     % (MIN_RANDOM_LISTEN_MINUTES, cfg["listen_minutes"]))
    cfg["min_gap_seconds"] = float(cfg["min_gap_seconds"])
    cfg["session_tolerance_seconds"] = float(cfg["session_tolerance_seconds"])
    cfg["report_window_minutes"] = float(cfg["report_window_minutes"])
    try:
        cfg["tz"] = ZoneInfo(cfg["timezone"])
    except Exception:
        sys.exit("Unknown timezone: %r (e.g. Europe/Lisbon)" % cfg["timezone"])
    day = cfg["weekday"].strip().lower()
    if day in ("daily", ""):
        cfg["weekday_num"] = None
    elif day in WEEKDAYS:
        cfg["weekday_num"] = WEEKDAYS[day]
    else:
        sys.exit("Unknown weekday: %r" % cfg["weekday"])
    try:
        cfg["start_hm"] = tuple(int(x) for x in cfg["start_time"].split(":"))
        dtime(*cfg["start_hm"])
    except (ValueError, TypeError):
        sys.exit("Invalid start_time: %r (expected HH:MM)" % cfg["start_time"])
    return cfg, args


def station_name(short_name, num):
    """The station is its node's short name (up to 4 characters, the one shown in
    the app), never a name typed in the message. A node whose user info the radio
    has not received shows the last 4 hex digits of its id, as the app does."""
    return short_name or "%04x" % (num & 0xFFFF)


def _preset_name(number):
    from meshtastic.protobuf import config_pb2  # imported late: only needed with a real radio
    return config_pb2.Config.LoRaConfig.ModemPreset.Name(number)


def _region_name(number):
    from meshtastic.protobuf import config_pb2
    return config_pb2.Config.LoRaConfig.RegionCode.Name(number)


def lora_mode(lora, aliases, preset_name=_preset_name):
    """A label for the radio's LoRa mode: the preset's name (LONG_FAST, NARROW_FAST...)
    or, for manual settings, BW<khz>-SF<n>-CR<n> (e.g. BW62-SF7-CR6), which `aliases`
    may rename."""
    if lora.use_preset:
        try:
            label = preset_name(lora.modem_preset)
        except (ValueError, TypeError, ImportError):
            label = "PRESET_%s" % lora.modem_preset
    else:
        label = "BW%d-SF%d-CR%d" % (lora.bandwidth, lora.spread_factor, lora.coding_rate)
    return aliases.get(label, label)


def _mean(values, digits):
    if not values:
        return None
    mean = sum(values) / len(values)
    return round(mean, digits) if digits else int(round(mean))


class Heard:
    """Messages of ours received, one entry per radio node and per kind of path:
    heard over RF, or after passing through MQTT (a gateway put it on the internet
    and another one back on the air). For each: how many of its messages arrived,
    hops, SNR and RSSI.

    `directory` tells who the nodes are: short_name(num), node_nums(), my_num."""

    def __init__(self, cfg, directory):
        self.cfg = cfg
        self.dir = directory
        self.rf = {}  # node number -> station (see _new)
        self.mqtt = {}  # same, for copies flagged via MQTT
        self.window_start = None  # epoch seconds: what the radio received before it is old news
        self.stale = 0  # messages of ours discarded for being older than the session
        self.lock = threading.Lock()

    def relay_hint(self, relay, sender, hops=None):
        """The packet only carries the LAST byte of the id of the node that last
        retransmitted it, so this lists the known nodes that could be it. With 0
        hops nobody relayed it: the sender was heard directly."""
        if hops == 0:
            return "direct"
        if relay is None:
            return ""
        names = sorted(station_name(self.dir.short_name(n), n) for n in self.dir.node_nums()
                       if n & 0xFF == relay and n not in (self.dir.my_num, sender))
        hint = "relay 0x%02x" % relay
        if names:
            hint += ": " + "/".join(names[:3]) + (" +%d" % (len(names) - 3) if len(names) > 3 else "")
        return hint

    @staticmethod
    def _new(name, when):
        return {"name": name, "place": "", "tags": set(), "seqs": set(), "unnumbered": 0, "total": None,
                "receptions": 0, "hops_min": None, "hops_max": None, "snr": [], "rssi": [], "note": "",
                "first": when, "last": when}

    def feed_packet(self, packet):
        """A packet from the library's `meshtastic.receive` topic."""
        decoded = packet.get("decoded") or {}
        text = decoded.get("text")
        if decoded.get("portnum") != "TEXT_MESSAGE_APP" or not text:
            return
        num = packet.get("from")
        if packet.get("channel", 0) != self.cfg["channel"] or num is None or num == self.dir.my_num:
            return
        name = station_name(self.dir.short_name(num), num)
        via_mqtt = bool(packet.get("viaMqtt"))
        start = packet.get("hopStart")  # absent when 0 (as is hopLimit)
        hops = start - packet.get("hopLimit", 0) if start else None
        snr, rssi = packet.get("rxSnr"), packet.get("rxRssi")  # absent when unknown (MQTT)
        m = self.cfg["msg_re"].match(text)
        if not m:
            # Kept in rx_file too, so that a real session shows which formats are missed.
            self._record(num, name, hops, via_mqtt, snr, rssi, "ignored", text)
            log.info("Ignored (not a test message): %s: %s", name, text)
            return
        seq = int(m.group("seq")) if m.group("seq") else None
        total = int(m.group("total")) if m.group("total") else None
        rx_time = packet.get("rxTime")  # when the RADIO got it; absent while its clock is not set
        if (self.window_start is not None and rx_time and rx_time > MIN_EPOCH
                and rx_time < self.window_start - self.cfg.get("session_tolerance_seconds", 60.0)):
            # While nobody is connected the radio keeps its latest messages and hands them over
            # on connect: the ones from before this session are not part of it.
            with self.lock:
                self.stale += 1
            self._record(num, name, hops, via_mqtt, snr, rssi, "before-session", text)
            log.info("Ignored (the radio received it %ds before the session): %s: %s",
                     self.window_start - rx_time, name, text)
            return
        hint = self.relay_hint(packet.get("relayNode"), num, hops)
        self._record(num, name, hops, via_mqtt, snr, rssi, hint or "relay=?", text)
        when = datetime.now(timezone.utc)
        with self.lock:
            table = self.mqtt if via_mqtt else self.rf
            st = table.setdefault(num, self._new(name, when))
            st["receptions"] += 1
            st["last"] = when
            st["tags"].add(m.group("mode").upper())
            if not st["place"]:
                st["place"] = m.group("place").strip()
            if seq is None:
                st["unnumbered"] += 1
            else:
                st["seqs"].add(seq)
            if total is not None:
                st["total"] = max(st["total"] or 0, total)
            if hops is not None:
                if st["hops_min"] is None or hops < st["hops_min"]:
                    st["hops_min"], st["note"] = hops, hint  # the relay hint of the closest reading
                st["hops_max"] = hops if st["hops_max"] is None else max(st["hops_max"], hops)
            if snr is not None:
                st["snr"].append(snr)
            if rssi is not None:
                st["rssi"].append(rssi)
        log.info("Heard%s: %s [%s] %s, %s hops, SNR %s, RSSI %s, %s", " (via MQTT)" if via_mqtt else "", name,
                 m.group("mode").upper(), "%d/%s" % (seq, total if total else "?") if seq else "n/a",
                 hops, snr, rssi, m.group("place").strip())

    def _record(self, num, name, hops, via_mqtt, snr, rssi, note, text):
        with self.lock, open(self.cfg["rx_file"], "a", encoding="utf-8") as f:
            f.write("%s\t!%08x\t%s\thops=%s\tmqtt=%d\tsnr=%s\trssi=%s\t%s\t%s\n" % (
                datetime.now().isoformat(timespec="seconds"), num, name, hops, via_mqtt,
                snr, rssi, note, text))

    def entries(self, own_mode=None):
        """One dict per station heard, for the report. A station heard over RF counts as
        RF (its MQTT copies are only counted); one heard ONLY after passing through MQTT
        is marked as such: that says nothing about RF reach."""
        with self.lock:
            out = []
            for num in sorted(set(self.rf) | set(self.mqtt)):
                st = self.rf.get(num) or self.mqtt[num]
                distinct = len(st["seqs"]) + st["unnumbered"]
                out.append({
                    "node": "!%08x" % num, "name": st["name"], "place": st["place"],
                    "path": "rf" if num in self.rf else "mqtt",
                    "mode_tags": sorted(st["tags"]),
                    "mode_match": None if not own_mode else all(t == own_mode.upper() for t in st["tags"]),
                    "received": distinct, "of": st["total"], "receptions": st["receptions"],
                    "duplicates": st["receptions"] - distinct,
                    "hops_min": st["hops_min"], "hops_max": st["hops_max"],
                    "snr_avg": _mean(st["snr"], 1), "snr_best": max(st["snr"]) if st["snr"] else None,
                    "rssi_avg": _mean(st["rssi"], 0), "rssi_best": max(st["rssi"]) if st["rssi"] else None,
                    "relay": st["note"], "mqtt_copies": len(self.mqtt[num]["seqs"]) + self.mqtt[num]["unnumbered"]
                    if num in self.rf and num in self.mqtt else 0,
                    "first": st["first"].astimezone(self.cfg["tz"]).isoformat(timespec="seconds"),
                    "last": st["last"].astimezone(self.cfg["tz"]).isoformat(timespec="seconds"),
                })
        return sorted(out, key=lambda e: e["name"].lower())


def build_report(cfg, radio, heard, start, end, report_at, sent):
    """The report of one session, as a dict (the JSON line is exactly this)."""
    lora = radio.lora or {}
    return {
        "schema": SCHEMA,
        "session": {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
                    "report_at": report_at.isoformat(timespec="seconds"), "timezone": cfg["timezone"]},
        "reporter": {"node": "!%08x" % radio.my_num if radio.my_num else None,
                     "name": station_name(radio.short_name(radio.my_num), radio.my_num or 0)
                     if radio.my_num else None, "place": cfg["place"]},
        "radio": dict(lora, mode=radio.mode, channel=cfg["channel"], channel_name=cfg["channel_name"]),
        "sent": sent,
        "heard": heard.entries(radio.mode),
        "outages": {"count": radio.outage_count(), "total_s": round(radio.outage_seconds())},
        "ignored": {"before_session": heard.stale},
    }


def _num(value, fmt="%s"):
    return "-" if value is None else fmt % value


def render_text(rep):
    """A readable version of the report: a header, then one line per station with
    its fields separated by " | "."""
    r, s, ra = rep["reporter"], rep["session"], rep["radio"]
    ok = sum(1 for m in rep["sent"] if m["ok"])
    lines = ["Session %s → %s | Reporter: %s (%s) | Place: %s | Mode: %s | Channel: %s%s | Sent: %d/%d" % (
        s["start"][:16].replace("T", " "), s["end"][11:16], r["name"] or "?", r["node"] or "?", r["place"],
        ra["mode"], ra["channel"], " (%s)" % ra["channel_name"] if ra["channel_name"] else "", ok, len(rep["sent"]))]
    if not rep["heard"]:
        lines.append("(no messages received)")
    else:
        lines.append("node | name | mode | received | duplicates | hops | avg SNR | avg RSSI | place | via | note")
    for e in rep["heard"]:
        hops = "-" if e["hops_min"] is None else (
            str(e["hops_min"]) if e["hops_min"] == e["hops_max"] else "%d-%d" % (e["hops_min"], e["hops_max"]))
        mode = "/".join(e["mode_tags"]) + ("" if e["mode_match"] is not False else " (≠ %s)" % ra["mode"])
        note = "; ".join(x for x in (e["relay"], "+%d MQTT copies" % e["mqtt_copies"] if e["mqtt_copies"] else "") if x)
        lines.append(" | ".join([e["node"], e["name"], mode, "%d/%s" % (e["received"], e["of"] or "?"),
                                 str(e["duplicates"]), hops, _num(e["snr_avg"]), _num(e["rssi_avg"], "%.0f"),
                                 e["place"], "RF" if e["path"] == "rf" else "MQTT (does not confirm RF)", note]))
    if rep["outages"]["count"]:
        lines.append("warning: the connection to the radio was interrupted %d time(s), ~%d s total; messages in "
                     "that interval may have been lost" % (rep["outages"]["count"], rep["outages"]["total_s"]))
    if rep.get("ignored", {}).get("before_session"):
        lines.append("warning: %d test message(s) the radio received before the session started were discarded"
                     % rep["ignored"]["before_session"])
    return "\n".join(lines)


class RadioError(Exception):
    """Something retrying will not fix (e.g. the channel is not the expected one)."""


def enable_keepalive(sock):
    """Have the OS notice a dead peer (power cut, WiFi gone) within ~1 minute; a
    silent half-open connection would otherwise look alive forever."""
    with contextlib.suppress(OSError, AttributeError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        idle = getattr(socket, "TCP_KEEPIDLE", getattr(socket, "TCP_KEEPALIVE", 0x10))
        sock.setsockopt(socket.IPPROTO_TCP, idle, 30)
        for opt, val in (("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
            if hasattr(socket, opt):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)


def tcp_factory(host, port=4403):
    """A connected, fully configured meshtastic TCPInterface, or an exception."""
    from meshtastic.tcp_interface import TCPInterface  # imported late: only needed for a real radio
    iface = TCPInterface(hostname=host, portNumber=port, connectNow=False)
    try:
        iface.connect()  # waits (up to 30 s) for the radio's config and node db
    except BaseException:
        with contextlib.suppress(Exception):
            iface.close()
        raise
    return iface


class Radio:
    """One long-lived connection to the radio, listening and sending on it.

    The library reconnects a dropped socket by itself, but gives up (and leaves a
    dead interface) if that fails, so a supervisor thread checks the connection
    every `tick` seconds and builds a new one when needed, with backoff. Packets
    that arrive while the radio is unreachable are lost; the time it was
    unreachable is kept in `outages` and shown in the report.

    The library also reconnects a dropped socket by ITSELF, and then never clears
    `isConnected`, so from outside the drop is invisible. Its `_reconnect` is wrapped to
    know when that happens: the time until the radio's configuration is back counts as an
    outage too, and a reconnection that does not finish within LIB_RECONNECT_TIMEOUT is
    given up and rebuilt from scratch.

    It is also the directory of nodes for Heard (names come from the radio's node
    db, kept in a cache so lookups work during a reconnect), and knows the radio's
    own LoRa mode."""

    LIB_RECONNECT_TIMEOUT = 60.0  # seconds the library gets to finish a reconnection of its own

    def __init__(self, cfg, factory=None, tick=2.0):
        self.cfg = cfg
        self._factory = factory or tcp_factory
        self._tick = tick
        self.iface = None
        self.heard = None
        self.lora = None  # the radio's LoRa settings, read on connect
        self.fatal = None  # message of an error that retrying cannot fix
        self.outages = []  # seconds of each period without connection
        self._names = {}  # node number -> short name
        self._my_num = None
        self._last_sent = 0.0
        self._down_since = None
        self._lib_down_since = None  # the library is reconnecting by itself since then
        self._keepalive_sock = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._subscribed = False

    # --- directory for Heard, and the radio's identity -------------------------
    @property
    def my_num(self):
        return self._my_num

    @property
    def mode(self):
        """The LoRa mode label for our messages: the `mode` setting if given, else
        what the radio is actually set to."""
        return self.cfg["mode"] or (self.lora or {}).get("mode") or "UNKNOWN"

    def _snapshot(self, iface):
        with contextlib.suppress(Exception):
            for num, node in list((getattr(iface, "nodesByNum", None) or {}).items()):
                self._names[num] = (node.get("user") or {}).get("shortName") or self._names.get(num, "")

    def short_name(self, num):
        node = (getattr(self.iface, "nodesByNum", None) or {}).get(num)
        if node is not None:
            self._names[num] = (node.get("user") or {}).get("shortName") or self._names.get(num, "")
        return self._names.get(num, "")

    def node_nums(self):
        return list(self._names)

    # --- connection ----------------------------------------------------------
    def start(self, heard):
        self.heard = heard
        try:
            from pubsub import pub  # comes with meshtastic
            pub.subscribe(self._on_receive, "meshtastic.receive")
            self._subscribed = True
        except ImportError:
            pass  # fake factories in tests deliver packets by calling _on_receive
        self._thread = threading.Thread(target=self._supervise, daemon=True, name="radio supervisor")
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._subscribed:
            with contextlib.suppress(Exception):
                from pubsub import pub
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
        if self._thread:
            self._thread.join(5)
        with contextlib.suppress(Exception):
            if self.iface:
                self.iface.close()

    def wait_ready(self, timeout):
        """True once connected (and the channel is the expected one)."""
        end = time.time() + timeout
        while time.time() < end and not self.fatal:
            if self._ready.wait(0.5):
                return True
        return False

    def _healthy(self):
        iface = self.iface
        if iface is None or not iface.isConnected.is_set():
            return False
        if self._lib_down_since is not None and time.time() - self._lib_down_since > self.LIB_RECONNECT_TIMEOUT:
            return False  # its own reconnection never finished: a connection that is up but deaf
        rx = getattr(iface, "_rxThread", None)
        return rx is None or rx.is_alive()

    def _watch_library_reconnects(self, iface):
        """Know when the library reconnects by itself (see the class docstring)."""
        original = getattr(iface, "_reconnect", None)
        if original is None:
            return

        def reconnect(*args, **kwargs):
            started = time.time()
            try:
                return original(*args, **kwargs)
            finally:
                # It restarts the configuration download (myInfo is cleared) only if it really
                # reconnected: another thread may already have done it.
                if getattr(iface, "myInfo", True) is None and self._lib_down_since is None and iface is self.iface:
                    self._lib_down_since = started
                    log.warning("The connection to the radio dropped; the library is reconnecting")
        iface._reconnect = reconnect

    def _check_channel(self, iface):
        if not self.cfg["channel_name"]:
            return
        channels = {c.index: c for c in (iface.localNode.channels or []) if c.role != 0}  # 0 = DISABLED
        found = channels.get(self.cfg["channel"])
        found = found.settings.name if found else None
        if found != self.cfg["channel_name"]:
            raise RadioError("Channel %d is called %r, expected %r. Channels: %s" % (
                self.cfg["channel"], found, self.cfg["channel_name"],
                {i: c.settings.name for i, c in channels.items()}))

    def _read_lora(self, iface):
        """The radio's own LoRa settings and the mode label derived from them."""
        try:
            lora = iface.localNode.localConfig.lora
            try:
                region = _region_name(lora.region)
            except (ValueError, TypeError, ImportError):
                region = str(lora.region)
            return {"mode": lora_mode(lora, self.cfg["mode_aliases"], _preset_name), "region": region,
                    "use_preset": bool(lora.use_preset), "modem_preset": lora.modem_preset,
                    "bandwidth": lora.bandwidth, "spread_factor": lora.spread_factor,
                    "coding_rate": lora.coding_rate, "channel_num": lora.channel_num}
        except Exception as e:
            log.warning("Could not read the radio's LoRa configuration (%s: %s)", type(e).__name__, e)
            return None

    def _connect(self):
        iface = self._factory(self.cfg["host"], self.cfg["port"])
        try:
            if self._stop.is_set():  # the session ended while we were connecting
                raise RadioError("session ended")
            self._check_channel(iface)
        except BaseException:
            with contextlib.suppress(Exception):
                iface.close()
            raise
        old, self.iface = self.iface, iface
        self._lib_down_since = None
        self._watch_library_reconnects(iface)
        with contextlib.suppress(Exception):
            self._my_num = iface.myInfo.my_node_num
        self.lora = self._read_lora(iface) or self.lora
        self._snapshot(iface)
        if old is not None:
            with contextlib.suppress(Exception):
                old.close()
        self._ready.set()
        log.info("Connected to radio %s:%s (node !%08x, %d known nodes, mode %s)", self.cfg["host"],
                 self.cfg["port"], self._my_num or 0, len(self._names), self.mode)

    def _supervise(self):
        backoff = 2.0
        while not self._stop.is_set():
            if self._healthy():
                sock = getattr(self.iface, "socket", None)
                if sock is not None and sock is not self._keepalive_sock:  # new socket after a library reconnect
                    enable_keepalive(sock)
                    self._keepalive_sock = sock
                self._snapshot(self.iface)
                if self._lib_down_since is not None and getattr(self.iface, "myInfo", None) is not None:
                    self.outages.append(time.time() - self._lib_down_since)  # its configuration is back
                    log.warning("Connection recovered by the library after ~%.0fs with no listening", self.outages[-1])
                    self._lib_down_since = None
                backoff = 2.0
                self._stop.wait(self._tick)
                continue
            if self._down_since is None and self._ready.is_set():
                self._down_since = self._lib_down_since or time.time()  # an outage the library began counts from then
                self._lib_down_since = None
                self._ready.clear()
                log.warning("Connection to the radio lost; reconnecting")
            try:
                self._connect()
            except RadioError as e:
                self.fatal = str(e)
                log.error("%s", e)
                return
            except Exception as e:
                log.warning("No connection to the radio (%s: %s); retrying in %.0fs",
                            type(e).__name__, e, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            else:
                if self._down_since is not None:
                    self.outages.append(time.time() - self._down_since)
                    log.warning("Connection recovered after %.0fs with no listening", self.outages[-1])
                    self._down_since = None
                self._keepalive_sock = None  # apply keepalive to the new socket

    def _downtimes(self):
        down = list(self.outages)
        if self._down_since is not None:  # still down at the end
            down.append(time.time() - self._down_since)
        elif self._lib_down_since is not None:  # the library is still reconnecting
            down.append(time.time() - self._lib_down_since)
        return down

    def outage_count(self):
        return len(self._downtimes())

    def outage_seconds(self):
        return sum(self._downtimes())

    # --- receive / send ------------------------------------------------------
    def _on_receive(self, packet, interface=None):
        try:
            self.heard.feed_packet(packet)
        except Exception:
            log.exception("Error handling a packet")

    def send(self, text, deadline):
        """Send `text` (a string, or a function that returns it, called right before
        sending) on the channel, waiting for the connection if needed and keeping
        `min_gap_seconds` after our previous message; gives up at `deadline` (a
        time.time() value). Returns the text sent, or None."""
        gap = self.cfg["min_gap_seconds"]
        while time.time() < deadline:
            if self.fatal:
                raise SystemExit(self.fatal)
            wait = self._last_sent + gap - time.time()
            if wait > 0:
                time.sleep(min(wait, 1.0))
                continue
            if self._healthy():
                body = text() if callable(text) else text
                try:
                    self.iface.sendText(body, channelIndex=self.cfg["channel"], wantAck=False)
                except Exception as e:
                    log.warning("Send failed (%s: %s); retrying", type(e).__name__, e)
                else:
                    self._last_sent = time.time()
                    log.info("Sent: %s", body)
                    return body
            time.sleep(1.0)
        log.error("The message could not be sent")
        return None


def next_window(cfg, now):
    """First (start, end) session window on the configured weekday that has not
    ended by `now` (timezone-aware, in cfg["tz"])."""
    for d in range(8):
        day = (now + timedelta(days=d)).date()
        if cfg["weekday_num"] is not None and day.weekday() != cfg["weekday_num"]:
            continue
        start = datetime.combine(day, dtime(*cfg["start_hm"]), tzinfo=cfg["tz"])
        end = start + timedelta(minutes=cfg["listen_minutes"])
        if end > now:
            return start, end
    raise RuntimeError("no window found")  # unreachable: a weekday recurs within 7 days


def plan_send_times(cfg, start, end, rng=random):
    """When to send our messages. The window is cut in `count` equal segments and
    each message falls at a random moment of its own segment, at least
    `min_gap_seconds` before the segment ends, so two consecutive messages are never
    closer than that. Without `random_schedule`: every `interval_minutes` from `start`
    (but never closer than `min_gap_seconds`)."""
    n = cfg["count"]
    gap = timedelta(seconds=cfg["min_gap_seconds"])
    if n <= 0:
        return []
    if not cfg["random_schedule"]:
        step = max(cfg["interval_minutes"] * 60, cfg["min_gap_seconds"])
        times = (start + timedelta(seconds=step * i) for i in range(n))
        return [t for t in times if t < end]
    if n > 1 and (end - start) / n < gap:
        fit = max(1, int((end - start) / gap))
        log.warning("The window is not long enough for %d messages %ds apart: sending %d", n,
                    cfg["min_gap_seconds"], fit)
        n = min(n, fit)
    seg = (end - start) / n
    times = []
    for i in range(n):
        lo = start + seg * i
        hi = max(lo, start + seg * (i + 1) - gap)
        times.append(lo + timedelta(seconds=int((hi - lo).total_seconds() * rng.random())))
    return times


def plan_report_time(cfg, end, last_send, rng=random):
    """A random moment between the end of the emission window and
    `report_window_minutes` later, never within `min_gap_seconds` of our last
    message."""
    at = end + timedelta(seconds=int(cfg["report_window_minutes"] * 60 * rng.random()))
    if last_send is not None:
        at = max(at, last_send + timedelta(seconds=cfg["min_gap_seconds"]))
    return at


def sleep_until(when, radio):
    """Sleep until `when` (aware datetime), waking up if the radio hit a fatal error."""
    while True:
        if radio.fatal:
            raise SystemExit(radio.fatal)
        left = (when - datetime.now(when.tzinfo)).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, 1.0))


def run_window(cfg, start, end, dry_run=False, factory=None):
    """One session: connect, listen from `start`, send our messages at planned
    moments, then write the report at a random moment after `end`."""
    slots = plan_send_times(cfg, start, end)
    total = len(slots)
    report_at = plan_report_time(cfg, end, slots[-1] if slots else None)

    log.info("Place %s | channel %d | radio %s:%s | zone %s", cfg["place"], cfg["channel"], cfg["host"],
             cfg["port"], cfg["timezone"])
    log.info("Sending at: %s | report at %s", ", ".join(s.strftime("%H:%M:%S") for s in slots),
             report_at.strftime("%H:%M:%S"))
    if dry_run:
        log.info("Message (the mode comes from the radio): %s",
                 build_message(cfg, cfg["mode"] or "<mode>", 1, max(total, 1)))
        return

    radio = Radio(cfg, factory)
    heard = Heard(cfg, radio)
    heard.window_start = start.timestamp()
    radio.start(heard)  # connects now, so a wrong channel or an unreachable radio shows up early
    sent = []
    try:
        if not radio.wait_ready(60):
            if radio.fatal:
                raise SystemExit(radio.fatal)
            log.warning("The radio has not responded yet; still retrying")
        sleep_until(start, radio)
        events = slots + [report_at]
        for i, at in enumerate(slots):
            sleep_until(at, radio)
            body = radio.send(lambda i=i: build_message(cfg, radio.mode, i + 1, total), events[i + 1].timestamp())
            sent.append({"seq": i + 1, "total": total, "planned": at.isoformat(timespec="seconds"),
                         "ok": body is not None, "text": body,
                         "at": datetime.now(cfg["tz"]).isoformat(timespec="seconds") if body else None})
        sleep_until(report_at, radio)
    finally:
        radio.close()

    rep = build_report(cfg, radio, heard, start, end, report_at, sent)
    text = render_text(rep)
    with open(cfg["report_file"], "a", encoding="utf-8") as f:
        f.write("=== %s ===\n%s\n\n" % (datetime.now(cfg["tz"]).strftime("%Y-%m-%d %H:%M"), text))
    with open(cfg["report_json_file"], "a", encoding="utf-8") as f:
        f.write(json.dumps(rep, ensure_ascii=False) + "\n")
    print(text, flush=True)


def schedule(cfg, dry_run=False):
    """Run a session every configured weekday, forever. A failed session is
    logged and the loop carries on with the next one."""
    after = datetime.now(cfg["tz"])
    while True:
        start, end = next_window(cfg, after)
        wake = start - timedelta(minutes=cfg["wake_before_minutes"])
        log.info("Next session: %s → %s (%s)", start.strftime("%a %Y-%m-%d %H:%M"),
                 end.strftime("%H:%M"), cfg["timezone"])
        if dry_run:
            run_window(cfg, max(start, datetime.now(cfg["tz"])), end, dry_run=True)
            return
        while True:  # short sleeps: robust to clock and DST changes
            wait = (wake - datetime.now(cfg["tz"])).total_seconds()
            if wait <= 0:
                break
            time.sleep(min(wait, 60))
        try:
            run_window(cfg, max(start, datetime.now(cfg["tz"])), end)
        except SystemExit as e:
            log.error("Session aborted: %s", e)
        except Exception:
            log.exception("Session failed")
        after = max(datetime.now(cfg["tz"]), end)  # never re-run the same window


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg, args = load_config(argv)

    now = datetime.now(cfg["tz"])
    if args.schedule:
        schedule(cfg, dry_run=args.dry_run)
        return 0
    if args.now:
        start = now
        end = start + timedelta(minutes=cfg["listen_minutes"])
    else:
        # Today's window at start_time, ignoring the weekday: one-off manual run.
        start = datetime.combine(now.date(), dtime(*cfg["start_hm"]), tzinfo=cfg["tz"])
        end = start + timedelta(minutes=cfg["listen_minutes"])
        if end <= now:
            sys.exit("The window %s–%s has already passed." % (start.strftime("%H:%M"), end.strftime("%H:%M")))
    run_window(cfg, max(start, now), end, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
