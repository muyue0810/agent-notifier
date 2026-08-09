#pragma once

#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 处理来自 PC 的一行 JSON 指令。
 *        解析后派发到 UI / sound 模块。容错：格式错误时忽略并回送错误。
 * @param line 单行 JSON 字符串（不含换行）
 * @param len  长度
 */
void protocol_handle_line(const char *line, size_t len);

/**
 * @brief 注册 UI 按钮回调等初始化
 */
void protocol_init(void);

/**
 * @brief 启动 heartbeat 监控任务（超时判离线）
 */
void protocol_start_heartbeat(void);

#ifdef __cplusplus
}
#endif
