/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "sdkconfig.h"
#include <string>
#if !CONFIG_EXAMPLE_DAFEIYU_CLOSEUP_ENABLE
#include "brookesia/service_helper.hpp"
#endif

namespace character_view {
#if CONFIG_EXAMPLE_DAFEIYU_CLOSEUP_ENABLE
bool start();
bool set_face(bool enabled);
bool handle_voice_command(const std::string &text);
bool register_mcp_tools();
bool set_emoji(const char *name);
bool insert_animation(const std::string &name, double duration_ms);
#else
inline bool start() { return true; }
inline bool handle_voice_command(const std::string &) { return false; }
inline bool register_mcp_tools() { return true; }
inline bool set_emoji(const char *name)
{
    using Emote = esp_brookesia::service::helper::ExpressionEmote;
    return Emote::call_function_async(Emote::FunctionId::SetEmoji, name);
}
inline bool insert_animation(const std::string &name, double duration_ms)
{
    using Emote = esp_brookesia::service::helper::ExpressionEmote;
    return Emote::call_function_async(Emote::FunctionId::InsertAnimation, name, duration_ms);
}
#endif
} // namespace character_view
