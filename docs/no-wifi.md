# Radios without WiFi (RAK4631, T-Echo, …)

The bot (and [MeshMonitor](meshmonitor.md)) talk to the radio over **TCP, port 4403**.
Radios with WiFi or Ethernet offer that themselves. Others, like the **RAK4631**
(nRF52840: USB and Bluetooth only), do not, so you put a small **bridge** in between:
a program that reads the radio over USB serial (or BLE) and offers the same stream on a
TCP port. Then you point `host`/`port` (`MTBOT_HOST`/`MTBOT_PORT`) at the bridge.

```
radio ⇄ USB cable ⇄ bridge (:4403) ⇄ bot   (or MeshMonitor, which the bot can then use)
```

The bridge only copies bytes; the Meshtastic stream is the same over serial and TCP.
It is the same single-client rule as always: **one program at a time** talks to the
radio, so if MeshMonitor is in the path, the bridge goes to MeshMonitor and the bot
connects to its Virtual Node.

**USB is the recommended way**: it is more reliable than Bluetooth, and it powers the
radio. If the bridge is on another machine, use that machine's address as the host.

## Linux or Raspberry Pi, with USB

Find the device (usually `/dev/ttyACM0`; `ls /dev/ttyACM* /dev/ttyUSB*` before and after
plugging the radio in), then run the bridge:

```sh
socat TCP-LISTEN:4403,fork,reuseaddr /dev/ttyACM0,raw,b115200
```

Or as a container, next to the bot (add it to your compose file, and set
`MTBOT_HOST=serial-bridge`):

```yaml
  serial-bridge:
    image: alpine/socat
    restart: unless-stopped
    devices:
      - /dev/ttyACM0:/dev/ttyACM0
    command: ["TCP-LISTEN:4403,fork,reuseaddr", "/dev/ttyACM0,raw,b115200"]
```

Your user may need to be in the `dialout` group to open the device outside Docker.
Close the Meshtastic app/CLI on that USB port first: it cannot be shared.

## macOS, with USB

Docker Desktop runs in a VM that cannot see USB (or Bluetooth) devices, so the bridge
has to run **on the Mac itself**, not in a container:

```sh
brew install socat
ls /dev/cu.usbmodem*          # the radio's port
socat TCP-LISTEN:4403,fork,reuseaddr /dev/cu.usbmodemXXXX,raw,b115200
```

From inside a container, the Mac is `host.docker.internal`, so use
`MTBOT_HOST=host.docker.internal` (and `MTBOT_PORT=4403`).

## Bluetooth (BLE)

Possible, but a worse choice:

- A RAK4631 accepts **one BLE client at a time**, so the phone app is locked out while
  the bridge is connected.
- Pairing and reconnecting are flakier than a cable.
- It needs a BLE-to-TCP bridge (the `meshtastic` Python CLI can do BLE with `--ble`;
  look for a bridge that serves the TCP protocol). On Linux a container needs the host's
  BlueZ (`network_mode: host`, `/var/run/dbus` mounted); on macOS it has to run natively,
  for the same reason as above.

I have not tested a specific BLE bridge with this bot.

## Checking it works

With the bridge running, `python3 -c "import socket; socket.create_connection(('HOST', 4403), 3)"`
from the bot's machine should return without an error. Then start the bot as usual:
its startup check says whether the radio answered and the channel is right.

*Not tested end to end with a physical RAK4631; the commands are the standard
`socat` forms.*
