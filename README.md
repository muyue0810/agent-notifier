# Agent Notifier — ESP32-S3-BOX 通知器

把 ESP32-S3-BOX 变成一个 **AI Agent 状态指示器**：PC 端通过 USB 虚拟串口发指令，BOX 屏幕显示 Agent 当前状态（完成 / 等待回复 / 处理中 / 离线），配合柔和钟声提醒和卡片闪烁动画。

适合挂在桌面上，让 Codex / Claude / 其他 Agent 的工作状态一目了然——不用切窗口看终端。

## 功能一览

| 功能 | 说明 |
|------|------|
| 状态卡片 | 4 种状态：Done(绿✓) / Waiting(黄!) / Working(蓝⚡) / Offline(灰×) |
| 来源 + 详情 | 卡片显示 `"Codex - answer ready"` 等来源和消息 |
| 闪烁提醒 | Waiting / Working 时卡片透明度闪烁，醒目提醒 |
| 钟声提示 | 收到 beep 时播放柔和钟声（880Hz 基频 + 泛音，指数衰减） |
| 静音 | 支持 mute/unmute，触摸 Mute 按钮或 CLI 指令 |
| 触摸按钮 | 底部三个按钮：Mute / History / More，点击事件回传 PC |
| 顶栏连接指示 | USB 连接状态实时显示（USB / ----） |

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
│   ├── ui.c / .h               # LVGL 界面（Agent Notifier 布局 + 闪烁动画）
│   ├── protocol.c / .h         # JSON 协议解析与派发
│   ├── sound.c / .h            # ES8311 codec 钟声提示音
│   └── idf_component.yml       # 组件依赖
└── tools/
    ├── box_cli.py              # PC 端 Python CLI（交互 + 单命令 + 全局快捷键）
    └── flash.ps1               # Windows 端一键烧录脚本
```

## 通信协议

PC ↔ BOX 通过 USB CDC（虚拟串口）双向通信，每条消息是一行 JSON。

### PC → BOX 指令

| 指令 | JSON 示例 | 效果 |
|------|-----------|------|
| 设置状态 | `{"t":"state","s":"done","src":"Codex","msg":"answer ready"}` | 切换状态卡片 |
| beep | `{"t":"beep"}` | 播放钟声（静音时忽略） |
| 静音 | `{"t":"mute","on":true}` | 静音/取消静音 |
| ping | `{"t":"ping"}` | 连通性测试，回 `{"ack":"pong"}` |

**状态值 `s`**：`done` / `wait` / `processing` / `offline`

### BOX → PC 事件

| 事件 | JSON | 触发时机 |
|------|------|----------|
| 就绪 | `{"event":"status","running":true,"app":"agent_notifier"}` | PC 打开串口时 |
| 按钮 | `{"event":"btn","id":"mute","muted":false}` | 触摸底部按钮 |
| 应答 | `{"ack":"ok"}` / `{"ack":"pong"}` | 指令处理完成 |

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

由于 WSL2 无法在固件运行时（CDC 占用 USB）烧录，采用 **Windows 原生 esptool** 流程：

1. **复制 bin 到 Windows 临时目录**：
   ```bash
   cp build/bootloader/bootloader.bin /mnt/c/Windows/Temp/
   cp build/partition_table/partition-table.bin /mnt/c/Windows/Temp/
   cp build/pc_status_display.bin /mnt/c/Windows/Temp/
   ```

2. **设备进下载模式**：按住 BOX 的 **BOOT** 键，按一下 **RESET**，松开

3. **用 Windows esptool 烧录**（需先在 Windows 装 `pip install esptool`）：
   ```powershell
   # 先 unbind（如果 usbipd 绑定了设备）
   usbipd unbind --busid 1-2
   # 烧录（COM5 是下载模式端口，hard_reset 后按 RESET 正常启动）
   python -m esptool --chip esp32s3 -p COM5 --before default_reset --after hard_reset `
       write_flash 0x0 C:\Windows\Temp\bootloader.bin `
                   0x8000 C:\Windows\Temp\partition-table.bin `
                   0x10000 C:\Windows\Temp\pc_status_display.bin
   ```

   或直接用封装脚本 `tools/flash.ps1`。

4. **按 RESET 正常启动**（不按 BOOT）——设备会以 CDC 模式运行（VID:PID `303a:4001`）

> **注**：每次重新烧录都需要重复步骤 2-4（BOOT+RESET 进下载 → 烧录 → RESET 启动）。
> 软件自动进下载模式（OTA / boot 命令）未实现，详见"已知限制"。

## PC 端 CLI 使用

