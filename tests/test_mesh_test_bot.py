# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests. All names and nodes are invented; the message shapes are the ones this bot
sends. Nothing here touches a radio. Run: python3 -m unittest discover tests"""
import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mesh_test_bot as bot  # noqa: E402

PRESETS = {0: "LONG_FAST", 12: "NARROW_FAST"}


def preset_name(number):
    try:
        return PRESETS[number]
    except KeyError:
        raise ValueError(number)


def make_cfg(**kw):
    tmp = tempfile.mkdtemp()
    cfg = {"host": "x", "port": 4403, "channel": 2, "channel_name": "Test_Channel", "place": "Lisboa", "keyword": "MTBOT",
           "mode": "", "mode_aliases": {"BW62-SF7-CR6": "NARROW_FAST"}, "min_gap_seconds": 0.3,
           "session_tolerance_seconds": 60.0, "wake_before_minutes": bot.WAKE_BEFORE_MINUTES,
           "startup_check_seconds": bot.STARTUP_CHECK_SECONDS,
           "startup_check_min_lead_minutes": bot.STARTUP_CHECK_MIN_LEAD_MINUTES,
           "startup_check_backoff_seconds": bot.STARTUP_CHECK_BACKOFF_SECONDS,
           "startup_check_backoff_cap_seconds": bot.STARTUP_CHECK_BACKOFF_CAP_SECONDS,
           "startup_check_join_seconds": bot.STARTUP_CHECK_JOIN_SECONDS,
           "tz": ZoneInfo("Europe/Lisbon"), "timezone": "Europe/Lisbon",
           "rx_file": os.path.join(tmp, "rx.log"), "report_file": os.path.join(tmp, "report.txt"),
           "report_json_file": os.path.join(tmp, "report.jsonl")}
    cfg["msg_re"] = bot.message_regex(cfg["keyword"])
    cfg.update(kw)
    return cfg


def packet(frm, text, channel=2, hop_start=3, hop_limit=3, mqtt=False, relay=None, snr=None, rssi=None,
           rx_time=None):
    """A packet as the meshtastic library hands it to `meshtastic.receive` listeners
    (zero-valued fields are absent from the dict, as there)."""
    d = {"from": frm, "to": 0xFFFFFFFF, "id": 1, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text}}
    if channel:
        d["channel"] = channel
    if hop_limit:
        d["hopLimit"] = hop_limit
    if hop_start:
        d["hopStart"] = hop_start
    if mqtt:
        d["viaMqtt"] = True
    if relay is not None:
        d["relayNode"] = relay
    if snr is not None:
        d["rxSnr"] = snr
    if rssi is not None:
        d["rxRssi"] = rssi
    if rx_time is not None:
        d["rxTime"] = rx_time
    return d


class Directory:
    """Stands in for Radio as the node directory of Heard."""

    def __init__(self, names, my_num=9999):
        self.names, self.my_num = names, my_num

    def short_name(self, num):
        return self.names.get(num, "")

    def node_nums(self):
        return list(self.names)


class DefaultsFileTest(unittest.TestCase):
    """DEFAULTS is loaded from defaults.ini (next to the script) at import time."""

    def test_the_two_required_options_have_no_default(self):
        self.assertEqual((bot.DEFAULTS["channel"], bot.DEFAULTS["place"]), ("", ""))

    def test_a_missing_defaults_file_is_a_clear_error(self):
        with mock.patch.object(bot, "HERE", tempfile.mkdtemp()):
            with self.assertRaises(SystemExit) as cm:
                bot._load_defaults()
        self.assertIn("defaults.ini", str(cm.exception))

    def test_a_broken_defaults_file_is_a_clear_error_too(self):
        here = tempfile.mkdtemp()
        for bad in ("not even ini syntax [[[", "[wrong-section]\nhost = x\n"):
            open(os.path.join(here, "defaults.ini"), "w", encoding="utf-8").write(bad)
            with self.subTest(bad=bad), mock.patch.object(bot, "HERE", here):
                with self.assertRaises(SystemExit) as cm:
                    bot._load_defaults()
            self.assertIn("defaults.ini", str(cm.exception))


class ConfigTest(unittest.TestCase):
    def load(self, ini, *flags):
        path = os.path.join(tempfile.mkdtemp(), "bot.ini")
        open(path, "w", encoding="utf-8").write("[bot]\n" + ini)
        return bot.load_config(["--config", path] + list(flags))[0]

    def test_minimum_config_and_defaults(self):
        cfg = self.load("channel = 1\nplace = Lisboa\n")
        self.assertEqual((cfg["channel"], cfg["place"], cfg["keyword"], cfg["message_count"]), (1, "Lisboa", "MTBOT", 3))
        self.assertEqual(cfg["mode"], "")  # read from the radio
        self.assertEqual(cfg["mode_aliases"], {"BW62-SF7-CR6": "NARROW_FAST"})
        self.assertTrue(os.path.isabs(cfg["report_json_file"]))
        self.assertEqual(cfg["session_tolerance_seconds"], 60.0)
        self.assertEqual(cfg["listen_minutes"], 120.0)

    def test_flags_override_the_file(self):
        cfg = self.load("channel = 1\nplace = Lisboa\n", "--place", "Porto", "--count", "5", "--mode", "MY_MODE")
        self.assertEqual((cfg["place"], cfg["message_count"], cfg["mode"]), ("Porto", 5, "MY_MODE"))

    def test_channel_and_place_are_required(self):
        for ini in ("place = Lisboa\n", "channel = 1\n", "channel = 1\nplace =   \n"):
            with self.subTest(ini=ini), self.assertRaises(SystemExit):
                self.load(ini)

    def test_bad_values_stop_the_start(self):
        for ini in ("place = A|B\n", "keyword = a b\n", "mode = a b\n", "mode_aliases = xyz\n",
                    "place = " + "x" * 200 + "\n", "timezone = Foo/Bar\n", "weekday = xpto\n", "start_time = 25:99\n"):
            with self.subTest(ini=ini), self.assertRaises(SystemExit):
                self.load("channel = 1\n" + ("" if ini.startswith("place") else "place = Lisboa\n") + ini)

    def test_random_schedule_rejects_too_many_messages_or_too_short_a_window(self):
        for ini in ("message_count = 6\n", "listen_minutes = 119\n", "report_window_minutes = 59\n"):
            with self.subTest(ini=ini), self.assertRaises(SystemExit):
                self.load("channel = 1\nplace = Lisboa\n" + ini)
        # exactly at the limit is fine
        self.load("channel = 1\nplace = Lisboa\nmessage_count = 5\n")
        self.load("channel = 1\nplace = Lisboa\nlisten_minutes = 120\n")
        self.load("channel = 1\nplace = Lisboa\nreport_window_minutes = 60\n")

    def test_min_gap_session_tolerance_wake_before_and_startup_check_are_not_configurable(self):
        # a bot.ini setting them is simply ignored: they are internal, fixed values
        cfg = self.load("channel = 1\nplace = Lisboa\nmin_gap_seconds = 1\n"
                        "session_tolerance_seconds = 1\nwake_before_minutes = 99\n"
                        "startup_check_seconds = 1\nstartup_check_min_lead_minutes = 1\n"
                        "startup_check_backoff_seconds = 1\nstartup_check_backoff_cap_seconds = 1\n"
                        "startup_check_join_seconds = 1\n")
        self.assertEqual((cfg["min_gap_seconds"], cfg["session_tolerance_seconds"], cfg["wake_before_minutes"]),
                         (bot.MIN_GAP_SECONDS, bot.SESSION_TOLERANCE_SECONDS, bot.WAKE_BEFORE_MINUTES))
        self.assertEqual((cfg["startup_check_seconds"], cfg["startup_check_min_lead_minutes"],
                          cfg["startup_check_backoff_seconds"], cfg["startup_check_backoff_cap_seconds"],
                          cfg["startup_check_join_seconds"]),
                         (bot.STARTUP_CHECK_SECONDS, bot.STARTUP_CHECK_MIN_LEAD_MINUTES,
                          bot.STARTUP_CHECK_BACKOFF_SECONDS, bot.STARTUP_CHECK_BACKOFF_CAP_SECONDS,
                          bot.STARTUP_CHECK_JOIN_SECONDS))

    def test_fixed_schedule_is_exempt_from_the_random_schedule_limits(self):
        cfg = self.load("channel = 1\nplace = Lisboa\nmessage_count = 50\nlisten_minutes = 2\n", "--fixed-schedule")
        self.assertEqual((cfg["message_count"], cfg["listen_minutes"]), (50, 2.0))

    def test_message_is_built_and_parsed_back(self):
        cfg = self.load("channel = 1\nplace = Vila Nova\n")
        text = bot.build_message(cfg, "NARROW_FAST", 2, 3)
        self.assertEqual(text, "MTBOT NARROW_FAST | Vila Nova | 2/3")
        m = cfg["msg_re"].match(text)
        self.assertEqual((m["mode"], m["place"], m["seq"], m["total"]), ("NARROW_FAST", "Vila Nova", "2", "3"))


class EnvironmentTest(unittest.TestCase):
    """Every option can come from MTBOT_<NAME>; flags > environment > file > defaults."""

    def load(self, ini="", env=None, *flags):
        path = os.path.join(tempfile.mkdtemp(), "bot.ini")
        if ini is not None:
            open(path, "w", encoding="utf-8").write("[bot]\n" + ini)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MTBOT_")}
        with mock.patch.dict(os.environ, dict(clean, **(env or {})), clear=True):
            return bot.load_config(["--config", path] + list(flags))[0]

    def test_the_environment_alone_is_enough_no_file_needed(self):
        cfg = self.load(None, {"MTBOT_CHANNEL": "1", "MTBOT_PLACE": "Porto", "MTBOT_HOST": "10.0.0.2",
                               "MTBOT_PORT": "4404", "MTBOT_MESSAGE_COUNT": "5", "MTBOT_RANDOM_SCHEDULE": "false"})
        self.assertEqual((cfg["channel"], cfg["place"], cfg["host"], cfg["port"], cfg["message_count"]),
                         (1, "Porto", "10.0.0.2", 4404, 5))
        self.assertFalse(cfg["random_schedule"])

    def test_precedence_flags_then_environment_then_file_then_defaults(self):
        ini = "channel = 1\nplace = Ficheiro\nhost = 10.0.0.1\nmessage_count = 4\n"
        env = {"MTBOT_PLACE": "Ambiente", "MTBOT_HOST": "10.0.0.2"}
        cfg = self.load(ini, env, "--host", "10.0.0.3")
        self.assertEqual(cfg["host"], "10.0.0.3")  # flag
        self.assertEqual(cfg["place"], "Ambiente")  # environment beats the file
        self.assertEqual(cfg["message_count"], 4)  # file beats the default (3)
        self.assertEqual(cfg["keyword"], "MTBOT")  # default

    def test_an_empty_variable_counts_as_unset(self):
        cfg = self.load("channel = 1\nplace = Ficheiro\n", {"MTBOT_PLACE": "", "MTBOT_CHANNEL": "  "})
        self.assertEqual((cfg["place"], cfg["channel"]), ("Ficheiro", 1))

    def test_missing_required_options_say_where_to_set_them(self):
        with self.assertRaises(SystemExit) as cm:
            self.load(None, {"MTBOT_PLACE": "Porto"})
        self.assertIn("MTBOT_CHANNEL", str(cm.exception))

    def test_port_defaults_to_the_radios_and_is_validated(self):
        self.assertEqual(self.load("channel = 1\nplace = X\n")["port"], 4403)
        for bad in ("abc", "0", "70000"):
            with self.subTest(port=bad), self.assertRaises(SystemExit):
                self.load("channel = 1\nplace = X\n", {"MTBOT_PORT": bad})
        with self.assertRaises(SystemExit):
            self.load("channel = um\nplace = X\n")


class TcpFactoryTest(unittest.TestCase):
    def test_it_connects_to_the_given_host_and_port(self):
        try:
            import meshtastic.tcp_interface  # noqa: F401
        except ImportError:
            self.skipTest("meshtastic not installed")
        with mock.patch("meshtastic.tcp_interface.TCPInterface") as tcp:
            # Close enough to the real library for this test: _startHeartbeat fires as part
            # of the connection completing (see the heartbeat-wait tests below for the rest).
            tcp.return_value.connect.side_effect = lambda: tcp.return_value._startHeartbeat()
            iface = bot.tcp_factory("10.0.0.5", 4404)
        tcp.assert_called_once_with(hostname="10.0.0.5", portNumber=4404, connectNow=False)
        tcp.return_value.connect.assert_called_once_with()
        self.assertIs(iface, tcp.return_value)

    def test_a_failed_connection_closes_the_interface(self):
        try:
            import meshtastic.tcp_interface  # noqa: F401
        except ImportError:
            self.skipTest("meshtastic not installed")
        with mock.patch("meshtastic.tcp_interface.TCPInterface") as tcp:
            tcp.return_value.connect.side_effect = OSError("refused")
            with self.assertRaises(OSError):
                bot.tcp_factory("10.0.0.5", 4404)
        tcp.return_value.close.assert_called_once_with()

    def test_it_waits_for_the_first_heartbeat_before_returning(self):
        # The real library fires _startHeartbeat asynchronously (from its reader thread) once
        # the config handshake completes, which can be a little after connect() itself
        # returns control -- simulated here with a short delay from a background thread.
        try:
            import meshtastic.tcp_interface  # noqa: F401
        except ImportError:
            self.skipTest("meshtastic not installed")
        with mock.patch("meshtastic.tcp_interface.TCPInterface") as tcp:
            def fire_heartbeat_soon():
                time.sleep(0.1)
                tcp.return_value._startHeartbeat()
            tcp.return_value.connect.side_effect = (
                lambda: threading.Thread(target=fire_heartbeat_soon, daemon=True).start())
            t0 = time.time()
            bot.tcp_factory("10.0.0.5", 4404)
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 0.1)  # it actually waited for the heartbeat...
        self.assertLess(elapsed, 1.0)          # ...but not anywhere near the full bound

    def test_it_gives_up_waiting_if_the_heartbeat_never_fires(self):
        # Bounded: a factory call must not hang forever if something about the library's
        # connect-then-heartbeat sequence ever changes underneath this assumption.
        try:
            import meshtastic.tcp_interface  # noqa: F401
        except ImportError:
            self.skipTest("meshtastic not installed")
        with mock.patch("meshtastic.tcp_interface.TCPInterface") as tcp, \
                mock.patch.object(bot, "HEARTBEAT_SETTLE_SECONDS", 0.1):
            t0 = time.time()  # connect() does nothing here: the heartbeat never fires
            iface = bot.tcp_factory("10.0.0.5", 4404)
        self.assertLess(time.time() - t0, 1.0)
        self.assertIs(iface, tcp.return_value)

    def test_the_wrapped_heartbeat_still_calls_the_original(self):
        # Wrapping _startHeartbeat to observe it must not skip the library's own real work.
        try:
            import meshtastic.tcp_interface  # noqa: F401
        except ImportError:
            self.skipTest("meshtastic not installed")
        with mock.patch("meshtastic.tcp_interface.TCPInterface") as tcp:
            original = tcp.return_value._startHeartbeat
            tcp.return_value.connect.side_effect = lambda: tcp.return_value._startHeartbeat()
            bot.tcp_factory("10.0.0.5", 4404)
        original.assert_called_once_with()


class LoraModeTest(unittest.TestCase):
    def lora(self, **kw):
        base = dict(use_preset=True, modem_preset=0, bandwidth=250, spread_factor=11, coding_rate=5)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_preset_name(self):
        self.assertEqual(bot.lora_mode(self.lora(), {}, preset_name), "LONG_FAST")
        self.assertEqual(bot.lora_mode(self.lora(modem_preset=12), {}, preset_name), "NARROW_FAST")

    def test_unknown_preset_number(self):
        self.assertEqual(bot.lora_mode(self.lora(modem_preset=99), {}, preset_name), "PRESET_99")

    def test_manual_settings_are_named_by_their_parameters_and_can_be_aliased(self):
        manual = self.lora(use_preset=False, bandwidth=62, spread_factor=7, coding_rate=6)
        self.assertEqual(bot.lora_mode(manual, {}, preset_name), "BW62-SF7-CR6")
        self.assertEqual(bot.lora_mode(manual, {"BW62-SF7-CR6": "NARROW_FAST"}, preset_name), "NARROW_FAST")


class HeardTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        # 1004: in the radio's node db but without user info; 1006 and up: unknown
        names = {1001: "AB12", 1002: "CD34", 1003: "\U0001F98A", 1004: "", 1005: "GH78", 1009: "Me"}
        self.h = bot.Heard(self.cfg, Directory(names))

    def heard(self, frm, text, **kw):
        self.h.feed_packet(packet(frm, text, **kw))

    def entry(self, name):
        return next((e for e in self.h.entries("LONG_FAST") if e["name"] == name), None)

    def test_messages_are_parsed(self):
        cases = [
            (1001, "MTBOT LONG_FAST | Lisboa | 2/3", "AB12", "Lisboa", ["LONG_FAST"]),
            (1002, "mtbot narrow_fast | Vila, Sul | 1/3", "CD34", "Vila, Sul", ["NARROW_FAST"]),  # case, commas in the place
            (1005, "  MTBOT LONG_FAST|Serra|3/3", "GH78", "Serra", ["LONG_FAST"]),  # spacing
            (1003, "MTBOT LONG_FAST | Rio", "\U0001F98A", "Rio", ["LONG_FAST"]),  # not numbered
            (1006, "MTBOT SHORT_FAST | Praia | 1/1", "03ee", "Praia", ["SHORT_FAST"]),  # node unknown: id digits
            (1004, "MTBOT LONG_FAST | Alto | 1/2", "03ec", "Alto", ["LONG_FAST"]),  # no user info
        ]
        for frm, text, name, place, tags in cases:
            with self.subTest(text=text):
                self.heard(frm, text)
                e = self.entry(name)
                self.assertEqual((e["place"], e["mode_tags"], e["path"]), (place, tags, "rf"))

    def test_the_name_comes_from_the_node_never_from_the_text(self):
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 1/3")
        self.assertEqual([e["name"] for e in self.h.entries()], ["AB12"])

    def test_other_messages_are_ignored(self):
        for text in ("Hey folks, how are you?", "MTBOT", "MTBOT LONG_FAST Lisboa",  # no pipe
                     "MTBOT LONG_FAST | Lisboa | extra | 1/3",  # an extra field
                     "Test LONG_FAST | Lisboa", "General call, X, Village",  # other formats
                     "Confirmation, AB12, 3 hops", "see the MTBOT LONG_FAST | Lisboa"):  # not at the start
            with self.subTest(text=text):
                self.heard(1001, text)
        self.heard(1001, "MTBOT LONG_FAST | Lisboa", channel=0)  # other channel
        self.heard(9999, "MTBOT LONG_FAST | Lisboa")  # our own node
        self.h.feed_packet({"from": 1001, "decoded": {"portnum": "POSITION_APP"}, "channel": 2})  # not text
        self.h.feed_packet({"from": 1001, "channel": 2})  # encrypted, undecoded
        self.assertEqual((self.h.rf, self.h.mqtt), ({}, {}))

    def test_ignored_text_is_recorded_with_its_signal(self):
        self.heard(1001, "Hey folks, how are you?", snr=3.0, rssi=-101)
        self.heard(1001, "MTBOT LONG_FAST | Lisboa", channel=0)  # other channel: not ours to record
        self.heard(9999, "Hi, it's me")  # our own node
        lines = open(self.cfg["rx_file"], encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("\tmqtt=0\tsnr=3.0\trssi=-101\tignored\tHey folks, how are you?"), lines[0])

    def test_delivery_and_duplicates(self):
        for seq in (1, 1, 2):  # message 1 arrives twice, 2 once, 3 never
            self.heard(1001, "MTBOT LONG_FAST | Lisboa | %d/3" % seq)
        e = self.entry("AB12")
        self.assertEqual((e["received"], e["of"], e["receptions"], e["duplicates"]), (2, 3, 3, 1))

    def test_unnumbered_messages_each_count(self):
        self.heard(1001, "MTBOT LONG_FAST | Lisboa")
        self.heard(1001, "MTBOT LONG_FAST | Lisboa")
        e = self.entry("AB12")
        self.assertEqual((e["received"], e["of"], e["duplicates"]), (2, None, 0))

    def test_signal_statistics_ignore_what_is_missing(self):
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 1/3", snr=4.0, rssi=-100)
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 2/3", snr=5.0, rssi=-96)
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 3/3")  # no signal data (e.g. it came via MQTT)
        e = self.entry("AB12")
        self.assertEqual((e["snr_avg"], e["snr_best"], e["rssi_avg"], e["rssi_best"]), (4.5, 5.0, -98, -96))
        self.assertEqual(self.entry_none_signal(), (None, None))

    def entry_none_signal(self):
        self.heard(1002, "MTBOT LONG_FAST | Vila | 1/1")
        e = self.entry("CD34")
        return e["snr_avg"], e["rssi_avg"]

    def test_hops_range_and_the_relay_hint_of_the_closest_reading(self):
        self.heard(1002, "MTBOT LONG_FAST | Vila | 1/3", hop_start=5, hop_limit=2)  # 3 hops
        self.heard(1002, "MTBOT LONG_FAST | Vila | 2/3", hop_start=5, hop_limit=3, relay=0xE9)  # 2 hops
        self.heard(1002, "MTBOT LONG_FAST | Vila | 3/3", hop_start=5, hop_limit=5)  # 0 hops
        e = self.entry("CD34")
        self.assertEqual((e["hops_min"], e["hops_max"], e["relay"]), (0, 3, "direct"))

    def test_place_is_the_first_one_given(self):
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 1/2")
        self.heard(1001, "MTBOT LONG_FAST | Sintra | 2/2")
        self.assertEqual(self.entry("AB12")["place"], "Lisboa")

    def test_mqtt_copies_are_flagged_and_kept_apart(self):
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 1/3")  # RF
        self.heard(1001, "MTBOT LONG_FAST | Lisboa | 1/3", mqtt=True)  # the same, also via MQTT
        self.heard(1002, "MTBOT LONG_FAST | Vila | 1/3", mqtt=True)  # only via MQTT
        a, c = self.entry("AB12"), self.entry("CD34")
        self.assertEqual((a["path"], a["received"], a["mqtt_copies"]), ("rf", 1, 1))  # the MQTT copy is not counted
        self.assertEqual((c["path"], c["received"], c["mqtt_copies"]), ("mqtt", 1, 0))

    def test_a_mode_different_from_ours_is_flagged(self):
        self.heard(1001, "MTBOT NARROW_FAST | Lisboa | 1/3")
        self.heard(1002, "MTBOT LONG_FAST | Vila | 1/3")
        self.assertIs(self.entry("AB12")["mode_match"], False)
        self.assertIs(self.entry("CD34")["mode_match"], True)
        self.assertIsNone(self.h.entries(None)[0]["mode_match"])  # our own mode unknown


class StaleMessagesTest(unittest.TestCase):
    """The radio keeps its latest messages while nobody is connected and hands them over on
    connect. Those it received before the session started are not part of it."""

    # rxTime of eight messages read back from a real radio right after connecting (see the
    # experiment): the radio had received them between 70 and 272 seconds earlier.
    RX = [1790485099, 1790485139, 1790485165, 1790485188, 1790485221, 1790485235, 1790485262, 1790485301]
    CONNECTED_AT = 1790485371

    def setUp(self):
        self.cfg = make_cfg()
        self.h = bot.Heard(self.cfg, Directory({1001: "AB12"}))

    def feed(self, seq, rx_time, text=None):
        self.h.feed_packet(packet(1001, text or "MTBOT LONG_FAST | Lab | %d/8" % seq, rx_time=rx_time))

    def replay(self, window_start):
        self.h.window_start = window_start
        for i, rx in enumerate(self.RX, 1):
            self.feed(i, rx)

    def received(self):
        entries = self.h.entries("LONG_FAST")
        return entries[0]["received"] if entries else 0

    def test_a_session_that_starts_on_connecting_discards_all_the_stored_ones(self):
        self.replay(self.CONNECTED_AT)
        self.assertEqual((self.h.stale, self.received()), (8, 0))
        lines = open(self.cfg["rx_file"], encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 8)  # nothing is lost silently
        self.assertTrue(all("\tbefore-session\t" in line for line in lines))

    def test_only_the_ones_before_the_start_are_discarded(self):
        self.replay(1790485200)  # cut-off = start - 60 s = 1790485140: T1 (…099) and T2 (…139) are older
        self.assertEqual((self.h.stale, self.received()), (2, 6))

    def test_the_tolerance_boundary(self):
        self.h.window_start = 1_800_000_000
        self.feed(1, 1_800_000_000 - 60)  # exactly on the tolerance: counts
        self.feed(2, 1_800_000_000 - 61)  # one second older: discarded
        self.assertEqual((self.received(), self.h.stale), (1, 1))

    def test_messages_from_inside_the_session_count_even_when_delivered_late(self):
        self.h.window_start = 1_800_000_000
        self.feed(1, 1_800_000_030)  # e.g. handed over after a reconnection
        self.feed(2, 1_800_000_500)
        self.assertEqual((self.received(), self.h.stale), (2, 0))

    def test_an_unset_or_implausible_radio_clock_is_not_a_reason_to_discard(self):
        self.h.window_start = 1_800_000_000
        self.feed(1, None)  # rxTime absent: the radio has no time
        self.feed(2, 5000)  # not a real clock reading
        self.assertEqual((self.received(), self.h.stale), (2, 0))

    def test_without_a_session_window_nothing_is_filtered(self):
        self.feed(1, 1790485099)
        self.assertEqual((self.received(), self.h.stale), (1, 0))

    def test_the_tolerance_is_configurable(self):
        self.cfg["session_tolerance_seconds"] = 5.0
        self.h.window_start = 1_800_000_000
        self.feed(1, 1_800_000_000 - 6)
        self.assertEqual(self.h.stale, 1)

    def test_the_report_says_how_many_were_discarded_only_when_there_were(self):
        radio = SimpleNamespace(lora={"mode": "LONG_FAST"}, mode="LONG_FAST", my_num=9999, short_name=lambda n: "ME01",
                                outage_count=lambda: 0, outage_seconds=lambda: 0.0)
        tz = self.cfg["tz"]
        when = [datetime(2026, 9, 26, 21, 0, tzinfo=tz), datetime(2026, 9, 26, 21, 30, tzinfo=tz),
                datetime(2026, 9, 26, 21, 41, tzinfo=tz)]
        clean = bot.build_report(self.cfg, radio, self.h, *when, [])
        self.assertEqual(clean["ignored"], {"before_session": 0})
        self.assertNotIn("before the session started", bot.render_text(clean))
        self.replay(self.CONNECTED_AT)
        rep = bot.build_report(self.cfg, radio, self.h, *when, [])
        self.assertEqual(rep["ignored"], {"before_session": 8})
        self.assertIn("warning: 8 test message(s)", bot.render_text(rep))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.h = bot.Heard(self.cfg, Directory({1001: "AB12", 1002: "CD34"}))
        self.radio = SimpleNamespace(
            lora={"mode": "LONG_FAST", "region": "EU_868", "use_preset": True, "modem_preset": 0, "bandwidth": 250,
                  "spread_factor": 11, "coding_rate": 5, "channel_num": 1},
            mode="LONG_FAST", my_num=0xDEADBEEF, short_name=lambda n: "ME01", outage_count=lambda: 0,
            outage_seconds=lambda: 0.0)
        tz = self.cfg["tz"]
        self.t = [datetime(2026, 9, 26, 21, 0, tzinfo=tz), datetime(2026, 9, 26, 21, 30, tzinfo=tz),
                  datetime(2026, 9, 26, 21, 41, tzinfo=tz)]

    def report(self, sent=None):
        sent = sent if sent is not None else [{"seq": 1, "total": 1, "ok": True, "text": "MTBOT LONG_FAST | Lisboa | 1/1"}]
        return bot.build_report(self.cfg, self.radio, self.h, *self.t, sent)

    def test_the_json_report(self):
        self.h.feed_packet(packet(1001, "MTBOT LONG_FAST | Vila Nova | 1/3", snr=4.5, rssi=-98))
        rep = json.loads(json.dumps(self.report()))  # it must survive a round trip
        self.assertEqual(rep["schema"], 1)
        self.assertEqual(rep["reporter"], {"node": "!deadbeef", "name": "ME01", "place": "Lisboa"})
        self.assertEqual((rep["radio"]["mode"], rep["radio"]["channel"], rep["radio"]["bandwidth"]), ("LONG_FAST", 2, 250))
        self.assertEqual(rep["session"]["start"], "2026-09-26T21:00:00+01:00")
        self.assertEqual(rep["outages"], {"count": 0, "total_s": 0})
        e = rep["heard"][0]
        self.assertEqual((e["node"], e["name"], e["received"], e["of"], e["snr_avg"], e["rssi_avg"]),
                         ("!000003e9", "AB12", 1, 3, 4.5, -98))

    def test_the_text_report_has_fields_separated_by_pipes(self):
        self.h.feed_packet(packet(1001, "MTBOT LONG_FAST | Vila Nova | 1/3", snr=4.5, rssi=-98, hop_start=5, hop_limit=5))
        self.h.feed_packet(packet(1002, "MTBOT NARROW_FAST | Vale | 1/3", mqtt=True))
        lines = bot.render_text(self.report()).splitlines()
        self.assertIn("Reporter: ME01 (!deadbeef) | Place: Lisboa | Mode: LONG_FAST | Channel: 2 (Test_Channel) | Sent: 1/1", lines[0])

        header, row1, row2 = (line.split(" | ") for line in lines[1:4])
        columns = ["node", "name", "mode", "received", "duplicates", "hops", "avg SNR", "avg RSSI", "place", "via", "note"]
        self.assertEqual([c.strip() for c in header], columns)
        self.assertEqual([c.strip() for c in row1],
                         ["!000003e9", "AB12", "LONG_FAST", "1/3", "0", "0", "4.5", "-98", "Vila Nova", "RF", "direct"])
        self.assertEqual([c.strip() for c in row2],
                         ["!000003ea", "CD34", "NARROW_FAST (≠ LONG_FAST)", "1/3", "0", "0", "-", "-", "Vale",
                          "MQTT (does not confirm RF)", "direct"])

        # Every column (but the free-form last one, never padded) lines up: its "|"
        # falls at the same character position on every row.
        for i in range(len(columns) - 1):
            widths = {len(line.split(" | ")[i]) for line in lines[1:4]}
            self.assertEqual(len(widths), 1, "column %r not aligned: %r" % (columns[i], widths))

    def test_nothing_heard(self):
        self.assertIn("(no messages received)", bot.render_text(self.report()))
        self.assertEqual(self.report()["heard"], [])

    def test_failed_sends_and_outages_are_reported(self):
        self.radio.outage_count, self.radio.outage_seconds = (lambda: 2), (lambda: 7.4)
        rep = self.report(sent=[{"seq": 1, "total": 2, "ok": True, "text": "a"}, {"seq": 2, "total": 2, "ok": False, "text": None}])
        text = bot.render_text(rep)
        self.assertIn("Sent: 1/2", text)
        self.assertIn("interrupted 2 time(s), ~7 s", text)
        self.assertEqual(rep["outages"], {"count": 2, "total_s": 7})


class ScheduleTest(unittest.TestCase):
    START = datetime(2026, 9, 19, 21, 0)
    END = datetime(2026, 9, 19, 21, 30)

    def cfg(self, **kw):
        cfg = {"message_count": 3, "random_schedule": True, "min_gap_seconds": 60.0,
               "interval_minutes": 5.0, "report_window_minutes": 30.0}
        cfg.update(kw)
        return cfg

    def test_send_times_are_spread_and_never_too_close(self):
        gap = timedelta(seconds=60)
        for n in (1, 3, 10):
            cfg = self.cfg(message_count=n)
            seg = (self.END - self.START) / n
            for seed in range(300):
                t = bot.plan_send_times(cfg, self.START, self.END, random.Random(seed))
                self.assertEqual(len(t), n)
                for i, at in enumerate(t):  # each call inside its own segment, a gap before it ends
                    self.assertGreaterEqual(at, self.START + seg * i)
                    self.assertLessEqual(at, self.START + seg * (i + 1) - gap)
                for a, b in zip(t, t[1:]):
                    self.assertGreaterEqual(b - a, gap)

    def test_send_times_really_vary(self):
        firsts = {bot.plan_send_times(self.cfg(), self.START, self.END, random.Random(s))[0]
                  for s in range(50)}
        self.assertGreater(len(firsts), 10)

    def test_too_many_messages_for_the_window_are_reduced(self):
        end = self.START + timedelta(minutes=2)  # room for two calls a minute apart
        t = bot.plan_send_times(self.cfg(message_count=5), self.START, end, random.Random(1))
        self.assertEqual(len(t), 2)
        self.assertGreaterEqual(t[1] - t[0], timedelta(seconds=60))

    def test_tiny_window_sends_at_the_start(self):
        end = self.START + timedelta(seconds=24)
        t = bot.plan_send_times(self.cfg(message_count=1), self.START, end, random.Random(1))
        self.assertEqual(t, [self.START])

    def test_fixed_schedule_is_the_old_behaviour(self):
        cfg = self.cfg(random_schedule=False, message_count=3)
        t = bot.plan_send_times(cfg, self.START, self.END)
        self.assertEqual(t, [self.START + timedelta(minutes=m) for m in (0, 5, 10)])

    def test_fixed_schedule_never_goes_below_the_minimum_gap(self):
        cfg = self.cfg(random_schedule=False, message_count=3, interval_minutes=0.1)  # 6 s, but the gap is 60 s
        t = bot.plan_send_times(cfg, self.START, self.END)
        self.assertEqual(t, [self.START + timedelta(seconds=s) for s in (0, 60, 120)])

    def test_report_time_is_random_within_its_window(self):
        cfg = self.cfg()
        times = [bot.plan_report_time(cfg, self.END, self.END - timedelta(minutes=2), random.Random(s))
                 for s in range(300)]
        self.assertTrue(all(self.END <= t <= self.END + timedelta(minutes=30) for t in times))
        self.assertGreater(len(set(times)), 50)

    def test_report_never_right_after_the_last_message(self):
        cfg = self.cfg(report_window_minutes=0.0)
        last = self.END + timedelta(seconds=10)  # (fixed schedule can put a call close to the end)
        self.assertEqual(bot.plan_report_time(cfg, self.END, last), last + timedelta(seconds=60))

    def test_report_window_zero_means_at_the_end(self):
        self.assertEqual(bot.plan_report_time(self.cfg(report_window_minutes=0.0), self.END, None), self.END)


class FakeIface:
    """Just enough of a meshtastic TCPInterface for Radio."""

    def __init__(self, channels=None, nodes=None, my_num=9999, lora=None, restarts_config=True):
        self.restarts_config = restarts_config
        chans = channels if channels is not None else [(2, "Test_Channel")]
        self.isConnected = threading.Event()
        self.isConnected.set()
        lora = lora or SimpleNamespace(use_preset=True, modem_preset=0, bandwidth=250, spread_factor=11,
                                       coding_rate=5, region=3, channel_num=1)
        self.localNode = SimpleNamespace(
            channels=[SimpleNamespace(index=i, role=1, settings=SimpleNamespace(name=n)) for i, n in chans],
            localConfig=SimpleNamespace(lora=lora))
        self.myInfo = SimpleNamespace(my_node_num=my_num)
        self.nodesByNum = nodes if nodes is not None else {
            1002: {"user": {"shortName": "CD34"}}, 1004: {"num": 1004}}  # 1004: no user info
        self.socket = None
        self.sent, self.closed = [], False

    def sendText(self, text, channelIndex=0, wantAck=False):
        self.sent.append((text, channelIndex, wantAck))

    def _reconnect(self):
        """What the library's does when the socket drops: reconnect and ask for the
        configuration again (myInfo is cleared until it arrives). isConnected stays set."""
        if self.restarts_config:
            self.myInfo = None

    def close(self):
        self.closed = True
        self.isConnected.clear()


class RadioTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.ifaces, self.fail_first, self.lora = [], 0, None
        for name, fake in (("_preset_name", preset_name), ("_region_name", lambda n: "EU_868")):
            patcher = mock.patch.object(bot, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def factory(self, host, port):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise OSError("radio unreachable")
        iface = FakeIface(lora=self.lora)
        self.ifaces.append(iface)
        return iface

    def radio(self, **cfg):
        self.cfg.update(cfg)
        r = bot.Radio(self.cfg, self.factory, tick=0.05)
        r.start(bot.Heard(self.cfg, r))
        self.addCleanup(r.close)
        return r

    def test_connects_and_is_the_node_directory(self):
        r = self.radio()
        self.assertTrue(r.wait_ready(5))
        self.assertEqual(r.my_num, 9999)
        self.assertEqual(r.short_name(1002), "CD34")
        self.assertEqual(r.short_name(1004), "")  # known node, no user info
        self.assertEqual(r.short_name(4242), "")  # unknown node
        self.assertIn(1002, r.node_nums())

    def test_it_connects_to_the_configured_host_and_port(self):
        seen = []
        self.factory = lambda host, port: (seen.append((host, port)), FakeIface())[1]
        r = self.radio(host="meshmonitor", port=4404)
        self.assertTrue(r.wait_ready(5))
        self.assertEqual(seen, [("meshmonitor", 4404)])

    def test_the_mode_is_read_from_the_radio(self):
        r = self.radio()
        r.wait_ready(5)
        self.assertEqual(r.mode, "LONG_FAST")
        self.assertEqual((r.lora["region"], r.lora["bandwidth"], r.lora["spread_factor"]), ("EU_868", 250, 11))

    def test_a_narrow_preset_and_a_manual_narrow_setting_give_the_same_label(self):
        self.lora = SimpleNamespace(use_preset=True, modem_preset=12, bandwidth=0, spread_factor=0, coding_rate=0,
                                    region=3, channel_num=0)
        r = self.radio()
        r.wait_ready(5)
        self.assertEqual(r.mode, "NARROW_FAST")
        self.lora = SimpleNamespace(use_preset=False, modem_preset=0, bandwidth=62, spread_factor=7, coding_rate=6,
                                    region=3, channel_num=0)
        r2 = self.radio()
        r2.wait_ready(5)
        self.assertEqual(r2.mode, "NARROW_FAST")  # through the BW62-SF7-CR6 alias

    def test_the_mode_setting_wins_over_the_radio(self):
        r = self.radio(mode="MY_MODE")
        r.wait_ready(5)
        self.assertEqual(r.mode, "MY_MODE")

    def test_an_unreadable_lora_config_gives_unknown_and_does_not_stop_the_session(self):
        self.factory = lambda host, port: SimpleNamespace(**dict(vars(FakeIface()), localNode=SimpleNamespace(
            channels=FakeIface().localNode.channels)))
        r = self.radio()
        self.assertTrue(r.wait_ready(5))
        self.assertEqual(r.mode, "UNKNOWN")

    def test_names_survive_the_interface_being_replaced(self):
        r = self.radio()
        r.wait_ready(5)
        self.assertEqual(r.short_name(1002), "CD34")
        self.ifaces[0].nodesByNum = {}  # e.g. mid-reconnect, node db not downloaded yet
        self.assertEqual(r.short_name(1002), "CD34")

    def test_wrong_channel_is_fatal_and_nothing_is_sent(self):
        self.factory = lambda host, port: FakeIface(channels=[(2, "Other")])
        r = self.radio()
        self.assertFalse(r.wait_ready(3))
        self.assertIn("expected 'Test_Channel'", r.fatal)
        with self.assertRaises(SystemExit):
            r.send("x", time.time() + 2)

    def test_channel_check_can_be_disabled(self):
        self.factory = lambda host, port: FakeIface(channels=[(2, "Other")])
        self.assertTrue(self.radio(channel_name="").wait_ready(3))

    def test_retries_until_the_radio_answers(self):
        self.fail_first = 2
        r = self.radio()
        self.assertTrue(r.wait_ready(20))
        self.assertIsNone(r.fatal)

    def test_send_waits_for_the_connection(self):
        self.fail_first = 2
        r = self.radio()
        self.assertEqual(r.send("hello", time.time() + 20), "hello")
        self.assertEqual(self.ifaces[0].sent, [("hello", 2, False)])

    def test_send_builds_the_text_at_the_last_moment_with_the_current_mode(self):
        r = self.radio()
        r.wait_ready(5)
        body = r.send(lambda: bot.build_message(self.cfg, r.mode, 1, 3), time.time() + 5)
        self.assertEqual(body, "MTBOT LONG_FAST | Lisboa | 1/3")

    def test_send_keeps_the_minimum_gap(self):
        r = self.radio()
        r.wait_ready(5)
        self.assertTrue(r.send("a", time.time() + 5))
        t0 = time.time()
        self.assertTrue(r.send("b", time.time() + 5))
        self.assertGreaterEqual(time.time() - t0, 0.25)  # min_gap_seconds = 0.3

    def test_send_gives_up_at_its_deadline(self):
        self.fail_first = 10 ** 6  # never connects
        r = self.radio()
        t0 = time.time()
        self.assertIsNone(r.send("x", time.time() + 0.6))
        self.assertLess(time.time() - t0, 3)

    def test_reconnects_after_the_connection_dies_and_reports_the_outage(self):
        r = self.radio()
        r.wait_ready(5)
        self.ifaces[0].isConnected.clear()  # the library's reader died
        deadline = time.time() + 20
        while len(self.ifaces) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(self.ifaces), 2)
        time.sleep(0.2)
        self.assertEqual(r.outage_count(), 1)
        self.assertGreater(r.outage_seconds(), 0)
        self.assertTrue(self.ifaces[0].closed)  # the old interface was closed
        self.assertTrue(r.send("x", time.time() + 5))
        self.assertEqual(len(self.ifaces[1].sent), 1)  # ...and the new one is used

    def wait_for(self, condition, seconds=10):
        end = time.time() + seconds
        while time.time() < end and not condition():
            time.sleep(0.02)
        return condition()

    def test_a_reconnection_the_library_does_by_itself_is_counted(self):
        r = self.radio()
        r.wait_ready(5)
        iface = self.ifaces[0]
        iface._reconnect()  # the library's reader thread calls it; the radio has wrapped it
        self.assertIsNone(iface.myInfo)
        time.sleep(0.4)  # ...the configuration is not back yet: the radio is deaf
        self.assertEqual(r.outage_count(), 1)  # an outage in progress already shows
        iface.myInfo = SimpleNamespace(my_node_num=9999)  # ...the configuration is back
        self.assertTrue(self.wait_for(lambda: r.outages))
        self.assertEqual(len(r.outages), 1)
        self.assertGreaterEqual(r.outages[0], 0.35)
        self.assertEqual(len(self.ifaces), 1)  # no new connection was needed
        time.sleep(0.2)
        self.assertEqual(r.outage_count(), 1)  # and it is not counted twice

    def test_a_reconnect_that_changed_nothing_is_not_an_outage(self):
        self.factory = lambda host, port: (self.ifaces.append(FakeIface(restarts_config=False)), self.ifaces[-1])[1]
        r = self.radio()
        r.wait_ready(5)
        self.ifaces[0]._reconnect()  # e.g. another thread had already reconnected
        time.sleep(0.3)
        self.assertEqual((r.outage_count(), r.outage_seconds()), (0, 0))

    def test_a_reconnection_that_never_finishes_is_rebuilt_from_scratch(self):
        r = self.radio()
        r.LIB_RECONNECT_TIMEOUT = 0.3
        r.wait_ready(5)
        self.ifaces[0]._reconnect()  # ...and the configuration never comes back: up, but deaf
        self.assertTrue(self.wait_for(lambda: len(self.ifaces) == 2))
        self.assertTrue(self.wait_for(lambda: r.outages))
        self.assertEqual(len(r.outages), 1)
        self.assertGreaterEqual(r.outages[0], 0.3)  # counted from when the library began, not from the give-up
        self.assertTrue(self.ifaces[0].closed)
        self.assertTrue(r.send("x", time.time() + 5))
        self.assertEqual(len(self.ifaces[1].sent), 1)  # the new connection is the one used

    def test_no_outage_when_all_went_well(self):
        r = self.radio()
        r.wait_ready(5)
        self.assertEqual((r.outage_count(), r.outage_seconds()), (0, 0))

    def test_a_failing_packet_handler_does_not_break_the_listener(self):
        r = self.radio()
        r.heard = SimpleNamespace(feed_packet=lambda p: 1 / 0)
        with self.assertLogs("bot", "ERROR"):
            r._on_receive(packet(1002, "MTBOT LONG_FAST | x"), None)  # must not raise


class CheckRadioTest(unittest.TestCase):
    """check_radio: one bare connectivity probe (see schedule()'s startup check)."""

    def setUp(self):
        self.cfg = make_cfg(startup_check_seconds=2.0)
        for name, fake in (("_preset_name", preset_name), ("_region_name", lambda n: "EU_868")):
            patcher = mock.patch.object(bot, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_reachable_radio_passes_the_check_and_is_released(self):
        ifaces = []

        def factory(host, port):
            ifaces.append(FakeIface())
            return ifaces[-1]
        with self.assertLogs("bot", "INFO") as cm:
            ok = bot.check_radio(self.cfg, factory)
        self.assertTrue(ok)
        self.assertTrue(ifaces[0].closed)
        self.assertTrue(any("Startup check OK" in m and "x:4403" in m and "!0000270f" in m for m in cm.output))
        # the supervisor thread stays alive (idling in its healthy loop) after a success --
        # that must not be confused with a still-in-flight connect attempt (connect_in_flight)
        self.assertFalse(any("waiting for a pending" in m for m in cm.output))

    def test_an_unreachable_radio_fails_naming_host_port_and_the_underlying_error(self):
        def factory(host, port):
            raise OSError("No route to host")
        with self.assertLogs("bot", "ERROR") as cm:
            ok = bot.check_radio(self.cfg, factory, timeout=0.3)
        self.assertIs(ok, False)
        msg = cm.output[-1]
        self.assertIn("Startup check FAILED", msg)
        self.assertIn("x:4403", msg)
        self.assertIn("OSError: No route to host", msg)
        self.assertIn("MTBOT_HOST", msg)

    def test_a_hanging_connection_times_out_and_is_fully_closed_before_check_radio_returns(self):
        # Simulates a connect attempt that eventually resolves on its own (e.g. once the OS's
        # own SYN retries give up) -- our own `timeout` gives up long before that, but
        # check_radio must not return until that straggling attempt is actually done, so a
        # later attempt or the session can never overlap it on the radio's single client slot.
        # The factory is released from a wrapped Radio.close, strictly after _stop.set(), so
        # there is no race against wait_ready's own ~0.5s polling granularity (a fixed delay
        # here could otherwise resolve the hang before -- or after -- wait_ready gives up).
        release = threading.Event()
        ifaces = []

        def factory(host, port):
            release.wait(10)
            ifaces.append(FakeIface())
            return ifaces[-1]

        original_close = bot.Radio.close
        seen = {}
        # Deliberately NOT Radio.close's own default (5.0), so this actually distinguishes
        # "check_radio passed our long join_timeout through" from "it silently fell back to
        # close()'s ordinary default".
        self.cfg["startup_check_join_seconds"] = 7.0

        def close_and_release(self, join_timeout=5.0):
            seen["join_timeout"] = join_timeout
            seen["thread"] = self._thread
            self._stop.set()
            release.set()
            original_close(self, join_timeout)
        with mock.patch.object(bot.log, "warning") as warn, mock.patch.object(bot.Radio, "close", close_and_release):
            with self.assertLogs("bot") as cm:
                ok = bot.check_radio(self.cfg, factory, timeout=0.3)
        self.assertIs(ok, False)
        errors = [m for m in cm.output if m.startswith("ERROR:")]
        self.assertIn("no answer", errors[-1])
        # no spurious log from the late-finishing RadioError("session ended") path in _supervise
        self.assertEqual(len(errors), 1)
        warn.assert_not_called()
        self.assertTrue(any("waiting for a pending" in m for m in cm.output))  # the in-flight INFO heads-up
        # the guarantee this test exists to prove: the long join was used, and really waited
        # the thread out, so it can never straggle into a later attempt or the session
        self.assertEqual(seen["join_timeout"], 7.0)
        self.assertFalse(seen["thread"].is_alive())
        self.assertTrue(ifaces and ifaces[0].closed)  # the late connection was torn down, not left dangling

    def test_a_connection_that_outlives_the_join_timeout_logs_a_warning(self):
        # STARTUP_CHECK_JOIN_SECONDS is generous but still bounded: if a connect attempt
        # somehow outlives it (e.g. a multi-address hostname, each address getting its own
        # SYN timeout), check_radio must not block forever -- it should return anyway, but
        # warn that a stale attempt may still be running.
        release = threading.Event()
        self.addCleanup(release.set)  # let the background thread finish, so it doesn't leak into later tests

        def factory(host, port):
            release.wait(10)
            return FakeIface()
        self.cfg["startup_check_join_seconds"] = 0.2  # deliberately shorter than the hang
        with mock.patch.object(bot.log, "warning") as warn:
            with self.assertLogs("bot", "ERROR"):
                ok = bot.check_radio(self.cfg, factory, timeout=0.3)
        self.assertIs(ok, False)
        self.assertTrue(any("may still be pending" in c.args[0] for c in warn.call_args_list))

    def test_a_hung_attempt_that_later_fails_leaves_no_trace_once_stopped(self):
        # Covers the _supervise branch "except Exception: if self._stop.is_set(): return" --
        # a normal (non-hanging-forever) exception arriving after close() must not overwrite
        # last_error or log anything, since check_radio has already reported its own result.
        # The factory is released from a wrapped Radio.close, strictly after _stop.set(), so
        # there is no race between "the attempt fails" and "close() marks it as stopped".
        release = threading.Event()

        def factory(host, port):
            release.wait(10)
            raise OSError("late failure, after the probe gave up")

        original_close = bot.Radio.close

        def close_then_release(self, join_timeout=5.0):
            self._stop.set()
            release.set()
            original_close(self, join_timeout)
        self.cfg["startup_check_join_seconds"] = 5.0
        with mock.patch.object(bot.log, "warning") as warn, mock.patch.object(bot.log, "error") as err, \
                mock.patch.object(bot.Radio, "close", close_then_release):
            ok = bot.check_radio(self.cfg, factory, timeout=0.3)
        self.assertIs(ok, False)
        err.assert_called_once()  # only check_radio's own FAILED line
        warn.assert_not_called()  # not the supervisor's "No connection ...; retrying" line

    def test_a_wrong_channel_fails_and_says_why(self):
        def factory(host, port):
            return FakeIface(channels=[(2, "Other")])
        with self.assertLogs("bot", "ERROR") as cm:
            ok = bot.check_radio(self.cfg, factory, timeout=2)
        self.assertIsNone(ok)
        msg = cm.output[-1]
        self.assertIn("expected 'Test_Channel'", msg)
        self.assertIn("MTBOT_CHANNEL_NAME", msg)

    def test_an_empty_channel_name_checks_reachability_only(self):
        self.cfg["channel_name"] = ""
        with self.assertLogs("bot", "INFO") as cm:
            ok = bot.check_radio(self.cfg, lambda h, p: FakeIface(channels=[(2, "Other")]), timeout=2)
        self.assertTrue(ok)
        self.assertTrue(any("not checked" in m for m in cm.output))

    def test_a_radio_that_answers_after_a_retry_passes_with_no_error_logged(self):
        self.fail_first = 1
        ifaces = []

        def factory(host, port):
            if self.fail_first > 0:
                self.fail_first -= 1
                raise OSError("radio unreachable")
            ifaces.append(FakeIface())
            return ifaces[-1]
        with mock.patch.object(bot.log, "error") as err:
            ok = bot.check_radio(self.cfg, factory, timeout=10)
        self.assertTrue(ok)
        err.assert_not_called()

    def test_messages_handed_over_on_connect_are_not_recorded(self):
        try:
            from pubsub import pub
        except ImportError:
            self.skipTest("pypubsub not installed")

        def factory(host, port):
            iface = FakeIface()
            pub.sendMessage("meshtastic.receive", packet=packet(1002, "MTBOT LONG_FAST | X | 1/3"), interface=iface)
            return iface
        with mock.patch.object(bot.log, "exception") as exc:
            ok = bot.check_radio(self.cfg, factory, timeout=2)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.cfg["rx_file"]))
        # if the probe were (wrongly) subscribed, _on_receive would hit self.heard being None
        # and swallow an AttributeError via log.exception -- assert that never happens
        exc.assert_not_called()

    def test_an_unexpected_error_in_the_check_is_logged_and_does_not_propagate(self):
        with mock.patch.object(bot.Radio, "start", side_effect=RuntimeError("boom")):
            with self.assertLogs("bot", "ERROR") as cm:
                ok = bot.check_radio(self.cfg, lambda h, p: FakeIface(), timeout=1)
        self.assertIs(ok, False)
        self.assertIn("RuntimeError: boom", cm.output[-1])

    def test_ctrl_c_during_the_check_still_releases_the_radio(self):
        closed = []
        original_close = bot.Radio.close

        def spy_close(self, join_timeout=5.0):
            closed.append(True)
            original_close(self, join_timeout)
        with mock.patch.object(bot.Radio, "wait_ready", side_effect=KeyboardInterrupt):
            with mock.patch.object(bot.Radio, "close", spy_close):
                with self.assertRaises(KeyboardInterrupt):
                    bot.check_radio(self.cfg, lambda h, p: FakeIface(), timeout=1)
        self.assertEqual(closed, [True])


