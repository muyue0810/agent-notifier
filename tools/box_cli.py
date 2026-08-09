#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
box_cli.py - ESP32-S3-BOX PC 状态指示器 配套 CLI

功能：
  1) 自动发现 BOX 设备串口（按 VID:PID 303a 或名称 ttyACM 匹配）
  2) 交互式命令：text / status / progress / beep / clear / ping
  3) 全局快捷键（可选，需 keyboard 库 + 权限）
  4) 单条命令模式：python box_cli.py text "hello"
  5) 监听 BOX 回传事件并打印

依赖：
  pip install pyserial
  （可选，用于全局快捷键）pip install keyboard

注意：
  - 全局快捷键在 Linux 下需要 root（sudo）或 uinput 权限。
  - WSL 下 keyboard 库行为可能受限，CLI 会优雅降级（无快捷键仅交互）。
"""

import argparse
import json
import sys
import time
import threading

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("缺少 pyserial，请运行: pip install pyserial")
    sys.exit(1)


# Espressif 的 USB VID
ESPRESSIF_VIDS = {0x303A}

# ============ 串口发现 ============

def find_box_port(prefer=None):
    """自动发现 BOX 串口。返回 device 路径或 None。"""
    ports = list(list_ports.comports())
    # 优先精确匹配 VID:PID 303a:xxxx
    cands = []
    for p in ports:
        vid = pid = None
        if p.vid is not None:
            vid = p.vid
            pid = p.pid
        name = (p.device or "").lower()
        desc = (p.description or "").lower()
        if vid in ESPRESSIF_VIDS:
            cands.append((0, p.device, p.description))
        elif "ttyacm" in name and ("box" in desc or "serial" in desc or "jtag" in desc):
            cands.append((1, p.device, p.description))
        elif "ttyacm" in name:
            cands.append((2, p.device, p.description))
    if prefer:
        for pri, dev, desc in cands:
            if prefer in dev:
                return dev
    if cands:
        cands.sort(key=lambda x: x[0])
        return cands[0][1]
    return None


# ============ 通信 ============

class BoxSerial:
    def __init__(self, port, baud=115200):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._rx_thread = None
        self._stop = False

    def send_json(self, obj):
        """发送一个 JSON 对象（一行）。"""
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self.ser.write(line.encode("utf-8"))

    def send_hello(self):
        """握手：报告协议版本。"""
        self.send_json({"t": "hello", "proto": 1})

    def send_state(self, state, src=None, msg=None, event_id=None, level=None):
        """state: done/wait/processing/approval/offline
           level: info/attention/urgent/silent（影响提示强度，默认 info）"""
        obj = {"t": "state", "s": state}
        if src:       obj["src"] = src
        if msg:       obj["msg"] = msg
        if event_id:  obj["event_id"] = event_id
        obj["level"] = level if level else "info"
        self.send_json(obj)

    def send_beep(self):
        self.send_json({"t": "beep"})

    def send_mute(self, on):
        self.send_json({"t": "mute", "on": bool(on)})

    def send_ping(self):
        self.send_json({"t": "ping"})

    def start_heartbeat(self, interval=5):
        """后台每 interval 秒 ping 一次（当作心跳，防止 BOX 判离线）。"""
        def _loop():
            while not self._stop:
                try:
                    self.send_ping()
                except Exception:
                    break
                time.sleep(interval)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def start_rx(self, on_event=None):
        def _loop():
            buf = b""
            while not self._stop:
                try:
                    data = self.ser.read(4096)
                except Exception:
                    break
                if not data:
                    continue
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line.decode("utf-8"))
                    except Exception:
                        print(f"[BOX-raw] {line.decode('utf-8', 'replace')}")
                        continue
                    if on_event:
                        on_event(evt)
                    else:
                        print(f"[BOX] {evt}")
        self._rx_thread = threading.Thread(target=_loop, daemon=True)
        self._rx_thread.start()

    def close(self):
        self._stop = True
        try:
            self.ser.close()
        except Exception:
            pass


# ============ 默认事件打印 ============

def default_on_event(evt):
    t = evt.get("ack") or evt.get("event") or "msg"
    print(f"[BOX] {t}: {evt}")


# ============ 全局快捷键 ============

def start_hotkeys(box, hotkeys):
    """hotkeys: list of (key_combo, callable_or_command_str)"""
    try:
        import keyboard
    except ImportError:
        print("[!] 未安装 keyboard 库，全局快捷键不可用。交互模式仍可用。")
        print("    安装: pip install keyboard")
        return False
    except Exception as e:
        print(f"[!] keyboard 库不可用: {e}")
        print("    Linux 下全局快捷键需 root 或 uinput 权限。")
        return False

    def _run(cmd):
        try:
            if callable(cmd):
                cmd()
            else:
                dispatch_command(box, cmd, print_ack=False)
        except Exception as e:
            print(f"[hotkey 执行错误] {e}")

    for combo, cmd in hotkeys:
        try:
            keyboard.add_hotkey(combo, _run, args=(cmd,))
            print(f"    快捷键 {combo} -> {cmd if isinstance(cmd, str) else '<func>'}")
        except Exception as e:
            print(f"    [!] 注册快捷键 {combo} 失败: {e}")
    print("[i] 全局快捷键已启用，按 ESC 退出程序。")
    return True


# ============ 命令派发 ============

STATE_TYPES = ("done", "wait", "processing", "approval", "offline")
LEVEL_TYPES = ("info", "attention", "urgent", "silent")
import time as _time

def _gen_event_id():
    return f"evt-{int(_time.time()*1000)}"

def _send_state_shortcut(box, state, arg):
    """快捷命令：cmd [src] [msg]，自动生成 event_id，默认 level=info。"""
    toks = arg.split(None, 1)
    src = toks[0] if len(toks) > 0 and toks[0] else None
    msg = toks[1] if len(toks) > 1 else None
    box.send_state(state, src=src, msg=msg, event_id=_gen_event_id(), level="info")

def dispatch_command(box, cmdline, print_ack=True):
    """解析并执行一条交互命令。"""
    parts = cmdline.strip().split(None, 1)
    if not parts:
        return
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("state", "s"):
        # state <type> [src] [msg] [--level X] [--eid ID]
        toks = arg.split(None, 2)
        if not toks or toks[0].lower() not in STATE_TYPES:
            print(f"    usage: state <{'|'.join(STATE_TYPES)}> [src] [msg] [--level {'|'.join(LEVEL_TYPES)}]")
            return
        state = toks[0].lower()
        src = toks[1] if len(toks) > 1 else None
        msg = toks[2] if len(toks) > 2 else None
        # 解析可选的 --level / --eid（简单处理）
        level = "info"
        eid = _gen_event_id()
        if msg:
            for lv in LEVEL_TYPES:
                tag = f"--level {lv}"
                if tag in msg:
                    level = lv
                    msg = msg.replace(tag, "").strip()
            if "--eid " in msg:
                import re
                m = re.search(r"--eid (\S+)", msg)
                if m:
                    eid = m.group(1)
                    msg = re.sub(r"--eid \S+", "", msg).strip()
        box.send_state(state, src=src or None, msg=msg or None, event_id=eid, level=level)

    elif cmd == "done":
        _send_state_shortcut(box, "done", arg)
    elif cmd in ("wait", "waiting"):
        _send_state_shortcut(box, "wait", arg)
    elif cmd in ("work", "processing", "busy"):
        _send_state_shortcut(box, "processing", arg)
    elif cmd in ("approval", "approve", "review"):
        # approval 默认用 attention 级别（更醒目）
        toks = arg.split(None, 1)
        src = toks[0] if len(toks) > 0 and toks[0] else None
        msg = toks[1] if len(toks) > 1 else None
        box.send_state("approval", src=src, msg=msg, event_id=_gen_event_id(), level="attention")
    elif cmd == "offline":
        box.send_state("offline")

    elif cmd == "beep":
        box.send_beep()
    elif cmd in ("mute", "unmute"):
        box.send_mute(cmd == "mute")
    elif cmd == "ping":
        box.send_ping()
    elif cmd in ("history", "hist"):
        # 用法：history "Codex - done" "Claude - waiting" ...
        toks = [t.strip() for t in arg.split('"') if t.strip()]
        items = toks if toks else ["Codex - build done", "Claude - needs reply"]
        box.send_json({"t": "history", "items": items})
    elif cmd == "settings":
        # settings volume=90 brightness=100 sound=on timeout=30
        obj = {"t": "settings"}
        for kv in arg.split():
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k in ("volume", "brightness", "screen_timeout"):
                    obj[k] = int(v) if v.lstrip("-").isdigit() else v
                elif k == "sound":
                    obj[k] = v.lower() in ("on", "true", "1")
                elif k == "timeout":
                    obj["screen_timeout"] = int(v)
        box.send_json(obj)
    elif cmd in ("help", "h", "?"):
        print_help()
    elif cmd in ("quit", "q", "exit"):
        raise KeyboardInterrupt
    else:
        print(f"    unknown command: {cmd} (type 'help' for commands)")


def print_help():
    print(f"""
