#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdarg.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief USB CDC 接收回调类型
 *        收到完整一行（以 \n 结尾）时调用，line 不含结尾换行符，已 null 结尾。
 */
typedef void (*usb_cdc_line_cb_t)(const char *line, size_t len);

/**
 * @brief 初始化 USB CDC（TinyUSB + CDC ACM）
 *        安装 TinyUSB 驱动、注册 CDC ACM、启动收发。
 * @param on_line 收到完整行时的回调（在 USB 任务上下文调用，不可阻塞）
 * @return ESP_OK 或错误码
 */
esp_err_t usb_cdc_init(usb_cdc_line_cb_t on_line);

/**
 * @brief 向 PC 发送一行文本（自动追加 \n）。
 * @return ESP_OK 或错误码
 */
esp_err_t usb_cdc_send_line(const char *fmt, ...);

/**
 * @brief CDC 是否已就绪（PC 打开了端口）
 */
bool usb_cdc_is_connected(void);

#ifdef __cplusplus
}
#endif
