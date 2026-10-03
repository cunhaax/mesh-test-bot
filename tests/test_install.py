# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests of the installer and of the files it installs. Docker is never started
(MTB_SKIP_START=1). Run: python3 -m unittest discover tests"""
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
    e.update(MTB_DIR=out, MTB_SOURCE=INSTALL, MTB_SKIP_START="1")
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
        m = re.fullmatch(r"(MTBOT_\w+)='(.*)'", line)
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
            "MTBOT_TIMEZONE": "Europe/Lisbon"})
        for name in ("docker-compose.yml", "env.example", "data"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)

    def test_answers_from_stdin_in_order(self):
        # host, port, channel, channel_name, place, keyword, report_prefix, timezone, run-once?
        p, out = run_installer("10.0.0.7\n4403\n3\nMeuCanal\nPorto\nCustomKey\nCustomRep\nAmerica/New_York\ny\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(read_env_file(os.path.join(out, ".env")), {
            "MTBOT_HOST": "10.0.0.7", "MTBOT_PORT": "4403", "MTBOT_CHANNEL": "3", "MTBOT_CHANNEL_NAME": "MeuCanal",
            "MTBOT_PLACE": "Porto", "MTBOT_KEYWORD": "CustomKey", "MTBOT_REPORT_PREFIX": "CustomRep",
            "MTBOT_TIMEZONE": "America/New_York"})

    def test_empty_answers_take_the_defaults(self):
        # port, channel, channel_name, keyword, report_prefix, run-once?, weekday, start_time, timezone: all blank
        p, out = run_installer("10.0.0.7\n\n\n\nPorto\n\n\n\n\n\n\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        env = read_env_file(os.path.join(out, ".env"))
        self.assertEqual((env["MTBOT_PORT"], env["MTBOT_CHANNEL"], env["MTBOT_CHANNEL_NAME"], env["MTBOT_KEYWORD"],
                          env["MTBOT_REPORT_PREFIX"], env["MTBOT_WEEKDAY"], env["MTBOT_START_TIME"], env["MTBOT_TIMEZONE"]),
                         ("4403", "1", "", "MTBOT", "ACK", "saturday", "06:00", "Europe/Lisbon"))

    def test_bad_answers_are_refused_and_nothing_is_created(self):
        good = dict(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        for name, bad in (("MTBOT_CHANNEL", "8"), ("MTBOT_CHANNEL", "abc"), ("MTBOT_HOST", "10.0.0.7 x"),
                          ("MTBOT_HOST", "a;b"), ("MTBOT_PLACE", "It's"), ("MTBOT_PLACE", "A|B"), ("MTBOT_PLACE", "A #b"),
                          ("MTBOT_CHANNEL_NAME", 'a"b'), ("MTBOT_PORT", "abc"), ("MTBOT_WEEKDAY", "funday"),
                          ("MTBOT_START_TIME", "25:99"), ("MTBOT_START_TIME", "25:30"), ("MTB_ONESHOT", "true")):
            with self.subTest(**{name: bad}):
                p, out = run_installer(**dict(good, **{name: bad}))
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("Error", p.stderr)
                self.assertFalse(os.path.exists(out))

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

    def test_it_never_overwrites_an_existing_configuration(self):
        p, out = run_installer(MTBOT_HOST="10.0.0.7", MTBOT_CHANNEL="1", MTBOT_CHANNEL_NAME="C", MTBOT_PLACE="Porto")
        self.assertEqual(p.returncode, 0)
        before = read(os.path.join(out, ".env"))
        p2 = subprocess.run(["sh", os.path.join(INSTALL, "install.sh")], capture_output=True, text=True, timeout=30,
                            start_new_session=True, env=dict(os.environ, MTB_DIR=out, MTB_SOURCE=INSTALL, MTB_SKIP_START="1",
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
        # host, port, channel, channel_name (blank), place, keyword, report_prefix, timezone, run-once?
        p, out = run_installer("10.0.0.7\n\n1\n\nPorto\n\n\n\ny\n")
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
    env.update(MTB_DIR=out, MTB_SOURCE=INSTALL, MTB_SKIP_START="1", TERM="dumb")
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
        # host, port, channel, channel_name, place, keyword, report_prefix, timezone, run-once?
        code, screen, out = run_like_curl_pipe(
            ["10.0.0.4", "4403", "2", "CanalX", "Braga", "MTBOT", "ACK", "Europe/Lisbon", "y"])
        self.assertEqual(code, 0, screen)
        self.assertEqual(read_env_file(os.path.join(out, ".env")), {
            "MTBOT_HOST": "10.0.0.4", "MTBOT_PORT": "4403", "MTBOT_CHANNEL": "2", "MTBOT_CHANNEL_NAME": "CanalX",
            "MTBOT_PLACE": "Braga", "MTBOT_KEYWORD": "MTBOT", "MTBOT_REPORT_PREFIX": "ACK",
            "MTBOT_TIMEZONE": "Europe/Lisbon"})
        self.assertIn("Radio's IP", screen)

    def test_curl_pipe_sh_refuses_a_bad_answer(self):
        code, screen, out = run_like_curl_pipe(
            ["10.0.0.4", "4403", "9", "CanalX", "Braga", "MTBOT", "ACK", "Europe/Lisbon", "y"])
        self.assertNotEqual(code, 0)
        self.assertIn("0 to 7", screen)
        self.assertFalse(os.path.exists(out))


class InstalledFilesTest(unittest.TestCase):
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
        self.assertEqual(svc["command"], ["--now", "--fixed-schedule"])

    def test_the_meshmonitor_example(self):
        import yaml
        services = yaml.safe_load(read(os.path.join(INSTALL, "with-meshmonitor.yml")))["services"]
        ports = [str(p) for p in services["meshmonitor"]["ports"]]
        self.assertTrue(all(p.startswith("127.0.0.1:") for p in ports))  # the web interface: this machine only
        self.assertFalse(any("4404" in p for p in ports))  # the virtual node has no authentication: never published
        env = services["bot"]["environment"]
        self.assertEqual((env["MTBOT_HOST"], env["MTBOT_PORT"]), ("meshmonitor", "4404"))
        for name in env:
            self.assertIn(name[len(bot.ENV_PREFIX):].lower(), bot.DEFAULTS, name)
        direct = yaml.safe_load(read(os.path.join(INSTALL, "docker-compose.yml")))["services"]["bot"]
        self.assertEqual(services["bot"]["image"], direct["image"])  # the same published image

    def test_the_installer_and_the_image_name_agree_with_the_workflow(self):
        import yaml
        compose = yaml.safe_load(read(os.path.join(INSTALL, "docker-compose.yml")))
        workflow = read(os.path.join(ROOT, ".github", "workflows", "docker.yml"))
        repo = compose["services"]["bot"]["image"].split("ghcr.io/")[1].split(":")[0]
        self.assertIn("ghcr.io/${{ github.repository }}", workflow)  # published as ghcr.io/<owner>/<repo>
        self.assertRegex(read(os.path.join(INSTALL, "install.sh")), re.escape(repo))


if __name__ == "__main__":
    unittest.main()
