# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests of the installer and of the files it installs. Docker is never started
Run: python3 -m unittest discover tests"""
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INSTALL = os.path.join(ROOT, "install")
sys.path.insert(0, ROOT)
import mesh_test_bot as bot  # noqa: E402


def run_installer(stdin="", **env):
    """Run install.sh in a temp dir; returns (process, the folder it would create)."""
    out = os.path.join(tempfile.mkdtemp(), "out")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("MTBOT_", "MTB_"))}
    e.update(MTB_DIR=out, MTB_SOURCE=INSTALL)
    e.update(env)
    # No controlling terminal: the script must then read its answers from stdin.
    proc = subprocess.run(["sh", os.path.join(INSTALL, "install.sh")], input=stdin, capture_output=True,
                          text=True, env=e, timeout=30, start_new_session=True)
    return proc, out


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_env_file(path):
    values = {}
    for line in read(path).splitlines():
        m = re.fullmatch(r"(\w+)='(.*)'", line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


@unittest.skipIf(shutil.which("sh") is None, "needs a POSIX sh")
class InstallerTest(unittest.TestCase):
    def test_answers_from_the_environment(self):
        p, out = run_installer(MTBOT_HOST="192.168.1.9", MTBOT_PORT="4403", MTBOT_CHANNEL="2",
                                MTBOT_CHANNEL_NAME="Canal", MTBOT_PLACE="Vila Nova", MTBOT_KEYWORD="MTBOT",
                                MTBOT_REPORT_PREFIX="ACK", MTB_ONESHOT="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(read_env_file(os.path.join(out, ".env")), {
            "MTBOT_HOST": "192.168.1.9", "MTBOT_PORT": "4403", "MTBOT_CHANNEL": "2", "MTBOT_CHANNEL_NAME": "Canal",
            "MTBOT_PLACE": "Vila Nova", "MTBOT_KEYWORD": "MTBOT", "MTBOT_REPORT_PREFIX": "ACK",
            "MTBOT_TIMEZONE": "Europe/Lisbon", "MTBOT_LISTEN_MINUTES": "120", "MTBOT_MESSAGE_COUNT": "3",
            "MTBOT_REPORT_WINDOW_MINUTES": "60"})
        for name in ("docker-compose.yml", "env.example", "data"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)

    def test_answers_from_stdin_in_order(self):
        # connection, host, port, channel, channel_name, place, keyword, report_prefix, timezone, run-once?
        answers = ["direct", "10.0.0.7", "4403", "3", "MeuCanal", "Porto", "CustomKey", "CustomRep",
                   "America/New_York", "on-demand", "90", "4", "15"]
        p, out = run_installer("\n".join(answers) + "\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(read_env_file(os.path.join(out, ".env")), {
            "MTBOT_HOST": "10.0.0.7", "MTBOT_PORT": "4403", "MTBOT_CHANNEL": "3", "MTBOT_CHANNEL_NAME": "MeuCanal",
            "MTBOT_PLACE": "Porto", "MTBOT_KEYWORD": "CustomKey", "MTBOT_REPORT_PREFIX": "CustomRep",
            "MTBOT_TIMEZONE": "America/New_York", "MTBOT_LISTEN_MINUTES": "90", "MTBOT_MESSAGE_COUNT": "4",
            "MTBOT_REPORT_WINDOW_MINUTES": "15"})

    def test_empty_answers_take_the_defaults(self):
        # connection, host, place given; everything else (port, channel, channel_name, keyword,
        # report_prefix, timezone, run-once?, weekday, start_time) blank -> takes its default
        answers = ["", "10.0.0.7", "", "", "", "Porto", "", "", "", "", "", "", "", "", ""]
        p, out = run_installer("\n".join(answers) + "\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_PORT"], env["MTBOT_CHANNEL"], env["MTBOT_CHANNEL_NAME"], env["MTBOT_KEYWORD"],
                          env["MTBOT_REPORT_PREFIX"], env["MTBOT_WEEKDAY"], env["MTBOT_START_TIME"], env["MTBOT_TIMEZONE"],
                          env["MTBOT_LISTEN_MINUTES"], env["MTBOT_MESSAGE_COUNT"], env["MTBOT_REPORT_WINDOW_MINUTES"]),
                         ("4403", "1", "", "MTBOT", "ACK", "saturday", "06:00", "Europe/Lisbon", "120", "3", "60"))

    def test_bad_answers_are_refused_and_nothing_is_created(self):
        good = dict(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        cases = (
            ("MTBOT_CHANNEL", "8", "from 0 to 7"), ("MTBOT_CHANNEL", "abc", "from 0 to 7"),
            ("MTBOT_HOST", "10.0.0.7 x", "invalid radio IP"), ("MTBOT_HOST", "a;b", "invalid radio IP"),
            ("MTBOT_PLACE", "It's", "cannot contain"), ("MTBOT_PLACE", "A|B", "cannot contain"),
            ("MTBOT_PLACE", "A #b", "cannot contain"), ("MTBOT_PLACE", "A\nMTBOT_HOST=x", "line breaks"),
            ("MTBOT_PLACE", "x" * 170, "too long"),
            ("MTBOT_CHANNEL_NAME", 'a"b', "cannot contain"), ("MTBOT_PORT", "abc", "must be a number"),
            ("MTBOT_PORT", "99999", "between 1 and 65535"), ("MTBOT_PORT", "0", "between 1 and 65535"),
            ("MTBOT_KEYWORD", "!!!", "letter or digit"), ("MTBOT_TIMEZONE", "Mars/Olympus", "unknown timezone"),
            ("MTBOT_WEEKDAY", "funday", "weekday must"), ("MTBOT_START_TIME", "25:99", "start time must"),
            ("MTBOT_START_TIME", "25:30", "start time must"), ("MTB_ONESHOT", "true", "MTB_ONESHOT must"),
        )
        for name, bad, expected in cases:
            with self.subTest(**{name: bad}):
                p, out = run_installer(**dict(good, **{name: bad}))
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(expected, p.stderr)
                self.assertFalse(os.path.exists(out))

    def test_a_docker_compose_file_in_the_working_directory_is_not_used_when_piped(self):
        work = tempfile.mkdtemp()
        with open(os.path.join(work, "docker-compose.yml"), "w", encoding="utf-8") as f:
            f.write("# not ours\n")
        out = os.path.join(tempfile.mkdtemp(), "out")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MTBOT_", "MTB_"))}
        env.update(MTB_DIR=out, MTB_RAW_BASE="file://" + INSTALL, MTB_ONESHOT="1", MTB_CONNECTION="direct",
                   MTBOT_LISTEN_MINUTES="120", MTBOT_MESSAGE_COUNT="3", MTBOT_REPORT_WINDOW_MINUTES="60",
                   MTBOT_HOST="10.0.0.7", MTBOT_PORT="4403", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C",
                   MTBOT_PLACE="Porto", MTBOT_KEYWORD="MTBOT", MTBOT_REPORT_PREFIX="ACK",
                   MTBOT_TIMEZONE="Europe/Lisbon")
        p = subprocess.run(["sh"], input=read(os.path.join(INSTALL, "install.sh")), capture_output=True, text=True,
                           cwd=work, env=env, timeout=30, start_new_session=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")),
                         read(os.path.join(INSTALL, "docker-compose.once.yml")))

    def test_oneshot_mode_skips_weekday_and_start_time_but_keeps_timezone(self):
        p, out = run_installer(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C",
                                MTBOT_PLACE="Porto", MTB_ONESHOT="1", MTBOT_TIMEZONE="Asia/Tokyo")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        for name in ("MTBOT_WEEKDAY", "MTBOT_START_TIME"):
            self.assertNotIn(name, env)
        self.assertEqual(env["MTBOT_TIMEZONE"], "Asia/Tokyo")  # still matters for report timestamps
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")),
                         read(os.path.join(INSTALL, "docker-compose.once.yml")))

    def test_recurring_mode_writes_the_weekly_schedule(self):
        p, out = run_installer(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C",
                                MTBOT_PLACE="Porto", MTB_ONESHOT="0", MTBOT_WEEKDAY="Sunday",
                                MTBOT_START_TIME="07:30", MTBOT_TIMEZONE="Atlantic/Azores")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_WEEKDAY"], env["MTBOT_START_TIME"], env["MTBOT_TIMEZONE"]),
                         ("sunday", "07:30", "Atlantic/Azores"))
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")),
                         read(os.path.join(INSTALL, "docker-compose.yml")))

    def test_direct_connection_never_writes_meshmonitor_only_vars(self):
        p, out = run_installer(MTB_CONNECTION="direct", MTBOT_HOST="1.2.3.4", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto", MTB_ONESHOT="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        for name in ("MESHTASTIC_NODE_IP", "ALLOWED_ORIGINS"):
            self.assertNotIn(name, env)

    def test_existing_meshmonitor_connection_just_points_host_and_port_at_it(self):
        p, out = run_installer(MTB_CONNECTION="existing", MTBOT_HOST="meshmonitor.lan", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto", MTB_ONESHOT="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_HOST"], env["MTBOT_PORT"]), ("meshmonitor.lan", "4404"))
        for name in ("MESHTASTIC_NODE_IP", "ALLOWED_ORIGINS"):
            self.assertNotIn(name, env)
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")),
                         read(os.path.join(INSTALL, "docker-compose.once.yml")))  # plain bot-only template

    def test_connection_mode_is_case_insensitive(self):
        p, out = run_installer(MTB_CONNECTION="Existing", MTBOT_HOST="meshmonitor.lan", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto", MTB_ONESHOT="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual(env["MTBOT_PORT"], "4404")  # took the "existing" branch's default, not "direct"'s 4403

    def test_setup_mode_fixes_host_and_port_but_keeps_the_on_demand_choice(self):
        p, out = run_installer(MTB_CONNECTION="setup", MESHTASTIC_NODE_IP="192.168.1.50", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto", MTB_ONESHOT="1",
                                MTBOT_HOST="1.2.3.4", MTBOT_PORT="9999")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_HOST"], env["MTBOT_PORT"]), ("meshmonitor", "4404"))
        for name in ("MTBOT_WEEKDAY", "MTBOT_START_TIME"):
            self.assertNotIn(name, env)
        self.assertIn("docker compose up -d meshmonitor", p.stdout)
        self.assertIn("docker compose run --rm bot --now\n", p.stdout)
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")),
                         read(os.path.join(INSTALL, "with-meshmonitor.yml")))

    def test_setup_mode_scheduled_prints_the_full_stack_start(self):
        p, out = run_installer(MTB_CONNECTION="setup", MESHTASTIC_NODE_IP="192.168.1.50", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto", MTB_ONESHOT="0")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("docker compose up -d\n", p.stdout)
        self.assertNotIn("run --rm bot --now", p.stdout)

    def test_setup_meshmonitor_connection_fixes_the_bot_at_the_virtual_node(self):
        p, out = run_installer(MTB_CONNECTION="setup", MESHTASTIC_NODE_IP="192.168.1.50", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_HOST"], env["MTBOT_PORT"]), ("meshmonitor", "4404"))
        self.assertEqual(env["MESHTASTIC_NODE_IP"], "192.168.1.50")
        self.assertEqual(env["ALLOWED_ORIGINS"], "http://localhost:8080")
        for name in ("MTBOT_WEEKDAY", "MTBOT_START_TIME"):  # always recurring: asked unconditionally
            self.assertIn(name, env)
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")),
                         read(os.path.join(INSTALL, "with-meshmonitor.yml")))

    def test_setup_meshmonitor_requires_the_radio_ip(self):
        p, out = run_installer(MTB_CONNECTION="setup", MESHTASTIC_NODE_IP="", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Error", p.stderr)
        self.assertFalse(os.path.exists(out))

    def test_bad_connection_mode_is_refused(self):
        p, out = run_installer(MTB_CONNECTION="bogus", MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1",
                                MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("MTB_CONNECTION", p.stderr)
        self.assertFalse(os.path.exists(out))

    def test_it_writes_the_config_and_prints_the_commands_without_starting_anything(self):
        out = os.path.join(tempfile.mkdtemp(), "out")
        env = {"PATH": "/usr/bin:/bin", "MTB_DIR": out, "MTB_SOURCE": INSTALL, "MTBOT_HOST": "10.0.0.7",
               "MTBOT_CHANNEL": "1", "MTBOT_CHANNEL_NAME": "C", "MTBOT_PLACE": "Porto", "MTB_ONESHOT": "1"}
        p = subprocess.run(["sh", os.path.join(INSTALL, "install.sh")], input="", capture_output=True, text=True,
                           env=env, timeout=30, start_new_session=True)
        self.assertEqual(p.returncode, 0, p.stderr)  # no Docker on this PATH, and that is fine
        self.assertTrue(os.path.exists(os.path.join(out, ".env")))
        self.assertIn("docker compose up -d", p.stdout)
        self.assertIn("Nothing is running yet", p.stdout)

    def test_an_existing_compose_file_alone_is_never_overwritten(self):  # review: generated files, not only .env
        out = os.path.join(tempfile.mkdtemp(), "out")
        os.makedirs(out)
        with open(os.path.join(out, "docker-compose.yml"), "w", encoding="utf-8") as f:
            f.write("# my own edits\n")
        p, _ = run_installer(MTB_DIR=out, MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("docker-compose.yml already exists", p.stderr)
        self.assertEqual(read(os.path.join(out, "docker-compose.yml")), "# my own edits\n")
        self.assertFalse(os.path.exists(os.path.join(out, ".env")))

    def test_out_of_limit_session_values_are_refused_and_nothing_is_created(self):
        good = dict(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto", MTB_ONESHOT="1")
        for name, bad, expected, flag in (("MTBOT_LISTEN_MINUTES", "29", "at least 30 minutes", True),
                                          ("MTBOT_MESSAGE_COUNT", "11", "at most 10 messages", True),
                                          ("MTBOT_REPORT_WINDOW_MINUTES", "9", "at least 10 minutes", False),
                                          ("MTBOT_LISTEN_MINUTES", "-5", "whole number", False),
                                          ("MTBOT_LISTEN_MINUTES", "9999999", "too large", False)):
            with self.subTest(**{name: bad}):
                p, out = run_installer(**dict(good, **{name: bad}))
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(expected, p.stderr)
                self.assertEqual("--unsafe-limits" in p.stderr, flag)
                self.assertFalse(os.path.exists(out))

    def test_values_at_the_limits_are_accepted(self):
        for listen, count, window in (("30", "10", "10"), ("120", "0", "60")):
            with self.subTest(listen=listen, count=count, window=window):
                p, out = run_installer("direct\n10.0.0.7\n\n1\n\nPorto\n\n\n\non-demand\n%s\n%s\n%s\n" % (listen, count, window))
                self.assertEqual(p.returncode, 0, p.stderr)
                env = read_env_file(os.path.join(out, ".env"))
                self.assertEqual((env["MTBOT_LISTEN_MINUTES"], env["MTBOT_MESSAGE_COUNT"], env["MTBOT_REPORT_WINDOW_MINUTES"]),
                                 (listen, count, window))

    def test_session_values_are_asked_and_written_in_scheduled_mode_too(self):
        p, out = run_installer("direct\n10.0.0.7\n\n1\n\nPorto\n\n\n\nscheduled\n45\n5\n20\nsunday\n07:30\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_LISTEN_MINUTES"], env["MTBOT_MESSAGE_COUNT"], env["MTBOT_REPORT_WINDOW_MINUTES"],
                          env["MTBOT_WEEKDAY"]), ("45", "5", "20", "sunday"))

    def test_it_never_overwrites_an_existing_configuration(self):
        p, out = run_installer(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertEqual(p.returncode, 0)
        before = read(os.path.join(out, ".env"))
        p2 = subprocess.run(["sh", os.path.join(INSTALL, "install.sh")], capture_output=True, text=True, timeout=30,
                            start_new_session=True, env=dict(os.environ, MTB_DIR=out, MTB_SOURCE=INSTALL,
                                                             MTBOT_HOST="9.9.9.9", MTBOT_CHANNEL="1", MTBOT_PLACE="Outro"))
        self.assertNotEqual(p2.returncode, 0)
        self.assertIn("already exists", p2.stderr)
        self.assertEqual(read(os.path.join(out, ".env")), before)

    def test_the_generated_configuration_is_accepted_by_the_bot(self):
        p, out = run_installer(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="2", MTBOT_CHANNEL_NAME="Canal Teste", MTBOT_PLACE="Vila Nova")
        env = read_env_file(os.path.join(out, ".env"))
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MTBOT_")}
        with mock.patch.dict(os.environ, dict(clean, **env), clear=True):
            cfg = bot.load_config(["--config", "/nonexistent.ini"])[0]
        self.assertEqual((cfg["host"], cfg["channel"], cfg["channel_name"], cfg["place"]),
                         ("10.0.0.7", 2, "Canal Teste", "Vila Nova"))

    def test_an_empty_channel_name_is_read_as_not_set(self):
        # connection, host, port, channel, channel_name (blank), place, keyword, report_prefix, timezone, run-once?
        answers = ["", "10.0.0.7", "", "1", "", "Porto", "", "", "", "on-demand", "", "", ""]
        p, out = run_installer("\n".join(answers) + "\n")
        env = read_env_file(os.path.join(out, ".env"))
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MTBOT_")}
        with mock.patch.dict(os.environ, dict(clean, **env), clear=True):
            self.assertEqual(bot.load_config(["--config", "/nonexistent.ini"])[0]["channel_name"], "")


def run_like_curl_pipe(answers):
    """`cat install.sh | sh` on a real (pseudo) terminal: stdin is the script itself, so the
    installer has to ask its questions on /dev/tty, and the answers are typed there."""
    import pty
    out = os.path.join(tempfile.mkdtemp(), "out")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MTBOT_", "MTB_"))}
    env.update(MTB_DIR=out, MTB_SOURCE=INSTALL, TERM="dumb")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # forking with threads around
        pid, fd = pty.fork()
    if pid == 0:  # child, with the pty as its controlling terminal
        os.execvpe("sh", ["sh", "-c", 'cat "$1" | sh', "sh", os.path.join(INSTALL, "install.sh")], env)
    seen, answered, deadline = b"", 0, time.time() + 30
    while time.time() < deadline:
        if not select.select([fd], [], [], 0.5)[0]:
            continue
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        seen += data
        if answered < len(answers) and seen.endswith(b": "):  # a question, waiting for its answer
            os.write(fd, (answers[answered] + "\n").encode())
            answered += 1
    _, status = os.waitpid(pid, 0)
    return os.WEXITSTATUS(status), seen.decode(errors="replace"), out


@unittest.skipIf(shutil.which("sh") is None or not hasattr(os, "fork"), "needs a POSIX sh and a pty")
class PipedInstallerTest(unittest.TestCase):
    def test_curl_pipe_sh_asks_on_the_terminal(self):
        # connection, host, port, channel, channel_name, place, keyword, report_prefix, timezone, run-once?
        code, screen, out = run_like_curl_pipe(
            ["direct", "10.0.0.4", "4403", "2", "CanalX", "Braga", "MTBOT", "ACK", "Europe/Lisbon", "on-demand", "120", "3", "60"])
        self.assertEqual(code, 0, screen)
        self.assertEqual(read_env_file(os.path.join(out, ".env")), {
            "MTBOT_HOST": "10.0.0.4", "MTBOT_PORT": "4403", "MTBOT_CHANNEL": "2", "MTBOT_CHANNEL_NAME": "CanalX",
            "MTBOT_PLACE": "Braga", "MTBOT_KEYWORD": "MTBOT", "MTBOT_REPORT_PREFIX": "ACK",
            "MTBOT_TIMEZONE": "Europe/Lisbon", "MTBOT_LISTEN_MINUTES": "120", "MTBOT_MESSAGE_COUNT": "3",
            "MTBOT_REPORT_WINDOW_MINUTES": "60"})
        self.assertIn("Radio's IP", screen)

    def test_curl_pipe_sh_refuses_a_bad_answer(self):
        code, screen, out = run_like_curl_pipe(
            ["direct", "10.0.0.4", "4403", "9", "CanalX", "Braga", "MTBOT", "ACK", "Europe/Lisbon", "on-demand", "120", "3", "60"])
        self.assertNotEqual(code, 0)
        self.assertIn("channel must be a number from 0 to 7: 9", screen)
        self.assertFalse(os.path.exists(out))


class InstalledFilesTest(unittest.TestCase):
    REMOVED = ("fixed-schedule", "random_schedule", "interval_minutes", "INTERVAL_MINUTES", "fixed interval",
               "intervalo fixo", "Random-schedule limits")

    def test_no_shipped_file_mentions_the_removed_fixed_spacing(self):  # [AC-session-limits-8]
        # docs/upgrading.md names the removed options on purpose (it is the upgrade note), so it is exempt
        files = [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "defaults.ini"),
                 os.path.join(ROOT, "docs", "index.html"), os.path.join(ROOT, "docs", "pt", "index.html")]
        files += [os.path.join(ROOT, "docs", f) for f in os.listdir(os.path.join(ROOT, "docs"))
                  if f.endswith(".md") and f != "upgrading.md"]
        files += [os.path.join(INSTALL, f) for f in os.listdir(INSTALL)]
        for path in files:
            text = read(path).lower()
            for word in self.REMOVED:
                with self.subTest(file=os.path.basename(path), word=word):
                    self.assertNotIn(word.lower(), text)

    def test_the_installer_never_offers_or_prints_unsafe_limits(self):  # [AC-session-limits-6]
        # the flag may appear in refusal messages (die ...), never in a question (ask ...)
        for name in os.listdir(INSTALL):
            with self.subTest(file=name):
                for line in read(os.path.join(INSTALL, name)).splitlines():
                    if line.lstrip().startswith("ask "):
                        self.assertNotIn("unsafe", line.lower())

    def test_both_index_pages_state_the_limits_and_the_flag(self):  # [AC-session-limits-8]
        for path in (os.path.join(ROOT, "docs", "index.html"), os.path.join(ROOT, "docs", "pt", "index.html")):
            with self.subTest(file=path):
                text = read(path)
                self.assertIn("--unsafe-limits", text)
                if path.endswith("pt/index.html"):
                    self.assertIn("máximo de 10", text)
                    self.assertIn("mínimo de 30 minutos", text)
                    self.assertIn("pelo menos 10 minutos", text)
                else:
                    self.assertIn("capped at 10", text)
                    self.assertIn("30-minute floor", text)
                    self.assertIn("report window is at least 10", text)

    def test_a_fresh_on_demand_install_is_not_refused_by_the_limits(self):  # [EDGE-session-limits-11]
        import yaml
        p, out = run_installer(MTB_ONESHOT="1", MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1",
                               MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertEqual(p.returncode, 0, p.stderr)
        command = yaml.safe_load(read(os.path.join(out, "docker-compose.yml")))["services"]["bot"]["command"]
        env = read_env_file(os.path.join(out, ".env"))
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MTBOT_")}
        with mock.patch.dict(os.environ, dict(clean, **env), clear=True):
            cfg = bot.load_config(["--config", "/nonexistent.ini"] + command)[0]
        self.assertEqual(cfg["min_gap_seconds"], 120.0)
        self.assertEqual(cfg["message_count"], 3)

    def test_printed_commands_name_only_real_options_and_no_removed_flags(self):  # [AC-session-limits-7]
        for env in (dict(MTB_ONESHOT="1"), dict(MTB_ONESHOT="0", MTBOT_WEEKDAY="sunday", MTBOT_START_TIME="07:30"),
                    dict(MTB_CONNECTION="setup", MESHTASTIC_NODE_IP="192.168.1.50", MTB_ONESHOT="1"),
                    dict(MTB_CONNECTION="setup", MESHTASTIC_NODE_IP="192.168.1.50", MTB_ONESHOT="0",
                         MTBOT_WEEKDAY="sunday", MTBOT_START_TIME="07:30")):
            with self.subTest(**env):
                p, _ = run_installer(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C",
                                     MTBOT_PLACE="Porto", **env)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("fixed-schedule", p.stdout)
                for name in re.findall(r"MTBOT_\w+", p.stdout):
                    self.assertIn(name[len(bot.ENV_PREFIX):].lower(), bot.DEFAULTS, name)
                if env.get("MTB_ONESHOT") == "1":
                    self.assertIn("within 120 min", p.stdout)
                    self.assertIn("report up to 60 min", p.stdout)
    def test_every_variable_in_env_example_is_a_real_option(self):
        names = set()
        for line in read(os.path.join(INSTALL, "env.example")).splitlines():
            m = re.match(r"#?(MTBOT_\w+)=", line)
            if m:
                names.add(m.group(1))
        self.assertTrue({"MTBOT_HOST", "MTBOT_CHANNEL", "MTBOT_CHANNEL_NAME", "MTBOT_PLACE"} <= names)
        for name in names:
            self.assertIn(name[len(bot.ENV_PREFIX):].lower(), bot.DEFAULTS, name)

    def test_the_example_values_of_the_required_ones_are_active_lines(self):
        active = {m.group(1) for line in read(os.path.join(INSTALL, "env.example")).splitlines()
                  if (m := re.match(r"(MTBOT_\w+)=", line))}
        self.assertEqual(active, {"MTBOT_HOST", "MTBOT_CHANNEL", "MTBOT_CHANNEL_NAME", "MTBOT_PLACE",
                                  "MTBOT_KEYWORD", "MTBOT_REPORT_PREFIX"})

    def test_the_end_user_compose_file(self):
        import yaml
        svc = yaml.safe_load(read(os.path.join(INSTALL, "docker-compose.yml")))["services"]["bot"]
        self.assertTrue(svc["image"].startswith("ghcr.io/"))
        self.assertNotIn("build", svc)  # people without the code cannot build
        self.assertEqual((svc["env_file"], svc["restart"], svc["volumes"]), (".env", "unless-stopped", ["./data:/data"]))

    def test_the_oneshot_compose_file(self):
        import yaml
        svc = yaml.safe_load(read(os.path.join(INSTALL, "docker-compose.once.yml")))["services"]["bot"]
        self.assertTrue(svc["image"].startswith("ghcr.io/"))
        self.assertNotIn("build", svc)
        self.assertEqual((svc["env_file"], svc["restart"], svc["volumes"]), (".env", "no", ["./data:/data"]))
        self.assertEqual(svc["command"], ["--now"])  # [AC-session-limits-7]

    def test_the_meshmonitor_example(self):
        import yaml
        services = yaml.safe_load(read(os.path.join(INSTALL, "with-meshmonitor.yml")))["services"]
        ports = [str(p) for p in services["meshmonitor"]["ports"]]
        self.assertTrue(all(p.startswith("127.0.0.1:") for p in ports))  # the web interface: this machine only
        self.assertFalse(any("4404" in p for p in ports))  # the virtual node has no authentication: never published
        self.assertEqual(services["bot"]["env_file"], ".env")
        self.assertEqual(services["meshmonitor"]["env_file"], ".env")
        direct = yaml.safe_load(read(os.path.join(INSTALL, "docker-compose.yml")))["services"]["bot"]
        self.assertEqual(services["bot"]["image"], direct["image"])  # the same published image

    def test_the_meshmonitor_env_example_has_the_fixed_virtual_node_address(self):
        env = {}
        for line in read(os.path.join(INSTALL, "meshmonitor.env.example")).splitlines():
            if m := re.match(r"(\w+)=(.*)", line):
                env[m.group(1)] = m.group(2)
        self.assertEqual((env["MTBOT_HOST"], env["MTBOT_PORT"]), ("meshmonitor", "4404"))
        for name in ("MESHTASTIC_NODE_IP", "ALLOWED_ORIGINS", "MTBOT_CHANNEL", "MTBOT_CHANNEL_NAME", "MTBOT_PLACE"):
            self.assertIn(name, env)
        for name in env:
            if name.startswith(bot.ENV_PREFIX):
                self.assertIn(name[len(bot.ENV_PREFIX):].lower(), bot.DEFAULTS, name)

    def test_the_installer_and_the_image_name_agree_with_the_workflow(self):
        import yaml
        compose = yaml.safe_load(read(os.path.join(INSTALL, "docker-compose.yml")))
        workflow = read(os.path.join(ROOT, ".github", "workflows", "docker.yml"))
        repo = compose["services"]["bot"]["image"].split("ghcr.io/")[1].split(":")[0]
        self.assertIn("ghcr.io/${{ github.repository }}", workflow)  # published as ghcr.io/<owner>/<repo>
        self.assertRegex(read(os.path.join(INSTALL, "install.sh")), re.escape(repo))


if __name__ == "__main__":
    unittest.main()
