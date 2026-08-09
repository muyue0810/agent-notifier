#include "usb_cdc.h"

#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "tinyusb.h"
#include "tusb_cdc_acm.h"

static const char *TAG = "usb_cdc";

#define CDC_RX_CHUNK   512      // 单次轮询读取块
#define CDC_LINE_BUF_MAX 2048   // 行缓冲区（多行累积）

// 行组装缓冲：轮询任务里按字节拼，遇到 \n 成一行
static char     s_line_buf[CDC_LINE_BUF_MAX];
static size_t   s_line_pos = 0;
static usb_cdc_line_cb_t s_on_line = NULL;

// 发送互斥（避免多任务并发写）
static SemaphoreHandle_t s_tx_mtx = NULL;

// USB 字符串描述符
static const char *s_str_descr[5] = {
    "",                                  // 0: Language (auto)
    "Espressif",                         // 1: Manufacturer
    "ESP32-S3-BOX",                      // 2: Product
    "",                                  // 3: Serial (auto filled by MAC)
    "BOX Status Display",                // 4: CDC interface
};

// dtr 表示 PC 打开了串口（host 应用打开 /dev/ttyACMx）
static volatile bool s_dtr_set = false;

static void handle_line_state(int itf, cdcacm_event_t *event)
{
    (void)itf;
    if (event) {
        s_dtr_set = (event->line_state_changed_data.dtr != 0);
        ESP_LOGD(TAG, "line state: dtr=%d rts=%d",
                 event->line_state_changed_data.dtr,
                 event->line_state_changed_data.rts);
    }
}

// 轮询接收任务：按官方推荐，在独立任务里 read，而不是在 rx 回调里
static void rx_task(void *arg)
{
    (void)arg;
    uint8_t tmp[CDC_RX_CHUNK];
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10));   // 100Hz 轮询，足够响应且不占 CPU

        size_t rlen = 0;
        // 非阻塞读：有数据就处理
        esp_err_t ret = tinyusb_cdcacm_read(TINYUSB_CDC_ACM_0, tmp, sizeof(tmp), &rlen);
        if (ret != ESP_OK || rlen == 0) {
            continue;
        }
        for (size_t i = 0; i < rlen; i++) {
            char c = (char)tmp[i];
            if (c == '\n') {
                s_line_buf[s_line_pos] = '\0';
                if (s_on_line) {
                    s_on_line(s_line_buf, s_line_pos);
                }
                s_line_pos = 0;
            } else if (c != '\r') {
                if (s_line_pos < sizeof(s_line_buf) - 1) {
                    s_line_buf[s_line_pos++] = c;
                } else {
                    s_line_pos = 0;   // 行过长，丢弃重来
                }
            }
        }
    }
}

esp_err_t usb_cdc_init(usb_cdc_line_cb_t on_line)
{
    s_on_line = on_line;
    s_line_pos = 0;
    s_tx_mtx = xSemaphoreCreateMutex();
    if (!s_tx_mtx) {
        return ESP_ERR_NO_MEM;
    }

    const tinyusb_config_t tusb_cfg = {
        .device_descriptor = NULL,
        .string_descriptor = s_str_descr,
        .string_descriptor_count = sizeof(s_str_descr) / sizeof(s_str_descr[0]),
        .external_phy = false,
        .configuration_descriptor = NULL,
    };
    esp_err_t ret = tinyusb_driver_install(&tusb_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "tinyusb_driver_install failed: %s", esp_err_to_name(ret));
        return ret;
    }

    tinyusb_config_cdcacm_t acm_cfg = {
        .usb_dev = TINYUSB_USBDEV_0,
        .cdc_port = TINYUSB_CDC_ACM_0,
        .rx_unread_buf_sz = 256,
        .callback_rx = NULL,                                  // 不用回调读，改轮询
        .callback_rx_wanted_char = NULL,
        .callback_line_state_changed = &handle_line_state,
        .callback_line_coding_changed = NULL,
    };
    ret = tusb_cdc_acm_init(&acm_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "tusb_cdc_acm_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // 启动轮询接收任务
    BaseType_t ok = xTaskCreate(rx_task, "cdc_rx", 4 * 1024, NULL, 12, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "create rx_task failed");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "USB CDC initialized (polling rx task)");
    return ESP_OK;
}

esp_err_t usb_cdc_send_line(const char *fmt, ...)
{
    char buf[CDC_LINE_BUF_MAX];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) {
        return ESP_FAIL;
    }
    if ((size_t)n >= sizeof(buf)) {
        n = sizeof(buf) - 1;  // 截断
    }
    // 追加换行
    if ((size_t)n + 1 < sizeof(buf)) {
        buf[n] = '\n';
        n++;
    }

    if (s_tx_mtx && xSemaphoreTake(s_tx_mtx, pdMS_TO_TICKS(100)) == pdTRUE) {
        size_t flushed = 0;
        tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0, (const uint8_t *)buf, (uint32_t)n);
        tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, &flushed);
        xSemaphoreGive(s_tx_mtx);
        return ESP_OK;
    }
    return ESP_ERR_TIMEOUT;
}

bool usb_cdc_is_connected(void)
{
    return s_dtr_set;
}
