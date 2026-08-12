"""Pure logic tests for the multi-process notifier service and hook adapter."""

import json
import os
import tempfile
import sys
import threading
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor


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

    def test_session_tags_keep_conversation_open_order(self):
        self.store.apply(event("a", "processing", "a-1", source="Codex:kernel"))
        self.store.apply(event("b", "done", "b-1", source="Claude:gadget"))
        self.store.apply(event("a", "approval", "a-2", source="Codex:kernel"))
        sessions = self.store.sessions()
        self.assertEqual(["a", "b"], [item["session_id"] for item in sessions])
        self.assertEqual("approval", sessions[0]["state"])

    def test_session_tags_remove_ended_conversation(self):
        self.store.apply(event("a", "processing", "a-1"))
        self.store.apply(event("b", "done", "b-1"))
        self.store.apply(event("a", "offline", "a-offline"))
        self.assertEqual(["b"], [item["session_id"] for item in self.store.sessions()])

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

    def test_concurrent_session_transitions_remain_consistent(self):
        states = ("processing", "done", "wait", "approval")

        def update_session(index):
            for turn in range(40):
                self.store.apply(
                    event(
                        "session-{}".format(index),
                        states[turn % len(states)],
                        "event-{}-{}".format(index, turn),
                        source="Codex:project-{}".format(index),
                    )
                )

        with ThreadPoolExecutor(max_workers=12) as executor:
            list(executor.map(update_session, range(12)))

        sessions = self.store.sessions()
        self.assertEqual(12, len(sessions))
        self.assertEqual(12, len({item["session_id"] for item in sessions}))
        self.assertEqual(12, len({item["sequence"] for item in sessions}))
        self.assertTrue(all(item["state"] == "approval" for item in sessions))
        self.assertTrue(all(item["event_id"].endswith("-39") for item in sessions))
        self.assertEqual("approval", self.store.snapshot()["state"])
        self.assertEqual(5, len(self.store.history()))

    def test_duplicate_event_is_idempotent_under_concurrency(self):
        duplicate = event("same", "done", "same-event")
        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(lambda _: self.store.apply(duplicate), range(64)))
        self.assertEqual(1, self.store.active_count())
        self.assertEqual(1, len(self.store.history()))

    def test_ten_thousand_state_transitions_keep_latest_session_state(self):
        states = ("processing", "done", "wait", "approval")
        session_count = 20
        transition_count = 10000
        for turn in range(transition_count):
            session_index = turn % session_count
            round_index = turn // session_count
            self.store.apply(
                event(
                    "soak-{}".format(session_index),
                    states[round_index % len(states)],
                    "soak-{}-{}".format(session_index, round_index),
                )
            )

        sessions = self.store.sessions()
        self.assertEqual(session_count, len(sessions))
        self.assertTrue(all(item["state"] == "approval" for item in sessions))
        self.assertTrue(all(item["event_id"].endswith("-499") for item in sessions))
        self.assertEqual(transition_count, max(item["sequence"] for item in sessions))
        self.assertEqual(5, len(self.store.history()))


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
        self.assertEqual("a", status["sessions"][0]["session_id"])
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

    def test_shared_inbox_accepts_concurrent_hook_writers(self):
        controller = notifierd.EventController()
        with tempfile.TemporaryDirectory() as directory:
            watcher = notifierd.InboxWatcher(directory, controller, poll_interval=0.01)
            watcher.start()
            try:
                items = [
                    event(
                        "inbox-{}".format(index),
                        "processing" if index % 2 else "done",
                        "inbox-event-{}".format(index),
                    )
                    for index in range(24)
                ]
                with ThreadPoolExecutor(max_workers=12) as executor:
                    list(executor.map(lambda item: agent_hook.publish_to_inbox(directory, item), items))

                deadline = time.monotonic() + 3.0
                while controller.store.active_count() < len(items) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(items), controller.store.active_count())
                self.assertEqual(
                    [],
                    [name for name in os.listdir(directory) if name.endswith((".json", ".tmp", ".bad"))],
                )
            finally:
                watcher.stop()


