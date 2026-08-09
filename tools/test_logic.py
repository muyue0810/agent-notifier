"""纯逻辑测试（不需要设备，可 CI 自动跑）。

验证协议构造、参数解析、JSON 格式、枚举完整性。
运行: python tools/test_logic.py
退出码: 0=全通过, 1=有失败
"""
import sys, os, json, io
sys.path.insert(0, os.path.dirname(__file__))

# ============ 导入被测代码（需要 pyserial）============
try:
    import serial  # noqa: F401
except ImportError:
    print("ERROR: 需要 pyserial。运行: pip install pyserial")
    sys.exit(2)
import box_cli  # noqa: E402

# ============ 测试框架（极简，不依赖 pytest）============
_results = []
def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    return bool(cond)

def section(title):
    print(f"\n--- {title} ---")

def run_all():
    fails = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 50)
    for name, ok, detail in _results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    print("=" * 50)
    total = len(_results)
    passed = total - len(fails)
    print(f"{passed}/{total} passed")
    if fails:
        print(f"FAILED: {', '.join(fails)}")
        return 1
    print("ALL PASSED")
    return 0

class FakeSerial:
    """模拟 pyserial，记录所有写入和读取响应"""
    def __init__(self, *a, **kw):
        self.written = []
        self._responses = []
        self.timeout = 0.2
        self.port = a[0] if a else "FAKE"
    def write(self, data):
        self.written.append(data)
    def flush(self): pass
    def read(self, n=4096):
        if self._responses:
            return self._responses.pop(0)
        return b""
    def close(self): pass

# ============ 测试用例 ============

def test_protocol_state_construction():
    """state 指令的 JSON 构造正确"""
    section("协议构造: state")
    import serial as _ser_mod
    orig_serial = _ser_mod.Serial
    _ser_mod.Serial = FakeSerial
    try:
        box = box_cli.BoxSerial("FAKE")
        # 完整字段
        box.send_state("done", src="Codex", msg="ready", event_id="e1", level="info")
        sent = json.loads(box.ser.written[-1].decode())
        check("state 完整字段 t", sent.get("t") == "state")
        check("state 完整字段 s", sent.get("s") == "done")
        check("state 完整字段 src", sent.get("src") == "Codex")
        check("state 完整字段 msg", sent.get("msg") == "ready")
        check("state 完整字段 event_id", sent.get("event_id") == "e1")
        check("state 完整字段 level", sent.get("level") == "info")

        # 可选字段省略
        box.send_state("offline")
        sent = json.loads(box.ser.written[-1].decode())
        check("state 省略 src 无该字段", "src" not in sent)
        check("state 省略 msg 无该字段", "msg" not in sent)
        check("level 默认 info", sent.get("level") == "info")

        # approval + urgent
        box.send_state("approval", src="X", level="urgent")
        sent = json.loads(box.ser.written[-1].decode())
        check("approval 状态值", sent.get("s") == "approval")
        check("urgent 等级", sent.get("level") == "urgent")
    finally:
        _ser_mod.Serial = orig_serial

def test_protocol_other_commands():
    """其他指令的 JSON 构造"""
    section("协议构造: 其他指令")
    import serial as _ser_mod
    orig_serial = _ser_mod.Serial
    _ser_mod.Serial = FakeSerial
    try:
        box = box_cli.BoxSerial("FAKE")
        box.send_hello();   h = json.loads(box.ser.written[-1].decode())
        box.send_beep();    b = json.loads(box.ser.written[-1].decode())
        box.send_mute(True); m = json.loads(box.ser.written[-1].decode())
        box.send_ping();    p = json.loads(box.ser.written[-1].decode())

        check("hello 含 proto:1", h.get("t") == "hello" and h.get("proto") == 1)
        check("beep", b.get("t") == "beep")
        check("mute on=true", m.get("t") == "mute" and m.get("on") is True)
        check("ping", p.get("t") == "ping")
    finally:
        _ser_mod.Serial = orig_serial

def test_state_types_enum():
    """STATE_TYPES 包含全部 5 种状态"""
    section("枚举完整性")
    expected = {"done", "wait", "processing", "approval", "offline"}
    check("STATE_TYPES 5 种", set(box_cli.STATE_TYPES) == expected,
          f"got {box_cli.STATE_TYPES}")

    expected_levels = {"info", "attention", "urgent", "silent"}
    check("LEVEL_TYPES 4 种", set(box_cli.LEVEL_TYPES) == expected_levels,
          f"got {box_cli.LEVEL_TYPES}")

def test_dispatch_command():
    """dispatch_command 正确解析各种命令"""
    section("命令派发")
    import serial as _ser_mod
    orig_serial = _ser_mod.Serial
    _ser_mod.Serial = FakeSerial
    try:
        box = box_cli.BoxSerial("FAKE")

        # done 快捷
        box.ser.written.clear()
        box_cli.dispatch_command(box, 'done Codex "answer ready"')
        sent = json.loads(box.ser.written[-1].decode())
        check("done 快捷 s=done", sent.get("s") == "done")
        check("done 快捷 含 src", sent.get("src") == "Codex")
        check("done 快捷 自动 event_id", "event_id" in sent)

        # approval 快捷
        box.ser.written.clear()
        box_cli.dispatch_command(box, 'approval Codex "review"')
        sent = json.loads(box.ser.written[-1].decode())
        check("approval 快捷 s=approval", sent.get("s") == "approval")
        check("approval 快捷 level=attention", sent.get("level") == "attention")

        # beep
        box.ser.written.clear()
        box_cli.dispatch_command(box, "beep")
        check("beep 派发", json.loads(box.ser.written[-1].decode()).get("t") == "beep")

        # mute
        box.ser.written.clear()
        box_cli.dispatch_command(box, "mute")
        sent = json.loads(box.ser.written[-1].decode())
        check("mute 派发 on=true", sent.get("t") == "mute" and sent.get("on") is True)

        # history
        box.ser.written.clear()
        box_cli.dispatch_command(box, 'history "a" "b"')
        sent = json.loads(box.ser.written[-1].decode())
        check("history 含 items", sent.get("t") == "history" and len(sent.get("items", [])) == 2)

        # settings
        box.ser.written.clear()
        box_cli.dispatch_command(box, "settings volume=50 brightness=80")
        sent = json.loads(box.ser.written[-1].decode())
        check("settings volume", sent.get("volume") == 50)
        check("settings brightness", sent.get("brightness") == 80)
    finally:
        _ser_mod.Serial = orig_serial

def test_event_id_uniqueness():
    """_gen_event_id 每次不同"""
    section("event_id 生成")
    ids = {box_cli._gen_event_id() for _ in range(100)}
    check("100 次 event_id 唯一", len(ids) == 100, f"only {len(ids)} unique")

def test_find_port_cross_platform():
    """find_box_port 跨平台（COMx 和 ttyACMx）"""
    section("串口发现跨平台")
    # 这个函数依赖 list_ports.comports()，无设备时返回 None 是正常的
    # 主要验证不抛异常
    try:
        r = box_cli.find_box_port()
        check("find_box_port 不抛异常", True)
    except Exception as e:
        check("find_box_port 不抛异常", False, str(e))


if __name__ == "__main__":
    test_protocol_state_construction()
    test_protocol_other_commands()
    test_state_types_enum()
    test_dispatch_command()
    test_event_id_uniqueness()
    test_find_port_cross_platform()
    sys.exit(run_all())
