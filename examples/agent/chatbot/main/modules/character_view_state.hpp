/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <algorithm>
#include <cstdint>
#include <optional>
#include <string>

namespace character_view {

// Must match the selected resource bundle. Both moves take 1.2 seconds.
constexpr int64_t TRANSITION_MS = 1200;
#if defined(CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M) && CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M
constexpr int VIEW_FRAMES = 24; // 20 fps, build_dafeiyu_smooth.py
#else
constexpr int VIEW_FRAMES = 18;
#endif

// One continuous face performance. Keep these periods in sync with EAF tables.
#if defined(CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M) && CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M
constexpr int64_t FACE_LOOP_MS = 4300; // 86 frames at 20 fps
#else
constexpr int64_t FACE_LOOP_MS = 3000; // 36 frames at 12 fps
#endif

inline std::optional<bool> voice_command(std::string text)
{
    for (const auto *mark : {" ", "\t", "\r", "\n", "，", "。", "！", "？", ",", ".", "!", "?", "、"}) {
        for (size_t at; (at = text.find(mark)) != std::string::npos;) text.erase(at, std::string(mark).size());
    }
    for (const auto *prefix : {"请", "帮我"}) {
        if (text.rfind(prefix, 0) == 0) text.erase(0, std::string(prefix).size());
    }
    for (const auto *command : {"进入贴脸模式", "开启贴脸模式", "打开贴脸模式"}) {
        if (text == command) return true;
    }
    for (const auto *command : {"退出贴脸模式", "关闭贴脸模式", "恢复全身", "恢复全身模式", "进入全身模式"}) {
        if (text == command) return false;
    }
    return std::nullopt;
}

struct Render {
    std::string emoji;
    std::string animation;
    int64_t animation_ms = 0;
    int first_frame = -1;
    int last_frame = -1;
};

// The owner serializes commands and tick(). Switching expressions during a
// camera move updates the intent, so ordinary events never interrupt the move.
class State {
public:
    void set_face(bool face) { target_face_ = face; }
    bool target_face() const { return target_face_; }
    void set_emoji(const std::string &emoji)
    {
        if (emoji_ == emoji) return; // Preserve the running blink on repeated status updates.
        emoji_ = emoji;
        dirty_ = true;
    }
    void insert_animation(const std::string &name, int64_t duration_ms, int64_t now)
    {
        animation_ = name;
        animation_until_ = now + std::max<int64_t>(0, duration_ms);
        dirty_ = true;
    }

    template<class RenderFunction> bool tick(int64_t now, RenderFunction render)
    {
        if (transitioning_) {
            if (now < transition_until_) return true;
            face_ = transition_face_;
            transitioning_ = false;
            dirty_ = true;
        }
        if (target_face_ != face_) {
            // Finish the current blink cycle before returning the camera to its
            // neutral endpoint. The deadline is captured once, not rolled forward.
            if (face_ && face_loop_started_ >= 0) {
                if (exit_at_ < 0) {
                    const int64_t elapsed = std::max<int64_t>(0, now - face_loop_started_);
                    exit_at_ = face_loop_started_ + ((elapsed + FACE_LOOP_MS - 1) / FACE_LOOP_MS) * FACE_LOOP_MS;
                }
                if (now < exit_at_) return true;
            }
            if (!render(Render{"view_zoom", "", 0, target_face_ ? 0 : VIEW_FRAMES,
                               target_face_ ? VIEW_FRAMES - 1 : 2 * VIEW_FRAMES - 1})) return false;
            face_loop_started_ = -1;
            transition_face_ = target_face_;
            transitioning_ = true;
            transition_until_ = now + TRANSITION_MS;
            return true;
        }
        exit_at_ = -1; // A cancelled exit keeps the ongoing performance intact.
        if (face_) {
            // Speaking/DSH/emotion events remain in the logical state, but do
            // not splice another character pose into the continuous close-up.
            if (face_loop_started_ < 0) {
                if (!render(Render{"face_neutral", "", 0})) return false;
                face_loop_started_ = now;
            }
            dirty_ = false;
            return true;
        }
        if (!animation_.empty() && now >= animation_until_) {
            animation_.clear();
            dirty_ = true;
        }
        if (!dirty_) return true;
        const std::string prefix = face_ ? "face_" : "";
        Render command{prefix + emoji_, animation_.empty() ? "" : prefix + animation_,
                       animation_.empty() ? 0 : animation_until_ - now};
        if (!render(command)) return false;
        dirty_ = false;
        return true;
    }

private:
    bool target_face_ = false;
    bool face_ = false;
    bool transitioning_ = false;
    bool transition_face_ = false;
    bool dirty_ = false;
    int64_t transition_until_ = 0;
    std::string emoji_ = "neutral";
    std::string animation_;
    int64_t animation_until_ = 0;
    int64_t face_loop_started_ = -1;
    int64_t exit_at_ = -1;
};
} // namespace character_view
