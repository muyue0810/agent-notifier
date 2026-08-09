/* Agent Notifier - ESP32-S3-BOX 通知器主程序
 *
 * 通过 USB CDC（虚拟串口）接收 PC 发来的 JSON 指令，
 * 在屏幕上显示 Agent 状态（Done/Waiting/Working/Offline），
 * 并播放钟声提示音。支持触摸按钮事件回传。
 */
#include "esp_log.h"
#include "esp_chip_info.h"
#include "bsp/esp-box.h"

#include "usb_cdc.h"
#include "ui.h"
#include "sound.h"
#include "protocol.h"

static const char *TAG = "main";

/* USB CDC 收到一行 -> 转交 protocol 解析 */
static void on_cdc_line(const char *line, size_t len)
{
    protocol_handle_line(line, len);
}

/* 监控 CDC 连接状态：PC 打开串口时更新顶栏指示灯 + 发送 hello */
static void monitor_task(void *arg)
{
    (void)arg;
    bool last = false;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(500));
        bool cur = usb_cdc_is_connected();
        if (cur != last) {
            ui_set_connected(cur);
            if (cur) {
                usb_cdc_send_line("{\"event\":\"status\",\"running\":true,\"app\":\"agent_notifier\",\"proto\":1}");
            }
            last = cur;
        }
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "=== Agent Notifier boot ===");
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    ESP_LOGI(TAG, "chip: %s rev %d, cores=%d", CONFIG_IDF_TARGET, chip.revision, chip.cores);

    /* 初始化顺序：I2C → 显示/UI → 音频 → USB CDC → 协议 → 监控 */
    ESP_ERROR_CHECK(bsp_i2c_init());            /* 音频 codec 需要 I2C */
    ESP_ERROR_CHECK(ui_init());                 /* 显示 + 触摸 + LVGL 界面 */

    if (sound_init() != ESP_OK) {               /* 提示音（失败不阻塞） */
        ESP_LOGW(TAG, "sound disabled, continuing without beep");
    }

    ESP_ERROR_CHECK(usb_cdc_init(on_cdc_line)); /* USB CDC 收发 */
    protocol_init();                            /* 注册按钮回调等 */
    protocol_start_heartbeat();                 /* 心跳监控任务 */
    xTaskCreate(monitor_task, "mon", 3 * 1024, NULL, 4, NULL);

    ESP_LOGI(TAG, "=== Ready. Waiting for PC commands via USB CDC ===");
    /* 主任务结束，工作由 LVGL 任务 / CDC rx 任务 / beep 任务接管 */
}