```bash
# 安装依赖（在 WSL 里用 venv）
cd pc_status_display
python3 -m venv .venv
.venv/bin/pip install pyserial

# 确保设备已 bind+attach 到 WSL
# （Windows PowerShell 管理员）
#   usbipd bind --busid 1-2 --force
#   usbipd attach --wsl --busid 1-2

# 单条命令模式（发完即走）
.venv/bin/python tools/box_cli.py done Codex "answer ready"
.venv/bin/python tools/box_cli.py wait Claude "needs your reply"
.venv/bin/python tools/box_cli.py work Codex "building..."
.venv/bin/python tools/box_cli.py beep
.venv/bin/python tools/box_cli.py mute      # 静音
.venv/bin/python tools/box_cli.py unmute    # 取消静音
.venv/bin/python tools/box_cli.py ping

# 交互模式（带全局快捷键，需 sudo）
sudo .venv/bin/python tools/box_cli.py
# 默认快捷键：
#   Ctrl+F1 → done Codex "answer ready"
#   Ctrl+F2 → wait Claude "needs your reply"
#   Ctrl+F3 → beep
#   Ctrl+F4 → work Codex "building..."
#   Ctrl+F5 → offline
#   Ctrl+F6 → mute
```

### CLI 命令一览

| 命令 | 说明 |
|------|------|
| `state <done\|wait\|processing\|offline> [src] [msg]` | 设置状态卡片 |
| `done [src] [msg]` | 快捷：状态=完成 |
| `wait [src] [msg]` | 快捷：状态=等待回复 |
| `work [src] [msg]` | 快捷：状态=处理中 |
| `offline` | 快捷：状态=离线 |
| `beep` | 播放钟声 |
| `mute` / `unmute` | 静音切换 |
| `ping` | 连通性测试 |
| `help` | 显示帮助 |
| `quit` | 退出交互模式 |

## 在你的工作流里集成

CLI 是普通的串口通信，任何能发串口数据的脚本都能用。例如在 CI/CD 或 Agent 脚本里：

```bash
# Agent 开始工作
.venv/bin/python tools/box_cli.py work Codex "running tests"

# ... 执行任务 ...

# 完成，响一声
.venv/bin/python tools/box_cli.py done Codex "tests passed"
.venv/bin/python tools/box_cli.py beep
```

或用 Python 直接发 JSON：

```python
import serial, json
s = serial.Serial('/dev/ttyACM0', 115200)
s.write((json.dumps({"t":"state","s":"wait","src":"Claude","msg":"needs your reply"}) + "\n").encode())
```

## 已知限制与设计说明

1. **仅支持英文** —— LVGL 默认 Montserrat 字体不含中文，中文显示为方框。如需中文需自行生成中文字体。

2. **烧录需手动 BOOT+RESET** —— 固件运行时 USB 被 CDC 占用，esptool 连不上。软件自动进下载（boot 命令）未调通（RTC magic 跨 `esp_restart()` 未保留）。每次烧录需 BOOT+RESET → Windows esptool 烧录 → RESET 启动。

3. **WSL2 USB 透传** —— 设备需通过 `usbipd` 从 Windows 透传到 WSL：
   - 绑定（管理员）：`usbipd bind --busid 1-2 --force`
   - 附加：`usbipd attach --wsl --busid 1-2`
   - 烧录时需先 unbind 让 Windows 直连

4. **版本锁定** —— 因 BSP v3.1 触摸 bug 和 button v3 API 变更，`idf_component.yml` 锁定了 `esp-box: 3.0.5` 和 `button: 2.5.6`，升级需重新验证。

5. **tt21100 patch** —— 每次 `set-target` 重新拉取组件后，需手动移除 `esp_lcd_touch_tt21100.h` 里的 `scl_speed_hz` 行（该字段在新 IDF 已废弃）。

## 关键技术决策

- **CDC 而非 HID**：状态指示器需要自由文本双向通信，CDC（虚拟串口）零驱动、pyserial 即可，比 HID 灵活。
- **轮询接收**：CDC rx 回调里直接读数据不可靠，改为独立任务 100Hz 轮询 `tinyusb_cdcacm_read`（与 esp_tinyusb 官方 test 一致）。
- **绕过 flex 用绝对定位**：LVGL flex 布局在居中上踩坑较多，UI 改用 `LV_ALIGN_TOP_MID` + y 偏移的绝对定位，可控性更好。
- **钟声合成**：正弦基频 + 二倍频泛音 + 指数衰减包络，模拟钟声自然衰减，比单调蜂鸣悦耳。
