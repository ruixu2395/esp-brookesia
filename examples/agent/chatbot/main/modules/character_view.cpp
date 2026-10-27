/* SPDX-License-Identifier: Apache-2.0 */
#include "character_view.hpp"

#if CONFIG_EXAMPLE_DAFEIYU_CLOSEUP_ENABLE
#include <cmath>
#include <mutex>
#include <vector>
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "brookesia/mcp_utils/mcp_utils.hpp"
#include "brookesia/service_helper/agent/xiaozhi.hpp"
#include "brookesia/service_helper/expression/emote.hpp"
#include "character_view_state.hpp"

namespace {
using namespace esp_brookesia;
using Emote = service::helper::ExpressionEmote;
using XiaoZhi = service::helper::XiaoZhi;
constexpr const char *TAG = "CharacterView";
std::mutex view_mutex;
character_view::State state;
TaskHandle_t worker_handle = nullptr;
int64_t now_ms() { return esp_timer_get_time() / 1000; }

void worker(void *)
{
    while (true) {
        bool success;
        {
            std::lock_guard<std::mutex> lock(view_mutex);
            success = state.tick(now_ms(), [](const character_view::Render &command) {
                // All expression sources use this worker. Keep the emergency
                // dialog from covering the camera move or retaining an old view.
                // Stopping is best effort: esp_emote reports an error when no
                // dialog is active, and that must not block the character view
                // (it left the screen stuck on whatever was rendered last).
                if (auto stopped = Emote::call_function_sync(Emote::FunctionId::StopAnimation); !stopped) {
                    ESP_LOGW(TAG, "StopAnimation: %s", stopped.error().c_str());
                }
                if (command.first_frame >= 0) {
                    auto segment = Emote::call_function_sync(Emote::FunctionId::SetEmojiSegment, command.emoji,
                                                            double(command.first_frame), double(command.last_frame));
                    if (!segment) {
                        ESP_LOGW(TAG, "SetEmojiSegment(%s, %d..%d): %s", command.emoji.c_str(),
                                 command.first_frame, command.last_frame, segment.error().c_str());
                        return false;
                    }
                    return true;
                }
                if (auto emoji = Emote::call_function_sync(Emote::FunctionId::SetEmoji, command.emoji); !emoji) {
                    ESP_LOGW(TAG, "SetEmoji(%s): %s", command.emoji.c_str(), emoji.error().c_str());
                    return false;
                }
                if (!command.animation.empty()) {
                    auto inserted = Emote::call_function_sync(Emote::FunctionId::InsertAnimation,
                                                              command.animation, double(command.animation_ms));
                    if (!inserted) {
                        ESP_LOGW(TAG, "InsertAnimation(%s): %s", command.animation.c_str(),
                                 inserted.error().c_str());
                        return false;
                    }
                }
                return true;
            });
        }
        if (!success) ESP_LOGW(TAG, "Could not apply character view; check the flashed animation assets");
        vTaskDelay(pdMS_TO_TICKS(success ? 50 : 1000));
    }
}

service::FunctionResult change_view(bool face)
{
    const bool accepted = character_view::set_face(face);
    return service::FunctionResult{
        .success = accepted,
        .error_message = accepted ? "" : "Character view worker is unavailable",
        .data = service::FunctionValue(boost::json::object{
            {"accepted", accepted}, {"view", face ? "face" : "full"},
            {"transition_ms", character_view::TRANSITION_MS},
        }),
    };
}
service::FunctionResult enter_face(service::FunctionParameterMap &&) { return change_view(true); }
service::FunctionResult exit_face(service::FunctionParameterMap &&) { return change_view(false); }
} // namespace

bool character_view::start()
{
    std::lock_guard<std::mutex> lock(view_mutex);
    if (worker_handle) return true;
    return xTaskCreate(worker, "CharacterView", 6144, nullptr, 2, &worker_handle) == pdPASS;
}

bool character_view::set_face(bool enabled)
{
    std::lock_guard<std::mutex> lock(view_mutex);
    if (!worker_handle) return false;
    state.set_face(enabled);
    return true;
}

bool character_view::handle_voice_command(const std::string &text)
{
    auto command = voice_command(text);
    return command.has_value() && set_face(*command);
}

bool character_view::set_emoji(const char *name)
{
    std::lock_guard<std::mutex> lock(view_mutex);
    if (!worker_handle) return Emote::call_function_async(Emote::FunctionId::SetEmoji, name);
    state.set_emoji(name);
    return true;
}

bool character_view::insert_animation(const std::string &name, double duration_ms)
{
    if (!std::isfinite(duration_ms) || duration_ms < 0 || duration_ms > 3600000) return false;
    std::lock_guard<std::mutex> lock(view_mutex);
    if (!worker_handle) return Emote::call_function_async(Emote::FunctionId::InsertAnimation, name, duration_ms);
    state.insert_animation(name, static_cast<int64_t>(duration_ms), now_ms());
    return true;
}

bool character_view::register_mcp_tools()
{
    std::vector<mcp_utils::CustomTool> tools;
    tools.push_back({
        .schema = {
            .name = "Dafeiyu.EnterCloseUp",
            .description = "进入贴脸模式：将屏幕上的大肥鱼从全身逐渐拉近到脸部特写。"
                           "用户说进入/开启/打开贴脸模式时调用。重复调用保持当前视角，不重播过渡。",
            .require_scheduler = false,
        },
        .callback = enter_face,
    });
    tools.push_back({
        .schema = {
            .name = "Dafeiyu.ExitCloseUp",
            .description = "退出贴脸模式：将大肥鱼从脸部特写逐渐退回全身。"
                           "用户说退出/关闭贴脸模式或恢复全身时调用。",
            .require_scheduler = false,
        },
        .callback = exit_face,
    });
    auto result = XiaoZhi::call_function_sync<boost::json::array>(
        XiaoZhi::FunctionId::AddMCP_ToolsWithCustomFunction, BROOKESIA_DESCRIBE_TO_JSON(tools).as_array());
    return result.has_value() && result->size() == tools.size();
}
#endif
