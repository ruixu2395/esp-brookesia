// SPDX-License-Identifier: Apache-2.0
#pragma once
using TaskHandle_t = void *;
inline int xTaskCreate(void (*)(void *), const char *, unsigned, void *, unsigned, TaskHandle_t *out)
{
    *out = reinterpret_cast<void *>(1);
    return 1;
}
inline void vTaskDelay(int) {}
