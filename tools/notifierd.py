#!/usr/bin/env python3
"""Agent Notifier daemon.

The daemon is the only process that owns the ESP32-S3-BOX serial port. Codex,
Claude Code, and manual clients send short JSON events to its local TCP socket.
It keeps one state per agent session and displays the highest-priority pending
state, so a new worker cannot hide an approval or reply request from another
terminal.
"""

import argparse
import json
import logging
import socketserver
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import box_cli
from box_cli import BoxSerial, find_box_port


LOG = logging.getLogger("notifierd")
DEFAULT_LISTEN = "127.0.0.1:45831"
MAX_REQUEST_BYTES = 64 * 1024
VALID_STATES = ("done", "wait", "processing", "approval", "offline")
VALID_LEVELS = ("info", "attention", "urgent", "silent")
STATE_PRIORITY = {
    "offline": 0,
    "processing": 10,
    "done": 20,
    "wait": 30,
    "approval": 40,
}


@dataclass(frozen=True)
class DisplayEvent:
    session_id: str
    source: str
    state: str
    message: str
    event_id: str
    level: str
    sequence: int


class SessionStore:
    """Thread-safe multi-session state and five-item display history."""

    def __init__(self, history_limit=5):
        self._lock = threading.RLock()
        self._sessions = {}
        self._history = deque(maxlen=history_limit)
        self._sequence = 0

    def apply(self, payload):
        state = str(payload.get("state", "")).lower()
        if state not in VALID_STATES:
            raise ValueError("state must be one of: " + ", ".join(VALID_STATES))

        session_id = _bounded_text(payload.get("session_id"), 128)
        if not session_id:
            raise ValueError("session_id is required")

        with self._lock:
            if state == "offline":
                self._sessions.pop(session_id, None)
                return self._snapshot_unlocked()

            source = _bounded_text(payload.get("source"), 40) or "Agent"
            message = _bounded_text(payload.get("message"), 80) or state
            # Firmware stores event ids in a 40-byte C buffer, including NUL.
            event_id = _bounded_text(payload.get("event_id"), 39)
            if not event_id:
                event_id = "{}-{}".format(session_id[-16:], int(time.time() * 1000))
            level = str(payload.get("level", "info")).lower()
            if level not in VALID_LEVELS:
                raise ValueError("level must be one of: " + ", ".join(VALID_LEVELS))

            previous = self._sessions.get(session_id)
            if previous and previous.event_id == event_id:
                return self._snapshot_unlocked()

            self._sequence += 1
            event = DisplayEvent(
                session_id=session_id,
                source=source,
                state=state,
                message=message,
                event_id=event_id,
                level=level,
                sequence=self._sequence,
            )
            self._sessions[session_id] = event
            self._history.appendleft(_history_text(event))
            return self._snapshot_unlocked()

    def acknowledge(self, event_id):
        event_id = _bounded_text(event_id, 39)
        if not event_id:
            return False, self.snapshot()
        with self._lock:
            for session_id, event in list(self._sessions.items()):
                if event.event_id == event_id:
                    del self._sessions[session_id]
                    return True, self._snapshot_unlocked()
            return False, self._snapshot_unlocked()

    def snapshot(self):
        with self._lock:
            return self._snapshot_unlocked()

    def history(self):
        with self._lock:
            return list(self._history)

    def active_count(self):
        with self._lock:
            return len(self._sessions)

    def _snapshot_unlocked(self):
        if not self._sessions:
            return {
                "session_id": "",
                "source": "",
                "state": "offline",
                "message": "",
                "event_id": "",
                "level": "silent",
                "sequence": 0,
            }
        event = max(
            self._sessions.values(),
            key=lambda item: (STATE_PRIORITY[item.state], item.sequence),
        )
        return asdict(event)


