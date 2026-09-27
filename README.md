# mesh-test-bot

A bot for automatic **range tests** on a Meshtastic network. It sends a few short
messages on a channel, listens for other bots' messages on the same channel, and at
the end writes a report of who was heard, over how many hops, with what SNR and RSSI,
and how many of each station's messages arrived. Runs in Docker (or with Python),
connected to your radio over WiFi, and repeats itself every week.

Useful, for instance, to compare LoRa modes (`LONG_FAST`, `NARROW_FAST`…) on the same
channel: the same bot configuration, with the radio's own mode tagging the data.

> **Unofficial project.** Not affiliated with or endorsed by the Meshtastic project.
> "Meshtastic" is a trademark of Meshtastic LLC, used here only to indicate which
> technology this bot is compatible with.

## The message

The bot sends messages shaped like this:

```
MTBOT NARROW_FAST | City | 2/3
```

| Field | What it is |
|---|---|
| `MTBOT` | the keyword (`keyword`): tells the bot's messages apart from regular chat |
| `NARROW_FAST` | the radio's **LoRa mode**, read from the radio's own configuration (never typed by hand) |
| `City` | where you are, at city level (`place`) |
| `2/3` | this is message 2 of 3 in the session, which lets a delivery rate be computed |

It's meant for machines: strict, short (max 200 bytes) and easy to parse. Whoever
wants to hear your messages only has to use the same keyword.

**The mode** comes from `lora` in the radio's configuration: the preset's name
(`LONG_FAST`, `NARROW_FAST`…) or, for manual settings, `BW<kHz>-SF<n>-CR<n>` (e.g.
`BW62-SF7-CR6`), which `mode_aliases` can rename. So when you change the radio's mode,
the messages change by themselves. A message received with a mode different from yours
is flagged in the report (it can only come from MQTT or from a wrong label: a radio
only listens in the mode it is set to).

## The report

Each session writes two files in the configuration folder:

**`report.txt`**, readable, fields separated by ` | `:

```
Session 2026-09-26 21:00 → 21:30 | Reporter: AB12 (!deadbeef) | Place: City | Mode: LONG_FAST | Channel: 1 (TestChannel) | Sent: 3/3
node | name | mode | received | duplicates | hops | avg SNR | avg RSSI | place | via | note
!cafef00d | CD34 | LONG_FAST | 2/3 | 1 | 0 | 5.3 | -96 | Villatown | RF | direct
!0a1b2c3d | EF56 | LONG_FAST | 3/3 | 0 | 1-2 | -2.5 | -112 | Valley | RF | relay 0xe9
```

**`report.jsonl`**, one JSON line per session (the same content, for machines, and the
object that can later go to a server). Excerpt:

```json
{"schema": 1,
 "session": {"start": "2026-09-26T21:00:00+01:00", "end": "2026-09-26T21:30:00+01:00", "report_at": "2026-09-26T21:41:07+01:00", "timezone": "Europe/Lisbon"},
 "reporter": {"node": "!deadbeef", "name": "AB12", "place": "City"},
 "radio": {"mode": "LONG_FAST", "region": "EU_868", "use_preset": true, "modem_preset": 0, "bandwidth": 250, "spread_factor": 11, "coding_rate": 5, "channel_num": 1, "channel": 1, "channel_name": "TestChannel"},
 "sent": [{"seq": 1, "total": 3, "planned": "…", "ok": true, "text": "MTBOT LONG_FAST | City | 1/3", "at": "…"}],
 "heard": [{"node": "!cafef00d", "name": "CD34", "place": "Villatown", "path": "rf", "mode_tags": ["LONG_FAST"], "mode_match": true,
            "received": 2, "of": 3, "receptions": 3, "duplicates": 1, "hops_min": 0, "hops_max": 0,
            "snr_avg": 5.3, "snr_best": 6.0, "rssi_avg": -96, "rssi_best": -94, "relay": "direct", "mqtt_copies": 0, "first": "…", "last": "…"}],
 "outages": {"count": 0, "total_s": 0}}
```

- **`received` / `of`**: how many (distinct) messages from the station arrived, out of
  what it said it sent. **`duplicates`**: repeated readings of the same message.
