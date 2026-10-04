# mesh-test-bot

A bot for automatic **range tests** on a Meshtastic network. It sends a few short
messages on a channel, listens for other bots' messages on the same channel, and at
the end writes a report of who was heard, over how many hops, with what SNR and RSSI,
and how many of each station's messages arrived. Runs in Docker (or with Python),
connected to your radio over WiFi, and repeats itself every week.

The report also records the radio's own LoRa mode for each session (`Mode:` in
`report.txt`'s header, `radio.mode` in `report.jsonl`), so you can compare sessions
run in different modes on the same channel. It's read from the radio's own
configuration, never typed by hand: the preset's name (`LONG_FAST`, `NARROW_FAST`…)
or, for manual settings, `BW<kHz>-SF<n>-CR<n>` (e.g. `BW62-SF7-CR6`), which
`mode_aliases` can rename. Only set `mode` yourself to force a label instead.

> **Unofficial project.** Not affiliated with or endorsed by the Meshtastic project.
> "Meshtastic" is a trademark of Meshtastic LLC, used here only to indicate which
> technology this bot is compatible with.

## The message and the report

The bot sends short messages such as `MTBOT | ABCD | City | 2/3`, listens for the same format
from other stations, and writes a readable report and a JSON line per session. The field-by-field
format, the report layout, how messages are read and the random timing are in
[The message, the report and how messages are read](docs/message-and-report.md).

## Prerequisites

- A Meshtastic radio with WiFi, reachable by IP, with the test channel already
  configured. The bot does not configure the radio.
- A **test channel**, ideally: whatever the bot transmits is heard by everyone on that
  channel, in the same LoRa mode.
- The radio accepts **only one TCP client at a time**: close the app and any other
  program connected to the radio (including other bots) during the session, at the
  brief startup check `--schedule` does (below), and on each retry of it.

## Quick install (Docker, no git)

You only need **Docker with Compose v2**: Docker Desktop on Windows and macOS, and on
Linux or a Raspberry Pi `curl -fsSL https://get.docker.com | sh`. No need for git or
the source code.

### With the installer (Linux, macOS, Raspberry Pi)

```sh
curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/install.sh | sh
```

