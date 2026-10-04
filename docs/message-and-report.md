# The message, the report and how messages are read

This page is the reference for what goes on air and what the bot writes. The [README](../README.md) has the short version.

## The message

The bot sends messages shaped like this:

```
MTBOT | ABCD | City | 2/3
```

| Field | What it is |
|---|---|
| `MTBOT` | the prefix (`keyword`): tells the bot's messages apart from regular chat |
| `ABCD` | the station's own short name, read from the radio (never typed by hand) |
| `City` | where you are, at city level (`place`) |
| `2/3` | this is message 2 of 3 in the session, which lets a delivery rate be computed |

Short (max 200 bytes) and easy to parse, but not strict: a message is "ours" as soon
as it starts with the prefix, however it's punctuated or capitalized, even if the
rest of it doesn't look exactly like the shape above — see [How messages are
interpreted](#how-messages-are-interpreted) below for the parsing rules. Whoever wants
to hear your messages only has to configure the same prefix (`keyword`); it can be
several words, e.g. `keyword = FIELD TEST ALPHA`.

## The report

Each session writes two files in the configuration folder:

**`report.txt`**, readable, one short line per station — meant to be read or pasted
elsewhere as-is, not as a table (there's no column header):

```
=== 2026-09-26 21:41 ===
Reporter: AB12 (!deadbeef) - Place: City - Sent: 3/3
ACK | CD34 | 0   | Villatown | RF
ACK | EF56 | 1-2 | Valley    | RF
```

The `=== ... ===` line marks where each session's report starts in the file (several
sessions accumulate in the same `report.txt`). The header right after it is `Reporter:
<name> (<node>) - Place: <place> - Sent: <delivered>/<total>`. Each station's line below
it is `<report_prefix> | <name> | <hops> | <place> | RF or MQTT`. `report_prefix` is its
own option (default `ACK`), separate from the message prefix above. The richer numbers —
session start/end, LoRa mode, channel, delivery rate, duplicates, SNR, RSSI — stay in
`report.jsonl` only; see below.

**`report.jsonl`**, one JSON line per session (the same session's full data, for
machines, and the object that can later go to a server). Excerpt:

```json
{"schema": 2,
 "session": {"start": "2026-09-26T21:00:00+01:00", "end": "2026-09-26T21:30:00+01:00", "report_at": "2026-09-26T21:41:07+01:00", "timezone": "Europe/Lisbon"},
 "reporter": {"node": "!deadbeef", "name": "AB12", "place": "City"},
 "radio": {"mode": "LONG_FAST", "region": "EU_868", "use_preset": true, "modem_preset": 0, "bandwidth": 250, "spread_factor": 11, "coding_rate": 5, "channel_num": 1, "channel": 1, "channel_name": "TestChannel"},
 "sent": [{"seq": 1, "total": 3, "planned": "…", "ok": true, "text": "MTBOT | AB12 | City | 1/3", "at": "…"}],
 "heard": [{"node": "!cafef00d", "name": "CD34", "place": "Villatown", "path": "rf",
            "received": 2, "of": 3, "receptions": 3, "duplicates": 1, "hops_min": 0, "hops_max": 0,
            "snr_avg": 5.3, "snr_best": 6.0, "rssi_avg": -96, "rssi_best": -94, "relay": "direct", "mqtt_copies": 0, "first": "…", "last": "…"}],
 "outages": {"count": 0, "total_s": 0}}
```

- **`received` / `of`**: how many (distinct) messages from the station arrived, out of
  what it said it sent. **`duplicates`**: repeated readings of the same message.
- **`hops`**: the lowest and highest seen (`hopStart − hopLimit`). **`SNR` and
  `RSSI`**: average and best value.
- **`place`**: can be `"N/A"` if the message matched the prefix but didn't have one —
  see [How messages are interpreted](#how-messages-are-interpreted).
- **`relay`**: `direct` (0 hops) or a hint of the node that relayed it (see below).
- If the connection to the radio drops during the session, the report says how many
  times and how much time that added up to.

`rx.log` keeps every reading, one per line: time, node, name, `hops=`, `mqtt=0/1`,
`snr=`, `rssi=`, the note (the relay hint, `ignored` or `before-session`) and the
original text — including text **ignored** on the channel (marked `ignored`), so you
can see which formats are being left out.

## Random cadence

If many people use the bot, everyone transmitting at the exact same time would be
counterproductive. So, by default:

- **Messages:** the emission window is split into `message_count` equal parts and each message
  falls at a **random** moment of its own part. They end up spread out, and **never
  two in a row closer than 2 minutes** (unless `--unsafe-limits` is used, see
  [Session limits](session-limits.md)).
- **Report:** comes out at a random moment between the end of the emission window and
  `report_window_minutes` later, never closer than 2 minutes to the last message. The
  bot keeps listening until then, which also catches delayed messages.
- **Listening starts right at the beginning of the window**, even if the first message
  only goes out later.

There's no fixed-times mode: sessions are always random, which is what keeps many
participants from transmitting at once. For a private-channel load test, see
[Session limits](session-limits.md) and `--unsafe-limits`.

## How messages are interpreted

- **Station = the radio node.** The station's name is the node's **short name** (up to
  4 characters, the one shown in the app's circle, read from the radio's node
  database), **never** whatever is written in the message itself. If the radio hasn't
  received that node's user info yet, it uses the last 4 hex digits of its id, same as
  the app does (`!a696428c` → `428c`).
- **Matching is tolerant, not strict.** A message counts as ours as soon as it starts
  with the configured prefix (`keyword`) — case doesn't matter, and any punctuation or
  spacing (or none) between the prefix's words is accepted. Everything after the prefix
  is split into fields on `,`, `-` or `|`: if the last field looks like `n/total`, the
  field right before it is the place; otherwise the last field itself is the place and
  there's no numbering. Any other fields in between are ignored. A message that starts
  with the prefix but has nothing usable after it still counts as heard, with place
  `"N/A"` rather than being dropped. Everything that doesn't start with the prefix at
  all (chat, other formats) is ignored and kept in `rx.log`.
- **Hops:** `hopStart − hopLimit` from the packet. **SNR and RSSI** come from the
  packet itself (absent for packets that arrived via MQTT).
- **MQTT:** a packet can arrive over LoRa and still have gone through the internet (a
  radio with uplink published it, and another one with downlink sent it out again).
  The point is to measure RF range, so those packets are **always flagged**
  (`viaMqtt`) and **never count as RF**: a station heard only that way shows up with
  `via = MQTT` (shown as just `MQTT` in the compact `report.txt` line — it does not
  confirm RF reach). A station heard over RF too counts as RF, and its MQTT copies are
  only counted separately (`mqtt_copies`).
- **Relay hint:** the packet only carries the **last byte** of the id of the node that
  last relayed it, so the report lists the known nodes it could be (`relay 0xe9:
  AB12/CD34`). It's a hint, not an identification.
- **Old messages:** while nobody is connected, **the radio keeps recent messages** and
  hands them over once the bot connects (the `T1`…`T8` we tested all arrived at the
  same instant). Every packet carries `rxTime`, the time the radio received it, and
  the bot **discards test messages the radio received before the session started**
  (with a 60 s slack, fixed), so they don't count towards the wrong session. Discarded
  ones are kept in `rx.log` with the note `before-session`, and the report says how
  many there were. Something delivered late
  but received **inside** the session (e.g. after a reconnection) counts normally. If
  the radio's clock isn't set, the packet has no `rxTime` and the bot does not discard it.
  The `--schedule` startup check (below) also connects briefly and so also drains any
  stored messages, but never records or counts them (it isn't listening).
- **Ignores:** other channels, its own node, and messages that don't start with the prefix.