- **`hops`**: the lowest and highest seen (`hopStart − hopLimit`). **`SNR` and
  `RSSI`**: average and best value.
- **`note`**: `direct` (0 hops) or a hint of the node that relayed it (see below).
- If the connection to the radio drops during the session, the report says how many
  times and how much time that added up to.

`rx.log` keeps every reading, one per line: time, node, name, `hops=`, `mqtt=0/1`,
`snr=`, `rssi=`, the note (the relay hint, `ignored` or `before-session`) and the
original text — including text **ignored** on the channel (marked `ignored`), so you
can see which formats are being left out.

## Random cadence

If many people use the bot, everyone transmitting at the exact same time would be
counterproductive. So, by default:

- **Messages:** the emission window is split into `count` equal parts and each message
  falls at a **random** moment of its own part. They end up spread out, and **never
  two in a row closer than `min_gap_seconds`** (60 s by default).
- **Report:** comes out at a random moment between the end of the emission window and
  `report_window_minutes` later, never closer than `min_gap_seconds` to the last
  message. The bot keeps listening until then, which also catches delayed messages.
- **Listening starts right at the beginning of the window**, even if the first message
  only goes out later.

`random_schedule = false` (or `--fixed-schedule`) reverts to a deterministic mode: one
message every `interval_minutes` from the start (never below `min_gap_seconds`), and
the report right at the end. That's what suits testing.

## How messages are interpreted

- **Station = the radio node.** The station's name is the node's **short name** (up to
  4 characters, the one shown in the app's circle, read from the radio's node
  database), **never** whatever is written in the message itself. If the radio hasn't
  received that node's user info yet, it uses the last 4 hex digits of its id, same as
  the app does (`!a696428c` → `428c`).
- **Only the exact shape counts**: `<keyword> <mode> | <place> [| n/total]`. Everything
  else on the channel (chat, other formats, an extra `|`) is ignored and kept in
  `rx.log`.
- **Hops:** `hopStart − hopLimit` from the packet. **SNR and RSSI** come from the
  packet itself (absent for packets that arrived via MQTT).
- **MQTT:** a packet can arrive over LoRa and still have gone through the internet (a
  radio with uplink published it, and another one with downlink sent it out again).
  The point is to measure RF range, so those packets are **always flagged**
  (`viaMqtt`) and **never count as RF**: a station heard only that way shows up with
  `via = MQTT (does not confirm RF)`. A station heard over RF too counts as RF, and its
  MQTT copies are only counted separately (`mqtt_copies`).
- **Relay hint:** the packet only carries the **last byte** of the id of the node that
  last relayed it, so the report lists the known nodes it could be (`relay 0xe9:
  AB12/CD34`). It's a hint, not an identification.
- **Old messages:** while nobody is connected, **the radio keeps recent messages** and
  hands them over once the bot connects (the `T1`…`T8` we tested all arrived at the
  same instant). Every packet carries `rxTime`, the time the radio received it, and
  the bot **discards test messages the radio received before the session started**
  (with a `session_tolerance_seconds` slack, 60 s by default), so they don't count
  towards the wrong session. Discarded ones are kept in `rx.log` with the note
  `before-session`, and the report says how many there were. Something delivered late
  but received **inside** the session (e.g. after a reconnection) counts normally. If
  the radio's clock isn't set, the packet has no `rxTime` and the bot does not discard it.
- **Ignores:** other channels, its own node, and messages that don't match the shape above.

## Prerequisites

- A Meshtastic radio with WiFi, reachable by IP, with the test channel already
  configured. The bot does not configure the radio.
- A **test channel**, ideally: whatever the bot transmits is heard by everyone on that
  channel, in the same LoRa mode.
- The radio accepts **only one TCP client at a time**: close the app and any other
  program connected to the radio (including other bots) during the session.

## Quick install (Docker, no git)

You only need **Docker with Compose v2**: Docker Desktop on Windows and macOS, and on
Linux or a Raspberry Pi `curl -fsSL https://get.docker.com | sh`. No need for git or
the source code.

### With the installer (Linux, macOS, Raspberry Pi)

```sh
curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/install.sh | sh
```

