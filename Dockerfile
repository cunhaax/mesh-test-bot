FROM python:3.12-slim

# meshtastic is pinned in requirements.txt: bump it only after re-testing.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only affects log timestamps; session times follow `timezone` in the config.
ENV TZ=Europe/Lisbon

COPY mesh_test_bot.py .

# Config, report and rx log live in the mounted /data volume. Extra flags
# replace only CMD, so e.g. `docker compose run bot --now` keeps the config.
ENTRYPOINT ["python", "-u", "mesh_test_bot.py", "--config", "/data/bot.ini"]
CMD ["--schedule"]
