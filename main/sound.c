/* sound.c - ES8311 codec 提示音
 *
 * 柔和钟声（880Hz 基频 + 1760Hz 泛音，指数衰减）。
 * 按通知等级响不同次数：info=1 / attention=2 / urgent=3 急促。
 * 2 秒节流：短时间多次请求合并为一次（取最高等级）。
 */
#include "sound.h"

#include <math.h>
#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "bsp/esp-box.h"
#include "esp_codec_dev.h"

static const char *TAG = "sound";

#define SAMPLE_RATE     22050
#define BEEP_MS         500
#define BEEP_SAMPLES    (SAMPLE_RATE * BEEP_MS / 1000)
#define QUEUE_LEN       8
#define THROTTLE_MS     2000    /* 节流窗口 */

static esp_codec_dev_handle_t s_spk = NULL;
static QueueHandle_t s_queue = NULL;
static bool s_enabled = false;

/* 预生成钟声 PCM */
static int16_t s_beep_pcm[BEEP_SAMPLES];

static void gen_beep(void)
{
    const float f1 = 880.0f;
    const float f2 = 1760.0f;
    const float decay = 5.5f;
    const float amp = 26000.0f;
    int attack = SAMPLE_RATE / 200;
    for (int i = 0; i < BEEP_SAMPLES; i++) {
        float t = (float)i / SAMPLE_RATE;
        float env = expf(-decay * t);
        if (i < attack) env *= (float)i / attack;
        float wave = sinf(2.0f * M_PI * f1 * t)
                   + 0.4f * sinf(2.0f * M_PI * f2 * t) * expf(-decay * 2.0f * t);
        s_beep_pcm[i] = (int16_t)(wave * amp * env * 0.7f);
    }
}

/* 播放一声钟声（阻塞，在 beep_task 调用）*/
static void play_one_beep(void)
{
    esp_codec_dev_sample_info_t fs = {
        .sample_rate = SAMPLE_RATE,
        .channel = 1,
        .bits_per_sample = 16,
    };
    if (esp_codec_dev_open(s_spk, &fs) != 0) return;
    const int chunk = 1024;
    for (int off = 0; off < BEEP_SAMPLES; off += chunk) {
        int n = chunk;
        if (off + n > BEEP_SAMPLES) n = BEEP_SAMPLES - off;
        esp_codec_dev_write(s_spk, s_beep_pcm + off, n * sizeof(int16_t));
    }
    esp_codec_dev_close(s_spk);
}

/* beep 任务：按队列里的等级响对应次数 */
static void beep_task(void *arg)
{
    (void)arg;
    int lvl;
    while (true) {
        if (xQueueReceive(s_queue, &lvl, portMAX_DELAY) != pdTRUE) continue;
        if (!s_enabled || !s_spk || lvl <= SOUND_LEVEL_SILENT) continue;

        int times = 1;
        int gap_ms = 200;       /* 两声间隔 */
        if (lvl == SOUND_LEVEL_INFO)          { times = 1; gap_ms = 0; }
        else if (lvl == SOUND_LEVEL_ATTENTION){ times = 2; gap_ms = 150; }
        else if (lvl == SOUND_LEVEL_URGENT)   { times = 3; gap_ms = 100; }

        for (int i = 0; i < times; i++) {
            play_one_beep();
            if (i < times - 1) vTaskDelay(pdMS_TO_TICKS(gap_ms));
        }
    }
}

esp_err_t sound_init(void)
{
    gen_beep();
    s_spk = bsp_audio_codec_speaker_init();
    if (!s_spk) {
        ESP_LOGW(TAG, "speaker codec init failed");
        return ESP_FAIL;
    }
    esp_codec_dev_set_out_vol(s_spk, 90);

    s_queue = xQueueCreate(QUEUE_LEN, sizeof(int));
    if (!s_queue) return ESP_ERR_NO_MEM;
    if (xTaskCreate(beep_task, "beep", 4 * 1024, NULL, 5, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    s_enabled = true;
    ESP_LOGI(TAG, "sound initialized");
    return ESP_OK;
}

/* 节流：记录上次入队时间和等级，窗口内取最高等级合并 */
static int64_t s_last_notify_us = 0;
static int      s_throttle_max_level = SOUND_LEVEL_SILENT;

void sound_notify(sound_level_t level)
{
    if (!s_enabled || !s_queue) return;
    if (level <= SOUND_LEVEL_SILENT) return;   /* silent 直接忽略 */

    int64_t now = esp_timer_get_time();        /* us */
    int64_t elapsed_ms = (now - s_last_notify_us) / 1000;

    if (elapsed_ms < THROTTLE_MS) {
        /* 节流窗口内：记录最高等级，不重复入队 */
        if ((int)level > s_throttle_max_level) {
            s_throttle_max_level = (int)level;
        }
        return;
    }
    /* 窗口外：发出（含窗口内累积的最高等级）*/
    int emit = (int)level;
    if (s_throttle_max_level > emit) emit = s_throttle_max_level;
    s_throttle_max_level = SOUND_LEVEL_SILENT;
    s_last_notify_us = now;

    xQueueSend(s_queue, &emit, 0);
}

void sound_beep(void)
{
    sound_notify(SOUND_LEVEL_INFO);
}

void sound_set_volume(int volume)
{
    if (volume < 0) volume = 0;
    if (volume > 100) volume = 100;
    if (s_spk) {
        esp_codec_dev_set_out_vol(s_spk, volume);
    }
}