Asks 4 questions (the radio's IP, the test channel's index and name, and your city),
creates a `mesh-test-bot/` folder with the configuration, and starts the bot. If you'd
rather read the script before running it, download it and open it: it's about a
hundred lines.

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
- If the container keeps restarting, `docker compose logs` says what's missing or wrong.
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
in the log right after it starts.

Run a session right away, without waiting (e.g. to test):

```sh
docker compose run --rm bot --now --fixed-schedule
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

## Installing without Docker

Needs Python 3.9+ and the `meshtastic` library (pinned in `requirements.txt`).

```sh
pip install -r requirements.txt
printf '[bot]\nhost = 192.168.1.50\nchannel = 1\nplace = City\n' > bot.ini   # edit it
python3 mesh_test_bot.py --schedule        # keeps running, repeats every week
python3 mesh_test_bot.py --now             # or: one session right away
```

Instead of `--schedule` you can use the system's cron.

## With MeshMonitor (optional)

If you want the full message history in a web UI, or need more than one program to
talk to the radio (which only accepts one TCP client), the bot can connect to
[MeshMonitor](https://meshmonitor.org)'s **Virtual Node** instead of the radio. Just
point `MTBOT_HOST` and `MTBOT_PORT` at it: there's an [example
`docker-compose`](install/with-meshmonitor.yml) and a [guide](docs/meshmonitor.md)
with the steps, the caveats and what was tested. MeshMonitor is an independent project
and this bot is not affiliated with or endorsed by it.

## Configuration

See the comments in [`defaults.ini`](defaults.ini) for every option and what it does
— it's the bot's own default settings, shipped with the code (don't edit it). Your own
`bot.ini` only needs the options you want to change; `channel` and `place` are the only
two with no default. Each option can come from four places, in this order of priority:

1. a **flag** (`python3 mesh_test_bot.py --help`);
2. an **environment variable** `MTBOT_<NAME>`, the option's name in upper case
   (`MTBOT_PLACE`, `MTBOT_CHANNEL`, `MTBOT_MIN_GAP_SECONDS`…). An empty variable counts
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
- **Message:** `place` (required), `keyword` and, to force the mode, `mode`.
- **Testing without disturbing the network:** point `channel`/`channel_name` at a
  private channel and use `--now --fixed-schedule`. `--dry-run` shows the schedule
  without connecting to the radio.
- **Testing with more than just yourself:** see [Random-schedule
  limits](#random-schedule-limits) below.

## Random-schedule limits

LoRa has no real collision avoidance, so a test with many stations crammed into a
short window causes exactly the congestion it's meant to measure: 100 stations each
sending a handful of messages inside a half-hour window, on the same channel, can
easily flood it once relays are counted. With `random_schedule` (the default), the bot
refuses to start if `count` is above 5 or `listen_minutes` is under 120 (2h) — both
`sys.exit` at startup, like an invalid `channel` or `place` would.

This isn't based on how many stations are actually testing, on purpose: that number
can't be known reliably (someone in a quiet spot and someone in the middle of a dense
city could both be testing alongside 100 others, and neither could tell that from their
own radio), and a check that depends on every participant correctly entering it would
only be as good as the least careful one. A flat cap on `count` and a flat floor on
`listen_minutes` need nothing to be coordinated beyond what already has to be agreed for
people to hear each other at all (`channel`, `weekday`, `start_time`) — they just keep
any single random-schedule session, run by anyone, out of the range where it risks
flooding the channel regardless of how many others join in.

`random_schedule = false` (`--fixed-schedule`) is exempt from both: it's meant for a
single operator's own controlled testing (see [Tests](#tests) below), not a shared
session with an unknown number of participants.

## How it works, and its limits

- **One single connection, kept open for the whole session.** Uses the `meshtastic`
  Python library (not the CLI). The radio only accepts one TCP client at a time, so
  the bot **listens and sends on the same connection**: no deaf windows between
  messages, no processes restarting. It reads received packets directly (no parsing of
  debug text), and node names come from the radio's node database.
- **Recovers from drops.** The library reconnects a dropped socket by itself, and
  while that's happening the radio is deaf until its configuration comes back, without
  the connection looking dropped from outside. The bot detects those reconnections,
  measures how long they lasted, and counts them as outages. If the library can't
  manage it (radio rebooting, WiFi down, a reconnection that never finishes within
  60 s), the connection is treated as dead or deaf, and a supervisor, which checks the
  connection every 2 s, throws it away and builds a new one, with growing backoff. The
  operating system also detects, within about a minute, a radio that vanished without
  closing the connection (TCP keepalive). A message that can't go out waits for the
  connection and only gives up once it's time for the next one.
- **What can still be lost:** only whatever the radio transmits while the connection
  is down, usually around 1 second. If there are outages, the report says how many
  there were and how much time they added up to.
- **Two of our messages are never closer than `min_gap_seconds`**, even across a
  connection outage.
- The bot depends on the packets' fields and on the library's reconnection behaviour,
  so `requirements.txt` pins its version. Only bump it after testing.
- The report stays in a file: it isn't sent anywhere. A future version may let you opt
  in to submitting it somewhere, for aggregated coverage stats across everyone running
  the bot.

## Tests

### Automated

```sh
python3 -m unittest discover tests
```

The tests use made-up names and a fake radio for the connection, the reconnection and
a full session. No radio needed.

### Real, against the radio (with Docker)

Once installed (see above), these are ways to try it out before leaving it scheduled.
Rules for all of them:

- **Use a test channel**: whatever you transmit is heard by everyone on that channel.
  Pick it with `--channel <index> --channel-name <channel name>`.
- **Stop the container first** (`docker compose down`): the radio only accepts one TCP
  client at a time.
- **Flags only apply to that run**; `data/bot.ini` is not changed.
- **Add `--fixed-schedule`** whenever you use `--now`, otherwise the first message and
  the report come out at random moments (the report can take up to
  `report_window_minutes`).

**1. Connection only** (listens for 2 minutes, **sends nothing**: notice `Sending
at:` comes out empty):

```sh
docker compose run --rm bot --now --fixed-schedule --listen-minutes 2 --count 0
```

Should print `Connected to radio … (node …, N known nodes, mode …)` and, at the end,
the report. If the channel's name isn't the expected one, it aborts before doing
anything.

**2. Receiving** (listens without sending; from another radio, send messages on the
test channel):

```sh
docker compose run --rm bot --now --fixed-schedule --listen-minutes 5 --count 0 \
  --channel <index> --channel-name <channel name>