class StartupCheckRetryPolicyTest(unittest.TestCase):
    """_startup_check's retry/backoff/give-up policy, isolated from check_radio's real
    networking (patched directly, so calls are instant) and from wall-clock timing (a fake
    clock plus a no-op time.sleep that just advances it) -- deterministic, unlike driving the
    policy through a real failing Radio (whose wait_ready() has its own ~0.5s polling
    granularity regardless of the requested timeout)."""

    def setUp(self):
        self.cfg = make_cfg(startup_check_min_lead_minutes=5.0, startup_check_backoff_seconds=5.0,
                            startup_check_backoff_cap_seconds=60.0)
        self.now = datetime.now(self.cfg["tz"])
        self.sleeps = []

        def fake_sleep(s):
            self.sleeps.append(s)
            self.now += timedelta(seconds=s)
        # Patch bot's own reference to the time module, not time.sleep process-wide: a
        # leftover daemon thread from another test calling the real time.sleep would
        # otherwise also append to self.sleeps and advance the fake clock, breaking the
        # exact-equality assertions below.
        # time=time.time (the real one) too: a leftover thread from another test calling
        # time.time() during this one must not hit an AttributeError on a bare sleep-only stub.
        time_patcher = mock.patch.object(bot, "time", SimpleNamespace(sleep=fake_sleep, time=time.time))
        time_patcher.start()
        self.addCleanup(time_patcher.stop)
        datetime_patcher = mock.patch.object(bot, "datetime", SimpleNamespace(now=lambda tz: self.now))
        datetime_patcher.start()
        self.addCleanup(datetime_patcher.stop)
        self.wake = self.now + timedelta(minutes=10)  # far enough away that the too-close-to-the-session skip never triggers

    def test_the_backoff_sequence_doubles_and_is_capped(self):
        with mock.patch.object(bot, "check_radio", side_effect=[False] * 6 + [True]):
            bot._startup_check(self.cfg, None, self.wake)  # must not raise
        self.assertEqual(self.sleeps, [5, 10, 20, 40, 60, 60])

    def test_the_final_delay_is_clamped_to_the_remaining_window_not_overshot(self):
        with mock.patch.object(bot, "check_radio", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                bot._startup_check(self.cfg, None, self.wake)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(sum(self.sleeps), 300)  # the 5-minute window, exactly -- never overshot
        self.assertTrue(all(s <= 60 for s in self.sleeps))
        self.assertLess(self.sleeps[-1], 60)  # the last wait was clamped short, not a full 60s

    def test_giving_up_is_logged_before_the_process_exits(self):
        with mock.patch.object(bot, "check_radio", return_value=False):
            with self.assertLogs("bot", "ERROR") as cm, self.assertRaises(SystemExit) as cm2:
                bot._startup_check(self.cfg, None, self.wake)
        self.assertEqual(cm2.exception.code, 1)
        self.assertTrue(any("giving up" in m for m in cm.output))

    def test_a_channel_mismatch_exits_at_once_with_no_retry_delay(self):
        with mock.patch.object(bot, "check_radio", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                bot._startup_check(self.cfg, None, self.wake)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self.sleeps, [])

    def test_success_after_some_failures_returns_normally_with_the_right_delays(self):
        with mock.patch.object(bot, "check_radio", side_effect=[False, False, True]):
            bot._startup_check(self.cfg, None, self.wake)  # must not raise
        self.assertEqual(self.sleeps, [5, 10])


class SessionTest(unittest.TestCase):
    """A whole (short) session against a fake radio."""

    def test_sends_listens_and_writes_both_reports(self):
        try:
            from pubsub import pub
        except ImportError:
            self.skipTest("pypubsub not installed")
        cfg = make_cfg(message_count=1, random_schedule=False, interval_minutes=0.0, min_gap_seconds=0.1,
                       report_window_minutes=0.0, listen_minutes=0.05)
        iface = FakeIface()
        start = datetime.now(cfg["tz"])
        end = start + timedelta(seconds=2.5)

        def deliver():
            time.sleep(0.8)  # a station is heard while we run
            pub.sendMessage("meshtastic.receive", packet=packet(1002, "MTBOT LONG_FAST | Vila Alta | 1/3", snr=4.5,
                                                               rssi=-98, hop_start=3, hop_limit=3), interface=iface)
        threading.Thread(target=deliver, daemon=True).start()

        with mock.patch.object(bot, "_preset_name", preset_name), mock.patch.object(bot, "_region_name", lambda n: "EU_868"):
            bot.run_window(cfg, start, end, factory=lambda host, port: iface)

        self.assertEqual(iface.sent, [("MTBOT LONG_FAST | Lisboa | 1/1", 2, False)])
        text = open(cfg["report_file"], encoding="utf-8").read()
        self.assertIn("!000003ea", text)
        self.assertIn("Vila Alta | RF", text)
        lines = open(cfg["report_json_file"], encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 1)  # one JSON object per session
        rep = json.loads(lines[0])
        self.assertEqual((rep["schema"], rep["radio"]["mode"], rep["outages"]["count"]), (1, "LONG_FAST", 0))
        self.assertEqual([(m["seq"], m["total"], m["ok"], m["text"]) for m in rep["sent"]],
                         [(1, 1, True, "MTBOT LONG_FAST | Lisboa | 1/1")])
        self.assertEqual([(e["name"], e["received"], e["of"], e["path"]) for e in rep["heard"]], [("CD34", 1, 3, "rf")])
        self.assertTrue(iface.closed)


class _StopSchedule(BaseException):
    """Raised by a patched run_window to stop schedule()'s infinite loop after a set number
    of sessions, without being caught by schedule()'s own except SystemExit/except Exception
    (both of which only catch Exception, not BaseException)."""


class ScheduleStartupCheckTest(unittest.TestCase):
    """schedule()'s one-time startup connectivity check (see check_radio), integrated with
    the rest of the scheduling loop."""

    def setUp(self):
        for name, fake in (("_preset_name", preset_name), ("_region_name", lambda n: "EU_868")):
            patcher = mock.patch.object(bot, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def window(self, cfg, start_in):
        now = datetime.now(cfg["tz"])
        start = now + timedelta(seconds=start_in)
        return start, start + timedelta(minutes=30)

    def test_a_reachable_radio_is_checked_once_after_next_session_is_logged_then_the_session_runs(self):
        cfg = make_cfg(wake_before_minutes=0.0, startup_check_min_lead_minutes=0.02)
        start, end = self.window(cfg, 2.0)
        factory = lambda h, p: FakeIface()  # noqa: E731
        run_window = mock.Mock(side_effect=_StopSchedule)
        with mock.patch.object(bot, "next_window", return_value=(start, end)), \
                mock.patch.object(bot, "run_window", run_window):
            with self.assertLogs("bot", "INFO") as cm, self.assertRaises(_StopSchedule):
                bot.schedule(cfg, factory=factory)
        next_idx = next(i for i, m in enumerate(cm.output) if "Next session" in m)
        ok_idx = next(i for i, m in enumerate(cm.output) if "Startup check OK" in m)
        self.assertLess(next_idx, ok_idx)
        run_window.assert_called_once()
        args, kwargs = run_window.call_args
        self.assertEqual(args[2], end)
        self.assertIs(kwargs.get("factory"), factory)  # the same factory schedule() was given

    def test_a_channel_mismatch_exits_immediately_without_retry(self):
        cfg = make_cfg(wake_before_minutes=0.0, startup_check_min_lead_minutes=0.02)
        start, end = self.window(cfg, 2.0)
        factory_calls = []

        def factory(host, port):
            factory_calls.append(1)
            return FakeIface(channels=[(2, "Other")])
        run_window = mock.Mock(side_effect=_StopSchedule)
        with mock.patch.object(bot, "next_window", return_value=(start, end)), \
                mock.patch.object(bot, "run_window", run_window):
            with self.assertRaises(SystemExit) as cm:
                bot.schedule(cfg, factory=factory)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(factory_calls), 1)  # no retry: a mismatch will not fix itself
        run_window.assert_not_called()

    def test_an_unreachable_radio_retries_then_succeeds_and_the_session_still_runs(self):
        # check_radio itself is exercised for real elsewhere (CheckRadioTest); here we only
        # need schedule()/_startup_check() to call it repeatedly and react to its result, so
        # it's mocked directly -- deterministic, unlike going through a real failing Radio
        # (whose wait_ready() has its own ~0.5s polling granularity regardless of `timeout`).
        cfg = make_cfg(wake_before_minutes=0.0, startup_check_min_lead_minutes=0.02,
                       startup_check_backoff_seconds=0.01)
        start, end = self.window(cfg, 2.0)
        run_window = mock.Mock(side_effect=_StopSchedule)
        with mock.patch.object(bot, "next_window", return_value=(start, end)), \
                mock.patch.object(bot, "run_window", run_window), \
                mock.patch.object(bot, "check_radio", side_effect=[False, False, True]) as check:
            with self.assertLogs("bot", "INFO") as cm, self.assertRaises(_StopSchedule):
                bot.schedule(cfg, factory="the-factory")
        self.assertEqual(check.call_count, 3)
        self.assertTrue(any("Next session" in m for m in cm.output))
        run_window.assert_called_once()
        self.assertIs(run_window.call_args.kwargs["factory"], "the-factory")

    def test_a_persistently_unreachable_radio_gives_up_and_exits(self):
        cfg = make_cfg(wake_before_minutes=0.0, startup_check_min_lead_minutes=0.005,  # 300ms window
                       startup_check_backoff_seconds=0.02)
        start, end = self.window(cfg, 2.0)
        run_window = mock.Mock(side_effect=_StopSchedule)
        with mock.patch.object(bot, "next_window", return_value=(start, end)), \
                mock.patch.object(bot, "run_window", run_window), \
                mock.patch.object(bot, "check_radio", return_value=False) as check:
            with self.assertRaises(SystemExit) as cm:
                bot.schedule(cfg, factory="the-factory")
        self.assertEqual(cm.exception.code, 1)
        self.assertGreater(check.call_count, 1)  # it did retry, not just try once
        run_window.assert_not_called()

    def test_the_check_is_skipped_when_the_session_connects_soon_anyway(self):
        cfg = make_cfg(wake_before_minutes=0.0, startup_check_min_lead_minutes=5.0)
        start, end = self.window(cfg, 0.3)
        factory_calls = []

        def factory(host, port):
            factory_calls.append(1)
            return FakeIface()
        run_window = mock.Mock(side_effect=_StopSchedule)
        with mock.patch.object(bot, "next_window", return_value=(start, end)), \
                mock.patch.object(bot, "run_window", run_window):
            with self.assertLogs("bot", "INFO") as cm, self.assertRaises(_StopSchedule):
                bot.schedule(cfg, factory=factory)
        self.assertTrue(any("Startup check skipped" in m for m in cm.output))
        self.assertEqual(factory_calls, [])
        run_window.assert_called_once()

    def test_the_check_runs_once_per_process_not_before_every_session(self):
        cfg = make_cfg(wake_before_minutes=0.0, startup_check_seconds=2.0,
                       startup_check_min_lead_minutes=0.02, startup_check_backoff_seconds=0.05)
        win1 = self.window(cfg, 3.0)
        win2 = self.window(cfg, 3.2)
        windows = iter([win1, win2])
        calls = {"run_window": 0}

        def run_window_stub(*a, **k):
            calls["run_window"] += 1
            if calls["run_window"] >= 2:
                raise _StopSchedule()
        factory_calls = []

        def factory(host, port):
            factory_calls.append(1)
            return FakeIface()
        with mock.patch.object(bot, "next_window", side_effect=lambda *a: next(windows)), \
                mock.patch.object(bot, "run_window", side_effect=run_window_stub):
            with self.assertRaises(_StopSchedule):
                bot.schedule(cfg, factory=factory)
        self.assertEqual(calls["run_window"], 2)
        self.assertEqual(len(factory_calls), 1)  # the second session is not re-checked


class MainStartupCheckTest(unittest.TestCase):
    """--dry-run (with or without --schedule) and one-shot runs must never probe the radio."""

    def ini(self, extra=""):
        path = os.path.join(tempfile.mkdtemp(), "bot.ini")
        open(path, "w", encoding="utf-8").write("[bot]\nchannel = 1\nplace = X\n" + extra)
        return path

    def test_schedule_dry_run_never_probes_the_radio(self):
        path = self.ini()
        with mock.patch.object(bot, "check_radio") as check:
            rc = bot.main(["--config", path, "--schedule", "--dry-run"])
        self.assertEqual(rc, 0)
        check.assert_not_called()

    def test_plain_dry_run_never_probes_the_radio(self):
        path = self.ini()
        with mock.patch.object(bot, "check_radio") as check:
            rc = bot.main(["--config", path, "--now", "--dry-run"])
        self.assertEqual(rc, 0)
        check.assert_not_called()

    def test_one_shot_runs_never_call_check_radio(self):
        path = self.ini()
        with mock.patch.object(bot, "check_radio") as check, mock.patch.object(bot, "run_window") as rw:
            rc = bot.main(["--config", path, "--now", "--fixed-schedule", "--listen-minutes", "0", "--count", "0"])
        self.assertEqual(rc, 0)
        check.assert_not_called()
        rw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
