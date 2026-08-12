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
| **多进程聚合** | 常驻服务独占串口，同时接收多个 Codex / Claude 会话事件 |
| **优先级防覆盖** | Approval > Waiting > Done > Working，低优先级进程不会盖住待处理提醒 |
| **Agent Hooks** | 支持 Codex / Claude Code 生命周期 hooks，自动显示工作、完成、待回复和待批准 |

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
├── tools/
│   ├── box_cli.py              # PC 端 Python CLI（跨平台：Windows/Linux/WSL）
│   ├── box_cli.bat             # Windows 启动脚本
│   ├── notifierd.py / .bat     # 多进程聚合服务（唯一串口持有者）
│   ├── agent_hook.py / .bat    # Codex / Claude Code hook 适配器
│   ├── test_notifier.py        # 聚合规则与 hook 映射测试
│   └── flash.ps1               # Windows 端一键烧录脚本
└── examples/
    ├── codex-hooks.json            # Codex hooks 配置模板
    └── claude-settings-hooks.json  # Claude Code hooks 配置模板
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

CLI 是纯 Python（pyserial），**Windows / Linux / WSL 均可运行**。

### Windows 直接运行（推荐，最简单）

Windows 不需要 WSL/usbipd，设备插上就能用：

```powershell
# 首次：装 pyserial
pip install pyserial

# 单条命令（自动发现 COM 口）
python tools\box_cli.py done Codex "answer ready"
python tools\box_cli.py wait Claude "needs your reply"
python tools\box_cli.py approval Codex "review merge"
python tools\box_cli.py beep
python tools\box_cli.py ping

# 或用批处理脚本
tools\box_cli.bat done Codex "answer ready"

# 交互模式（注：全局快捷键需额外装 keyboard 库且以管理员运行）
python tools\box_cli.py

# 指定 COM 口（自动发现失败时）
python tools\box_cli.py -p COM7 done Codex "answer ready"

# 列出所有串口
python tools\box_cli.py --list
```

### WSL/Linux 运行

```bash
# 安装依赖（用 venv）
cd pc_status_display
python3 -m venv .venv
.venv/bin/pip install pyserial

# WSL 需先把设备透传进来（Windows PowerShell 管理员）：
#   usbipd bind --busid 1-2 --force
#   usbipd attach --wsl --busid 1-2
#   WSL 内首次还需：sudo modprobe vhci_hcd

# 单条命令
.venv/bin/python tools/box_cli.py done Codex "answer ready"
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

## 多进程常驻服务（推荐）

多个 Codex / Claude 终端不能同时直接打开同一个 COM/tty 串口。`notifierd.py` 是唯一串口持有者，Agent hook 只向它提交短事件，由服务统一决定 BOX 当前显示哪一条。

聚合优先级为：

1. `approval`：需要批准
2. `wait`：需要回复
3. `done`：回答完成
4. `processing`：工作中

同一会话的新状态会替换旧状态；不同会话按优先级和更新时间聚合。BOX 上点击 Ack 会移除当前事件并自动显示下一条待处理事件，History 也由服务自动维护最近 5 条。

### 同一系统运行

```bash
# 安装依赖
python -m pip install pyserial

# Windows
tools\notifierd.bat --port COM7

# Linux / WSL（设备已透传时）
python3 tools/notifierd.py --port /dev/ttyACM0
```

服务默认监听 `127.0.0.1:45831`。可先发送模拟事件验证：

```bash
python3 tools/agent_hook.py --emit processing --source Codex:kernel --message "running tests"
python3 tools/agent_hook.py --emit wait --source Claude:gadget --message "needs your reply"
python3 tools/agent_hook.py --status --verbose
```

### Windows 服务 + WSL Agent（无需把 BOX 透传给 WSL）

使用 Windows/WSL 共享事件目录，不开放网络端口：

```powershell
# Windows：服务直接使用 COM 口，并监视共享收件箱
tools\notifierd.bat --port COM7 --inbox "$env:USERPROFILE\.agent-notifier\inbox"
```

```bash
# WSL：Codex / Claude hook 写入同一个 Windows 目录
export AGENT_NOTIFIER_INBOX=/mnt/c/Users/<windows-user>/.agent-notifier/inbox

# 手动验证
python3 tools/agent_hook.py --emit done --source Codex:test --message "answer ready"
```

Hook 采用临时文件 + 原子重命名，多进程同时写入不会互相覆盖。可把 `AGENT_NOTIFIER_INBOX` 放进 WSL shell 启动配置，或在 hook 命令后显式加 `--inbox /mnt/c/...`。

## 自动接入 Codex / Claude Code

### Codex CLI

1. 将 `examples/codex-hooks.json` 复制/合并到 `~/.codex/hooks.json`。
2. 把模板里的 `/ABSOLUTE/PATH/` 改成仓库实际绝对路径；Windows 端同时修改 `commandWindows`。
3. 若服务运行在 Windows、Codex 运行在 WSL，在每条命令后加共享目录参数：
   `--inbox /mnt/c/Users/<windows-user>/.agent-notifier/inbox`。
4. 重新进入 Codex，运行 `/hooks` 检查并信任新增 hooks。

映射关系：`UserPromptSubmit → Working`、`PermissionRequest → Approval`、`Stop → Done/Waiting`、`SessionEnd → 清除该会话`。Stop 的最后一条回复像问题或明确要求用户提供/确认信息时显示 Waiting，否则显示 Done。配置格式依据 [Codex Hooks 官方文档](https://developers.openai.com/codex/hooks)。

### Claude Code

将 `examples/claude-settings-hooks.json` 中的 `hooks` 合并到 `~/.claude/settings.json`，修改绝对路径，并按运行环境配置同一个 `--inbox`。不要直接覆盖已有 settings 中的其他配置。

映射关系：`UserPromptSubmit → Working`、`Notification(permission_prompt) → Approval`、`Notification(idle_prompt) → Waiting`、`Stop → Done/Waiting`、`StopFailure → Urgent`、`SessionEnd → 清除该会话`。配置格式依据 [Claude Code Hooks 官方文档](https://docs.anthropic.com/en/docs/claude-code/hooks)。

Hook 适配器连接失败时会安静退出，不会阻塞 Codex / Claude 的正常工作；需要排查时在 hook 命令后加 `--verbose`。

## 在你的工作流里集成

常驻服务运行时，CI/CD 或 Agent 脚本应向服务发送事件，避免和服务抢串口：

```bash
# Agent 开始工作
python3 tools/agent_hook.py --emit processing --source Codex:ci --message "running tests"

# ... 执行任务 ...

# 完成并响铃（urgent 级别，3 声急促）
python3 tools/agent_hook.py --emit done --source Codex:ci --message "tests passed" --level urgent
```

未运行 `notifierd.py` 时，也可用 Python 直接发串口 JSON（proto:1）：

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
