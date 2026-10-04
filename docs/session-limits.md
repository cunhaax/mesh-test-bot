# Session limits

Limits that every session is held to, and the flag for private-channel load tests. The [README](../README.md) has the short version.

LoRa has no real collision avoidance, so a test with many stations crammed into a short
window causes exactly the congestion it's meant to measure. Every session, scheduled or
started by hand, is therefore held to these limits:

- `message_count` at most **10**;
- `listen_minutes` (the emission and listening window) at least **30**;
- `report_window_minutes` at least **10**.

A session outside them refuses to start, like an invalid `channel` or `place` would. The
report window is also how long the bot keeps listening after the emission window, so a
large multi-hop mesh has time to finish propagating before the report is written.

These limits don't depend on how many stations are actually testing, on purpose: that
number can't be known reliably from one radio. Flat caps and floors need nothing
coordinated beyond what already has to be agreed for people to hear each other at all
(`channel`, `weekday`, `start_time`).

### `--unsafe-limits`: private-channel load tests only

For a stress test on a channel only you use (for example several of your own radios),
add `--unsafe-limits` to the command line. It lifts the minimum session length, the
message cap and the 2-minute gap between your own messages. The report-window minimum
stays. The 2-minute gap is kept whenever each message has at least 2 minutes of session
to itself; in shorter sessions the gap becomes half of that share (`session ÷ (2 × count)`)
so that the messages still spread out. The bot prints a warning at startup that lists
exactly what was relaxed. The report is still written at a random moment after the session, but
under this flag the minimum wait after the last message is the shortened gap, not 2 minutes.

It is a command-line flag only. It can't be set in `bot.ini`, in an environment variable
or by the installer, so it can't be switched on by accident in a shared `.env`. Never use
it on a channel other people use. For a scheduled load test, put it in the compose
`command:` (for example `["--schedule", "--unsafe-limits"]`), on the same private channel.
