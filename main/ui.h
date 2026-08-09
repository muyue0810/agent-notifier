#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 状态卡片类型 */
typedef enum {
    UI_STATE_DONE = 0,        // 绿 ✓  "Done"
    UI_STATE_WAIT,            // 黄 !  "Waiting"
    UI_STATE_PROCESSING,      // 蓝 ⚡ "Working"
    UI_STATE_APPROVAL,        // 橙 ★ "Approval"（需要用户批准）
    UI_STATE_OFFLINE,         // 灰 × "Offline"
} ui_state_t;

/* 触摸按钮事件回调 */
typedef enum {
    UI_BTN_MUTE = 0,          // 静音切换
    UI_BTN_ACK,               // 确认"我看到了"
    UI_BTN_MORE,              // 更多
} ui_btn_t;

typedef void (*ui_btn_cb_t)(ui_btn_t btn);

/**
 * @brief 初始化显示 + 触摸 + LVGL（Agent Notifier 布局）
 *        包含触摸初始化。
 */
esp_err_t ui_init(void);

/**
 * @brief 设置状态卡片
 * @param s    状态类型
 * @param src  来源（如 "Codex"），可为 NULL
 * @param msg  详情文本（如 "answer ready"），可为 NULL
 */
void ui_set_state(ui_state_t s, const char *src, const char *msg);

/**
 * @brief 在顶栏更新 USB 连接状态
 */
void ui_set_connected(bool connected);

/**
 * @brief 在顶栏更新 PC 服务在线状态（心跳超时则 false）
 *        区分 USB 枚举成功和服务在线：顶栏会显示 "USB"/"----"/"SVC"
 */
void ui_set_service_online(bool online);

/**
 * @brief 设置静音状态（影响底部 Mute 按钮显示）
 */
void ui_set_muted(bool muted);

/**
 * @brief 注册底部按钮触摸回调
 */
void ui_register_btn_cb(ui_btn_cb_t cb);

/* ---- 屏幕唤醒/息屏 ---- */

/**
 * @brief 强制唤醒屏幕（背光全亮，重置息屏计时）。
 *        新的重要通知（wait/approval/done）应调用。
 */
void ui_wake(void);

/**
 * @brief 设置息屏超时（秒）。0=永不息屏。
 *        超时后背光变暗。
 */
void ui_set_screen_timeout(uint32_t timeout_sec);

/**
 * @brief 设置屏幕亮度（0-100）
 */
void ui_set_brightness(int percent);

/* ---- History 页面 ---- */

/**
 * @brief 更新历史记录列表（PC 下发的最近几条）。
 *        items 是 NULL 结尾的字符串数组，每条 "src - msg"。
 *        最多显示前 5 条。
 */
void ui_set_history(const char *items[], int count);

/**
 * @brief 切换到历史页面（显示）/ 返回主页（隐藏）
 */
void ui_show_history(bool show);

#ifdef __cplusplus
}
#endif