class MockUIBridgeTests(unittest.TestCase):
    def setUp(self):
        self.controller = notifierd.EventController()
        self.bridge = notifierd.MockUIBridge(self.controller, ("127.0.0.1", 0))
        self.controller.set_bridge(self.bridge)
        self.bridge.start()
        host, port = self.bridge.server_address
        self.base_url = "http://{}:{}".format(host, port)

    def tearDown(self):
        self.bridge.stop()

    def get_json(self, path):
        with urllib.request.urlopen(self.base_url + path, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, payload):
        request = urllib.request.Request(
            self.base_url + "/api/action",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_page_and_state_api_are_available_without_serial(self):
        with urllib.request.urlopen(self.base_url + "/", timeout=2.0) as response:
            html = response.read().decode("utf-8")
        self.assertIn("ESP32-S3-BOX Simulator", html)
        self.assertIn('data-testid="session-grid"', html)
        self.assertIn('className = `session-card ${item.state}`', html)
        self.assertNotIn('data-testid="state-card"', html)
        self.assertIn("Load 6-session demo", html)

        status = self.get_json("/api/state")
        self.assertEqual("mock-ui", status["transport"])
        self.assertTrue(status["service_online"])
        self.assertEqual("offline", status["display"]["state"])

    def test_event_priority_and_ack_round_trip_through_http(self):
        self.post_json(event("a", "done", "done-1"))
        self.post_json(event("b", "approval", "approval-1"))

        status = self.get_json("/api/state")
        self.assertEqual(2, status["active_sessions"])
        self.assertEqual(["a", "b"], [item["session_id"] for item in status["sessions"]])
        self.assertEqual("approval", status["display"]["state"])

        response = self.post_json({"action": "ack", "event_id": "approval-1"})
        self.assertTrue(response["removed"])
        status = self.get_json("/api/state")
        self.assertEqual("done", status["display"]["state"])
        self.assertEqual("done-1", status["display"]["event_id"])

    def test_mute_is_reflected_in_mock_device_state(self):
        response = self.post_json({"action": "mute", "on": True})
        self.assertTrue(response["muted"])
        self.assertTrue(self.get_json("/api/state")["muted"])

    def test_http_bridge_accepts_many_simultaneous_sessions(self):
        items = [
            event(
                "http-{}".format(index),
                ("processing", "done", "wait", "approval")[index % 4],
                "http-event-{}".format(index),
                source=("Claude" if index % 2 else "Codex") + ":project-{}".format(index),
            )
            for index in range(32)
        ]
        with ThreadPoolExecutor(max_workers=16) as executor:
            responses = list(executor.map(self.post_json, items))

        self.assertTrue(all(response["ok"] for response in responses))
        status = self.get_json("/api/state")
        self.assertEqual(32, status["active_sessions"])
        self.assertEqual(
            {"http-{}".format(index) for index in range(32)},
            {item["session_id"] for item in status["sessions"]},
        )
        self.assertEqual("approval", status["display"]["state"])

    def test_tcp_hook_to_http_ui_and_ack_full_chain(self):
        server = notifierd.NotifierTCPServer(("127.0.0.1", 0), self.controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = "127.0.0.1:{}".format(server.server_address[1])
            items = [
                event(
                    "chain-{}".format(index),
                    ("processing", "done", "wait", "approval", "processing", "done")[index],
                    "chain-event-{}".format(index),
                    source=("Claude" if index % 2 else "Codex") + ":chain-{}".format(index),
                )
                for index in range(6)
            ]
            with ThreadPoolExecutor(max_workers=6) as executor:
                responses = list(executor.map(lambda item: agent_hook.send_request(address, item), items))
            self.assertTrue(all(response["ok"] for response in responses))

            browser_state = self.get_json("/api/state")
            self.assertEqual(6, browser_state["active_sessions"])
            self.assertEqual("approval", browser_state["display"]["state"])
            self.assertEqual("chain-event-3", browser_state["display"]["event_id"])

            ack = self.post_json({"action": "ack", "event_id": "chain-event-3"})
            self.assertTrue(ack["removed"])
            tcp_status = agent_hook.send_request(address, {"action": "status"})
            self.assertEqual(5, tcp_status["active_sessions"])
            self.assertEqual("wait", tcp_status["display"]["state"])
            self.assertEqual("chain-event-2", tcp_status["display"]["event_id"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)


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
