"""Pure logic tests for the multi-process notifier service and hook adapter."""

import os
import tempfile
import sys
import threading
import time
import unittest


sys.path.insert(0, os.path.dirname(__file__))

import agent_hook  # noqa: E402
import notifierd  # noqa: E402


class FakeBridge:
    connected = True

    def __init__(self):
        self.updates = []

    def update(self, snapshot, history):
        self.updates.append((dict(snapshot), list(history)))


def event(session, state, event_id, source="Codex:test#0000", message=None):
    return {
        "action": "event",
        "session_id": session,
        "source": source,
        "state": state,
        "message": message or state,
        "event_id": event_id,
        "level": "info",
    }


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = notifierd.SessionStore()

    def test_attention_states_win_across_sessions(self):
        self.store.apply(event("a", "wait", "wait-1"))
        snapshot = self.store.apply(event("b", "processing", "work-1"))
        self.assertEqual("wait", snapshot["state"])
        self.assertEqual("wait-1", snapshot["event_id"])

        snapshot = self.store.apply(event("c", "approval", "approval-1"))
        self.assertEqual("approval", snapshot["state"])

    def test_same_session_transition_replaces_old_state(self):
        self.store.apply(event("a", "wait", "wait-1"))
        snapshot = self.store.apply(event("a", "processing", "work-2"))
        self.assertEqual("processing", snapshot["state"])
        self.assertEqual(1, self.store.active_count())

    def test_ack_reveals_next_priority_session(self):
        self.store.apply(event("a", "done", "done-1"))
        self.store.apply(event("b", "approval", "approval-1"))
        removed, snapshot = self.store.acknowledge("approval-1")
        self.assertTrue(removed)
        self.assertEqual("done", snapshot["state"])
        self.assertEqual("done-1", snapshot["event_id"])

    def test_offline_removes_only_its_session(self):
        self.store.apply(event("a", "done", "done-1"))
        self.store.apply(event("b", "wait", "wait-1"))
        snapshot = self.store.apply(event("b", "offline", "offline-1"))
        self.assertEqual("done", snapshot["state"])
        self.assertEqual(1, self.store.active_count())

    def test_duplicate_event_id_does_not_duplicate_history(self):
        item = event("a", "done", "same")
        self.store.apply(item)
        self.store.apply(item)
        self.assertEqual(1, len(self.store.history()))

    def test_history_keeps_five_newest_items(self):
        for index in range(7):
            self.store.apply(event(str(index), "done", "e{}".format(index)))
        self.assertEqual(5, len(self.store.history()))
        self.assertIn("done", self.store.history()[0])


class ControllerTests(unittest.TestCase):
    def test_box_ack_updates_display(self):
        bridge = FakeBridge()
        controller = notifierd.EventController()
        controller.set_bridge(bridge)
        controller.handle(event("a", "done", "done-1"))
        controller.on_box_event({"event": "btn", "id": "ack", "event_id": "done-1"})
        self.assertEqual("offline", bridge.updates[-1][0]["state"])

    def test_status_reports_active_sessions(self):
        bridge = FakeBridge()
        controller = notifierd.EventController()
        controller.set_bridge(bridge)
        controller.handle(event("a", "done", "done-1"))
        status = controller.handle({"action": "status"})
        self.assertTrue(status["ok"])
        self.assertEqual(1, status["active_sessions"])
        self.assertTrue(status["box_connected"])

    def test_tcp_client_round_trip(self):
        controller = notifierd.EventController()
        server = notifierd.NotifierTCPServer(("127.0.0.1", 0), controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = "127.0.0.1:{}".format(server.server_address[1])
            result = agent_hook.send_request(address, event("a", "done", "done-1"))
            self.assertTrue(result["ok"])
            self.assertEqual("done", result["display"]["state"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    def test_shared_inbox_round_trip(self):
        controller = notifierd.EventController()
        with tempfile.TemporaryDirectory() as directory:
            watcher = notifierd.InboxWatcher(directory, controller, poll_interval=0.01)
            watcher.start()
            try:
                agent_hook.publish_to_inbox(directory, event("a", "wait", "wait-1"))
                deadline = time.monotonic() + 1.0
                while controller.store.active_count() == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual("wait", controller.store.snapshot()["state"])
                leftovers = [name for name in os.listdir(directory) if name.endswith(".json")]
                self.assertEqual([], leftovers)
            finally:
                watcher.stop()


class HookMappingTests(unittest.TestCase):
    def test_codex_stop_maps_to_done(self):
        payload = {
            "hook_event_name": "Stop",
            "session_id": "codex-session",
            "turn_id": "turn-1",
            "cwd": "/work/kernel",
        }
        mapped = agent_hook.normalize_hook(payload, "codex")
        self.assertEqual("done", mapped["state"])
        self.assertEqual("answer ready", mapped["message"])
        self.assertTrue(mapped["source"].startswith("Codex:kernel#"))

    def test_codex_permission_maps_to_approval(self):
        payload = {
            "hook_event_name": "PermissionRequest",
            "session_id": "codex-session",
            "turn_id": "turn-2",
            "cwd": "/work/kernel",
        }
        mapped = agent_hook.normalize_hook(payload, "codex")
        self.assertEqual("approval", mapped["state"])
        self.assertEqual("attention", mapped["level"])

    def test_claude_idle_notification_maps_to_wait(self):
        payload = {
            "hook_event_name": "Notification",
            "notification_type": "idle_prompt",
            "session_id": "claude-session",
            "permission_mode": "default",
            "cwd": r"C:\src\gadget",
        }
        mapped = agent_hook.normalize_hook(payload, "auto")
        self.assertEqual("wait", mapped["state"])
        self.assertTrue(mapped["source"].startswith("Claude:gadget#"))

    def test_session_end_maps_to_offline(self):
        payload = {
            "hook_event_name": "SessionEnd",
            "session_id": "codex-session",
            "cwd": "/work/kernel",
        }
        mapped = agent_hook.normalize_hook(payload, "codex")
        self.assertEqual("offline", mapped["state"])

    def test_stop_question_maps_to_wait(self):
        payload = {
            "hook_event_name": "Stop",
            "session_id": "codex-session",
            "turn_id": "turn-3",
            "cwd": "/work/kernel",
            "last_assistant_message": "请确认要使用哪个方案？",
        }
        mapped = agent_hook.normalize_hook(payload, "codex")
        self.assertEqual("wait", mapped["state"])

    def test_event_id_is_stable_for_hook_retry(self):
        payload = {
            "hook_event_name": "Stop",
            "session_id": "codex-session",
            "turn_id": "turn-1",
            "cwd": "/work/kernel",
        }
        first = agent_hook.normalize_hook(payload, "codex")
        second = agent_hook.normalize_hook(payload, "codex")
        self.assertEqual(first["event_id"], second["event_id"])

    def test_legacy_codex_notify_turns_get_distinct_ids(self):
        base = {
            "type": "agent-turn-complete",
            "thread-id": "codex-session",
            "cwd": "/work/kernel",
        }
        first = agent_hook.normalize_hook(dict(base, **{"turn-id": "turn-1"}), "codex")
        second = agent_hook.normalize_hook(dict(base, **{"turn-id": "turn-2"}), "codex")
        self.assertNotEqual(first["event_id"], second["event_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
