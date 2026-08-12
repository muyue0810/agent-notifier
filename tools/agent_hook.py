#!/usr/bin/env python3
"""Codex and Claude Code hook adapter for Agent Notifier.

Hook JSON is read from stdin, normalized into the daemon event schema, and sent
over a short-lived local TCP connection. The script always prints a valid empty
JSON object on stdout so Stop and other structured hook events remain advisory.
Connection errors are intentionally non-blocking for the agent process.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
import uuid
from pathlib import Path


DEFAULT_SERVER = "127.0.0.1:45831"
STATE_DEFAULTS = {
    "done": ("answer ready", "info"),
    "wait": ("needs your reply", "attention"),
    "processing": ("working", "silent"),
    "approval": ("permission required", "attention"),
    "offline": ("", "silent"),
}


def normalize_hook(payload, agent):
    """Return a daemon event or None when the hook does not affect the display."""
    if not isinstance(payload, dict):
        return None

    event_name = str(payload.get("hook_event_name") or payload.get("type") or "")
    notification_type = str(payload.get("notification_type") or "")
    state = None
    message = None
    level = None

    if event_name in ("SessionStart", "UserPromptSubmit"):
        state = "processing"
        message = "session started" if event_name == "SessionStart" else "working"
    elif event_name == "PermissionRequest":
        state = "approval"
    elif event_name == "Notification":
        if notification_type == "permission_prompt":
            state = "approval"
        else:
            state = "wait"
            if notification_type == "idle_prompt":
                message = "needs your reply"
            else:
                message = _safe_message(payload.get("message"), "needs attention")
    elif event_name in ("Stop", "agent-turn-complete", "turn-complete"):
        last_message = payload.get("last_assistant_message") or payload.get("last-assistant-message")
        if _looks_like_question(last_message):
            state = "wait"
        else:
            state = "done"
    elif event_name == "StopFailure":
        state = "done"
        message = "stopped with error"
        level = "urgent"
    elif event_name == "SessionEnd":
        state = "offline"
    else:
        return None

    default_message, default_level = STATE_DEFAULTS[state]
    message = message if message is not None else default_message
    level = level if level is not None else default_level
    session = str(payload.get("session_id") or payload.get("thread-id") or "unknown")
    agent_name = _agent_name(agent, payload)
    source = _source_label(agent_name, payload.get("cwd"), session)

    return {
        "action": "event",
        "session_id": "{}:{}".format(agent_name.lower(), session),
        "source": source,
        "state": state,
        "message": message,
        "event_id": _event_id(agent_name, event_name, notification_type, payload),
        "level": level,
    }


def manual_event(args):
    agent_name = _agent_name(args.agent, {})
    session = args.session or "manual-{}".format(os.getpid())
    message, level = STATE_DEFAULTS[args.emit]
    return {
        "action": "event",
        "session_id": "{}:{}".format(agent_name.lower(), session),
        "source": args.source or agent_name,
        "state": args.emit,
        "message": args.message if args.message is not None else message,
        "event_id": args.event_id or "evt-manual-{}".format(uuid.uuid4().hex[:20]),
        "level": args.level or level,
    }


def send_request(address, event, timeout=0.8):
    host, port = parse_address(address)
    data = json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(data.encode("utf-8"))
        sock.settimeout(timeout)
        response = b""
        while b"\n" not in response and len(response) < 64 * 1024:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    if not response:
        raise RuntimeError("notifierd returned no response")
    result = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "notifierd rejected the event"))
    return result


def publish_to_inbox(path, event):
    """Atomically publish an event into a Windows/WSL shared directory."""
    inbox = Path(path).expanduser()
    inbox.mkdir(parents=True, exist_ok=True)
    token = "{}-{}-{}".format(int(time.time() * 1000), os.getpid(), uuid.uuid4().hex[:8])
    temporary = inbox / (".event-{}.tmp".format(token))
    destination = inbox / ("event-{}.json".format(token))
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(event, handle, ensure_ascii=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            # Some Windows/WSL shared filesystems do not expose fsync.
            pass
    os.replace(str(temporary), str(destination))
    return {"ok": True, "queued": str(destination)}


def parse_address(value):
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise ValueError("server must use HOST:PORT")
    port = int(port_text)
    if port < 1 or port > 65535:
        raise ValueError("server port must be between 1 and 65535")
    return host, port


def load_hook_payload(explicit_json=None):
    if explicit_json:
        return json.loads(explicit_json)
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else {}


def _agent_name(agent, payload):
    if agent and agent.lower() != "auto":
        return "Claude" if agent.lower().startswith("claude") else "Codex"
    if "permission_mode" in payload or "notification_type" in payload:
        return "Claude"
    return "Codex"


def _source_label(agent_name, cwd, session):
    normalized = str(cwd or "").replace("\\", "/").rstrip("/")
    project = normalized.rsplit("/", 1)[-1] if normalized else "shell"
    project = re.sub(r"[^A-Za-z0-9._-]+", "-", project).strip("-._") or "project"
    session_tag = _short_hash(session)[:4]
    return "{}:{}#{}".format(agent_name, project[:14], session_tag)[:30]


def _event_id(agent_name, event_name, notification_type, payload):
    discriminator = (
        payload.get("turn_id")
        or payload.get("turn-id")
        or payload.get("prompt_id")
        or payload.get("tool_use_id")
        or payload.get("message")
        or payload.get("reason")
        or "event"
    )
    raw = "|".join(
        str(value)
        for value in (
            agent_name,
            payload.get("session_id") or payload.get("thread-id") or "unknown",
            event_name,
            notification_type,
            discriminator,
        )
    )
    return "evt-" + _short_hash(raw)[:24]


def _short_hash(value):
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()


def _safe_message(value, fallback):
    text = str(value or "").strip()
    if not text or not text.isascii():
        return fallback
    text = re.sub(r"\s+", " ", text)
    return text[:48]


def _looks_like_question(value):
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text.endswith(("?", "？")):
        return True
    markers = (
        "需要你",
        "请提供",
        "请选择",
        "请确认",
        "告诉我",
        "which option",
        "would you like",
        "do you want",
        "please provide",
        "please choose",
        "please confirm",
    )
    return any(marker in text for marker in markers)


def main():
    parser = argparse.ArgumentParser(description="Forward agent hook events to notifierd")
    parser.add_argument("--agent", choices=("auto", "codex", "claude"), default="auto")
    parser.add_argument(
        "--server",
        default=os.environ.get("AGENT_NOTIFIER_ADDR", DEFAULT_SERVER),
        help="notifierd address (HOST:PORT)",
    )
    parser.add_argument(
        "--inbox",
        default=os.environ.get("AGENT_NOTIFIER_INBOX"),
        help="shared event directory; takes precedence over --server",
    )
    parser.add_argument("--input", help="hook JSON; stdin is used when omitted")
    parser.add_argument("--emit", choices=tuple(STATE_DEFAULTS), help="send a manual state")
    parser.add_argument("--session", help="manual event session id")
    parser.add_argument("--source", help="manual event source label")
    parser.add_argument("--message", help="manual event display text")
    parser.add_argument("--event-id", help="manual event id")
    parser.add_argument("--level", choices=("info", "attention", "urgent", "silent"))
    parser.add_argument("--status", action="store_true", help="query daemon status")
    parser.add_argument("--dry-run", action="store_true", help="print normalized event to stderr")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on forwarding errors")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    error = None
    result = None
    try:
        if args.status:
            event = {"action": "status"}
        elif args.emit:
            event = manual_event(args)
        else:
            event = normalize_hook(load_hook_payload(args.input), args.agent)

        if event and args.dry_run:
            print(json.dumps(event, ensure_ascii=True, indent=2), file=sys.stderr)
        if event and not args.dry_run:
            if args.inbox and not args.status:
                result = publish_to_inbox(args.inbox, event)
            else:
                result = send_request(args.server, event)
        if args.verbose and result:
            print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
    except Exception as exc:
        error = exc
        if args.verbose or args.strict:
            print("agent_hook: {}".format(exc), file=sys.stderr)

    # Codex and Claude Stop hooks require structured JSON on stdout.
    print("{}")
    if error and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
