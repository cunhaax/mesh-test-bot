# Copyright (C) 2026 André Cunha
# SPDX-License-Identifier: GPL-3.0-or-later
"""The real meshtastic library against a tiny fake radio (a local TCP server that speaks just
enough of the protocol to finish the handshake). It checks what the fakes in the other
tests only assume: how the library behaves when its socket drops."""
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mesh_test_bot as bot  # noqa: E402

try:
    from meshtastic.protobuf import mesh_pb2
except ImportError:  # pragma: no cover
    mesh_pb2 = None


class FakeRadioServer:
    """Accepts clients, and answers the configuration request the way a radio does."""

    def __init__(self):
        self.port, self.connections, self.clients = None, 0, []
        self._listener = None
        self._open = 0
        self._lock = threading.Lock()

    def open_clients(self):
        """How many clients are currently connected (a real client disconnecting -- not
        just drop_clients() -- decrements this, unlike `connections`, which only counts
        up)."""
        with self._lock:
            return self._open

    def start(self, port=0):
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", port))
        self._listener.listen(5)
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._accept, args=(self._listener,), daemon=True).start()

    def _accept(self, listener):
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            self.connections += 1
            self.clients.append(conn)
            with self._lock:
                self._open += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _send(conn, from_radio):
        body = from_radio.SerializeToString()
        conn.sendall(bytes([0x94, 0xC3]) + len(body).to_bytes(2, "big") + body)

    def _serve(self, conn):
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
                while True:
                    # Like a radio, resynchronise on the start of a frame: the library first sends 32
                    # filler bytes (0x94) to wake a sleeping device.
                    start = buf.find(b"\x94\xc3")
                    if start < 0:
                        buf = buf[-1:]
                        break
                    buf = buf[start:]
                    if len(buf) < 4:
                        break
                    size = int.from_bytes(buf[2:4], "big")
                    if len(buf) < 4 + size:
                        break
                    msg = mesh_pb2.ToRadio.FromString(buf[4:4 + size])
                    buf = buf[4 + size:]
                    if msg.HasField("want_config_id"):
                        self._send(conn, mesh_pb2.FromRadio(my_info=mesh_pb2.MyNodeInfo(my_node_num=1234)))
                        self._send(conn, mesh_pb2.FromRadio(config_complete_id=msg.want_config_id))
        except OSError:
            return
        finally:
            with self._lock:
                self._open -= 1

    def drop_clients(self):
        for conn in self.clients:
            for op in (lambda c: c.shutdown(socket.SHUT_RDWR), lambda c: c.close()):
                try:
                    op(conn)
                except OSError:
                    pass
        self.clients = []

    def stop(self):  # like a service that went away: no listener, no clients
        if self._listener:
            # On Linux, close() alone does not wake up the thread blocked in accept(), and the
            # socket would go on listening: shut it down first.
            try:
                self._listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._listener.close()
        self.drop_clients()


