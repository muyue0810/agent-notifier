# Agent Notifier — ESP32-S3-BOX 通知器

把 ESP32-S3-BOX 变成一个 **AI Agent 状态指示器**：PC 端通过 USB 虚拟串口发指令，BOX 屏幕显示 Agent 当前状态，配合钟声提醒、卡片闪烁、息屏唤醒等效果。

适合挂在桌面上，让 Codex / Claude / 其他 Agent 的工作状态一目了然——不用切窗口看终端。

## 功能一览

| 功能 | 说明 |
|------|------|
| **5 种状态卡片** | Done(绿✓) / Waiting(黄!) / Working(蓝⚡) / Approval(橙★) / Offline(灰×) |
| **来源 + 详情** | 卡片显示 `"Codex - answer ready"` 等来源和消息 |
| **通知等级** | `info`(1声) / `attention`(2声) / `urgent`(3声急促) / `silent`(不响) |
| **柔和钟声** | 880Hz 基频 + 1760Hz 泛音 + 指数衰减，模拟钟声自然衰减 |
| **重复通知去重** | 相同 `event_id` 重复收到只更新 UI，不重复响铃 |
| **声音节流** | 2 秒内多个通知合并提示（取最高等级），防疯狂响铃 |
| **卡片闪烁** | Waiting / Working / Approval 状态卡片透明度闪烁提醒 |
| **触摸按钮** | 底部 Mute / Ack / More，Ack 回传当前 `event_id` 给 PC |
| **heartbeat 心跳** | PC 定期 ping，超时 15 秒自动转 Offline |
| **服务状态指示** | 顶栏三态：`SVC`(服务在线) / `USB`(仅连接) / `----`(未连接) |
| **屏幕息屏/唤醒** | 超时自动变暗；wait/approval/done 通知强制唤醒亮屏 |
| **History 页面** | PC 下发最近 5 条历史，点 More 按钮切换查看 |
| **Settings** | 运行时调整音量 / 亮度 / 提示音开关 / 息屏时间 |
| **协议版本握手** | `hello` 握手报告 `proto:1`，便于后续兼容升级 |

## 硬件 / 软件要求

- **开发板**：ESP32-S3-BOX（老款，LCD 320×240 + ST7789 + ES8311 音频 + TT21100 触摸）
- **ESP-IDF**：release/v5.1（commit `22cfbf30c3`）
- **BSP**：`espressif/esp-box` v3.0.5（已锁定，v3.1 触摸有 bug）
- **button 组件**：v2.5.6（已锁定，v3+ API 不兼容）
- **运行环境**：WSL2（开发/编译）+ Windows（烧录，因 USB 透传限制）

## 目录结构

```
pc_status_display/
├── CMakeLists.txt              # 顶层 CMake
├── partitions.csv              # 分区表（nvs + factory 3MB）
├── sdkconfig.defaults          # 关键配置：USB CDC、PSRAM、LVGL
├── main/
│   ├── main.c                  # app_main：初始化编排
│   ├── usb_cdc.c / .h          # TinyUSB CDC（轮询接收，发送）
│   ├── ui.c / .h               # LVGL 界面 + 闪烁 + 息屏/唤醒 + History 页
│   ├── protocol.c / .h         # JSON 协议解析（proto:1）
│   ├── sound.c / .h            # ES8311 钟声 + 等级响应 + 节流
│   └── idf_component.yml       # 组件依赖（锁版本）
└── tools/
    ├── box_cli.py              # PC 端 Python CLI（交互/单命令/快捷键/心跳）
    └── flash.ps1               # Windows 端一键烧录脚本
```

## 通信协议（proto:1）

PC ↔ BOX 通过 USB CDC（虚拟串口）双向通信，每条消息是一行 JSON。

### 握手

PC 打开串口后发 `{"t":"hello","proto":1}`，BOX 回：
```json
{"event":"hello","proto":1,"app":"agent_notifier"}
{"ack":"ok"}
```

### PC → BOX 指令

| 指令 | JSON 示例 | 效果 |
|------|-----------|------|
| **state** | `{"t":"state","s":"done","src":"Codex","msg":"answer ready","event_id":"evt-1","level":"info"}` | 切换状态卡片 + 按等级响铃 |
| beep | `{"t":"beep"}` | 播放钟声（静音时忽略） |
| mute | `{"t":"mute","on":true}` | 静音/取消静音 |
| ping | `{"t":"ping"}` | 心跳（回 pong，重置超时计时） |
| hello | `{"t":"hello","proto":1}` | 握手 |
| **history** | `{"t":"history","items":["Codex - done","Claude - waiting"]}` | 下发历史记录（最多5条） |
| **settings** | `{"t":"settings","volume":90,"brightness":100,"sound":true,"screen_timeout":30}` | 运行时设置（字段全可选） |

