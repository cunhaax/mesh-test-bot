# Radios without WiFi (RAK4631, T-Echo, …)

The bot (and [MeshMonitor](meshmonitor.md)) talk to the radio over **TCP, port 4403**.
Radios with WiFi or Ethernet offer that themselves. Others, like the **RAK4631**
(nRF52840: USB and Bluetooth only), do not, so you put a small **bridge** in between:
a program that reads the radio over USB serial (or BLE) and offers the same stream on a
TCP port. Then you point `host`/`port` (`MTBOT_HOST`/`MTBOT_PORT`) at the bridge.

```
radio ⇄ USB cable ⇄ bridge (:4403) ⇄ bot   (or MeshMonitor, which the bot can then use)
```

A **USB** bridge only copies bytes (serial and TCP use the same framing). A **Bluetooth**
bridge also has to translate between BLE's raw protobuf and TCP's framed protocol, so it
needs a purpose-built program (below). Either way it is the same single-client rule as
always: **one program at a time** talks to the radio, so if MeshMonitor is in the path,
the bridge goes to MeshMonitor and the bot connects to its Virtual Node.

## The easiest way: MeshMonitor with its bridge, then the installer's "existing" mode

MeshMonitor has a ready-made bridge for each case, and documents the whole setup
(overlay file, device mapping, pairing):

