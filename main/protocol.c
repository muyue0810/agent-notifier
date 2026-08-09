/* protocol.c - JSON 协议解析与派发
 *
 * 协议版本 1（proto:1）。
 * 支持：state（带 event_id 去重）、beep、mute、ack、ping、heartbeat。
 */
#include "protocol.h"

#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "cJSON.h"
#include "ui.h"
#include "sound.h"
#include "usb_cdc.h"

static const char *TAG = "proto";

#define PROTOCOL_VERSION  1

/* ---- 静音状态 ---- */
static bool s_muted = false;

/* ---- 通知去重：记录最近一次 event_id ---- */
static char  s_last_event_id[40] = {0};
static bool  s_has_last_event = false;

/* ---- heartbeat 看门狗 ---- */
#define HEARTBEAT_TIMEOUT_MS  15000   /* 15 秒无心跳判离线 */
static int64_t s_last_pc_ts = 0;       /* esp_timerGetTime() 单位 us */
static bool    s_service_online = false;

/* ---- 当前通知 event_id（Ack 时回传）---- */
static char  s_cur_event_id[40] = {0};
static bool  s_has_cur_event = false;

/* 判断两个 event_id 是否相同（都非空时比较）*/
static bool event_id_eq(const char *a, const char *b)
{
    if (!a || !a[0] || !b || !b[0]) return false;
    return strcmp(a, b) == 0;
}

/* ---- UI 按钮回调：回传事件，Ack 带上 event_id ---- */
static void on_ui_btn(ui_btn_t btn)
{
    const char *name = "unknown";
    const char *act = NULL;
    switch (btn) {
        case UI_BTN_MUTE:
            name = "mute";
            s_muted = !s_muted;
            ui_set_muted(s_muted);
            break;
        case UI_BTN_ACK:
            name = "ack";
            act = s_has_cur_event ? s_cur_event_id : NULL;
            break;
        case UI_BTN_MORE:
            name = "more";
            break;
    }
    if (act) {
        usb_cdc_send_line("{\"event\":\"btn\",\"id\":\"%s\",\"event_id\":\"%s\",\"muted\":%s}",
                         name, act, s_muted ? "true" : "false");
    } else {
        usb_cdc_send_line("{\"event\":\"btn\",\"id\":\"%s\",\"muted\":%s}",
                         name, s_muted ? "true" : "false");
    }
}

static void send_ack(const char *status, const char *detail)
{
    if (detail) {
        usb_cdc_send_line("{\"ack\":\"%s\",\"detail\":%s}", status, detail);
    } else {
        usb_cdc_send_line("{\"ack\":\"%s\"}", status);
    }
}

void protocol_init(void)
{
    ui_register_btn_cb(on_ui_btn);
    s_last_pc_ts = esp_timer_get_time();
}

/* 更新 PC 通信时间戳（任何来自 PC 的消息都算心跳）*/
static void touch_heartbeat(void)
{
    s_last_pc_ts = esp_timer_get_time();
    if (!s_service_online) {
        s_service_online = true;
    }
}

/* heartbeat 监控任务：超时判离线 */
static void heartbeat_task(void *arg)
{
    (void)arg;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(2000));
        if (s_service_online) {
            int64_t elapsed_ms = (esp_timer_get_time() - s_last_pc_ts) / 1000;
            if (elapsed_ms > HEARTBEAT_TIMEOUT_MS) {
                ESP_LOGW(TAG, "heartbeat timeout (%lld ms), offline", (long long)elapsed_ms);
                s_service_online = false;
                ui_set_service_online(false);
                ui_set_state(UI_STATE_OFFLINE, NULL, NULL);
            }
        }
    }
}

/* 启动 heartbeat 监控（由 main 调用）*/
void protocol_start_heartbeat(void)
{
    xTaskCreate(heartbeat_task, "hb", 3 * 1024, NULL, 4, NULL);
}

