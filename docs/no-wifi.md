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

**Bluetooth on Linux or a Raspberry Pi?** Skip to [Bluetooth (BLE)](#bluetooth-ble): one
compose file holds the bridge, MeshMonitor and the bot. **On Windows**, see
[Bluetooth on Windows](#bluetooth-on-windows).

| Connection | Linux / Raspberry Pi | Windows | macOS |
|---|---|---|---|
| Bluetooth | [`with-ble.yml`](#bluetooth-ble) | [Native bridge app](#bluetooth-on-windows) | Not possible |
| USB | [Serial bridge](#linux-or-raspberry-pi-with-usb) | WSL2 and usbipd-win ([MeshMonitor's page](https://meshmonitor.org/configuration/serial-bridge.html)) | [`socat` on the Mac](#macos-with-usb) |

## Two ways to set it up

MeshMonitor has a ready-made bridge for each case ([Serial Bridge](https://meshmonitor.org/configuration/serial-bridge.html)
for USB, [BLE Bridge](https://meshmonitor.org/configuration/ble-bridge.html) for
Bluetooth). Both ways below use them, with MeshMonitor and its Virtual Node in between.

### A. Starting from nothing: the installer plus a bridge overlay

1. Run the installer ([README](../README.md#with-the-installer-linux-macos-raspberry-pi)),
   answer **set up a new MeshMonitor here**, and when it asks for the radio's IP type
   `serial-bridge` (USB) or `ble-bridge` (Bluetooth). That is the bridge's name inside
   Docker, which the next steps create.
2. In the `mesh-test-bot/` folder it made, download the overlay and tell Compose to use
   it, so the commands the installer printed keep working as they are (shown for USB;
   use `ble-bridge.yml` for Bluetooth):
   ```sh
   curl -fsSLO https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/serial-bridge.yml
   echo "COMPOSE_FILE=docker-compose.yml:serial-bridge.yml" >> .env
   echo "SERIAL_DEVICE='/dev/ttyACM0'" >> .env      # Bluetooth: BLE_ADDRESS='AA:BB:CC:DD:EE:FF'
   ```
3. Continue with the commands the installer printed (`docker compose pull`,
   `docker compose up -d`) and enable the Virtual Node as it says. Bluetooth needs the
   radio paired on the host first (see below).

### B. You already run MeshMonitor

1. Set up MeshMonitor with the bridge, following its own page above, and enable the
   **Virtual Node** on the radio's source (see the [MeshMonitor guide](meshmonitor.md)).
2. Run the installer and answer **existing**; give it the Virtual Node's address and port (4404).
3. The bot must be able to reach that address, and the Virtual Node has no
   authentication, so MeshMonitor's own compose files do not publish 4404. Publish it
   (`4404:4404` on the MeshMonitor service), use the MeshMonitor machine's LAN address as
   the host, and restrict who can reach port 4404 with a firewall rule.

**USB is the recommended way**: it is more reliable than Bluetooth, and it powers the
radio. If the bridge is on another machine, use that machine's address as the host.

## Linux or Raspberry Pi, with USB

[MeshMonitor's Serial Bridge](https://meshmonitor.org/configuration/serial-bridge.html)
(`ghcr.io/yeraze/meshtastic-serial-bridge`) is a container around `socat`. According to
its page it also turns off the serial line's hang-up behaviour (HUPCL), which otherwise
can make the radio reboot when the port is opened, and it reconnects by itself (I have
not checked either, nor what happens after unplugging and replugging the cable). Find the
device with `ls /dev/serial/by-id/` (the stable name; `/dev/ttyACM0` can change) before
and after plugging the radio in, then:

- **With MeshMonitor** (recommended: it keeps the single connection and offers a Virtual
  Node to the bot): way A above, with `SERIAL_DEVICE` set to that device. On a desktop
  Linux, ModemManager may grab `ttyACM*` devices; stop it if the bridge cannot open the port.
- **Bot only**: add the bridge to the bot's compose file and set `MTBOT_HOST=serial-bridge`
  (nothing else can use the radio then):
  ```yaml
    serial-bridge:
      image: ghcr.io/yeraze/meshtastic-serial-bridge:latest
      restart: unless-stopped
      logging:
        driver: json-file
        options:
          max-size: "10m"
          max-file: "3"
      devices:
        - /dev/ttyACM0:/dev/ttyACM0
      environment:
        - SERIAL_DEVICE=/dev/ttyACM0
        - BAUD_RATE=115200
        - TCP_PORT=4403
  ```
- **Without Docker**, bot on the same machine:
  `socat TCP-LISTEN:4403,bind=127.0.0.1,fork,reuseaddr /dev/ttyACM0,raw,b115200,hupcl=0`
  (your user needs to be in the `dialout` group to open the device).

Notes:
- Close the Meshtastic app/CLI on that USB port first: it cannot be shared.
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
socat TCP-LISTEN:4403,bind=127.0.0.1,fork,reuseaddr /dev/cu.usbmodemXXXX,raw,b115200,hupcl=0
```

From inside a container, the Mac is `host.docker.internal`, so use
`MTBOT_HOST=host.docker.internal` (and `MTBOT_PORT=4403`), or
`MESHTASTIC_NODE_IP=host.docker.internal` with MeshMonitor. `bind=127.0.0.1` keeps the
unauthenticated radio off your network; I expect Docker Desktop's
`host.docker.internal` to reach it, but have not tried. If a container cannot connect,
bind to the Mac's LAN address instead and block port 4403 from other machines with the
firewall.

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

It needs a **Linux host** with BlueZ and D-Bus. Docker Desktop cannot reach Bluetooth, so
on macOS use USB, and on Windows use the [native bridge](#bluetooth-on-windows). Steps:

1. **Pair the radio on the host**, once:
   ```sh
   bluetoothctl
   agent on
   default-agent
   scan on                   # wait for Meshtastic_XXXX, note its address
   pair AA:BB:CC:DD:EE:FF    # asks for the PIN
   trust AA:BB:CC:DD:EE:FF
   exit
   ```
   The PIN is shown on the radio's screen; a radio without one, like the RAK4631, uses
   the default `123456` unless you changed it.
   (The address also shows with
   `docker run --rm --privileged -v /var/run/dbus:/var/run/dbus ghcr.io/yeraze/meshtastic-ble-bridge:latest --scan`.)
2. Download the all-in-one compose file and its settings, and fill in the settings
   (`BLE_ADDRESS` is the address from step 1):
   ```sh
   mkdir mesh-test-bot && cd mesh-test-bot && mkdir data
   curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/with-ble.yml -o docker-compose.yml
   curl -fsSL https://raw.githubusercontent.com/cunhaax/mesh-test-bot/master/install/ble.env.example -o .env
   nano .env
   docker compose up -d
   ```
3. Check the bridge:
   ```sh
   docker compose logs ble-bridge
   ```
   It should say it connected to the BLE device and that the TCP server is listening on
   :4403 (the wording is MeshMonitor's, not checked here).
4. Open MeshMonitor at the `ALLOWED_ORIGINS` address, log in with `admin` / `changeme`,
   change the password and [enable the Virtual Node](meshmonitor.md#enabling-the-virtual-node).
   This step can't go in the compose file. The bot retries by itself and connects within a
   few minutes.

Already installed with the installer, or running MeshMonitor? Way A above with
`ble-bridge.yml` adds the bridge to that install, and way B uses MeshMonitor's own overlay
from its BLE page.

Notes: the bridge runs `privileged` and its TCP port has no authentication or
encryption, so do not publish 4403 (the overlay does not). The bridge can also run on a
different Linux machine (`network_mode: host`); then set `MESHTASTIC_NODE_IP` to that
machine's address. Without MeshMonitor you can point the bot straight at the bridge
(`MTBOT_HOST` = its address), but then nothing else can use the radio.

**Not tested with this bot or a real RAK4631**: it follows MeshMonitor's documentation,
and the bot sees the Virtual Node like any other radio, but nobody has run this
combination yet.

## Bluetooth on Windows

MeshMonitor's [BLE bridge](https://meshmonitor.org/configuration/ble-bridge.html) also comes
as a native Windows tray app, `meshtastic-ble-bridge.exe`, serving the same TCP port (4403
by default) on Windows itself. Pair the radio in Windows' Bluetooth settings and start the
app with the radio's address, following that page. The bot then runs in Docker Desktop, where
the Windows machine is `host.docker.internal`, so set `MTBOT_HOST=host.docker.internal` in
`.env`. Nothing else can use the radio while the bot does.

The port has no authentication or encryption, so don't allow it through the Windows
firewall for other machines.

**Not tested with this bot**: it combines MeshMonitor's documentation with Docker Desktop's
standard address for the host.

## Checking it works

With the bridge running, `python3 -c "import socket; socket.create_connection(('HOST', 4403), 3)"`
from the bot's machine should return without an error. Then start the bot as usual:
its startup check says whether the radio answered and the channel is right.

*Not tested end to end with a physical RAK4631; the USB and Bluetooth setups follow
MeshMonitor's documentation and standard `socat` usage.*