- USB: [Serial Bridge](https://meshmonitor.org/configuration/serial-bridge.html)
- Bluetooth: [BLE Bridge](https://meshmonitor.org/configuration/ble-bridge.html)

So, if you have MeshMonitor, or are willing to install it:

1. Set up MeshMonitor with the bridge for your radio, following its pages above, and
   enable the **Virtual Node** on the radio's source (see the [MeshMonitor guide](meshmonitor.md)).
2. Run this bot's installer and answer **existing** when it asks how to connect. Give it
   the Virtual Node's address and port (4404).
3. The bot must be able to reach that address. The Virtual Node has no authentication, so
   MeshMonitor's own compose files do not publish 4404. Either publish it
   (`4404:4404` on the MeshMonitor service) and restrict who can reach it with a firewall
   rule, or, if both run on the same machine, put the bot on MeshMonitor's Docker network
   and use the service name.

If you are starting from nothing, the overlays below do the same thing in one
`docker compose` command. The rest of this page explains each piece.

**USB is the recommended way**: it is more reliable than Bluetooth, and it powers the
radio. If the bridge is on another machine, use that machine's address as the host.

## Linux or Raspberry Pi, with USB

[MeshMonitor's Serial Bridge](https://meshmonitor.org/configuration/serial-bridge.html)
(`ghcr.io/yeraze/meshtastic-serial-bridge`) is a container around `socat`. It also turns
off the serial line's hang-up behaviour (HUPCL), which otherwise can make the radio reboot
when the port is opened, and it reconnects by itself. Find the device (usually
`/dev/ttyACM0`; `ls /dev/ttyACM* /dev/ttyUSB*` before and after plugging the radio in), then:

- **With MeshMonitor** (recommended: it keeps the single connection and offers a Virtual
  Node to the bot, see [the MeshMonitor guide](meshmonitor.md)): add to the `.env`
  ```
  SERIAL_DEVICE=/dev/ttyACM0
  MESHTASTIC_NODE_IP=serial-bridge
  ```
  and start with the [`serial-bridge.yml`](../install/serial-bridge.yml) overlay:
  ```sh
  docker compose -f with-meshmonitor.yml -f serial-bridge.yml up -d
  ```
- **Bot only**: add the bridge to the bot's compose file and set `MTBOT_HOST=serial-bridge`
  (nothing else can use the radio then):
  ```yaml
    serial-bridge:
      image: ghcr.io/yeraze/meshtastic-serial-bridge:latest
      restart: unless-stopped
      devices:
        - /dev/ttyACM0:/dev/ttyACM0
      environment:
        - SERIAL_DEVICE=/dev/ttyACM0
        - BAUD_RATE=115200
        - TCP_PORT=4403
  ```
- **Without Docker**: `socat TCP-LISTEN:4403,fork,reuseaddr /dev/ttyACM0,raw,b115200,hupcl=0`

Notes:
- If the container cannot open the device, your user may need to be in the `dialout`
  group. Close the Meshtastic app/CLI on that USB port first: it cannot be shared.
- MeshMonitor's page says to prepare the radio first with
  `meshtastic --set serial.enabled true`, `--set serial.echo false`,
  `--set serial.mode SIMPLE` and `--set serial.baud BAUD_115200`. I could not confirm
  this is needed for a RAK4631's USB port (those settings are for the UART serial module),
  so try without it first, and apply it if the bridge connects but no data arrives.
- The bridge's TCP port has no authentication or encryption: do not publish 4403.

## macOS, with USB

Docker Desktop runs in a VM that cannot see USB (or Bluetooth) devices, so the bridge
has to run **on the Mac itself**, not in a container:

```sh
brew install socat
ls /dev/cu.usbmodem*          # the radio's port
socat TCP-LISTEN:4403,fork,reuseaddr /dev/cu.usbmodemXXXX,raw,b115200,hupcl=0
```

From inside a container, the Mac is `host.docker.internal`, so use
`MTBOT_HOST=host.docker.internal` (and `MTBOT_PORT=4403`), or
`MESHTASTIC_NODE_IP=host.docker.internal` with MeshMonitor.

## Bluetooth (BLE)

Less reliable than a cable: range is about 10 to 30 m, 2.4 GHz interference causes
disconnects, and a RAK4631 accepts **one BLE client at a time**, so the phone app cannot
use Bluetooth while the bridge holds it. If you can use USB, do.

[MeshMonitor's BLE bridge](https://meshmonitor.org/configuration/ble-bridge.html)
(`ghcr.io/yeraze/meshtastic-ble-bridge`) connects to the radio over BLE and serves TCP on
:4403. **The suggested setup is bridge + MeshMonitor + its Virtual Node**, because it
solves both single-client limits (the radio's BLE slot is held by the bridge, the bridge's
TCP slot by MeshMonitor, and the bot, the web UI and the app can all use the Virtual Node):

```
radio ⇄ BLE ⇄ ble-bridge (:4403) ⇄ MeshMonitor ⇄ Virtual Node (:4404) ⇄ bot
```

It needs a **Linux host** with BlueZ and D-Bus (on macOS, Docker cannot reach Bluetooth;
use USB). Steps:

1. **Pair the radio on the host**, once:
   ```sh
   bluetoothctl
   scan on                   # wait for Meshtastic_XXXX, note its address
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   exit
   ```
   (The address also shows with
   `docker run --rm --privileged -v /var/run/dbus:/var/run/dbus ghcr.io/yeraze/meshtastic-ble-bridge:latest --scan`.)
2. Follow the [MeshMonitor guide](meshmonitor.md) with the `with-meshmonitor.yml` and
   `meshmonitor.env.example` files, and add two lines to the `.env`:
   ```
   BLE_ADDRESS=AA:BB:CC:DD:EE:FF
   MESHTASTIC_NODE_IP=ble-bridge
   ```
3. Start everything with the [`ble-bridge.yml`](../install/ble-bridge.yml) overlay, then
   enable the Virtual Node as the guide says:
   ```sh
   docker compose -f with-meshmonitor.yml -f ble-bridge.yml up -d
   docker compose -f with-meshmonitor.yml -f ble-bridge.yml logs ble-bridge
   ```
   The bridge log should say it connected to the BLE device and that the TCP server is
   listening on :4403.

Notes: the bridge runs `privileged` and its TCP port has no authentication or
encryption, so do not publish 4403 (the overlay does not). The bridge can also run on a
different Linux machine (`network_mode: host`); then set `MESHTASTIC_NODE_IP` to that
machine's address. Without MeshMonitor you can point the bot straight at the bridge
(`MTBOT_HOST` = its address), but then nothing else can use the radio.

**Not tested with this bot or a real RAK4631**: it follows MeshMonitor's documentation,
and the bot sees the Virtual Node like any other radio, but nobody has run this
combination yet.

## Checking it works

With the bridge running, `python3 -c "import socket; socket.create_connection(('HOST', 4403), 3)"`
from the bot's machine should return without an error. Then start the bot as usual:
its startup check says whether the radio answered and the channel is right.

*Not tested end to end with a physical RAK4631; the USB and Bluetooth setups follow
MeshMonitor's documentation and standard `socat` usage.*
