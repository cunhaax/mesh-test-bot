# The bot with MeshMonitor

[MeshMonitor](https://meshmonitor.org) is an independent web application (BSD-3
license) that connects to a radio, stores **every** message and telemetry reading it
hears, and shows them in a UI. This guide is not affiliated with or endorsed by it.

## When it makes sense

- **The radio only accepts one TCP client at a time.** With MeshMonitor holding that
  connection, the bot (and the app over WiFi, and any other program) would be locked
  out. MeshMonitor's **Virtual Node** solves that: it keeps the real connection and
  forwards the stream to any clients that connect to it.
- **You get the history.** The bot only records what matches the shape of its own
  messages; MeshMonitor stores everything the radio hears, on every channel, and you
  can read it in the UI.
- **It costs an extra piece** in the path. If you only want the bot, direct mode (see
  the README) is simpler.

```
radio (WiFi, :4403) ⇄ MeshMonitor ⇄ Virtual Node (:4404, Docker network only) ⇄ bot
                          ⇅
                     web UI (:8080)
```

The bot receives the **same stream** as MeshMonitor: opening or reading messages in
the UI does not take them away from it (the UI reads from its own database, without
opening any connection to the radio).

## Installation

1. Download [`install/with-meshmonitor.yml`](../install/with-meshmonitor.yml) into a
   folder and edit the 5 lines marked `<-- change` (the radio's IP, and the bot's
   channel, channel name and place).
2. `docker compose -f with-meshmonitor.yml up -d`
3. Open `http://localhost:8080`, log in with `admin` / `changeme` and **change the
   password** (click the username, *Change Password*).
4. **Enable the Virtual Node** (next step). Until you do, the bot keeps trying to
   connect and transmits nothing.

## Enabling the Virtual Node

The `ENABLE_VIRTUAL_NODE=true` variable that appears in MeshMonitor's add-on
documentation **did not open the port** in version 4.16.1. It's a **per-source**
setting:

- **In the UI:** *Dashboard → Sources →* edit the radio's source *→ Enable Virtual
  Node*, port `4404`, and leave *Admin commands* **disabled**.
- **Via the API** (observed, not documented; the UI is the official path). With
  `curl`, log in and save the session, request a CSRF token, and `PUT` to the source:

  ```sh
  B=http://localhost:8080
  T=$(curl -s -c jar -b jar $B/api/csrf-token | sed 's/.*"csrfToken":"\([^"]*\)".*/\1/')
  curl -s -c jar -b jar -H "X-CSRF-Token: $T" -H "Origin: $B" -H 'Content-Type: application/json' \
       -d '{"username":"admin","password":"YOUR_PASSWORD"}' $B/api/auth/login
  ID=$(curl -s -b jar -H "Origin: $B" $B/api/sources | sed 's/.*"id":"\([^"]*\)".*/\1/')
  curl -s -X PUT -b jar -c jar -H "X-CSRF-Token: $T" -H "Origin: $B" -H 'Content-Type: application/json' \
       -d '{"config":{"host":"RADIO_IP","port":4403,"virtualNode":{"enabled":true,"port":4404,"allowAdminCommands":false}}}' \
       $B/api/sources/$ID
  ```

MeshMonitor's log then shows `Virtual node server listening on port 4404`.

## Verifying

- `docker compose -f with-meshmonitor.yml logs bot` should show `Connected to radio
  meshmonitor:4404 (node …, N known nodes, mode …)`.
- In the UI (*Messages*) you see the messages the bot sends, as its own node's
  messages, and the ones arriving from others.

## Rules and caveats

- **Nothing else connects directly to the radio** while MeshMonitor holds it: a direct
  connection **knocks it off**, and with it disconnected the Virtual Node has no
  configuration to serve. Use its UI, or connect to the Virtual Node instead.
- **The Virtual Node has no authentication.** Whoever can reach it can use it. That's
  why the example does not publish 4404 outside Docker, and admin commands stay
  disabled.
- **The UI stays on `127.0.0.1` only.** To open it from another computer, change the
  port mapping and `ALLOWED_ORIGINS` to the address you open it at (it must match
  exactly: `localhost` and `127.0.0.1` count as different origins), and keep the
  password changed.
- **MeshMonitor's automation features come disabled** on a fresh install (auto
  replies, announcements, traceroutes, time sync), so it transmits nothing on its own.
  Worth checking under *Settings* before enabling them: every transmission of its own
  is airtime, and the bot expects the test channel to stay clean.
- **The bot ignores messages sent by its own node**, including ones you send through
  MeshMonitor's UI: they go out under the same node, and don't count as heard.
- **A bot session only sees what arrives from the moment it connects.** Whether the
  Virtual Node replays stored messages to new clients is not known (it did not in our
  tests).

## What was tested

| | |
|---|---|
| MeshMonitor | 4.16.1 (the image exists for amd64, arm64 and arm/v7) |
| Radio | Heltec V4, firmware 2.7.26, `LONG_FAST`, on a private channel |
| Bot | sending and receiving through the Virtual Node: mode and channel read correctly, packets from another node with hops, SNR, RSSI and relay intact |
| Restarting MeshMonitor mid-session | the bot resumed sending and receiving by itself, no intervention needed |

With firmware **2.7.26 on a simulator** (`meshtasticd`), MeshMonitor 4.16.1 lost the
connection when requesting modules that firmware doesn't know; with firmware 2.8.1 it
was stable. On the real radio with 2.7.26 there were no drops. If your radio has older
firmware and MeshMonitor keeps dropping, update one of the two.

**Not verified:** multi-day runs; behavior with multiple sources; and the report's
outage warning after restarting a real MeshMonitor (the fix that produces it was
tested with the real library against a simulated radio, but not repeated against an
actual MeshMonitor).

## Going back to direct mode

Stop MeshMonitor (`docker compose -f with-meshmonitor.yml down`) and use the README's
[`install/docker-compose.yml`](../install/docker-compose.yml), with `MTBOT_HOST`
pointing at the radio and `MTBOT_PORT` set to `4403` (or without that variable).
