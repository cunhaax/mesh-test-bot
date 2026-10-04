# Upgrading from an older version

Read this before you update an install from before v0.3.0. The [README](../README.md) links here.

**First, get the new image.** Compose only uses the image it already has, so an older
install keeps running the old version until you pull:

```sh
docker compose pull && docker compose up -d --force-recreate
```

Before the session limits, scheduled installs could use shorter sessions, a `random_schedule`
setting and `--fixed-schedule`. Now:

- A session with `listen_minutes` below 30, `report_window_minutes` below 10 or
  `message_count` above 10 refuses to start. Under Docker, that shows up as a container that
  keeps restarting: `docker compose logs` says which limit it is. Raise the values in
  `data/bot.ini` (or `.env`), or, on a private channel only, set the compose `command:` to
  `["--schedule", "--unsafe-limits"]` (keep `--schedule`: a bare flag would replace it).
- `random_schedule` and `interval_minutes` no longer exist; the bot warns about them and
  ignores them. Messages always go out at random moments.
- On-demand runs in `docker-compose.yml` (`command: ["--now", "--fixed-schedule"]`) and
  `docker compose run --rm bot --now --fixed-schedule` must drop `--fixed-schedule`, and
  `--interval` is gone too. The bot refuses both and says so.