**state 字段说明**：
- `s`：`done` / `wait` / `processing` / `approval` / `offline`
- `src`：来源（如 "Codex"、"Claude"），可选
- `msg`：详情文本，可选
- `event_id`：通知唯一 ID，相同 ID 重复发不重复响铃，可选
- `level`：`info`(默认，1声) / `attention`(2声) / `urgent`(3声急促) / `silent`(不响)

### BOX → PC 事件

| 事件 | JSON | 触发时机 |
|------|------|----------|
| 就绪 | `{"event":"status","running":true,"app":"agent_notifier","proto":1}` | PC 打开串口时 |
| 握手 | `{"event":"hello","proto":1,"app":"agent_notifier"}` | 收到 hello |
| 按钮 | `{"event":"btn","id":"ack","event_id":"evt-1","muted":false}` | 触摸按钮（Ack 带 event_id） |
| 应答 | `{"ack":"ok"}` / `{"ack":"ok","detail":"dup"}` / `{"ack":"pong"}` | 指令处理完成 |

## 编译与烧录

### 首次编译

```bash
# 在 WSL 里
cd pc_status_display
. /home/muyue/esp/esp-idf/export.sh
idf.py set-target esp32s3
# 首次配置后，需要 patch tt21100 头文件（移除已废弃的 scl_speed_hz 字段）
python3 -c "
f='managed_components/espressif__esp_lcd_touch_tt21100/include/esp_lcd_touch_tt21100.h'
lines=open(f).read().splitlines(keepends=True)
open(f,'w').write(''.join(l for l in lines if 'scl_speed_hz' not in l))
"
idf.py build
```

### 烧录（Windows 端，绕开 WSL USB 限制）

WSL2 无法在固件运行时（CDC 占用 USB）烧录，采用 **Windows 原生 esptool** 流程：

1. **复制 bin 到 Windows 临时目录**：
   ```bash
   cp build/bootloader/bootloader.bin /mnt/c/Windows/Temp/
   cp build/partition_table/partition-table.bin /mnt/c/Windows/Temp/
   cp build/pc_status_display.bin /mnt/c/Windows/Temp/
   ```

2. **设备进下载模式**：按住 BOX 的 **BOOT** 键，按一下 **RESET**，松开

3. **用 Windows esptool 烧录**（需先在 Windows 装 `pip install esptool`）：
   ```powershell
   usbipd unbind --busid 1-2   # 如果被 usbipd 绑定
   python -m esptool --chip esp32s3 -p COM5 --before default_reset --after hard_reset `
       write_flash 0x0 C:\Windows\Temp\bootloader.bin `
                   0x8000 C:\Windows\Temp\partition-table.bin `
                   0x10000 C:\Windows\Temp\pc_status_display.bin
   ```
   或直接用封装脚本 `tools/flash.ps1`。

4. **按 RESET 正常启动**（不按 BOOT）——设备以 CDC 模式运行（VID:PID `303a:4001`）

> **注**：每次烧录需重复步骤 2-4。软件自动进下载（boot 命令）未实现，详见"已知限制"。

## PC 端 CLI 使用

```bash
# 安装依赖（在 WSL 里用 venv）
cd pc_status_display
python3 -m venv .venv
.venv/bin/pip install pyserial

# 确保设备已 bind+attach 到 WSL（Windows PowerShell 管理员）
#   usbipd bind --busid 1-2 --force
#   usbipd attach --wsl --busid 1-2
# WSL 首次还需：sudo modprobe vhci_hcd

# 单条命令模式（发完即走，自动握手+心跳）
.venv/bin/python tools/box_cli.py done Codex "answer ready"
.venv/bin/python tools/box_cli.py wait Claude "needs your reply"
.venv/bin/python tools/box_cli.py approval Codex "review merge"   # 橙色+attention级
.venv/bin/python tools/box_cli.py work Codex "building..."
.venv/bin/python tools/box_cli.py beep
.venv/bin/python tools/box_cli.py history "Codex - done" "Claude - waiting"
.venv/bin/python tools/box_cli.py settings volume=50 brightness=80 sound=on timeout=30

