#include "ui.h"

#include <string.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include "bsp/esp-box.h"
#include "lvgl.h"

static const char *TAG = "ui";

/* ---- LVGL v8 API（当前组件是 8.4.0）---- */
#ifndef LV_OPA_FULL
#define LV_OPA_FULL  255
#endif
#ifndef LV_OPA_TRANSP
#define LV_OPA_TRANSP 0
#endif
/* v8 用 _style_ 版本 */
#define ui_set_flex_main_place(obj, v)   lv_obj_set_style_flex_main_place(obj, v, 0)
#define ui_set_flex_cross_place(obj, v)  lv_obj_set_style_flex_cross_place(obj, v, 0)
#define ui_set_pad_hor(obj, v)           lv_obj_set_style_pad_hor(obj, v, 0)

/* ---- 设计配色（运行时用 lv_color_hex，因 C 不允许静态常量调函数）---- */
#define HEX_BG          0x111827
#define HEX_CARD_DONE   0x19a979
#define HEX_CARD_WAIT   0xed9007
#define HEX_CARD_PROC   0x2878d0
#define HEX_CARD_APRV   0xd97706   /* 橙（approval，比 wait 更醒目）*/
#define HEX_CARD_OFF    0x4b5563
#define HEX_ICON_BG     0xF5F7FA
#define HEX_TOOLBAR_BR  0x263143
#define HEX_TEXT_DIM    0x9ca6b5
#define HEX_TEXT        0xFFFFFF
#define HEX_CONN_OK     0x78e0b9

/* ---- 控件句柄 ---- */
static lv_obj_t *s_scr          = NULL;
static lv_obj_t *s_conn_dot     = NULL;
static lv_obj_t *s_conn_label   = NULL;
static lv_obj_t *s_card         = NULL;
static lv_obj_t *s_icon_circle  = NULL;
static lv_obj_t *s_icon_label   = NULL;
static lv_obj_t *s_state_label  = NULL;
static lv_obj_t *s_detail_label = NULL;
static lv_obj_t *s_btn_mute_ic  = NULL;
static lv_obj_t *s_btn_mute_lb  = NULL;
static lv_obj_t *s_history_layer = NULL;   /* 历史页面覆盖层 */
static lv_obj_t *s_history_list  = NULL;   /* 历史列表 */
static bool      s_history_visible = false;

static ui_state_t   s_cur_state = UI_STATE_OFFLINE;
static bool         s_muted     = false;
static ui_btn_cb_t  s_btn_cb    = NULL;
static bool         s_blink_on  = false;
static bool         s_usb_connected = false;
static bool         s_service_online = false;

/* ---- 息屏 ---- */
#define SCREEN_TIMEOUT_DEFAULT  30   /* 默认 30 秒息屏 */
#define BRIGHTNESS_ON   100
#define BRIGHTNESS_DIM  5
static TimerHandle_t s_screen_timer = NULL;
static uint32_t      s_screen_timeout = SCREEN_TIMEOUT_DEFAULT;
static bool          s_screen_awake = true;

/* 息屏定时器回调 */
static void screen_timer_cb(TimerHandle_t xTimer)
{
    (void)xTimer;
    if (s_screen_awake) {
        bsp_display_brightness_set(BRIGHTNESS_DIM);
        s_screen_awake = false;
    }
}

/* 状态属性（运行时填颜色）*/
struct state_attr {
    uint32_t    card_bg_hex;
    uint32_t    icon_fg_hex;
    const char *sym;
    const char *name;
};
static const struct state_attr s_attrs[] = {
    [UI_STATE_DONE]       = { HEX_CARD_DONE, HEX_CARD_DONE, LV_SYMBOL_OK,     "Done"     },
    [UI_STATE_WAIT]       = { HEX_CARD_WAIT, HEX_CARD_WAIT, "!",               "Waiting"  },
    [UI_STATE_PROCESSING] = { HEX_CARD_PROC, HEX_CARD_PROC, LV_SYMBOL_CHARGE,  "Working"  },
    [UI_STATE_APPROVAL]   = { HEX_CARD_APRV, HEX_CARD_APRV, LV_SYMBOL_WARNING, "Approval" },
    [UI_STATE_OFFLINE]    = { HEX_CARD_OFF,  HEX_CARD_OFF,  LV_SYMBOL_CLOSE,   "Offline"  },
};

