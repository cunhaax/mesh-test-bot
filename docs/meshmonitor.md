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

**With the installer**: run `install.sh` (see the README's Quick Install) and answer
"set up a new MeshMonitor here" when asked how the bot should connect. It writes the
`.env` below for you and prints the commands to start them (nothing starts until you run them).

**By hand**:

1. Download [`install/with-meshmonitor.yml`](../install/with-meshmonitor.yml) and
   [`install/meshmonitor.env.example`](../install/meshmonitor.env.example) into a
   folder, copy the second one to `.env` and adjust it (the radio's IP, and the bot's
   channel, channel name and place).
2. `docker compose -f with-meshmonitor.yml up -d`.
3. Open `http://localhost:8080`, log in with `admin` / `changeme` and **change the
   password** (click the username, *Change Password*).
4. **Enable the Virtual Node** (next step). At startup the bot briefly connects to
   check the radio is reachable, retrying for a few minutes and then **exiting** if it
   never can -- which is what happens on every restart before this step is done. Its
   own `restart: unless-stopped` recovers on its own, within a few minutes, once the
   Virtual Node is enabled -- no manual restart or service ordering required.

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

- `docker compose -f with-meshmonitor.yml logs bot` should show `Startup check OK:
  radio at meshmonitor:4404 answered (node …, mode …; channel name …)` within about a
  minute of starting the bot. If instead you see `Startup check FAILED` repeating and
  then the bot exiting, the Virtual Node likely isn't enabled yet (or MeshMonitor isn't
  connected to the radio) -- fix that and `docker compose -f with-meshmonitor.yml
  restart bot`.
- Later, at the session itself, the log shows `Connected to radio meshmonitor:4404
  (node …, N known nodes, mode …)` again -- that's the session's own connection, a
  separate one from the startup check.
- In the UI (*Messages*) you see the messages the bot sends, as its own node's
  messages, and the ones arriving from others.

## Rules and caveats

- **Nothing else connects directly to the radio** while MeshMonitor holds it: a direct
  connection **knocks it off**, and with it disconnected the Virtual Node has no
  configuration to serve. Use its UI, or connect to the Virtual Node instead.
- **The Virtual Node has no authentication.** Whoever can reach it can use it. That's
  why the example does not publish 4404 outside Docker, and admin commands stay
  disabled.
- **The UI is published on every interface of the machine, so your LAN can reach it.**
  Change the default password (`admin` / `changeme`) before anyone else connects. Set
  `ALLOWED_ORIGINS` to the exact address you open it at (`localhost` and `127.0.0.1` count as
  different origins). To keep it on this machine only, change the port mapping to
  `"127.0.0.1:8080:3001"`.
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
