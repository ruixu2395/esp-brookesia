/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <algorithm>
#include <cstdint>
#include <optional>
#include <string>

namespace character_view {

// Shared with tools/import_dafeiyu_closeup.py: 12 frames at 10 fps.
constexpr int64_t TRANSITION_MS = 1200;
constexpr int VIEW_FRAMES = 12;

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
            if (!render(Render{"view_zoom", "", 0, target_face_ ? 0 : VIEW_FRAMES,
                               target_face_ ? VIEW_FRAMES - 1 : 2 * VIEW_FRAMES - 1})) return false;
            transition_face_ = target_face_;
            transitioning_ = true;
            transition_until_ = now + TRANSITION_MS;
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
};
} // namespace character_view
