#!/bin/sh
# mesh-test-bot: installer. Asks a few questions (press Enter to accept each
# default), creates the folder, writes the configuration and prints the commands
# that start the bot in Docker. It never starts anything itself.
#
#   curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/install.sh | sh
#
# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Variables (all optional):
#   MTB_CONNECTION=direct|existing|setup   how the bot reaches the radio: directly,
#                  through an existing MeshMonitor, or a new MeshMonitor set up here
#                  (anything else stops the install)
#   MTBOT_HOST, MTBOT_PORT, MTBOT_CHANNEL, MTBOT_CHANNEL_NAME, MTBOT_PLACE,
#   MTBOT_KEYWORD, MTBOT_REPORT_PREFIX, MTBOT_WEEKDAY, MTBOT_START_TIME,
#   MTBOT_TIMEZONE   answers, skip the matching prompt
#   MESHTASTIC_NODE_IP, ALLOWED_ORIGINS   only with MTB_CONNECTION=setup (MeshMonitor's own settings)
#   MTB_ONESHOT=1  skip the weekday/start-time prompts, run once now instead
#                  (1/0 or y/yes/n/no only -- anything else stops the install)
#                  With MTB_CONNECTION=setup, MTBOT_HOST/MTBOT_PORT are always fixed at
#                  meshmonitor:4404, silently overriding any pre-set value for those two.
#   MTB_DIR       folder to create (default ./mesh-test-bot)
#   MTB_SOURCE    local folder with docker-compose.yml and env.example, instead of downloading them
#   MTB_RAW_BASE  where to download them from (default GitHub)
set -eu

REPO="${MTB_REPO:-cunhaax/mesh-test-bot}"
RAW="${MTB_RAW_BASE:-https://raw.githubusercontent.com/$REPO/master/install}"
DIR="${MTB_DIR:-mesh-test-bot}"
SRC="${MTB_SOURCE:-}"
if [ -z "$SRC" ] && [ -f "$(dirname "$0")/docker-compose.yml" ]; then  # run from a local copy
    SRC="$(cd "$(dirname "$0")" && pwd)"
fi

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# Ask something, unless it is already in the environment. Reads from the terminal even
# when the script comes through "curl | sh" (where stdin is the script itself).
ask() {
    name=$1; prompt=$2; def=${3:-}
    eval "cur=\${$name:-}"
    [ -z "$cur" ] || return 0
    if [ -n "$def" ]; then printf '%s [%s]: ' "$prompt" "$def"; else printf '%s: ' "$prompt"; fi
    if [ -t 0 ]; then IFS= read -r ans || ans=""
    # (the subshell: under dash, a failed redirection on ":" would exit the whole script)
    elif [ -r /dev/tty ] && ( : </dev/tty ) 2>/dev/null; then IFS= read -r ans </dev/tty || ans=""
    else IFS= read -r ans || ans=""; fi
    [ -n "$ans" ] || ans=$def
    eval "$name=\$ans"
}

fetch() {  # fetch <file> <destination>
    if [ -n "$SRC" ]; then
        cp "$SRC/$1" "$2" || die "could not find $SRC/$1"
    elif command -v curl >/dev/null 2>&1; then
        curl -fsSL "$RAW/$1" -o "$2" || die "could not download $RAW/$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$RAW/$1" || die "could not download $RAW/$1"
    else
        die "need curl or wget to download the files"
    fi
}

[ ! -e "$DIR/.env" ] || die "$DIR/.env already exists: edit it by hand, or delete it to start over"

say "mesh-test-bot: a few questions (press Enter to accept the default in [brackets])."
ask MTB_CONNECTION "Connect directly to the radio, through an existing MeshMonitor, or set up a new MeshMonitor here? [direct/existing/setup]" direct
MTB_CONNECTION=$(printf '%s' "$MTB_CONNECTION" | tr 'A-Z' 'a-z')
case $MTB_CONNECTION in
    direct|d) MTB_CONNECTION=direct ;;
    existing|e) MTB_CONNECTION=existing ;;
    setup|s) MTB_CONNECTION=setup ;;
    *) die "MTB_CONNECTION must be direct/existing/setup: $MTB_CONNECTION" ;;