Agent Notifier commands (proto:1):
  state <{'|'.join(STATE_TYPES)}> [src] [msg] [--level {'|'.join(LEVEL_TYPES)}]
                            Set state card with optional level & event_id.
                            e.g: state approval Codex "merge PR" --level urgent
  done [src] [msg]          Shortcut: state=done, level=info
  wait [src] [msg]          Shortcut: state=wait (needs your reply)
  work [src] [msg]          Shortcut: state=processing (busy)
  approval [src] [msg]      Shortcut: state=approval, level=attention
  offline                   Shortcut: state=offline
  beep                      Play notification sound
  mute / unmute             Mute / unmute sound
  ping                      Test connectivity (expect pong)
  history "a" "b" ...       Push history items (tap More to view)
  settings volume=90 brightness=100 sound=on timeout=30
                            Adjust box settings
  help                      Show this help
  quit                      Exit

Levels: info(1 beep) / attention(2) / urgent(3) / silent(no sound)
Box auto-dedupes by event_id (repeated state with same id won't beep).
Box shows Offline after 15s without PC heartbeat.
Touch buttons: Mute / Ack (sends event_id back) / More (toggle history)
""")

Box bottom buttons (touch): Mute / History / More
  - Pressing them sends {"event":"btn","id":"mute|history|more"} to PC
""")