class EventController:
    def __init__(self, store=None):
        self.store = store or SessionStore()
        self.bridge = None

    def set_bridge(self, bridge):
        self.bridge = bridge
        self._publish()

    def handle(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        action = payload.get("action", "event")
        if action == "event":
            snapshot = self.store.apply(payload)
            self._publish(snapshot)
            return {
                "ok": True,
                "display": snapshot,
                "active_sessions": self.store.active_count(),
            }
        if action == "ack":
            removed, snapshot = self.store.acknowledge(payload.get("event_id"))
            self._publish(snapshot)
            return {
                "ok": True,
                "removed": removed,
                "display": snapshot,
                "active_sessions": self.store.active_count(),
            }
        if action == "status":
            return {
                "ok": True,
                "display": self.store.snapshot(),
                "history": self.store.history(),
                "active_sessions": self.store.active_count(),
                "box_connected": bool(self.bridge and self.bridge.connected),
            }
        raise ValueError("unknown action: {}".format(action))

    def on_box_event(self, event):
        if event.get("event") == "btn" and event.get("id") == "ack":
            event_id = event.get("event_id")
            if event_id:
                removed, snapshot = self.store.acknowledge(event_id)
                if removed:
                    LOG.info("acknowledged event %s", event_id)
                    self._publish(snapshot)

    def _publish(self, snapshot=None):
        if self.bridge:
            self.bridge.update(snapshot or self.store.snapshot(), self.store.history())


class SerialBridge:
    """Reconnectable serial owner with a desired-state queue."""

    def __init__(self, on_event, port=None, baud=115200, retry_interval=2.0):
        self._on_event = on_event
        self._port = port
        self._baud = baud
        self._retry_interval = retry_interval
        self._lock = threading.Lock()
        self._desired_snapshot = None
        self._desired_history = []
        self._desired_version = 0
        self._sent_version = -1
        self._box = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    @property
    def connected(self):
        return self._box is not None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="box-serial", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._disconnect()

    def update(self, snapshot, history):
        with self._lock:
            self._desired_snapshot = dict(snapshot)
            self._desired_history = list(history[:5])
            self._desired_version += 1
        self._wake.set()

    def _run(self):
        next_ping = 0.0
        while not self._stop.is_set():
            try:
                if self._box is None:
                    self._connect()
                    next_ping = 0.0

                now = time.monotonic()
                if now >= next_ping:
                    self._box.send_ping()
                    next_ping = now + 5.0
                self._flush_desired()
            except Exception as exc:
                LOG.warning("BOX disconnected: %s", exc)
                self._disconnect()
                self._stop.wait(self._retry_interval)
                continue

            self._wake.wait(0.5)
            self._wake.clear()

    def _connect(self):
        port = self._port or find_box_port()
        if not port:
            raise RuntimeError("BOX serial port not found")
        box = BoxSerial(port, self._baud)
        box.start_rx(on_event=self._on_event)
        box.send_hello()
        self._box = box
        self._sent_version = -1
        LOG.info("BOX connected on %s", port)

    def _disconnect(self):
        box, self._box = self._box, None
        if box:
            box.close()

    def _flush_desired(self):
        with self._lock:
            if self._desired_snapshot is None or self._sent_version == self._desired_version:
                return
            snapshot = dict(self._desired_snapshot)
            history = list(self._desired_history)
            version = self._desired_version

        self._box.send_json({"t": "history", "items": history})
        self._box.send_state(
            snapshot["state"],
            src=snapshot.get("source") or None,
            msg=snapshot.get("message") or None,
            event_id=snapshot.get("event_id") or None,
            level=snapshot.get("level") or "info",
        )
        self._sent_version = version


class InboxWatcher:
    """Watch a shared directory for atomically published hook events."""

    def __init__(self, path, controller, poll_interval=0.2):
        self.path = Path(path).expanduser()
        self.controller = controller
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.path.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="event-inbox", daemon=True)
        self._thread.start()
        LOG.info("watching event inbox %s", self.path)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            for path in sorted(self.path.glob("event-*.json")):
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                    self.controller.handle(payload)
                    path.unlink()
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    LOG.warning("invalid inbox event %s: %s", path.name, exc)
                    try:
                        path.replace(path.with_suffix(".bad"))
                    except OSError:
                        pass
            self._stop.wait(self.poll_interval)


class NotifierTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, controller):
        self.controller = controller
        super().__init__(address, NotifierRequestHandler)


class NotifierRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(line) > MAX_REQUEST_BYTES:
            self._reply({"ok": False, "error": "request too large"})
            return
        try:
            payload = json.loads(line.decode("utf-8"))
            response = self.server.controller.handle(payload)
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            LOG.exception("request failed")
            response = {"ok": False, "error": "internal error"}
        self._reply(response)

    def _reply(self, response):
        data = json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n"
        self.wfile.write(data.encode("utf-8"))


def parse_address(value):
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise ValueError("address must use HOST:PORT")
    port = int(port_text)
    if port < 1 or port > 65535:
        raise ValueError("port must be between 1 and 65535")
    return host, port


def _bounded_text(value, limit):
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _history_text(event):
    text = "{} - {}".format(event.source, event.message)
    return text[:76]


def main():
    parser = argparse.ArgumentParser(description="Agent Notifier multi-process service")
    parser.add_argument("--listen", default=DEFAULT_LISTEN, help="local event socket (HOST:PORT)")
    parser.add_argument(
        "--inbox",
        help="optional shared event directory (useful for WSL hooks and a Windows daemon)",
    )
    parser.add_argument("--port", help="BOX serial port; auto-detected when omitted")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--retry", type=float, default=2.0, help="serial reconnect interval")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if box_cli.serial is None:
        parser.error("pyserial is required; run: python -m pip install pyserial")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        address = parse_address(args.listen)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))

    controller = EventController()
    bridge = SerialBridge(
        controller.on_box_event,
        port=args.port,
        baud=args.baud,
        retry_interval=max(args.retry, 0.2),
    )
    controller.set_bridge(bridge)
    server = NotifierTCPServer(address, controller)
    inbox = InboxWatcher(args.inbox, controller) if args.inbox else None
    bridge.start()
    if inbox:
        inbox.start()

    LOG.info("listening on %s:%d", *server.server_address)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOG.info("stopping")
    finally:
        server.shutdown()
        server.server_close()
        if inbox:
            inbox.stop()
        bridge.stop()


if __name__ == "__main__":
    main()
