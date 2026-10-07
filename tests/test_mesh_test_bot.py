# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests. All names and nodes are invented; the message shapes are the ones this bot
sends. Nothing here touches a radio. Run: python3 -m unittest discover tests"""
import json
import os
import random
import subprocess
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
           "report_prefix": "ACK", "mode": "", "mode_aliases": {"BW62-SF7-CR6": "NARROW_FAST"}, "min_gap_seconds": 0.3,
           "session_tolerance_seconds": 60.0, "wake_before_minutes": bot.WAKE_BEFORE_MINUTES,
           "startup_check_seconds": bot.STARTUP_CHECK_SECONDS,
           "startup_check_min_lead_minutes": bot.STARTUP_CHECK_MIN_LEAD_MINUTES,
           "startup_check_backoff_seconds": bot.STARTUP_CHECK_BACKOFF_SECONDS,
           "startup_check_backoff_cap_seconds": bot.STARTUP_CHECK_BACKOFF_CAP_SECONDS,
           "startup_check_join_seconds": bot.STARTUP_CHECK_JOIN_SECONDS,
           "tz": ZoneInfo("Europe/Lisbon"), "timezone": "Europe/Lisbon",
           "rx_file": os.path.join(tmp, "rx.log"), "report_file": os.path.join(tmp, "report.txt"),
           "report_json_file": os.path.join(tmp, "report.jsonl")}
    cfg.update(kw)
    cfg["prefix_re"] = bot.prefix_regex(cfg["keyword"])  # after update(): keyword= overrides take effect
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
        self.assertEqual(cfg["report_prefix"], "ACK")
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
        for ini in ("place = A|B\n", "mode = a b\n", "mode_aliases = xyz\n",
                    "place = " + "x" * 200 + "\n", "timezone = Foo/Bar\n", "weekday = xpto\n", "start_time = 25:99\n"):
            with self.subTest(ini=ini), self.assertRaises(SystemExit):
                self.load("channel = 1\n" + ("" if ini.startswith("place") else "place = Lisboa\n") + ini)

    def test_keyword_may_be_a_multi_word_phrase(self):
        # file, env and flag all accept it; punctuation between words is also fine, since
        # the prefix regex treats it as a harmless joiner (see ParseMessageTest).
        cfg = self.load("channel = 1\nplace = Lisboa\nkeyword = FIELD TEST ALPHA\n")
        self.assertEqual(cfg["keyword"], "FIELD TEST ALPHA")
        self.assertTrue(cfg["prefix_re"].match("field test alpha | x | 1/3"))
        cfg = self.load("channel = 1\nplace = Lisboa\nkeyword = FIELD-TEST, ALPHA\n")
        self.assertTrue(cfg["prefix_re"].match("field test alpha | x | 1/3"))

    def test_unsafe_keywords_stop_the_start(self):
        for bad in ("a|b", "!!!", "---", "", "A\x0bB"):
            # the "=" form, not two separate argv items: argparse would otherwise treat a
            # dash-leading value like "---" as an unrecognized option of its own.
            with self.subTest(keyword=bad), self.assertRaises(SystemExit) as cm:
                self.load("channel = 1\nplace = Lisboa\n", "--keyword=" + bad)
            self.assertIn("keyword", str(cm.exception))

    def test_a_long_keyword_and_place_that_do_not_fit_stop_the_start(self):
        with self.assertRaises(SystemExit) as cm:
            self.load("channel = 1\nplace = " + "x" * 80 + "\nkeyword = " + "y " * 60 + "\n")
        self.assertIn("keyword", str(cm.exception))

    def test_place_with_a_comma_or_hyphen_warns_but_still_starts(self):
        # Peers only see the text after the last separator -- a warning, not a hard
        # rejection, since this merged the moment it ships, reaching deployments that
        # auto-update and already have such a place configured.
        with self.assertLogs("bot", "WARNING"):
            cfg = self.load("channel = 1\nplace = Vila, Sul\n")
        self.assertEqual(cfg["place"], "Vila, Sul")
        with self.assertLogs("bot", "WARNING"):
            self.load("channel = 1\nplace = Vila-Nova\n")

    def test_report_prefix_defaults_to_ack_and_is_configurable(self):
        self.assertEqual(self.load("channel = 1\nplace = Lisboa\n")["report_prefix"], "ACK")
        cfg = self.load("channel = 1\nplace = Lisboa\nreport_prefix = DONE\n")
        self.assertEqual(cfg["report_prefix"], "DONE")

    def test_bad_report_prefix_stops_the_start(self):
        with self.assertRaises(SystemExit):
            self.load("channel = 1\nplace = Lisboa\nreport_prefix =\n")

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

    def test_message_is_built_and_parsed_back(self):
        cfg = self.load("channel = 1\nplace = Vila Nova\n")
        text = bot.build_message(cfg, "ABCD", 2, 3)
        self.assertEqual(text, "MTBOT | ABCD | Vila Nova | 2/3")
        self.assertEqual(bot.parse_message(text, cfg["prefix_re"]), bot.Parsed("Vila Nova", 2, 3))


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
                               "MTBOT_PORT": "4404", "MTBOT_MESSAGE_COUNT": "5"})
        self.assertEqual((cfg["channel"], cfg["place"], cfg["host"], cfg["port"], cfg["message_count"]),
                         (1, "Porto", "10.0.0.2", 4404, 5))

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
            t0 = time.time()
            iface = bot.tcp_factory("10.0.0.5", 4404)
        tcp.assert_called_once_with(hostname="10.0.0.5", portNumber=4404, connectNow=False)
        tcp.return_value.connect.assert_called_once_with()
        self.assertIs(iface, tcp.return_value)
        # If _startHeartbeat were wrapped only AFTER connect() (recreating the original race),
        # this synchronous side_effect would call the unwrapped original, the Event would
        # never be set, and this would take the full HEARTBEAT_SETTLE_SECONDS instead.
        self.assertLess(time.time() - t0, 1.0)

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
            t0 = time.time()
            bot.tcp_factory("10.0.0.5", 4404)
        original.assert_called_once_with()
        self.assertLess(time.time() - t0, 1.0)  # see the same note in test_it_connects_...

    def test_the_heartbeat_signal_still_fires_if_the_original_raises(self):
        # The wrapper's `finally` must set the Event even when the original call fails (e.g.
        # a real sendall() failure) -- otherwise that failure mode would silently regress to
        # the full HEARTBEAT_SETTLE_SECONDS wait on every such connection.
        try:
            import meshtastic.tcp_interface  # noqa: F401
        except ImportError:
            self.skipTest("meshtastic not installed")
        with mock.patch("meshtastic.tcp_interface.TCPInterface") as tcp:
            tcp.return_value._startHeartbeat.side_effect = OSError("send failed")

            def fire_heartbeat_soon():
                time.sleep(0.05)
                try:
                    tcp.return_value._startHeartbeat()  # by now, our wrapper around the failing original
                except OSError:
                    pass
            tcp.return_value.connect.side_effect = (
                lambda: threading.Thread(target=fire_heartbeat_soon, daemon=True).start())
            t0 = time.time()
            bot.tcp_factory("10.0.0.5", 4404)
        self.assertLess(time.time() - t0, 1.0)


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


class ParseMessageTest(unittest.TestCase):
    """prefix_regex + parse_message: the flexible prefix match and the tolerant,
    right-anchored field parser. FIELD TEST ALPHA is only ever an illustrative
    placeholder here, never a real default."""

    def setUp(self):
        self.prefix_re = bot.prefix_regex("FIELD TEST ALPHA")

    def parse(self, text):
        return bot.parse_message(text, self.prefix_re)

    def test_prefix_matching_is_forgiving(self):
        for text in ("FIELD TEST ALPHA | x", "FIELD, test alpha | x", "field testalpha | x",
                    "FIELD...TEST__ALPHA | x"):
            with self.subTest(text=text):
                self.assertIsNotNone(self.parse(text))

    def test_prefix_must_end_at_a_word_boundary(self):
        self.assertIsNone(self.parse("FIELD TEST ALPHAS | Lisboa | 1/3"))
        self.assertIsNotNone(self.parse("FIELD TEST ALPHA| Lisboa | 1/3"))

    def test_leading_junk_is_allowed_but_not_a_prefix_mid_text(self):
        self.assertEqual(self.parse("  >> FIELD TEST ALPHA | Cascais | 1/3").place, "Cascais")
        self.assertIsNone(self.parse("see FIELD TEST ALPHA | Cascais"))

    def test_non_ascii_prefix_words(self):
        prefix_re = bot.prefix_regex("ÁLFA TESTE")
        self.assertEqual(bot.parse_message("álfa-teste | Cascais", prefix_re).place, "Cascais")

    # The 8 worked examples from the spec, verbatim.
    def test_worked_free_text_then_place_then_numbering(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA, random text blah, my super city, 1/3"),
                         bot.Parsed("my super city", 1, 3))

    def test_worked_last_field_is_place_when_not_numbered(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA, radio name, city, locality"),
                         bot.Parsed("locality", None, None))

    def test_worked_irregular_spacing(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA  ,  name, city"), bot.Parsed("city", None, None))

    def test_worked_hyphen_separators_and_joined_prefix(self):
        self.assertEqual(self.parse("field testalpha - ABCD - Cascais - 2/3"), bot.Parsed("Cascais", 2, 3))

    def test_worked_own_outgoing_format_round_trips(self):
        text = "FIELD TEST ALPHA | ABCD | Cascais | 2/3"
        self.assertEqual(self.parse(text), bot.Parsed("Cascais", 2, 3))

    def test_worked_prefix_alone_is_unparseable(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA"), bot.Parsed(bot.UNKNOWN_PLACE, None, None))

    def test_worked_prefix_and_numbering_only(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA, 2/3"), bot.Parsed(bot.UNKNOWN_PLACE, 2, 3))

    def test_worked_unrelated_chat_is_not_ours(self):
        self.assertIsNone(self.parse("just some unrelated chat"))

    def test_empty_fields_are_dropped(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA || Cascais ,, 2/3 -"), bot.Parsed("Cascais", 2, 3))

    def test_numbering_with_spaces_and_malformed_numbering(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA | Cascais | 2 / 3"), bot.Parsed("Cascais", 2, 3))
        self.assertEqual(self.parse("FIELD TEST ALPHA | Cascais | 2/"), bot.Parsed("2/", None, None))
        self.assertEqual(self.parse("FIELD TEST ALPHA | 2/3/4"), bot.Parsed("2/3/4", None, None))
        self.assertEqual(self.parse("FIELD TEST ALPHA | a/3"), bot.Parsed("a/3", None, None))

    def test_only_separators_after_prefix_is_unparseable(self):
        self.assertEqual(self.parse("FIELD TEST ALPHA , | -"), bot.Parsed(bot.UNKNOWN_PLACE, None, None))

    def test_separator_in_the_name_field_is_harmless(self):
        self.assertEqual(bot.parse_message("MTBOT | A-B | Lisboa | 1/3", bot.prefix_regex("MTBOT")),
                         bot.Parsed("Lisboa", 1, 3))

    def test_line_breaks_inside_a_field_do_not_reach_the_place(self):
        place = self.parse("FIELD TEST ALPHA\nCascais\n2/3").place
        self.assertNotIn("\n", place)

    def test_old_format_messages_still_parse(self):
        # A v0.2 sender's message (keyword, mode, place, n/m) under the new parser: the
        # mode becomes just another ignored middle field.
        self.assertEqual(bot.parse_message("MTBOT NARROW_FAST | Lisboa | 2/3", bot.prefix_regex("MTBOT")),
                         bot.Parsed("Lisboa", 2, 3))


class HeardTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        # 1004: in the radio's node db but without user info; 1006 and up: unknown
        names = {1001: "AB12", 1002: "CD34", 1003: "\U0001F98A", 1004: "", 1005: "GH78", 1009: "Me"}
        self.h = bot.Heard(self.cfg, Directory(names))

    def heard(self, frm, text, **kw):
        self.h.feed_packet(packet(frm, text, **kw))

    def entry(self, name):
        return next((e for e in self.h.entries() if e["name"] == name), None)

    def test_messages_are_parsed(self):
        cases = [
            (1001, "MTBOT LONG_FAST | Lisboa | 2/3", "AB12", "Lisboa"),
            (1002, "mtbot narrow_fast | Vila, Sul | 1/3", "CD34", "Sul"),  # comma splits the place: last segment wins
            (1005, "  MTBOT LONG_FAST|Serra|3/3", "GH78", "Serra"),  # spacing
            (1003, "MTBOT LONG_FAST | Rio", "\U0001F98A", "Rio"),  # not numbered
            (1006, "MTBOT SHORT_FAST | Praia | 1/1", "03ee", "Praia"),  # node unknown: id digits
            (1004, "MTBOT LONG_FAST | Alto | 1/2", "03ec", "Alto"),  # no user info
        ]
        for frm, text, name, place in cases:
            with self.subTest(text=text):
                self.heard(frm, text)
                e = self.entry(name)
                self.assertEqual((e["place"], e["path"]), (place, "rf"))

    def test_the_name_comes_from_the_node_never_from_the_text(self):
        self.heard(1001, "MTBOT | ZZZZ | Lisboa | 1/3")  # ZZZZ (a name field) is never used for the name
        self.assertEqual([e["name"] for e in self.h.entries()], ["AB12"])

    def test_other_messages_are_ignored(self):
        for text in ("Hey folks, how are you?",  # no prefix at all
                     "Test LONG_FAST | Lisboa", "General call, X, Village",  # other formats
                     "Confirmation, AB12, 3 hops", "see the MTBOT LONG_FAST | Lisboa",  # not at the start
                     "MTBOTS | Lisboa | 1/3"):  # prefix immediately followed by a letter: not a word boundary
            with self.subTest(text=text):
                self.heard(1001, text)
        self.heard(1001, "MTBOT LONG_FAST | Lisboa", channel=0)  # other channel
        self.heard(9999, "MTBOT LONG_FAST | Lisboa")  # our own node
        self.h.feed_packet({"from": 1001, "decoded": {"portnum": "POSITION_APP"}, "channel": 2})  # not text
        self.h.feed_packet({"from": 1001, "channel": 2})  # encrypted, undecoded
        self.assertEqual((self.h.rf, self.h.mqtt), ({}, {}))

    def test_a_bare_prefix_and_an_extra_field_both_now_count_as_ours(self):
        # These used to be rejected by the old strict regex (no pipe, or too many fields).
        # The new tolerant parser accepts both -- "MTBOT" alone is unparseable (place N/A,
        # still heard); the extra-field one picks the field right before the "n/m", per spec.
        self.heard(1001, "MTBOT")
        self.assertEqual(self.entry("AB12")["place"], bot.UNKNOWN_PLACE)
        self.heard(1002, "MTBOT LONG_FAST | Lisboa | extra | 1/3")
        self.assertEqual(self.entry("CD34")["place"], "extra")
        self.heard(1005, "MTBOT LONG_FAST Lisboa")  # no separator at all after the prefix: one whole field
        self.assertEqual(self.entry("GH78")["place"], "LONG_FAST Lisboa")

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

    def test_unparseable_messages_are_counted_with_na_place_and_full_signal(self):
        self.heard(1001, "MTBOT", snr=4.0, rssi=-100, hop_start=5, hop_limit=3, relay=0xE9)
        e = self.entry("AB12")
        self.assertEqual((e["place"], e["received"], e["of"], e["path"]), (bot.UNKNOWN_PLACE, 1, None, "rf"))
        self.assertEqual((e["hops_min"], e["hops_max"]), (2, 2))
        self.assertEqual(e["snr_avg"], 4.0)
        lines = open(self.cfg["rx_file"], encoding="utf-8").read().splitlines()
        self.assertNotIn("\tignored\t", lines[-1])  # it matched the prefix: not the same as "not ours"

    def test_numbering_without_place_keeps_the_numbering(self):
        self.heard(1001, "MTBOT | 2/3")
        e = self.entry("AB12")
        self.assertEqual((e["place"], e["received"], e["of"]), (bot.UNKNOWN_PLACE, 1, 3))

    def test_a_real_place_replaces_na_but_not_the_reverse(self):
        self.heard(1001, "MTBOT")  # N/A
        self.heard(1001, "MTBOT | X | Lisboa | 1/3")  # upgrades N/A -> a real place
        self.heard(1001, "MTBOT")  # does not downgrade a real place back to N/A
        self.heard(1001, "MTBOT | X | Sintra | 2/3")  # a later real place never replaces the first one either
        self.assertEqual(self.entry("AB12")["place"], "Lisboa")

    def test_unparseable_via_mqtt_only_is_flagged_mqtt(self):
        self.heard(1001, "MTBOT", mqtt=True)
        e = self.entry("AB12")
        self.assertEqual((e["path"], e["place"]), ("mqtt", bot.UNKNOWN_PLACE))


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
        entries = self.h.entries()
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

    def test_an_unparseable_stale_message_is_discarded_too(self):
        self.h.window_start = 1_800_000_000
        self.feed(1, 1_800_000_000 - 70, text="MTBOT")  # matches the prefix, but has no usable place
        self.assertEqual((self.received(), self.h.stale), (0, 1))

    def test_the_report_says_how_many_were_discarded_only_when_there_were(self):
        radio = SimpleNamespace(lora={"mode": "LONG_FAST"}, mode="LONG_FAST", my_num=9999, short_name=lambda n: "ME01",
                                outage_count=lambda: 0, outage_seconds=lambda: 0.0)
        tz = self.cfg["tz"]
        when = [datetime(2026, 9, 26, 21, 0, tzinfo=tz), datetime(2026, 9, 26, 21, 30, tzinfo=tz),
                datetime(2026, 9, 26, 21, 41, tzinfo=tz)]
        clean = bot.build_report(self.cfg, radio, self.h, *when, [])
        self.assertEqual(clean["ignored"], {"before_session": 0})
        self.assertNotIn("before the session started", bot.render_text(clean, "ACK"))
        self.replay(self.CONNECTED_AT)
        rep = bot.build_report(self.cfg, radio, self.h, *when, [])
        self.assertEqual(rep["ignored"], {"before_session": 8})
        self.assertIn("warning: 8 test message(s)", bot.render_text(rep, "ACK"))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.h = bot.Heard(self.cfg, Directory({1001: "AB12", 1002: "CD34", 1005: "EF56"}))
        self.radio = SimpleNamespace(
            lora={"mode": "LONG_FAST", "region": "EU_868", "use_preset": True, "modem_preset": 0, "bandwidth": 250,
                  "spread_factor": 11, "coding_rate": 5, "channel_num": 1},
            mode="LONG_FAST", my_num=0xDEADBEEF, short_name=lambda n: "ME01", outage_count=lambda: 0,
            outage_seconds=lambda: 0.0)
        tz = self.cfg["tz"]
        self.t = [datetime(2026, 9, 26, 21, 0, tzinfo=tz), datetime(2026, 9, 26, 21, 30, tzinfo=tz),
                  datetime(2026, 9, 26, 21, 41, tzinfo=tz)]

    def report(self, sent=None):
        sent = sent if sent is not None else [{"seq": 1, "total": 1, "ok": True, "text": "MTBOT | ME01 | Lisboa | 1/1"}]
        return bot.build_report(self.cfg, self.radio, self.h, *self.t, sent)

    def test_the_json_report(self):
        self.h.feed_packet(packet(1001, "MTBOT LONG_FAST | Vila Nova | 1/3", snr=4.5, rssi=-98))
        rep = json.loads(json.dumps(self.report()))  # it must survive a round trip
        self.assertEqual(rep["schema"], 2)
        self.assertEqual(rep["reporter"], {"node": "!deadbeef", "name": "ME01", "place": "Lisboa"})
        self.assertEqual((rep["radio"]["mode"], rep["radio"]["channel"], rep["radio"]["bandwidth"]), ("LONG_FAST", 2, 250))
        self.assertEqual(rep["session"]["start"], "2026-09-26T21:00:00+01:00")
        self.assertEqual(rep["outages"], {"count": 0, "total_s": 0})
        e = rep["heard"][0]
        self.assertEqual((e["node"], e["name"], e["place"], e["received"], e["of"], e["snr_avg"], e["rssi_avg"]),
                         ("!000003e9", "AB12", "Vila Nova", 1, 3, 4.5, -98))

    def test_json_heard_entries_have_exactly_the_schema_2_keys(self):
        self.h.feed_packet(packet(1001, "MTBOT"))  # unparseable: place must still be "N/A", not missing
        rep = json.loads(json.dumps(self.report()))
        self.assertEqual(rep["schema"], bot.SCHEMA)
        self.assertEqual(set(rep["heard"][0]), {
            "node", "name", "place", "path", "received", "of", "receptions", "duplicates", "hops_min", "hops_max",
            "snr_avg", "snr_best", "rssi_avg", "rssi_best", "relay", "mqtt_copies", "first", "last"})
        self.assertEqual(rep["heard"][0]["place"], "N/A")

    def test_the_text_report_has_one_compact_line_per_station(self):
        self.h.feed_packet(packet(1001, "MTBOT LONG_FAST | Vila Nova | 1/3", hop_start=5, hop_limit=5))  # RF, 0 hops
        self.h.feed_packet(packet(1002, "MTBOT NARROW_FAST | Vale | 1/3", mqtt=True, hop_start=0, hop_limit=0))
        self.h.feed_packet(packet(1005, "MTBOT", hop_start=4, hop_limit=3))  # unparseable, 1 hop
        self.h.feed_packet(packet(1005, "MTBOT", hop_start=4, hop_limit=1))  # unparseable, 3 hops
        lines = bot.render_text(self.report(), "ACK").splitlines()
        self.assertIn("Reporter: ME01 (!deadbeef) - Place: Lisboa - Sent: 1/1", lines[0])
        self.assertEqual(len(lines), 1 + 3)  # header + one line per station, no column-header row of its own

        rows = [line.split(" | ") for line in lines[1:]]
        self.assertEqual([c.strip() for c in rows[0]], ["ACK", "AB12", "0", "Vila Nova", "RF"])
        self.assertEqual([c.strip() for c in rows[1]], ["ACK", "CD34", "-", "Vale", "MQTT"])
        self.assertEqual([c.strip() for c in rows[2]], ["ACK", "EF56", "1-3", "N/A", "RF"])

        full = "\n".join(lines)
        for leaked in ("!000003e9", "SNR", "duplicates", "does not confirm RF"):
            self.assertNotIn(leaked, full)
        for line in lines[1:]:
            self.assertEqual(line, line.rstrip())  # no trailing whitespace

        # Every column but the last (free-form place) lines up: its "|" falls at the same
        # character position on every station row.
        for i in range(4):
            widths = {len(line.split(" | ")[i]) for line in lines[1:]}
            self.assertEqual(len(widths), 1, "column %d not aligned: %r" % (i, widths))

    def test_the_text_report_uses_the_configured_report_prefix(self):
        self.h.feed_packet(packet(1001, "MTBOT LONG_FAST | Vila Nova | 1/3"))
        lines = bot.render_text(self.report(), "DONE").splitlines()
        self.assertTrue(lines[1].startswith("DONE | "))

    def test_nothing_heard(self):
        self.assertIn("(no messages received)", bot.render_text(self.report(), "ACK"))
        self.assertEqual(self.report()["heard"], [])

    def test_failed_sends_and_outages_are_reported(self):
        self.radio.outage_count, self.radio.outage_seconds = (lambda: 2), (lambda: 7.4)
        rep = self.report(sent=[{"seq": 1, "total": 2, "ok": True, "text": "a"}, {"seq": 2, "total": 2, "ok": False, "text": None}])
        text = bot.render_text(rep, "ACK")
        self.assertIn("Sent: 1/2", text)
        self.assertIn("interrupted 2 time(s), ~7 s", text)
        self.assertEqual(rep["outages"], {"count": 2, "total_s": 7})


class ScheduleTest(unittest.TestCase):
    START = datetime(2026, 9, 19, 21, 0)
    END = datetime(2026, 9, 19, 21, 30)

    def cfg(self, **kw):
        cfg = {"message_count": 3, "min_gap_seconds": 60.0, "report_window_minutes": 30.0}
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

    def test_send_builds_the_text_at_the_last_moment_with_the_stations_own_name(self):
        r = self.radio()
        r.wait_ready(5)
        body = r.send(lambda: bot.build_message(
            self.cfg, bot.station_name(r.short_name(r.my_num), r.my_num), 1, 3), time.time() + 5)
        self.assertEqual(body, "MTBOT | 270f | Lisboa | 1/3")  # my_num 9999 = 0x270f, not in nodesByNum

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
        cfg = make_cfg(message_count=1, min_gap_seconds=0.1, report_window_minutes=0.0, listen_minutes=0.05)
        iface = FakeIface()
        start = datetime.now(cfg["tz"])
        end = start + timedelta(seconds=2.5)

        def deliver():
            time.sleep(0.8)  # a station is heard while we run
            pub.sendMessage("meshtastic.receive", packet=packet(1002, "MTBOT LONG_FAST | Vila Alta | 1/3", snr=4.5,
                                                               rssi=-98, hop_start=3, hop_limit=3), interface=iface)
        threading.Thread(target=deliver, daemon=True).start()

        with mock.patch.object(bot, "_preset_name", preset_name), mock.patch.object(bot, "_region_name", lambda n: "EU_868"), \
                mock.patch.object(bot.random, "random", lambda: 0.5):  # the one message at the middle of its window
            bot.run_window(cfg, start, end, factory=lambda host, port: iface)

        self.assertEqual(iface.sent, [("MTBOT | 270f | Lisboa | 1/1", 2, False)])  # my_num 9999 = 0x270f
        text = open(cfg["report_file"], encoding="utf-8").read()
        self.assertIn("ACK | CD34 | 0 | Vila Alta | RF", text)
        lines = open(cfg["report_json_file"], encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 1)  # one JSON object per session
        rep = json.loads(lines[0])
        self.assertEqual((rep["schema"], rep["radio"]["mode"], rep["outages"]["count"]), (bot.SCHEMA, "LONG_FAST", 0))
        self.assertEqual([(m["seq"], m["total"], m["ok"], m["text"]) for m in rep["sent"]],
                         [(1, 1, True, "MTBOT | 270f | Lisboa | 1/1")])
        self.assertEqual([(e["name"], e["place"], e["received"], e["of"], e["path"]) for e in rep["heard"]],
                         [("CD34", "Vila Alta", 1, 3, "rf")])
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
            rc = bot.main(["--config", path, "--now", "--listen-minutes", "30", "--count", "0"])
        self.assertEqual(rc, 0)
        check.assert_not_called()
        rw.assert_called_once()


class SessionLimitsTest(unittest.TestCase):
    """Always-random spacing, the normal limits, and --unsafe-limits. [AC/EDGE tags name the plan item]"""

    def load(self, ini, *flags, env=None):
        path = os.path.join(tempfile.mkdtemp(), "bot.ini")
        open(path, "w", encoding="utf-8").write("[bot]\nchannel = 1\nplace = Lisboa\n" + ini)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MTBOT_")}
        with mock.patch.dict(os.environ, dict(clean, **(env or {})), clear=True):
            return bot.load_config(["--config", path] + list(flags))[0]

    def test_defaults_have_no_fixed_spacing_settings(self):  # [AC-session-limits-1]
        self.assertNotIn("random_schedule", bot.DEFAULTS)
        self.assertNotIn("interval_minutes", bot.DEFAULTS)

    def test_help_lists_the_unsafe_flag_and_not_the_removed_ones(self):  # [AC-session-limits-1]
        out = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "mesh_test_bot.py"),
                              "--help"], capture_output=True, text=True, timeout=30).stdout
        self.assertIn("--unsafe-limits", out)
        self.assertNotIn("--fixed-schedule", out)
        self.assertNotIn("--interval", out)

    def test_normal_limits_refuse_and_accept_at_the_boundary(self):  # [AC-session-limits-2] [EDGE-session-limits-2]
        for ini in ("message_count = 11\n", "listen_minutes = 29.9\n", "report_window_minutes = 9.9\n"):
            with self.subTest(ini=ini), self.assertRaises(SystemExit):
                self.load(ini)
        self.load("message_count = 10\nlisten_minutes = 30\nreport_window_minutes = 10\n")

    def test_the_limits_apply_to_now_and_schedule_alike(self):  # [AC-session-limits-2]
        path = os.path.join(tempfile.mkdtemp(), "bot.ini")
        open(path, "w", encoding="utf-8").write("[bot]\nchannel = 1\nplace = X\n")
        with self.assertRaises(SystemExit):
            bot.main(["--config", path, "--now", "--listen-minutes", "20", "--dry-run"])
        with self.assertRaises(SystemExit):
            bot.main(["--config", path, "--schedule", "--count", "11", "--dry-run"])

    def test_a_negative_message_count_is_refused_in_both_modes(self):  # [AC-session-limits-2] (review)
        for flags in ((), ("--unsafe-limits",)):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as cm:
                self.load("message_count = -1\n", *flags)
            self.assertIn("cannot be negative", str(cm.exception))

    def test_normal_mode_gap_is_two_minutes(self):  # [AC-session-limits-3]
        self.assertEqual(self.load("")["min_gap_seconds"], 120.0)

    def test_ten_messages_in_thirty_minutes_are_all_sent_two_minutes_apart(self):  # [AC-session-limits-3] [EDGE-session-limits-1]
        start = datetime(2026, 9, 19, 21, 0)
        end = start + timedelta(minutes=30)
        cfg = {"message_count": 10, "min_gap_seconds": 120.0, "report_window_minutes": 10.0}
        for seed in range(300):
            with mock.patch.object(bot.log, "warning") as warn:
                t = bot.plan_send_times(cfg, start, end, random.Random(seed))
            self.assertEqual(len(t), 10)
            self.assertTrue(all(start <= x < end for x in t))
            self.assertTrue(all(b - a >= timedelta(seconds=120) for a, b in zip(t, t[1:])))
            warn.assert_not_called()

    def test_unsafe_limits_allows_short_dense_sessions_with_a_scaled_gap(self):  # [AC-session-limits-4]
        cfg = self.load("listen_minutes = 5\nmessage_count = 20\n", "--unsafe-limits")
        self.assertEqual(cfg["min_gap_seconds"], 7.5)

    def test_unsafe_scaled_gap_spreads_every_message_across_the_short_window(self):  # [AC-session-limits-4]
        start = datetime(2026, 9, 19, 21, 0)
        end = start + timedelta(minutes=5)
        cfg = {"message_count": 20, "min_gap_seconds": 7.5, "report_window_minutes": 10.0}
        for seed in range(300):
            t = bot.plan_send_times(cfg, start, end, random.Random(seed))
            self.assertEqual(len(t), 20)
            self.assertTrue(all(b - a >= timedelta(seconds=7.5) for a, b in zip(t, t[1:])))
            self.assertGreaterEqual(t[-1], start + (end - start) * 19 / 20)  # the last one is in the final segment

    def test_unsafe_limits_warns_listing_exactly_the_relaxed_limits(self):  # [AC-session-limits-5]
        with self.assertLogs("bot", "WARNING") as logs:
            self.load("listen_minutes = 5\nmessage_count = 20\n", "--unsafe-limits")
        unsafe = [r for r in logs.output if "--unsafe-limits" in r]
        self.assertEqual(len(unsafe), 1)
        for fragment in ("session length", "now 5 min", "message cap", "now 20",
                         "minimum gap", "now 7.5 s", "Still enforced: report window of at least 10 min"):
            self.assertIn(fragment, unsafe[0])

    def test_unsafe_limits_cannot_come_from_env_or_file(self):  # [AC-session-limits-6] [EDGE-session-limits-8]
        with self.assertRaises(SystemExit) as cm:
            self.load("listen_minutes = 5\n", env={"MTBOT_UNSAFE_LIMITS": "1"})
        self.assertIn("--unsafe-limits", str(cm.exception))
        with self.assertRaises(SystemExit):
            self.load("listen_minutes = 5\nunsafe_limits = true\n")
        with self.assertLogs("bot", "WARNING"):  # the flag is set in the environment: say so, and ignore it
            cfg = self.load("", env={"MTBOT_UNSAFE_LIMITS": "1"})
        self.assertEqual(cfg["min_gap_seconds"], 120.0)

    def test_the_report_window_floor_stays_under_unsafe_limits(self):  # [EDGE-session-limits-3]
        with self.assertRaises(SystemExit) as cm:
            self.load("report_window_minutes = 5\n", "--unsafe-limits")
        self.assertNotIn("--unsafe-limits", str(cm.exception))

    def test_unsafe_limits_refuses_a_zero_or_negative_session(self):  # [EDGE-session-limits-4]
        for flags in (("--unsafe-limits", "--listen-minutes", "0"), ("--unsafe-limits", "--listen-minutes=-1")):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as cm:
                self.load("", *flags)
            self.assertIn("must be above 0", str(cm.exception))

    def test_listen_only_sessions_load_and_plan_nothing(self):  # [EDGE-session-limits-5]
        cfg = self.load("listen_minutes = 30\nmessage_count = 0\n")
        self.assertEqual(bot.plan_send_times(cfg, datetime(2026, 9, 19, 21, 0), datetime(2026, 9, 19, 21, 30)), [])

    def test_unsafe_limits_keeps_the_two_minute_gap_when_the_segments_allow_it(self):  # [AC-session-limits-4]
        self.assertEqual(self.load("listen_minutes = 30\nmessage_count = 10\n", "--unsafe-limits")["min_gap_seconds"], 120.0)

    def test_unsafe_limits_on_a_long_session_keeps_the_two_minute_gap(self):  # [EDGE-session-limits-6]
        with self.assertLogs("bot", "WARNING") as logs:
            cfg = self.load("", "--unsafe-limits")
        self.assertEqual(cfg["min_gap_seconds"], 120.0)
        self.assertTrue(any("now 120 s" in r for r in logs.output))

    def test_non_finite_limits_are_refused(self):  # [AC-session-limits-2] (review F6)
        for ini in ("listen_minutes = nan\n", "report_window_minutes = inf\n", "listen_minutes = inf\n"):
            with self.subTest(ini=ini), self.assertRaises(SystemExit):
                self.load(ini)

    def test_legacy_fixed_spacing_settings_warn_and_are_ignored(self):  # [EDGE-session-limits-9]
        with self.assertLogs("bot", "WARNING") as logs:
            cfg = self.load("random_schedule = false\ninterval_minutes = 5\n")
        self.assertTrue(any("no longer exist" in r and "random_schedule" in r for r in logs.output))
        self.assertNotIn("random_schedule", cfg)
        with self.assertLogs("bot", "WARNING") as logs:
            self.load("", env={"MTBOT_RANDOM_SCHEDULE": "false", "MTBOT_INTERVAL_MINUTES": "5"})
        self.assertTrue(any("MTBOT_RANDOM_SCHEDULE" in r and "no longer exist" in r for r in logs.output))

    def test_removed_flags_exit_with_the_fix(self):  # [EDGE-session-limits-10]
        for flags in (["--fixed-schedule"], ["--interval", "5"], ["--interval=5"]):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as cm:
                self.load("", *flags)
            self.assertIn("was removed", str(cm.exception))
            self.assertIn('["--now"]', str(cm.exception))

    def test_removed_flags_are_caught_on_the_real_command_line_too(self):  # [EDGE-session-limits-10] review F5
        path = os.path.join(tempfile.mkdtemp(), "bot.ini")
        open(path, "w", encoding="utf-8").write("[bot]\nchannel = 1\nplace = X\n")
        with mock.patch.object(sys, "argv", ["mesh_test_bot.py", "--config", path, "--now", "--fixed-schedule"]), \
                mock.patch.object(bot, "run_window") as rw, self.assertRaises(SystemExit) as cm:
            bot.main()
        self.assertIn("was removed", str(cm.exception))
        rw.assert_not_called()

    def test_a_late_start_reduces_the_count_and_keeps_the_gap(self):  # [EDGE-session-limits-7]
        start = datetime(2026, 9, 19, 21, 0)
        end = start + timedelta(minutes=5)
        cfg = {"message_count": 10, "min_gap_seconds": 120.0, "report_window_minutes": 10.0}
        with self.assertLogs("bot", "WARNING"):
            t = bot.plan_send_times(cfg, start, end, random.Random(1))
        self.assertEqual(len(t), 2)
        self.assertGreaterEqual(t[1] - t[0], timedelta(seconds=120))

    def test_min_gap_in_the_file_is_still_ignored(self):  # [EDGE-session-limits-12]
        self.assertEqual(self.load("min_gap_seconds = 1\n")["min_gap_seconds"], 120.0)


class AppendRotatingTest(unittest.TestCase):
    def test_rotates_and_keeps_only_the_newest_files(self):
        path = os.path.join(tempfile.mkdtemp(), "rx.log")
        with mock.patch.object(bot, "MAX_FILE_BYTES", 10), mock.patch.object(bot, "KEEP_FILES", 3):
            for i in range(8):
                bot.append_rotating(path, "line-%d\n" % i)  # 7 bytes: each file holds two lines
        read = lambda p: open(p, encoding="utf-8").read()
        self.assertEqual(read(path), "line-6\nline-7\n")
        self.assertEqual(read(path + ".1"), "line-4\nline-5\n")
        self.assertEqual(read(path + ".2"), "line-2\nline-3\n")
        self.assertEqual(read(path + ".3"), "line-0\nline-1\n")
        self.assertFalse(os.path.exists(path + ".4"))  # KEEP_FILES old ones, no more

    def test_small_file_is_only_appended_to(self):
        path = os.path.join(tempfile.mkdtemp(), "report.txt")
        bot.append_rotating(path, "a\n")
        bot.append_rotating(path, "b\n")
        self.assertEqual(open(path, encoding="utf-8").read(), "a\nb\n")
        self.assertFalse(os.path.exists(path + ".1"))


if __name__ == "__main__":
    unittest.main()
