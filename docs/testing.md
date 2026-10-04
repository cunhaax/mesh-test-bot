# Tests

Automated tests, and the manual recipes against a real radio. The [README](../README.md) links here.

### Automated

```sh
python3 -m unittest discover tests
```

The tests use made-up names and a fake radio for the connection, the reconnection and
a full session. No radio needed.

The landing page's airtime calculator ([`docs/assets/calculator.js`](assets/calculator.js))
has its own, separate suite (Node's built-in test runner, no dependencies):

```sh
node --test tests/test_calculator.js
```

### Real, against the radio (with Docker)

Once installed (see [Quick install](../README.md#quick-install-docker-no-git)), these are ways to try it out before leaving it scheduled.
Rules for all of them:

- **Use a test channel**: whatever you transmit is heard by everyone on that channel.
  Pick it with `--channel <index> --channel-name <channel name>`.
- **Stop the container first** (`docker compose down`): the radio only accepts one TCP
  client at a time.
- **Flags only apply to that run**; `data/bot.ini` is not changed.
- **Recipes 1–3 use `--unsafe-limits`** because their sessions are shorter than the normal
  limits allow. Use it only on a private channel.

**1. Connection only** (listens for 2 minutes, **sends nothing**: notice `Sending
at:` comes out empty):

```sh
docker compose run --rm bot --now --unsafe-limits --report-window-minutes 10 --listen-minutes 2 --count 0
```

Should print `Connected to radio … (node …, N known nodes, mode …)` and, at the end,
the report. If the channel's name isn't the expected one, it aborts before doing
anything.

**2. Receiving** (listens without sending; from another radio, send messages on the
test channel):

```sh
docker compose run --rm bot --now --unsafe-limits --report-window-minutes 10 --listen-minutes 5 --count 0 \
  --channel <index> --channel-name <channel name>
```

Messages to send from the other radio (the first two should come out as `Heard: …`,
the last two as `Ignored (not a test message): …` — they don't start with the prefix
at all, so they're genuinely not ours, unlike a message that merely has an unusual
shape after the prefix):

```
MTBOT | ABCD | City | 1/3
MTBOT | ABCD | City | 2/3
Hey, this is regular chat
not MTBOT at the start
```

**3. Sending and receiving** (sends 1 message on the test channel, and listens for 10
minutes):

```sh
docker compose run --rm bot --now --unsafe-limits --report-window-minutes 10 --listen-minutes 10 --count 1 \
  --channel <index> --channel-name <channel name>
```

**4. Full scheduled session** (the only one that tests the scheduler). Put a session a
few minutes from now, on the test channel, in `data/bot.ini`:

```ini
channel = <index>
channel_name = <channel name>
weekday = <today, in English: monday, tuesday…>
start_time = <~5 minutes from now, HH:MM>
listen_minutes = 30
message_count = 3
report_window_minutes = 10
```

Those are the smallest values the normal limits allow (see [Session limits](session-limits.md)):
the session takes 30 minutes, and the report comes out up to 10 minutes after it.

```sh
docker compose up -d
docker compose logs -f --no-log-prefix
```

The log shows the scheduled session (`Next session: …`), then (since the session in
this demo is only ~5 minutes out -- close enough that its own connection acts as the
check) `Startup check skipped: …` instead of a separate `Startup check OK`, the
connection to the radio 2 minutes before, the planned send times (`Sending at: … |
report at …`), each message sent and each message heard, and at the end the report and
the following session, a week later. **When you're done, run `docker compose down`**
and restore the production `.ini`, or the container will transmit on the test channel
again the following week.

**Without touching the radio:** `--dry-run` shows just the schedule (`docker compose
run --rm bot --dry-run`).