void protocol_handle_line(const char *line, size_t len)
{
    if (len == 0 || !line) return;

    const char *p = line;
    while (*p && (*p == ' ' || *p == '\t')) p++;
    if (*p == '\0') return;

    /* 任何来自 PC 的完整消息都算心跳 */
    touch_heartbeat();

    cJSON *root = cJSON_ParseWithLength(line, len);
    if (!root) {
        ESP_LOGW(TAG, "JSON parse failed: %.*s", (int)len, line);
        usb_cdc_send_line("{\"ack\":\"bad_json\",\"err\":\"parse_failed\"}");
        return;
    }

    cJSON *jt = cJSON_GetObjectItem(root, "t");
    if (!cJSON_IsString(jt)) {
        send_ack("bad_request", "\"missing or invalid 't'\"");
        cJSON_Delete(root);
        return;
    }
    const char *type = jt->valuestring;

    if (strcmp(type, "state") == 0) {
        cJSON *js    = cJSON_GetObjectItem(root, "s");
        cJSON *jsrc  = cJSON_GetObjectItem(root, "src");
        cJSON *jmsg  = cJSON_GetObjectItem(root, "msg");
        cJSON *jeid  = cJSON_GetObjectItem(root, "event_id");
        cJSON *jlvl  = cJSON_GetObjectItem(root, "level");   /* info/attention/urgent/silent */

        ui_state_t s = UI_STATE_OFFLINE;
        if (cJSON_IsString(js)) {
            const char *v = js->valuestring;
            if      (strcmp(v, "done") == 0)       s = UI_STATE_DONE;
            else if (strcmp(v, "wait") == 0)       s = UI_STATE_WAIT;
            else if (strcmp(v, "processing")==0)   s = UI_STATE_PROCESSING;
            else if (strcmp(v, "approval") == 0)   s = UI_STATE_APPROVAL;
            else if (strcmp(v, "offline") == 0)    s = UI_STATE_OFFLINE;
        }
        const char *src = cJSON_IsString(jsrc) ? jsrc->valuestring : NULL;
        const char *msg = cJSON_IsString(jmsg) ? jmsg->valuestring : NULL;
        const char *eid = cJSON_IsString(jeid) ? jeid->valuestring : NULL;

        /* 去重：相同 event_id 重复收到，只更新 UI 不重复响 */
        bool is_dup = false;
        bool should_beep = false;
        if (eid && eid[0]) {
            if (s_has_last_event && event_id_eq(eid, s_last_event_id)) {
                is_dup = true;
            } else {
                strncpy(s_last_event_id, eid, sizeof(s_last_event_id) - 1);
                s_has_last_event = true;
            }
        }

        /* 记录当前 event_id（供 Ack 回传）*/
        if (eid && eid[0]) {
            strncpy(s_cur_event_id, eid, sizeof(s_cur_event_id) - 1);
            s_has_cur_event = true;
        } else {
            s_has_cur_event = false;
        }

        /* 根据等级决定提示强度（非重复时）*/
        sound_level_t snd_lvl = SOUND_LEVEL_INFO;
        const char *lvl = cJSON_IsString(jlvl) ? jlvl->valuestring : "info";
        if      (strcmp(lvl, "silent") == 0)    snd_lvl = SOUND_LEVEL_SILENT;
        else if (strcmp(lvl, "info") == 0)      snd_lvl = SOUND_LEVEL_INFO;
        else if (strcmp(lvl, "attention")==0)   snd_lvl = SOUND_LEVEL_ATTENTION;
        else if (strcmp(lvl, "urgent") == 0)    snd_lvl = SOUND_LEVEL_URGENT;

        ui_set_state(s, src, msg);

        if (!is_dup && !s_muted && snd_lvl > SOUND_LEVEL_SILENT) {
            sound_notify(snd_lvl);
        }

        send_ack("ok", is_dup ? "\"dup\"" : NULL);

    } else if (strcmp(type, "beep") == 0) {
        if (!s_muted) sound_beep();
        send_ack("ok", NULL);

    } else if (strcmp(type, "mute") == 0) {
        cJSON *jon = cJSON_GetObjectItem(root, "on");
        if (cJSON_IsBool(jon)) {
            s_muted = cJSON_IsTrue(jon);
        } else {
            s_muted = !s_muted;
        }
        ui_set_muted(s_muted);
        send_ack("ok", s_muted ? "\"muted\"" : "\"unmuted\"");

    } else if (strcmp(type, "ping") == 0) {
        /* ping 也是心跳 */
        send_ack("pong", NULL);

    } else if (strcmp(type, "history") == 0) {
        /* PC 下发历史记录：{"t":"history","items":["src - msg", ...]} */
        cJSON *jitems = cJSON_GetObjectItem(root, "items");
        const char *items[5] = {0};
        int count = 0;
        if (cJSON_IsArray(jitems)) {
            int n = cJSON_GetArraySize(jitems);
            if (n > 5) n = 5;
            for (int i = 0; i < n; i++) {
                cJSON *ji = cJSON_GetArrayItem(jitems, i);
                items[i] = cJSON_IsString(ji) ? ji->valuestring : "";
                count++;
            }
        }
        ui_set_history(items, count);
        send_ack("ok", NULL);

    } else if (strcmp(type, "settings") == 0) {
        /* 设置项：{"t":"settings","volume":90,"brightness":100,
         *          "sound":true,"screen_timeout":30}
         *  所有字段可选，只更新提供的。*/
        cJSON *jvol = cJSON_GetObjectItem(root, "volume");
        cJSON *jbri = cJSON_GetObjectItem(root, "brightness");
        cJSON *jsnd = cJSON_GetObjectItem(root, "sound");
        cJSON *jto  = cJSON_GetObjectItem(root, "screen_timeout");

        if (cJSON_IsNumber(jvol)) {
            int v = jvol->valueint;
            if (v < 0) v = 0;
            if (v > 100) v = 100;
            sound_set_volume(v);
        }
        if (cJSON_IsNumber(jbri)) {
            int b = jbri->valueint;
            if (b < 0) b = 0;
            if (b > 100) b = 100;
            ui_set_brightness(b);
        }
        if (cJSON_IsBool(jsnd)) {
            s_muted = !cJSON_IsTrue(jsnd);
            ui_set_muted(s_muted);
        }
        if (cJSON_IsNumber(jto)) {
            ui_set_screen_timeout((uint32_t)jto->valueint);
        }
        send_ack("ok", NULL);

    } else if (strcmp(type, "hello") == 0) {
        /* 握手：报告协议版本 */
        cJSON *jproto = cJSON_GetObjectItem(root, "proto");
        int pc_proto = cJSON_IsNumber(jproto) ? jproto->valueint : 0;
        ESP_LOGI(TAG, "PC hello, proto=%d", pc_proto);
        ui_set_service_online(true);
        usb_cdc_send_line("{\"event\":\"hello\",\"proto\":%d,\"app\":\"agent_notifier\"}",
                         PROTOCOL_VERSION);
        send_ack("ok", NULL);

    } else {
        ESP_LOGW(TAG, "unknown type: %s", type);
        send_ack("unknown_type", NULL);
    }

    cJSON_Delete(root);
}