@unittest.skipIf(mesh_pb2 is None, "meshtastic not installed")
class RealLibraryReconnectTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeRadioServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        tmp = tempfile.mkdtemp()
        self.cfg = {"host": "127.0.0.1", "port": self.server.port, "channel": 0, "channel_name": "", "place": "X",
                    "keyword": "MTBOT", "mode": "", "mode_aliases": {}, "min_gap_seconds": 0.1,
                    "session_tolerance_seconds": 60.0, "tz": ZoneInfo("Europe/Lisbon"), "timezone": "Europe/Lisbon",
                    "rx_file": os.path.join(tmp, "rx.log")}
        self.cfg["msg_re"] = bot.message_regex("MTBOT")
        self.ifaces = []

        def factory(host, port):
            iface = bot.tcp_factory(host, port)
            self.ifaces.append(iface)
            return iface
        self.radio = bot.Radio(self.cfg, factory, tick=0.1)
        self.radio.start(bot.Heard(self.cfg, self.radio))
        self.addCleanup(self.radio.close)
        self.assertTrue(self.radio.wait_ready(20), self.radio.fatal)

    def wait_for(self, condition, seconds=30):
        end = time.time() + seconds
        while time.time() < end and not condition():
            time.sleep(0.05)
        return condition()

    def test_it_connects_and_reads_the_node(self):
        self.assertEqual(self.radio.my_num, 1234)
        self.assertEqual(len(self.ifaces), 1)

    def test_a_dropped_socket_is_reconnected_by_the_library_and_counted(self):
        """The radio closes the connection but is still there: the library reconnects by itself
        (isConnected never drops), which used to go unnoticed."""
        self.server.drop_clients()
        self.assertTrue(self.wait_for(lambda: self.radio.outages), "the outage was never counted")
        self.assertEqual(len(self.radio.outages), 1)
        self.assertGreaterEqual(self.radio.outages[0], 0.9)  # the library waits a second before reconnecting
        self.assertEqual(len(self.ifaces), 1)  # the same interface: no rebuild was needed
        self.assertEqual(self.server.connections, 2)
        self.assertEqual(self.radio.my_num, 1234)
        self.assertEqual(self.radio.outage_count(), 1)  # ...and only once

    def test_a_radio_that_disappears_for_a_while_is_rebuilt_and_counted(self):
        """The radio is gone for a few seconds: the library's own reconnection fails, its reader
        thread dies, and the supervisor has to build a new connection."""
        port = self.server.port
        self.server.stop()
        with self.assertRaises(OSError):  # it really is gone, on every platform
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        time.sleep(2.5)
        self.server.start(port)  # it is back on the same port
        self.assertTrue(self.wait_for(lambda: self.radio.outages), "the outage was never counted")
        self.assertEqual(len(self.radio.outages), 1)
        self.assertGreaterEqual(self.radio.outages[0], 2.0)
        self.assertGreaterEqual(len(self.ifaces), 2)  # a new connection had to be built
        self.assertTrue(self.radio._healthy())


@unittest.skipIf(mesh_pb2 is None, "meshtastic not installed")
class RealLibraryStartupCheckTest(unittest.TestCase):
    """check_radio against the real meshtastic TCP client and a fake radio server, to check
    what the fakes elsewhere only assume: the probe connection is actually released."""

    def setUp(self):
        self.server = FakeRadioServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        tmp = tempfile.mkdtemp()
        self.cfg = {"host": "127.0.0.1", "port": self.server.port, "channel": 0, "channel_name": "", "place": "X",
                    "keyword": "MTBOT", "mode": "", "mode_aliases": {}, "min_gap_seconds": 0.1,
                    "session_tolerance_seconds": 60.0, "tz": ZoneInfo("Europe/Lisbon"), "timezone": "Europe/Lisbon",
                    "rx_file": os.path.join(tmp, "rx.log"), "startup_check_seconds": 20.0,
                    "startup_check_join_seconds": 10.0, "startup_check_close_delay_seconds": 0.05}
        self.cfg["msg_re"] = bot.message_regex("MTBOT")

    def wait_for(self, condition, seconds=20):
        end = time.time() + seconds
        while time.time() < end and not condition():
            time.sleep(0.05)
        return condition()

    def test_the_check_really_disconnects_from_the_radio(self):
        with self.assertLogs("bot", "INFO") as cm:
            ok = bot.check_radio(self.cfg, bot.tcp_factory)
        self.assertTrue(ok)
        self.assertTrue(any("Startup check OK" in m for m in cm.output))
        self.assertTrue(self.wait_for(lambda: self.server.open_clients() == 0))
        self.assertEqual(self.server.connections, 1)

    def test_a_stopped_server_fails_the_check(self):
        port = self.server.port
        self.server.stop()
        with self.assertLogs("bot", "ERROR") as cm:
            ok = bot.check_radio(self.cfg, bot.tcp_factory, timeout=3)
        self.assertIs(ok, False)
        self.assertIn("127.0.0.1:%d" % port, cm.output[-1])


if __name__ == "__main__":
    unittest.main()