# ============ 主入口 ============

def build_default_hotkeys(box):
    """默认快捷键示例，可自行修改。"""
    return [
        ("ctrl+f1", 'done Codex "answer ready"'),
        ("ctrl+f2", 'wait Claude "needs your reply"'),
        ("ctrl+f3", "beep"),
        ("ctrl+f4", 'work Codex "building..."'),
        ("ctrl+f5", 'approval Codex "review merge"'),
        ("ctrl+f6", "mute"),
    ]


def main():
    ap = argparse.ArgumentParser(description="BOX PC 状态指示器 CLI")
    ap.add_argument("--port", "-p", help="串口设备路径（不指定则自动发现）")
    ap.add_argument("--baud", "-b", type=int, default=115200, help="波特率（CDC 通常忽略）")
    ap.add_argument("--no-hotkey", action="store_true", help="禁用全局快捷键")
    ap.add_argument("--list", action="store_true", help="列出串口后退出")
    # 单条命令模式
    ap.add_argument("cmd", nargs="?", help="单条命令（text/status/progress/beep/clear/ping）")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="命令参数")
    args = ap.parse_args()

    if args.list:
        for p in list_ports.comports():
            print(f"{p.device}  vid={p.vid:#x} pid={p.pid:#x}  {p.description}")
        return

    port = args.port or find_box_port()
    if not port:
        print("[!] 未发现 BOX 串口。请用 --list 查看，或用 -p 指定。")
        sys.exit(1)
    print(f"[i] 使用串口: {port}")

    box = BoxSerial(port, args.baud)
    # 等设备枚举稳定
    time.sleep(0.3)
    box.start_rx(on_event=default_on_event)
    # 握手 + 启动心跳（保持 BOX 不判离线）
    box.send_hello()
    box.start_heartbeat(interval=5)   # 每 5 秒 ping 一次

    # ---- 单条命令模式：发完即走 ----
    if args.cmd:
        full = args.cmd + (" " + " ".join(args.args) if args.args else "")
        try:
            dispatch_command(box, full)
            time.sleep(0.4)  # 等待 ack 回显
        except KeyboardInterrupt:
            pass
        box.close()
        return

    # ---- 交互模式 ----
    print("[i] 进入交互模式。输入 help 查看命令，quit 退出。")
    if not args.no_hotkey:
        print("[i] 尝试启用全局快捷键...")
        start_hotkeys(box, build_default_hotkeys(box))
    print_help()

    try:
        while True:
            try:
                line = input("box> ")
            except EOFError:
                break
            dispatch_command(box, line)
    except KeyboardInterrupt:
        print("\n[i] 退出。")
    finally:
        box.close()


if __name__ == "__main__":
    main()
