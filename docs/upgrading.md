# Upgrading

## Every upgrade

```sh
docker compose pull && docker compose up -d
```

Compose only uses the image it already has, so an install keeps running the old version
until you pull. `up -d` then recreates the container with the new image; your `data/`
folder is not touched. Afterwards, read the release notes (GitHub, Releases) of the versions you skipped, looking for a
"Changes to your compose file" section: that is the only thing you may have to apply by hand.

## What updates by itself, and what does not

| | Updates with `pull`? | What to do |
|---|---|---|
| The bot's code and its defaults (`defaults.ini`) | Yes | Nothing. |
| **New options** | Yes, they ship with a default | Nothing: your `.env` and `data/bot.ini` keep working. To change one, add it to `.env` as `MTBOT_<NAME>` (the names are in `env.example` and `defaults.ini`). |
| **Your `docker-compose.yml`** | **No** | The installer copied it once and never touches it again. If a release changes the template, apply the change by hand (below). |
| Your `.env` | No | Only if a release says a setting was renamed or removed. |

## When a release changes the compose file

Don't overwrite your file: you may have edited the image tag, ports, `command:`, or added
more services. Compare it with the current template and copy over only what is new:

```sh
curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/docker-compose.yml \
  | diff - docker-compose.yml
```

(Use `with-meshmonitor.yml` or `docker-compose.once.yml` instead if that is the one you
installed.) With several bots in one file, make the same change in each bot service.
Then run `docker compose up -d`: it recreates only the services whose configuration changed.

## Breaking changes

Anything a release asks you to change by hand (a renamed or removed setting, a compose
file change) is listed in that release's notes: see
[Releases](https://github.com/cunhaax/mesh-test-bot/releases) on GitHub.
