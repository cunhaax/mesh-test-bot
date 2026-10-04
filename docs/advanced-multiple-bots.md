# Several scheduled bots on one MeshMonitor

Use this when you want more than one automatic test, each on its own schedule, through the
same radio. It assumes MeshMonitor is already set up with its Virtual Node enabled (see the
[MeshMonitor guide](meshmonitor.md)). Each bot is one scheduled test.

## Rules that matter

- **Never overlap sessions on the same channel.** A session runs from its start time for
  `listen_minutes` plus up to `report_window_minutes`. Two sessions on one channel at the
  same time interfere with each other and with everyone else on that channel.
- **Give each test its own keyword** (`MTBOT_KEYWORD`). A report only counts messages with
  its own prefix, so tests on the same channel stay separate on air.
- **All bots on one radio share its node.** They send as the same station, and a bot ignores
  messages from its own node, so one bot never counts another bot's messages.
- **The session limits apply to each bot**: at least 30 minutes, at most 10 messages, report
  window at least 10 minutes. `--unsafe-limits` is for private channels only.

## Layout

One folder with the compose file and one `.env` per bot:

```
mesh-test-bot/
  docker-compose.yml   MeshMonitor, bot and bot2 services
  .env                 MeshMonitor and the first bot
  .env.bot2            the second bot
  data/                reports of the first bot
  data2/               reports of the second bot
```

`docker-compose.yml`:

```yaml
services:
  meshmonitor:
    image: ghcr.io/yeraze/meshmonitor:latest
    container_name: meshmonitor
    restart: unless-stopped
    env_file: .env
    ports:
      - "127.0.0.1:8080:3001"
    volumes:
      - meshmonitor-data:/data

  bot:
    image: ghcr.io/cunhaax/mesh-test-bot:latest
    container_name: mesh-test-bot
    restart: unless-stopped
    depends_on:
      - meshmonitor
    env_file: .env
    volumes:
      - ./data:/data

  bot2:
    image: ghcr.io/cunhaax/mesh-test-bot:latest
    container_name: mesh-test-bot-2
    restart: unless-stopped
    depends_on:
      - meshmonitor
    env_file: .env.bot2
    volumes:
      - ./data2:/data

volumes:
  meshmonitor-data:
```

`.env.bot2` (the second bot; the first bot's `.env` follows the usual install, with
`MTBOT_HOST=meshmonitor` and `MTBOT_PORT=4404`):

```sh
MTBOT_HOST=meshmonitor
MTBOT_PORT=4404
MTBOT_CHANNEL=1
MTBOT_CHANNEL_NAME=TestChannel
MTBOT_PLACE=City
MTBOT_KEYWORD=TEST B
MTBOT_REPORT_PREFIX=B
MTBOT_TIMEZONE=Europe/Lisbon
MTBOT_WEEKDAY=sunday
MTBOT_START_TIME=18:00
MTBOT_LISTEN_MINUTES=60
MTBOT_MESSAGE_COUNT=4
MTBOT_REPORT_WINDOW_MINUTES=20
```

The installer writes a single-bot setup, so a second bot is set up by hand as above.

## Start, check and stop

```sh
docker compose up -d
docker compose logs -f --no-log-prefix bot2
docker compose down
```

Each bot logs its own schedule when it starts (`Sending at: …`). Check that the two schedules
do not overlap before you leave them running.

## Updating

```sh
docker compose pull && docker compose up -d --force-recreate
```