/* ---- 底部按钮 ---- */
static lv_obj_t *create_tool_btn(lv_obj_t *parent, const char *icon,
                                 const char *label, ui_btn_t id)
{
    lv_obj_t *btn = lv_btn_create(parent);
    lv_obj_remove_style_all(btn);
    lv_obj_set_flex_flow(btn, LV_FLEX_FLOW_COLUMN);
    ui_set_flex_main_place(btn, LV_FLEX_ALIGN_CENTER);
    ui_set_flex_cross_place(btn, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_all(btn, 2, 0);
    lv_obj_set_style_bg_opa(btn, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_color(btn, lv_color_hex(0x1f2937), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(btn, LV_OPA_FULL, LV_STATE_PRESSED);
    lv_obj_set_flex_grow(btn, 1);
    lv_obj_set_height(btn, LV_PCT(100));
    lv_obj_clear_flag(btn, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *ic = lv_label_create(btn);
    lv_label_set_text(ic, icon);
    lv_obj_set_style_text_font(ic, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(ic, lv_color_hex(0xD2D8E2), 0);

    lv_obj_t *lb = lv_label_create(btn);
    lv_label_set_text(lb, label);
    lv_obj_set_style_text_font(lb, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lb, lv_color_hex(HEX_TEXT_DIM), 0);

    lv_obj_set_user_data(btn, (void *)(intptr_t)id);
    return btn;
}

static void tool_btn_event_cb(lv_event_t *e)
{
    lv_obj_t *btn = lv_event_get_target(e);
    while (btn && lv_obj_get_user_data(btn) == NULL) {
        btn = lv_obj_get_parent(btn);
    }
    if (!btn) return;
    ui_btn_t id = (ui_btn_t)(intptr_t)lv_obj_get_user_data(btn);

    /* More 按钮切换历史页面（UI 层处理）*/
    if (id == UI_BTN_MORE) {
        ui_show_history(!s_history_visible);
    }

    if (s_btn_cb) s_btn_cb(id);
}

esp_err_t ui_init(void)
{
    /* 用 BSP 高层 API（v3.0.5 内部正确处理 swap、触摸、亮度）*/
    bsp_display_cfg_t cfg = {
        .lvgl_port_cfg = ESP_LVGL_PORT_INIT_CONFIG(),
        .buffer_size = BSP_LCD_H_RES * CONFIG_BSP_LCD_DRAW_BUF_HEIGHT,
        .double_buffer = false,
        .flags = { .buff_dma = true, .buff_spiram = false },
    };
    lv_disp_t *disp = bsp_display_start_with_config(&cfg);
    if (!disp) {
        ESP_LOGE(TAG, "bsp_display_start_with_config failed");
        return ESP_FAIL;
    }
    bsp_display_backlight_on();

    /* ===== 构建 UI ===== */
    bsp_display_lock(0);
    s_scr = lv_disp_get_scr_act(disp);
    lv_obj_set_style_bg_color(s_scr, lv_color_hex(HEX_BG), 0);
    lv_obj_set_style_bg_opa(s_scr, LV_OPA_FULL, 0);
    lv_obj_set_flex_flow(s_scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(s_scr, 0, 0);
    lv_obj_set_style_pad_row(s_scr, 0, 0);
    lv_obj_set_style_pad_column(s_scr, 0, 0);
    lv_obj_set_style_border_width(s_scr, 0, 0);

    /* --- 顶栏 32px（全宽）--- */
    lv_obj_t *header = lv_obj_create(s_scr);
    lv_obj_remove_style_all(header);
    lv_obj_set_size(header, BSP_LCD_H_RES, 32);
    lv_obj_clear_flag(header, LV_OBJ_FLAG_SCROLLABLE);
    ui_set_pad_hor(header, 12);

    lv_obj_t *title = lv_label_create(header);
    lv_label_set_text(title, "Agent Notifier");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(HEX_TEXT), 0);
    lv_obj_align(title, LV_ALIGN_LEFT_MID, 0, 0);

    lv_obj_t *conn = lv_obj_create(header);
    lv_obj_remove_style_all(conn);
    lv_obj_set_size(conn, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_clear_flag(conn, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(conn, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(conn, 4, 0);
    lv_obj_align(conn, LV_ALIGN_RIGHT_MID, 0, 0);

    s_conn_dot = lv_obj_create(conn);
    lv_obj_remove_style_all(s_conn_dot);
    lv_obj_set_size(s_conn_dot, 6, 6);
    lv_obj_set_style_radius(s_conn_dot, 3, 0);
    lv_obj_set_style_bg_color(s_conn_dot, lv_color_hex(HEX_CONN_OK), 0);
    lv_obj_set_style_bg_opa(s_conn_dot, LV_OPA_FULL, 0);

    s_conn_label = lv_label_create(conn);
    lv_label_set_text(s_conn_label, "USB");
    lv_obj_set_style_text_font(s_conn_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_conn_label, lv_color_hex(HEX_CONN_OK), 0);

    /* --- 大状态卡片 ---
     * 显式尺寸：宽 302(320-18)，高 160(240-32-48)，紧贴 header 下方 */
    s_card = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_card);
    lv_obj_set_size(s_card, BSP_LCD_H_RES - 2 * 9, 160);
    lv_obj_align(s_card, LV_ALIGN_TOP_MID, 0, 32);   /* header 下方紧贴 */
    lv_obj_set_style_radius(s_card, 15, 0);
    lv_obj_set_style_pad_all(s_card, 0, 0);
    lv_obj_set_style_border_width(s_card, 0, 0);
    lv_obj_set_style_bg_opa(s_card, LV_OPA_FULL, 0);
    lv_obj_set_style_bg_color(s_card, lv_color_hex(HEX_CARD_OFF), 0);
    lv_obj_clear_flag(s_card, LV_OBJ_FLAG_SCROLLABLE);

    /* 大白圆图标：固定 54x54，居中偏上 */
    s_icon_circle = lv_obj_create(s_card);
    lv_obj_remove_style_all(s_icon_circle);
    lv_obj_set_size(s_icon_circle, 54, 54);
    lv_obj_set_style_radius(s_icon_circle, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_icon_circle, lv_color_hex(HEX_ICON_BG), 0);
    lv_obj_set_style_bg_opa(s_icon_circle, LV_OPA_FULL, 0);
    lv_obj_set_style_border_width(s_icon_circle, 0, 0);
    lv_obj_set_style_pad_all(s_icon_circle, 0, 0);
    lv_obj_clear_flag(s_icon_circle, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_align(s_icon_circle, LV_ALIGN_TOP_MID, 0, 20);

    s_icon_label = lv_label_create(s_icon_circle);
    lv_obj_set_style_text_font(s_icon_label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(s_icon_label, lv_color_hex(HEX_CARD_OFF), 0);
    lv_label_set_text(s_icon_label, LV_SYMBOL_CLOSE);
    lv_obj_align(s_icon_label, LV_ALIGN_CENTER, 0, 0);

    /* 状态名：圆下方居中 */
    s_state_label = lv_label_create(s_card);
    lv_obj_set_style_text_font(s_state_label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(s_state_label, lv_color_hex(HEX_TEXT), 0);
    lv_obj_set_style_text_align(s_state_label, LV_TEXT_ALIGN_CENTER, 0);   /* 文字在框内居中 */
    lv_obj_set_width(s_state_label, BSP_LCD_H_RES - 36);
    lv_label_set_text(s_state_label, "Offline");
    lv_obj_align(s_state_label, LV_ALIGN_TOP_MID, 0, 82);

    /* 详情：状态名下方居中 */
    s_detail_label = lv_label_create(s_card);
    lv_obj_set_style_text_font(s_detail_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_detail_label, lv_color_hex(HEX_TEXT), 0);
    lv_obj_set_style_text_opa(s_detail_label, 235, 0);
    lv_obj_set_style_text_align(s_detail_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(s_detail_label, BSP_LCD_H_RES - 36);
    lv_label_set_text(s_detail_label, "");
    lv_obj_align(s_detail_label, LV_ALIGN_TOP_MID, 0, 116);

    /* --- 底部工具栏 48px --- */
    lv_obj_t *toolbar = lv_obj_create(s_scr);
    lv_obj_remove_style_all(toolbar);
    lv_obj_set_size(toolbar, BSP_LCD_H_RES, 48);
    lv_obj_clear_flag(toolbar, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(toolbar, 0, 0);
    lv_obj_set_style_pad_column(toolbar, 0, 0);
    lv_obj_set_style_border_width(toolbar, 1, 0);
    lv_obj_set_style_border_side(toolbar, LV_BORDER_SIDE_TOP, 0);
    lv_obj_set_style_border_color(toolbar, lv_color_hex(HEX_TOOLBAR_BR), 0);
    lv_obj_set_flex_flow(toolbar, LV_FLEX_FLOW_ROW);

    lv_obj_t *b_mute = create_tool_btn(toolbar, LV_SYMBOL_BELL, "Mute", UI_BTN_MUTE);
    lv_obj_t *b_ack  = create_tool_btn(toolbar, LV_SYMBOL_OK,   "Ack",  UI_BTN_ACK);
    lv_obj_t *b_more = create_tool_btn(toolbar, LV_SYMBOL_REFRESH, "More", UI_BTN_MORE);
    s_btn_mute_ic = lv_obj_get_child(b_mute, 0);
    s_btn_mute_lb = lv_obj_get_child(b_mute, 1);
    /* 按钮间分隔线（覆盖在按钮之上）*/
    for (int i = 0; i < 2; i++) {
        lv_obj_t *sep = lv_obj_create(toolbar);
        lv_obj_remove_style_all(sep);
        lv_obj_set_width(sep, 1);
        lv_obj_set_height(sep, LV_PCT(80));
        lv_obj_align(sep, LV_ALIGN_TOP_LEFT, (i + 1) * (BSP_LCD_H_RES / 3), 4);
        lv_obj_set_style_bg_color(sep, lv_color_hex(HEX_TOOLBAR_BR), 0);
        lv_obj_set_style_bg_opa(sep, LV_OPA_FULL, 0);
        lv_obj_clear_flag(sep, LV_OBJ_FLAG_SCROLLABLE);
    }
    lv_obj_add_event_cb(b_mute, tool_btn_event_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(b_ack,  tool_btn_event_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(b_more, tool_btn_event_cb, LV_EVENT_CLICKED, NULL);

    /* --- 历史页面覆盖层（默认隐藏，More 按钮切换）--- */
    s_history_layer = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_history_layer);
    lv_obj_set_size(s_history_layer, BSP_LCD_H_RES, BSP_LCD_V_RES);
    lv_obj_set_style_bg_color(s_history_layer, lv_color_hex(HEX_BG), 0);
    lv_obj_set_style_bg_opa(s_history_layer, LV_OPA_FULL, 0);
    lv_obj_clear_flag(s_history_layer, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(s_history_layer, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(s_history_layer, 10, 0);
    lv_obj_add_flag(s_history_layer, LV_OBJ_FLAG_HIDDEN);   /* 默认隐藏 */

    lv_obj_t *h_title = lv_label_create(s_history_layer);
    lv_label_set_text(h_title, "History");
    lv_obj_set_style_text_font(h_title, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(h_title, lv_color_hex(HEX_TEXT), 0);
    lv_obj_set_style_pad_bottom(h_title, 8, 0);

    s_history_list = lv_label_create(s_history_layer);
    lv_obj_set_style_text_font(s_history_list, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_history_list, lv_color_hex(0xCCCCCC), 0);
    lv_obj_set_width(s_history_list, BSP_LCD_H_RES - 20);
    lv_label_set_long_mode(s_history_list, LV_LABEL_LONG_WRAP);
    lv_label_set_text(s_history_list, "(no history yet)\n\nTap More to go back.");

    bsp_display_unlock();

    ui_set_state(UI_STATE_OFFLINE, NULL, NULL);
    ui_set_connected(false);

    /* 创建并启动息屏定时器 */
    s_screen_timer = xTimerCreate("screen", pdMS_TO_TICKS(s_screen_timeout * 1000),
                                  pdFALSE, NULL, screen_timer_cb);
    if (s_screen_timer) {
        xTimerStart(s_screen_timer, 0);
    }

    ESP_LOGI(TAG, "UI initialized (%dx%d)", BSP_LCD_H_RES, BSP_LCD_V_RES);
    return ESP_OK;
}

/* 卡片闪烁动画回调：改变背景透明度 */
static void blink_anim_cb(void *var, int32_t v)
{
    lv_obj_set_style_bg_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

/* 启动卡片闪烁（Waiting/Processing 状态）*/
static void card_blink_start(void)
{
    if (s_blink_on) return;
    s_blink_on = true;
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_card);
    lv_anim_set_values(&a, LV_OPA_50, LV_OPA_FULL);   // 128 ↔ 255
    lv_anim_set_time(&a, 500);
    lv_anim_set_playback_time(&a, 500);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_exec_cb(&a, blink_anim_cb);
    if (bsp_display_lock(0)) {
        lv_anim_start(&a);
        bsp_display_unlock();
    }
}

/* 停止卡片闪烁，恢复完全不透明 */
static void card_blink_stop(void)
{
    if (!s_blink_on) return;
    s_blink_on = false;
    if (bsp_display_lock(0)) {
        lv_anim_del(s_card, blink_anim_cb);
        lv_obj_set_style_bg_opa(s_card, LV_OPA_FULL, 0);
        bsp_display_unlock();
    }
}

/* 唤醒屏幕 + 重置息屏计时 */
void ui_wake(void)
{
    if (!s_screen_awake) {
        bsp_display_brightness_set(BRIGHTNESS_ON);
        s_screen_awake = true;
    }
    if (s_screen_timer && s_screen_timeout > 0) {
        xTimerStart(s_screen_timer, 0);   /* 重启计时 */
    }
}

void ui_set_screen_timeout(uint32_t timeout_sec)
{
    s_screen_timeout = timeout_sec;
    if (s_screen_timer) {
        if (timeout_sec == 0) {
            xTimerStop(s_screen_timer, 0);
        } else {
            xTimerChangePeriod(s_screen_timer, pdMS_TO_TICKS(timeout_sec * 1000), 0);
            xTimerStart(s_screen_timer, 0);   /* 确保启动 */
        }
    }
}

void ui_set_brightness(int percent)
{
    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;
    bsp_display_brightness_set(percent);
    if (percent > BRIGHTNESS_DIM) {
        s_screen_awake = true;
    }
}

void ui_set_state(ui_state_t s, const char *src, const char *msg)
{
    if ((int)s < 0 || s >= sizeof(s_attrs) / sizeof(s_attrs[0])) s = UI_STATE_OFFLINE;
    s_cur_state = s;
    const struct state_attr *a = &s_attrs[s];

    if (bsp_display_lock(0)) {
        lv_obj_set_style_bg_color(s_card, lv_color_hex(a->card_bg_hex), 0);
        lv_label_set_text(s_icon_label, a->sym);
        lv_obj_set_style_text_color(s_icon_label, lv_color_hex(a->icon_fg_hex), 0);
        lv_label_set_text(s_state_label, a->name);

        if (src || msg) {
            char buf[64];
            if (src && msg) snprintf(buf, sizeof(buf), "%s - %s", src, msg);
            else snprintf(buf, sizeof(buf), "%s", src ? src : (msg ? msg : ""));
            lv_label_set_text(s_detail_label, buf);
        } else {
            lv_label_set_text(s_detail_label, "");
        }
        bsp_display_unlock();
    }

    /* 屏幕唤醒：wait/approval/done 强制唤醒；processing/offline 不主动唤醒 */
    if (s == UI_STATE_WAIT || s == UI_STATE_APPROVAL || s == UI_STATE_DONE) {
        ui_wake();
    }

    /* 闪烁提醒；Done/Offline 停止闪烁 */
    if (s == UI_STATE_WAIT || s == UI_STATE_PROCESSING || s == UI_STATE_APPROVAL) {
        card_blink_start();
    } else {
        card_blink_stop();
    }
}

/* 顶栏指示：综合 USB 连接和服务在线状态
 * USB+服务在线 = 绿 "SVC"；仅 USB = 黄 "USB"；无 = 灰 "----" */
static void refresh_conn_indicator(void)
{
    bool ok = s_usb_connected && s_service_online;
    uint32_t color = ok ? HEX_CONN_OK : (s_usb_connected ? HEX_CARD_WAIT : 0x6b7280);
    const char *txt = ok ? "SVC" : (s_usb_connected ? "USB" : "----");
    if (bsp_display_lock(0)) {
        lv_obj_set_style_bg_color(s_conn_dot, lv_color_hex(color), 0);
        lv_label_set_text(s_conn_label, txt);
        lv_obj_set_style_text_color(s_conn_label, lv_color_hex(color), 0);
        bsp_display_unlock();
    }
}

void ui_set_connected(bool connected)
{
    s_usb_connected = connected;
    refresh_conn_indicator();
}

void ui_set_service_online(bool online)
{
    s_service_online = online;
    refresh_conn_indicator();
}

void ui_set_muted(bool muted)
{
    s_muted = muted;
    if (bsp_display_lock(0)) {
        lv_label_set_text(s_btn_mute_lb, muted ? "Muted" : "Mute");
        bsp_display_unlock();
    }
}

void ui_set_history(const char *items[], int count)
{
    if (count < 0) count = 0;
    if (count > 5) count = 5;

    char buf[400];
    int pos = 0;
    if (count == 0) {
        snprintf(buf, sizeof(buf), "(no history yet)");
    } else {
        for (int i = 0; i < count && pos < (int)sizeof(buf) - 40; i++) {
            const char *it = items[i] ? items[i] : "";
            int n = snprintf(buf + pos, sizeof(buf) - pos, "%d. %s\n", i + 1, it);
            if (n < 0) break;
            pos += n;
        }
    }
    if (bsp_display_lock(0)) {
        lv_label_set_text(s_history_list, buf);
        bsp_display_unlock();
    }
}

void ui_show_history(bool show)
{
    s_history_visible = show;
    if (bsp_display_lock(0)) {
        if (show) {
            lv_obj_clear_flag(s_history_layer, LV_OBJ_FLAG_HIDDEN);
            lv_obj_move_foreground(s_history_layer);
        } else {
            lv_obj_add_flag(s_history_layer, LV_OBJ_FLAG_HIDDEN);
        }
        bsp_display_unlock();
    }
}

void ui_register_btn_cb(ui_btn_cb_t cb)
{
    s_btn_cb = cb;
}

