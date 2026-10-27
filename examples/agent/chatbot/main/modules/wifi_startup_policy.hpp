/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <string_view>

namespace wifi_startup {
constexpr int GRACE_MS = 60000;
constexpr int CHECK_MS = 5000;
enum class Decision { Connected, Wait, Provision, Stop };
inline Decision decide(std::string_view state)
{
    if (state == "Connected") return Decision::Connected;
    // Only fall back after the grace period AND when no connection is in flight.
    if (state == "Started") return Decision::Provision;
    if (state == "Stopping" || state == "Inited" || state == "Idle" || state == "Deiniting") {
        return Decision::Stop; // Do not undo a user's explicit stop.
    }
    return Decision::Wait; // Starting, Connecting, transient query failure.
}
}
