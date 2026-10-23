/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <cstdint>
#include <initializer_list>
#include <optional>
#include <string>
#include <string_view>

// Pure state arbitration shared by the ESP32 receiver and native host tests.
namespace dsh_pet_sync {

enum class WorkState { Idle, Thinking, Working, Result, Waiting, Success, Error };

inline const char *state_name(WorkState state)
{
    switch (state) {
    case WorkState::Thinking: return "thinking";
    case WorkState::Working: return "working";
    case WorkState::Result: return "result";
    case WorkState::Waiting: return "waiting";
    case WorkState::Success: return "success";
    case WorkState::Error: return "error";
    default: return "idle";
    }
}

inline std::optional<WorkState> parse_state(std::string_view name)
{
    for (auto state : {WorkState::Thinking, WorkState::Working, WorkState::Result,
                       WorkState::Waiting, WorkState::Success, WorkState::Error}) {
        if (name == state_name(state)) return state;
    }
    return std::nullopt;
}

class PetState {
public:
    static constexpr int64_t STALE_MS = 15000;
    static constexpr int64_t TERMINAL_MS = 5000;

    bool receive(const std::string &publisher, uint64_t sequence, bool available,
                 WorkState state, const std::string &task, int64_t now)
    {
        if (received_ && publisher == publisher_ && sequence <= sequence_) return false;
        // Heartbeats and task-only changes never restart a terminal animation.
        if (!received_ || publisher != publisher_ || available != source_available_ || state != state_) {
            changed_at_ = now;
        }
        publisher_ = publisher;
        sequence_ = sequence;
        source_available_ = available;
        state_ = state;
        task_ = available ? task : "";
        received_at_ = now;
        received_ = true;
        return true;
    }

    void set_voice_active(bool active)
    {
        if (voice_active_ && !active) rendered_.reset();
        voice_active_ = active;
    }

    void local_emoji_changed(int64_t now)
    {
        // Local provisioning, battery and Agent feedback gets a short lease.
        local_until_ = now + 1500;
        if (source_available_) rendered_.reset();
    }

    void network_lost() { source_available_ = false; }
    bool available(int64_t now) const
    {
        return received_ && source_available_ && now - received_at_ < STALE_MS;
    }
    bool voice_active() const { return voice_active_; }
    WorkState state() const { return state_; }
    const std::string &task() const { return task_; }
    int64_t received_at() const { return received_at_; }
    bool received() const { return received_; }

    // Returns a change only. The caller serializes this with local SetEmoji.
    const char *next_emoji(int64_t now)
    {
        if (!received_ || voice_active_ || now < local_until_) return nullptr;
        WorkState desired = available(now) ? state_ : WorkState::Idle;
        if ((desired == WorkState::Success || desired == WorkState::Error) && now - changed_at_ >= TERMINAL_MS) {
            desired = WorkState::Idle;
        }
        if (rendered_ && *rendered_ == desired) return nullptr;
        rendered_ = desired;
        switch (desired) {
        case WorkState::Thinking: return "thinking";
        case WorkState::Working: return "speaking";
        case WorkState::Result: return "thinking";
        case WorkState::Waiting: return "confused";
        case WorkState::Success: return "happy";
        case WorkState::Error: return "sad";
        default: return "neutral";
        }
    }

    void retry_render() { rendered_.reset(); }

private:
    bool received_ = false;
    bool source_available_ = false;
    bool voice_active_ = false;
    std::string publisher_;
    uint64_t sequence_ = 0;
    WorkState state_ = WorkState::Idle;
    std::string task_;
    int64_t received_at_ = 0;
    int64_t changed_at_ = 0;
    int64_t local_until_ = 0;
    std::optional<WorkState> rendered_;
};

} // namespace dsh_pet_sync
