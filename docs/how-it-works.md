# How it works, and its limits

How a session runs, and what the bot can and cannot do. The [README](../README.md) links here.

- **One single connection, kept open for the whole session.** Uses the `meshtastic`
  Python library (not the CLI). The radio only accepts one TCP client at a time, so
  the bot **listens and sends on the same connection**: no deaf windows between
  messages, no processes restarting. It reads received packets directly (no parsing of
  debug text), and node names come from the radio's node database.
- **A brief connectivity check at startup, in `--schedule` mode.** Otherwise a
  misconfigured host, port or channel would only surface once the first session
  opens -- potentially days later, on a container that's been running quietly the
  whole time. The check connects like a real session would, confirms the channel, then
  disconnects; it retries with backoff for a few minutes on failure and then exits, so
  a broken setup shows up as a restarting container instead of a silent wait.
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
- **Two of our messages are never closer than 2 minutes** (unless `--unsafe-limits` is used),
  even across a connection outage.
- The bot depends on the packets' fields and on the library's reconnection behaviour,
  so `requirements.txt` pins its version. Only bump it after testing.
- The report stays in a file: it isn't sent anywhere. A future version may let you opt
  in to submitting it somewhere, for aggregated coverage stats across everyone running
  the bot.