esac

if [ "$MTB_CONNECTION" = setup ]; then
    ask MESHTASTIC_NODE_IP "Radio's IP (on your network; MeshMonitor connects to it)"
    ask ALLOWED_ORIGINS "Address you'll open the MeshMonitor web UI at" "http://localhost:8080"
    MTBOT_HOST=meshmonitor
    MTBOT_PORT=4404
elif [ "$MTB_CONNECTION" = existing ]; then
    ask MTBOT_HOST "That MeshMonitor's address (its Virtual Node host, on your network)"
    ask MTBOT_PORT "Its Virtual Node port" 4404
else
    ask MTBOT_HOST "Radio's IP (on your network)"
    ask MTBOT_PORT "Radio's TCP port" 4403
fi
ask MTBOT_CHANNEL "Test channel index on the radio, 0 to 7 (as the app shows it)" 1
ask MTBOT_CHANNEL_NAME "That channel's name (recommended; empty = do not check)"
ask MTBOT_PLACE "Where you are (city)"
ask MTBOT_KEYWORD "Message prefix (tags this bot's messages; may be several words)" MTBOT
ask MTBOT_REPORT_PREFIX "Report-line prefix (starts every line of the readable report)" ACK
ask MTBOT_TIMEZONE "IANA timezone for report timestamps (e.g. Europe/Lisbon)" Europe/Lisbon
ask MTB_ONESHOT "Run on demand (one session when you start it) instead of on a recurring weekly schedule?" n
case $MTB_ONESHOT in
    1|[Yy]|[Yy][Ee][Ss]) MTB_ONESHOT=1 ;;
    0|[Nn]|[Nn][Oo]) MTB_ONESHOT=0 ;;
    *) die "MTB_ONESHOT must be 1/0 or y/yes/n/no: $MTB_ONESHOT" ;;
esac
if [ "$MTB_ONESHOT" = 0 ]; then
    ask MTBOT_WEEKDAY "Day of the week to run (monday..sunday, or 'daily')" saturday
    ask MTBOT_START_TIME "Start time, 24h (HH:MM)" 06:00
fi

# Validation: .env stores the values inside single quotes, so no quotes or '|' allowed.
vars_to_check="MTBOT_HOST MTBOT_PORT MTBOT_CHANNEL MTBOT_CHANNEL_NAME MTBOT_PLACE MTBOT_KEYWORD \
    MTBOT_REPORT_PREFIX MTBOT_WEEKDAY MTBOT_START_TIME MTBOT_TIMEZONE"
[ "$MTB_CONNECTION" != setup ] || vars_to_check="$vars_to_check MESHTASTIC_NODE_IP ALLOWED_ORIGINS"
for v in $vars_to_check; do
    eval "val=\${$v:-}"
    case $val in *"'"* | *'"'* | *'|'* | *'#'* ) die "$v cannot contain quotes, '|' or '#': $val" ;; esac
done
[ -n "$MTBOT_HOST" ] || die "missing the radio's IP"
case $MTBOT_HOST in *[!A-Za-z0-9._:-]*) die "invalid radio IP or hostname: $MTBOT_HOST" ;; esac
case $MTBOT_PORT in *[!0-9]*) die "the port must be a number: $MTBOT_PORT" ;; esac
case $MTBOT_CHANNEL in [0-7]) ;; *) die "the channel must be a number from 0 to 7: $MTBOT_CHANNEL" ;; esac
[ -n "$MTBOT_PLACE" ] || die "missing the place"
if [ "$MTB_CONNECTION" = setup ]; then
    [ -n "$MESHTASTIC_NODE_IP" ] || die "missing the radio's IP"
    case $MESHTASTIC_NODE_IP in *[!A-Za-z0-9._:-]*) die "invalid radio IP or hostname: $MESHTASTIC_NODE_IP" ;; esac
