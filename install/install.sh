#!/bin/sh
# mesh-test-bot: installer. Asks a few questions (press Enter to accept each
# default), creates the folder, writes the configuration and starts the bot
# in Docker.
#
#   curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/install.sh | sh
#
# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Variables (all optional):
#   MTBOT_HOST, MTBOT_PORT, MTBOT_CHANNEL, MTBOT_CHANNEL_NAME, MTBOT_PLACE,
#   MTBOT_KEYWORD, MTBOT_REPORT_PREFIX, MTBOT_WEEKDAY, MTBOT_START_TIME,
#   MTBOT_TIMEZONE   answers, skip the matching prompt
#   MTB_ONESHOT=1  skip the weekday/start-time prompts, run once now instead
#                  (1/0 or y/yes/n/no only -- anything else stops the install)
#   MTB_DIR       folder to create (default ./mesh-test-bot)
#   MTB_SOURCE    local folder with docker-compose.yml and env.example, instead of downloading them
#   MTB_RAW_BASE  where to download them from (default GitHub)
#   MTB_SKIP_START=1   only write the files, do not start Docker
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

if [ "${MTB_SKIP_START:-}" != 1 ]; then
    command -v docker >/dev/null 2>&1 || die "Docker is not installed (https://docs.docker.com/get-docker/)"
    docker compose version >/dev/null 2>&1 || die "missing Docker Compose v2 (the 'docker compose' command)"
fi
[ ! -e "$DIR/.env" ] || die "$DIR/.env already exists: edit it by hand, or delete it to start over"

say "mesh-test-bot: a few questions (press Enter to accept the default in [brackets])."
ask MTBOT_HOST "Radio's IP (on your network)"
ask MTBOT_PORT "Radio's TCP port" 4403
ask MTBOT_CHANNEL "Test channel index on the radio, 0 to 7 (as the app shows it)" 1
ask MTBOT_CHANNEL_NAME "That channel's name (recommended; empty = do not check)"
ask MTBOT_PLACE "Where you are (city)"
ask MTBOT_KEYWORD "Message prefix (tags this bot's messages; may be several words)" MTBOT
ask MTBOT_REPORT_PREFIX "Report-line prefix (starts every line of the readable report)" ACK
ask MTBOT_TIMEZONE "IANA timezone for report timestamps (e.g. Europe/Lisbon)" Europe/Lisbon
ask MTB_ONESHOT "Run once right now instead of on a recurring weekly schedule?" n
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
for v in MTBOT_HOST MTBOT_PORT MTBOT_CHANNEL MTBOT_CHANNEL_NAME MTBOT_PLACE MTBOT_KEYWORD \
         MTBOT_REPORT_PREFIX MTBOT_WEEKDAY MTBOT_START_TIME MTBOT_TIMEZONE; do
    eval "val=\${$v:-}"
    case $val in *"'"* | *'"'* | *'|'* | *'#'* ) die "$v cannot contain quotes, '|' or '#': $val" ;; esac
done
[ -n "$MTBOT_HOST" ] || die "missing the radio's IP"
case $MTBOT_HOST in *[!A-Za-z0-9._:-]*) die "invalid radio IP or hostname: $MTBOT_HOST" ;; esac
case $MTBOT_PORT in *[!0-9]*) die "the port must be a number: $MTBOT_PORT" ;; esac
case $MTBOT_CHANNEL in [0-7]) ;; *) die "the channel must be a number from 0 to 7: $MTBOT_CHANNEL" ;; esac
[ -n "$MTBOT_PLACE" ] || die "missing the place"
if [ "$MTB_ONESHOT" = 0 ]; then
    MTBOT_WEEKDAY=$(printf '%s' "$MTBOT_WEEKDAY" | tr 'A-Z' 'a-z')
    case $MTBOT_WEEKDAY in monday|tuesday|wednesday|thursday|friday|saturday|sunday|daily) ;;
        *) die "weekday must be monday..sunday or 'daily': $MTBOT_WEEKDAY" ;; esac
    case $MTBOT_START_TIME in [0-1][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
        *) die "start time must be HH:MM (24h, 00:00 to 23:59): $MTBOT_START_TIME" ;; esac
fi

mkdir -p "$DIR/data"
if [ "$MTB_ONESHOT" = 1 ]; then
    fetch docker-compose.once.yml "$DIR/docker-compose.yml"
else
    fetch docker-compose.yml "$DIR/docker-compose.yml"
fi
fetch env.example "$DIR/env.example"
{
    say "# Generated by install.sh. All available options are in env.example."
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
say "Done: configuration in $DIR/.env"
if [ "$MTB_ONESHOT" = 1 ]; then
    say "Runs once now, then the container stops (restart it with 'docker compose up -d' again)."
else
    say "Schedule: every $MTBOT_WEEKDAY at $MTBOT_START_TIME ($MTBOT_TIMEZONE)."
    say "  To change it, edit MTBOT_WEEKDAY / MTBOT_START_TIME / MTBOT_TIMEZONE in $DIR/.env."
fi

if [ "${MTB_SKIP_START:-}" = 1 ]; then
    say "(MTB_SKIP_START=1: Docker was not started)"
    exit 0
fi
cd "$DIR"
docker compose up -d
sleep 4
say ""
say "What the bot is saying:"
docker compose logs --no-log-prefix --tail 6
say ""
say "To follow it live:   cd $DIR && docker compose logs -f --no-log-prefix"
say "Reports and readings: $DIR/data/"
say "To stop:              cd $DIR && docker compose down"
say "To update:            cd $DIR && docker compose pull && docker compose up -d"