Asks a few questions — how the bot should reach the radio (directly, through an
existing MeshMonitor, or by setting up a new MeshMonitor right here; see [With
MeshMonitor](#with-meshmonitor-optional) below), the test channel's index and name,
your city, the message/report prefixes, the session length, number of messages and report
window, and whether to run on a recurring weekly schedule or on demand — press Enter to accept the default shown in brackets for any of them.
Creates a `mesh-test-bot/` folder with the configuration, and prints the exact
`docker compose` commands that start it (the installer never starts anything itself:
you run it, and know what it does). If
you'd rather read the script before running it, download it and open it: it's about a
short.

### By hand (also on Windows)

```sh
mkdir mesh-test-bot && cd mesh-test-bot
curl -fsSLO https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/env.example -o .env
```

On PowerShell use `curl.exe` instead of `curl`. Edit `.env` (the first 4 lines: the
radio's IP, the channel, the channel's name and your city) and start it:

```sh
mkdir data
docker compose up -d
```

### After installing

```sh
docker compose logs -f --no-log-prefix      # what it's doing (the 1st line says when the next session is)
docker compose pull && docker compose up -d # update
docker compose down                         # stop
```

- Reports (`report.txt`, `report.jsonl`) and `rx.log` are in the `data/` folder.
- To change the configuration, edit `.env` and run `docker compose up -d --force-recreate`.
- **At startup** (scheduled mode), the bot briefly connects to the radio to check it's reachable and on
  the right channel: `Startup check OK` in the log means it's set up correctly. If it
  can't connect, it retries for a few minutes, then exits and the container restarts
  (each cycle takes a few minutes, so `docker ps` usually shows `Up` with a low uptime
  rather than `Restarting`; `docker inspect -f '{{.RestartCount}}' mesh-test-bot` climbing
  is a clearer sign) until the setup is fixed, instead of waiting quietly for the first
  scheduled session. `docker compose logs` says what's wrong (usually the host/port in
  `.env`, or the radio being unreachable). On-demand runs have no startup check: they
  connect when the session starts, and a failed connection shows in the log.
- Every option can go in `.env` too, as `MTBOT_<NAME>`: see
  [`defaults.ini`](defaults.ini) for the full list and what each one does.

## Installing with Docker, from source

```sh
git clone <this repository> && cd mesh-test-bot
mkdir data && printf '[bot]\nhost = 192.168.1.50\nchannel = 1\nplace = City\n' > data/bot.ini
# edit data/bot.ini; every other option and its default is in defaults.ini
docker compose up -d
docker compose logs -f --no-log-prefix
```

The container keeps running and holds the session by itself on the configured
`weekday` and `start_time`. Reports land in `data/report.txt` and
`data/report.jsonl`, and readings in `data/rx.log`. You should see `Next session: …`
in the log right after it starts, followed within about a minute by `Startup check OK`
(it briefly connects to the radio to confirm it's reachable and on the right channel,
then disconnects until the session).

Run a session right away, without waiting (e.g. to test):

```sh
docker compose run --rm bot --now
```

### With environment variables only (no `.ini`)

Instead of `data/bot.ini`, the options can go straight into `docker-compose.yml`:

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    environment:
      MTBOT_HOST: "192.168.1.50"
      MTBOT_CHANNEL: "1"
      MTBOT_CHANNEL_NAME: "TestChannel"
      MTBOT_PLACE: "City"
    volumes:
      - ./data:/data        # reports and rx.log land here
```

Numeric values go in quotes, so YAML keeps them as text.

## Upgrading

Run `docker compose pull && docker compose up -d` first, so you get the newest
image. Then read [Upgrading from an older version](docs/upgrading.md): some settings and
schedules from before v0.3.0 are now refused or ignored.

## Installing without Docker

Needs Python 3.9+ and the `meshtastic` library (pinned in `requirements.txt`).

```sh
pip install -r requirements.txt
printf '[bot]\nhost = 192.168.1.50\nchannel = 1\nplace = City\n' > bot.ini   # edit it
python3 mesh_test_bot.py --schedule        # keeps running, repeats every week
python3 mesh_test_bot.py --now             # or: one session right away
```

Instead of `--schedule` you can use the system's cron.

**Run `--schedule` under a process supervisor** (systemd with `Restart=on-failure`, for
example), not bare in a terminal or a plain `nohup`/cron `@reboot` line: if the
radio is unreachable at startup, the bot retries for a few minutes and then **exits**
(see [After installing](#after-installing) above) -- without a supervisor to restart it,
the scheduler is simply gone until someone notices. The Docker Compose setup already has
this covered (`restart: unless-stopped`).

## With MeshMonitor (optional)

If you want the full message history in a web UI, or need more than one program to
talk to the radio (which only accepts one TCP client), the bot can connect to
[MeshMonitor](https://meshmonitor.org)'s **Virtual Node** instead of the radio. The
installer (above) can set this up for you — answer "set up a new MeshMonitor here"
when asked how to connect, or "through an existing MeshMonitor" if you already run
one elsewhere and just need to point the bot at its Virtual Node's address. Doing it
by hand is just pointing `MTBOT_HOST` and `MTBOT_PORT` at it: there's an [example
`docker-compose`](install/with-meshmonitor.yml) and a [guide](docs/meshmonitor.md)
with the steps, the caveats and what was tested. To run several scheduled tests through
the same MeshMonitor, see [several scheduled bots](docs/advanced-multiple-bots.md).
MeshMonitor is an independent project and this bot is not affiliated with or endorsed by it.

## Configuration

See the comments in [`defaults.ini`](defaults.ini) for every option and what it does
— it's the bot's own default settings, shipped with the code (don't edit it). Your own
`bot.ini` only needs the options you want to change; `channel` and `place` are the only
two with no default. Each option can come from four places, in this order of priority:

1. a **flag** (`python3 mesh_test_bot.py --help`);
2. an **environment variable** `MTBOT_<NAME>`, the option's name in upper case
   (`MTBOT_PLACE`, `MTBOT_CHANNEL`, `MTBOT_LISTEN_MINUTES`…). An empty variable counts
   as unset, so a `docker-compose.yml` can pass `${VAR}` through without accidentally
   clearing what's in the file;
3. your **`bot.ini`**, which doesn't even need to exist if the variables are enough;
4. `defaults.ini`.

- **Timezone:** `start_time` is read in `timezone` (default `Europe/Lisbon`), not the
  machine's own zone. Someone in another zone (the Azores, say) keeps the same time
  and the same zone, and the bot converts. Daylight saving is handled automatically.
- **Radio:** `host` and `port` (4403 by default, Meshtastic's port). The port only
  changes if you're connecting to another service that speaks the same TCP protocol
  instead of the radio.
- **Channel:** `channel` is required and is the channel's index on the radio (as shown
  in the app, starting at 0). If `channel_name` is set, the bot confirms on connect
  that the index has that name and aborts the session if it doesn't, so it never
  transmits on the wrong channel.
- **Message:** `place` (required), `keyword` (the message prefix) and, to force the
  mode shown in the report, `mode`.
- **Report:** `report_prefix`, the prefix repeated on every line of `report.txt`
  (unrelated to `keyword` above).
- **Testing without disturbing the network:** point `channel`/`channel_name` at a
  private channel and use `--now --unsafe-limits` for a short run. `--dry-run` shows the
  schedule without connecting to the radio.
- **Testing with more than just yourself:** see [Session limits](docs/session-limits.md).

## Session limits

Every session is held to limits: at most 10 messages, at least 30 minutes of listening, and a
report window of at least 10 minutes. A private-channel load test can lift the first two with
`--unsafe-limits` (command line only). Details: [Session limits](docs/session-limits.md).

## How it works, and its limits

See [How it works, and its limits](docs/how-it-works.md).

## Tests

The automated tests (`python3 -m unittest discover tests`) and the manual recipes against a real
radio are in [Tests](docs/testing.md).

## License

Copyright (C) 2026 André Cunha. This program is free software, distributed under the
[GNU General Public License](LICENSE) version 3 or, at your option, any later version
(`GPL-3.0-or-later`), **with no warranty of any kind**.

It depends on the Python `meshtastic` library, which is GPL-3.0, and the Docker image
includes it; the other dependencies have permissive licenses (BSD, MIT, Apache-2.0).
The full license text is in the [`LICENSE`](LICENSE) file.