fi
if [ "$MTB_ONESHOT" = 0 ]; then
    MTBOT_WEEKDAY=$(printf '%s' "$MTBOT_WEEKDAY" | tr 'A-Z' 'a-z')
    case $MTBOT_WEEKDAY in monday|tuesday|wednesday|thursday|friday|saturday|sunday|daily) ;;
        *) die "weekday must be monday..sunday or 'daily': $MTBOT_WEEKDAY" ;; esac
    case $MTBOT_START_TIME in [0-1][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
        *) die "start time must be HH:MM (24h, 00:00 to 23:59): $MTBOT_START_TIME" ;; esac
fi

mkdir -p "$DIR/data"
if [ "$MTB_CONNECTION" = setup ]; then
    fetch with-meshmonitor.yml "$DIR/docker-compose.yml"
elif [ "$MTB_ONESHOT" = 1 ]; then
    fetch docker-compose.once.yml "$DIR/docker-compose.yml"
else
    fetch docker-compose.yml "$DIR/docker-compose.yml"
fi
fetch env.example "$DIR/env.example"
{
    say "# Generated by install.sh. All available options are in env.example."
    if [ "$MTB_CONNECTION" = setup ]; then
        say "MESHTASTIC_NODE_IP='$MESHTASTIC_NODE_IP'"
        say "ALLOWED_ORIGINS='$ALLOWED_ORIGINS'"
    fi
    say "MTBOT_HOST='$MTBOT_HOST'"
    say "MTBOT_PORT='$MTBOT_PORT'"
    say "MTBOT_CHANNEL='$MTBOT_CHANNEL'"
    say "MTBOT_CHANNEL_NAME='$MTBOT_CHANNEL_NAME'"
    say "MTBOT_PLACE='$MTBOT_PLACE'"
    say "MTBOT_KEYWORD='$MTBOT_KEYWORD'"
    say "MTBOT_REPORT_PREFIX='$MTBOT_REPORT_PREFIX'"
    say "MTBOT_TIMEZONE='$MTBOT_TIMEZONE'"
    if [ "$MTB_ONESHOT" = 0 ]; then
        say "MTBOT_WEEKDAY='$MTBOT_WEEKDAY'"
        say "MTBOT_START_TIME='$MTBOT_START_TIME'"
    fi
} > "$DIR/.env"
say "Done: configuration in $DIR/.env. Nothing is running yet."
say ""
say "To start it, from $DIR:"
if [ "$MTB_CONNECTION" = setup ]; then
    if [ "$MTB_ONESHOT" = 1 ]; then
        say "  docker compose up -d meshmonitor"
        say "    starts MeshMonitor only; it keeps running."
    else
        say "  docker compose up -d"
        say "    starts MeshMonitor and the bot; both keep running (restart: unless-stopped)."
    fi
    say "  Then open $ALLOWED_ORIGINS, log in with admin / changeme, change the password,"
    say "  and enable the Virtual Node for this radio (port 4404, admin commands OFF):"
    say "    https://github.com/$REPO/blob/master/docs/meshmonitor.md"
    if [ "$MTB_ONESHOT" = 1 ]; then
        say "  Then, each time you want a session:"
        say "  docker compose run --rm bot --now --fixed-schedule"
        say "    runs one session now, using the duration and message settings in .env;"
        say "    the bot container stops when it is done. MeshMonitor keeps running."
    else
        say "  The bot retries connecting on its own, and succeeds within a few minutes of that."
    fi
elif [ "$MTB_ONESHOT" = 1 ]; then
    say "  docker compose up -d"
    say "    runs one session now, using the duration and message settings in .env;"
    say "    the container stops when it is done. Run the same command again for another."
else
    say "  docker compose up -d"
    say "    starts the bot; it runs every $MTBOT_WEEKDAY at $MTBOT_START_TIME ($MTBOT_TIMEZONE)"
    say "    and keeps running until you stop it."
fi
say ""
say "Other commands, from $DIR:"
say "  docker compose logs -f --no-log-prefix    follow the log"
say "  docker compose down                        stop it"
say "Reports and readings are written to $DIR/data/."
