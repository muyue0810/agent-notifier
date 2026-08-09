r"""集成验证脚本：一次打开串口，跑完所有命令，避免反复开关 COM 口。
用法（Windows）: python tools\_verify.py COM7
"""
import serial, time, json, sys

port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
s = serial.Serial(port, 115200, timeout=0.3)
time.sleep(0.2)
s.read(4096)  # 清缓冲

def send(obj):
    s.write((json.dumps(obj) + "\n").encode())
    s.flush()
    time.sleep(0.5)
    return s.read(4096).decode("utf-8", "replace").strip()

tests = [
    ("ping",      {"t": "ping"}),
    ("hello",     {"t": "hello", "proto": 1}),
    ("done",      {"t": "state", "s": "done", "src": "Codex", "msg": "answer ready", "event_id": "v1", "level": "info"}),
    ("wait",      {"t": "state", "s": "wait", "src": "Claude", "msg": "needs reply", "event_id": "v2", "level": "attention"}),
    ("approval",  {"t": "state", "s": "approval", "src": "Codex", "msg": "review PR", "event_id": "v3", "level": "urgent"}),
    ("work",      {"t": "state", "s": "processing", "src": "Codex", "msg": "building", "event_id": "v4"}),
    ("beep",      {"t": "beep"}),
    ("mute",      {"t": "mute", "on": True}),
    ("unmute",    {"t": "mute", "on": False}),
    ("dedup v1",  {"t": "state", "s": "done", "src": "x", "msg": "dup", "event_id": "v1", "level": "info"}),
    ("history",   {"t": "history", "items": ["Codex - done", "Claude - wait", "Codex - build"]}),
    ("settings",  {"t": "settings", "volume": 80, "brightness": 90}),
    ("offline",   {"t": "state", "s": "offline"}),
]

ok = 0
fail = 0
for name, obj in tests:
    resp = send(obj)
    status = "OK" if "ok" in resp or "pong" in resp or "hello" in resp else "FAIL"
    if status == "OK": ok += 1
    else: fail += 1
    print(f"[{status}] {name:10} => {resp[:80]}")

s.close()
print(f"\n=== {ok} passed, {fail} failed ===")
