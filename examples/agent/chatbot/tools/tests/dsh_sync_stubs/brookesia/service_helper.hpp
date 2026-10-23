// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <string>
namespace esp_brookesia::service::helper {
struct ExpressionEmote {
    enum class FunctionId { SetEmoji };
    static inline std::string last_emoji;
    static bool call_function_async(FunctionId, const char *name) { last_emoji = name; return true; }
};
}