# 交互模式（带全局快捷键，需 sudo）
sudo .venv/bin/python tools/box_cli.py
```

### CLI 命令一览

| 命令 | 说明 |
|------|------|
| `state <done\|wait\|processing\|approval\|offline> [src] [msg] [--level ...]` | 设置状态卡片 |
| `done [src] [msg]` | 快捷：state=done, level=info |
| `wait [src] [msg]` | 快捷：state=wait（需要回复） |
| `work [src] [msg]` | 快捷：state=processing（忙碌） |
| `approval [src] [msg]` | 快捷：state=approval, level=attention（需批准） |
| `offline` | 快捷：state=offline |
| `beep` | 播放钟声 |
| `mute` / `unmute` | 静音切换 |
| `ping` | 连通性测试（expect pong） |
| `history "a" "b" ...` | 下发历史记录（点 More 查看） |
| `settings volume=90 brightness=100 sound=on timeout=30` | 调整设置 |
| `help` / `quit` | 帮助 / 退出 |

**默认快捷键**（交互模式）：
- `Ctrl+F1` → done Codex "answer ready"
- `Ctrl+F2` → wait Claude "needs your reply"
- `Ctrl+F3` → beep
- `Ctrl+F4` → work Codex "building..."
- `Ctrl+F5` → approval Codex "review merge"
- `Ctrl+F6` → mute

## 在你的工作流里集成

CLI 是普通的串口通信，任何能发串口数据的脚本都能用。例如在 CI/CD 或 Agent 脚本里：

```bash
# Agent 开始工作
.venv/bin/python tools/box_cli.py work Codex "running tests"

# ... 执行任务 ...

# 完成并响铃（urgent 级别，3 声急促）
.venv/bin/python tools/box_cli.py state done Codex "tests passed" --level urgent
```

或用 Python 直接发 JSON（proto:1）：

```python
import serial, json
s = serial.Serial('/dev/ttyACM0', 115200)
# 握手
s.write((json.dumps({"t":"hello","proto":1}) + "\n").encode())
# 通知
s.write((json.dumps({
    "t":"state","s":"approval","src":"Claude",
    "msg":"needs your approval","event_id":"evt-001","level":"urgent"
}) + "\n").encode())
```

## 已知限制与设计说明

1. **仅支持英文** —— LVGL 默认 Montserrat 字体不含中文，中文显示为方框。如需中文需自行生成中文字体。

2. **烧录需手动 BOOT+RESET** —— 固件运行时 USB 被 CDC 占用，esptool 连不上。软件自动进下载（boot 命令）未调通（RTC magic 跨 `esp_restart()` 未保留）。每次烧录需 BOOT+RESET → Windows esptool 烧录 → RESET 启动。

3. **WSL2 USB 透传** —— 设备需通过 `usbipd` 从 Windows 透传到 WSL：
   - 绑定（管理员）：`usbipd bind --busid 1-2 --force`
   - 附加：`usbipd attach --wsl --busid 1-2`
   - WSL 内首次需 `sudo modprobe vhci_hcd`（重启/重连后可能需重做）
   - 烧录时需先 unbind 让 Windows 直连
   - USB 透传偶发不稳，必要时物理断电重连

4. **版本锁定** —— 因 BSP v3.1 触摸 bug 和 button v3 API 变更，`idf_component.yml` 锁定了 `esp-box: 3.0.5` 和 `button: 2.5.6`，升级需重新验证。

5. **tt21100 patch** —— 每次 `set-target` 重新拉取组件后，需手动移除 `esp_lcd_touch_tt21100.h` 里的 `scl_speed_hz` 行（该字段在新 IDF 已废弃）。

## 关键技术决策

- **CDC 而非 HID**：状态指示器需要自由文本双向通信，CDC（虚拟串口）零驱动、pyserial 即可，比 HID 灵活。
- **轮询接收**：CDC rx 回调里直接读数据不可靠，改为独立任务 100Hz 轮询 `tinyusb_cdcacm_read`（与 esp_tinyusb 官方 test 一致）。
- **绕过 flex 用绝对定位**：LVGL flex 布局在居中上踩坑较多，UI 改用 `LV_ALIGN_TOP_MID` + y 偏移的绝对定位，可控性更好。
- **钟声合成**：正弦基频 + 二倍频泛音 + 指数衰减包络，模拟钟声自然衰减，比单调蜂鸣悦耳。
- **声音节流**：2 秒窗口内合并多次通知，取最高等级播放一次，避免多 Agent 短时间连续通知时疯狂响铃。
- **服务/连接分离**：USB 枚举成功 ≠ 后台服务（notifierd）正常，顶栏用 SVC/USB/---- 三态区分。
