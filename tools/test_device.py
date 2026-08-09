r"""设备验证脚本（需要连接 BOX 设备）。

自动发送各指令、对比预期响应、输出 PASS/FAIL 汇总。
用法: python tools\test_device.py COM7
      python tools/test_device.py /dev/ttyACM0
退出码: 0=全通过, 1=有失败, 2=连接失败
"""
import serial, time, json, sys

port = sys.argv[1] if len(sys.argv) > 1 else None
if not port:
    print("用法: python tools/test_device.py <PORT>")
    print("例如: python tools/test_device.py COM7")
    print("     python tools/test_device.py /dev/ttyACM0")
    sys.exit(2)

results = []
def record(name, passed, detail=""):
    results.append((name, passed, detail))

try:
    s = serial.Serial(port, 115200, timeout=0.3)
except Exception as e:
    print(f"无法打开 {port}: {e}")
    sys.exit(2)

time.sleep(0.3)
s.read(4096)  # 清缓冲


def send_and_recv(obj, wait=0.6):
    """发送 JSON，等待响应，返回解析后的 JSON 列表"""
    s.write((json.dumps(obj) + "\n").encode())
    s.flush()
    time.sleep(wait)
    data = s.read(4096)
    lines = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except Exception:
            pass
    return lines


def find_in(responses, key, value):
    for r in responses:
        if r.get(key) == value:
            return r
    return None


# ===== 测试用例 =====
print("开始设备验证...\n")

# 1. hello 握手
r = send_and_recv({"t": "hello", "proto": 1})
hello_evt = find_in(r, "event", "hello")
record("hello 握手返回 proto:1",
       hello_evt is not None and hello_evt.get("proto") == 1,
       f"got {r}")

# 2. ping
r = send_and_recv({"t": "ping"})
record("ping 返回 pong", find_in(r, "ack", "pong") is not None, f"got {r}")

# 3. done 状态
r = send_and_recv({"t": "state", "s": "done", "src": "Test", "msg": "ok", "event_id": "t1", "level": "info"})
record("done 状态 ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 4. wait 状态
r = send_and_recv({"t": "state", "s": "wait", "src": "Test", "msg": "reply", "event_id": "t2", "level": "info"})
record("wait 状态 ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 5. approval 状态
r = send_and_recv({"t": "state", "s": "approval", "src": "Test", "msg": "review", "event_id": "t3", "level": "info"})
record("approval 状态 ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 6. processing 状态
r = send_and_recv({"t": "state", "s": "processing", "src": "Test", "msg": "work", "event_id": "t4"})
record("processing 状态 ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 7. offline 状态
r = send_and_recv({"t": "state", "s": "offline"})
record("offline 状态 ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 8. beep（静音状态下可能不响，但仍应 ack）
send_and_recv({"t": "mute", "on": False}, wait=0.3)  # 先确保不静音
r = send_and_recv({"t": "beep"})
record("beep ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 9. mute on
r = send_and_recv({"t": "mute", "on": True})
record("mute on ack", find_in(r, "ack", "ok") is not None, f"got {r}")

# 10. mute off
r = send_and_recv({"t": "mute", "on": False})
record("mute off ack", find_in(r, "ack", "ok") is not None, f"got {r}")

# 11. 去重：重复 event_id 返回 dup
r1 = send_and_recv({"t": "state", "s": "done", "src": "D", "msg": "first", "event_id": "dup1", "level": "silent"})
r2 = send_and_recv({"t": "state", "s": "done", "src": "D", "msg": "second", "event_id": "dup1", "level": "silent"})
ack2 = find_in(r2, "ack", "ok")
record("重复 event_id 返回 dup",
       ack2 is not None and ack2.get("detail") == "dup",
       f"got {r2}")

# 12. history 下发
r = send_and_recv({"t": "history", "items": ["A - done", "B - wait"]})
record("history ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 13. settings
r = send_and_recv({"t": "settings", "volume": 80, "brightness": 90})
record("settings ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

# 14. 屏幕超时设置
r = send_and_recv({"t": "settings", "screen_timeout": 30})
record("screen_timeout 设置 ack:ok", find_in(r, "ack", "ok") is not None, f"got {r}")

s.close()

# ===== 汇总 =====
print("=" * 50)
for name, passed, detail in results:
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if not passed else ""))
print("=" * 50)
fails = [n for n, p, _ in results if not p]
print(f"{len(results) - len(fails)}/{len(results)} passed")
if fails:
    print(f"FAILED: {', '.join(fails)}")
    sys.exit(1)
print("ALL PASSED")
sys.exit(0)
