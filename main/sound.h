#pragma once

#include "esp_err.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 通知等级（决定响铃模式）*/
typedef enum {
    SOUND_LEVEL_SILENT = 0,    /* 不响 */
    SOUND_LEVEL_INFO,          /* 1 声钟声 */
    SOUND_LEVEL_ATTENTION,     /* 2 声 */
    SOUND_LEVEL_URGENT,        /* 3 声急促 */
} sound_level_t;

/**
 * @brief 初始化音频（ES8311 扬声器 codec）
 *        失败不阻塞主流程。
 */
esp_err_t sound_init(void);

/**
 * @brief 按等级播放提示音（带节流：2 秒内合并重复请求）。
 *        异步：内部排队到独立任务，立即返回。
 *        silent 等级直接忽略。
 * @param level 通知等级
 */
void sound_notify(sound_level_t level);

/**
 * @brief 播放一声钟声（兼容旧接口，等同 sound_notify(SOUND_LEVEL_INFO)）。
 */
void sound_beep(void);

/**
 * @brief 设置音量（0-100）
 */
void sound_set_volume(int volume);

#ifdef __cplusplus
}
#endif