```

Messages to send from the other radio (the first two should come out as `Heard: …`,
the last two as `Ignored (not a test message): …`; swap `LONG_FAST` for the radios'
actual mode):

```
MTBOT LONG_FAST | City | 1/3
MTBOT LONG_FAST | City | 2/3
Hey, this is regular chat
MTBOT LONG_FAST City
```

**3. Sending and receiving** (sends 1 message on the test channel, and listens for 10
minutes):

```sh
docker compose run --rm bot --now --fixed-schedule --listen-minutes 10 --count 1 \
  --channel <index> --channel-name <channel name>
```

**4. Full scheduled session** (the only one that tests the scheduler). Put a session a
few minutes from now, on the test channel, in `data/bot.ini`:

```ini
channel = <index>
channel_name = <channel name>
weekday = <today, in English: monday, tuesday…>
start_time = <~5 minutes from now, HH:MM>
listen_minutes = 12
count = 3
random_schedule = true
min_gap_seconds = 60
report_window_minutes = 4
wake_before_minutes = 2
```

```sh
docker compose up -d
docker compose logs -f --no-log-prefix
```

The log shows the scheduled session (`Next session: …`), the connection to the radio 2
minutes before, the randomly picked moments (`Sending at: … | report at …`), each
message sent and each message heard, and at the end the report and the following
session, a week later. **When you're done, run `docker compose down`** and restore the
production `.ini`, or the container will transmit on the test channel again the
following week.

**Without touching the radio:** `--dry-run` shows just the schedule (`docker compose
run --rm bot --dry-run`).

## License

Copyright (C) 2026 André Cunha. This program is free software, distributed under the
[GNU General Public License](LICENSE) version 3 or, at your option, any later version
(`GPL-3.0-or-later`), **with no warranty of any kind**.

It depends on the Python `meshtastic` library, which is GPL-3.0, and the Docker image
includes it; the other dependencies have permissive licenses (BSD, MIT, Apache-2.0).
The full license text is in the [`LICENSE`](LICENSE) file.
