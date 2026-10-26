/* SPDX-License-Identifier: Apache-2.0 */
#include <cassert>
#include <iostream>
#include <set>
#include <vector>
#include "modules/character_view_state.hpp"

int main()
{
    using namespace character_view;
    assert(voice_command("进入贴脸模式。") == true);
    assert(voice_command("请打开贴脸模式！") == true);
    assert(voice_command("帮我恢复全身") == false);
    assert(voice_command("退出贴脸模式") == false);
    assert(!voice_command("不要进入贴脸模式"));
    assert(!voice_command("什么是贴脸模式？"));
    assert(!voice_command("他说进入贴脸模式"));
    std::vector<Render> seen;
    auto render = [&](const Render &command) { seen.push_back(command); return true; };
    State state;
    state.tick(0, render);
    assert(seen.empty());
    state.set_face(true);
    state.tick(100, render);
    assert(seen.size() == 1 && seen.back().emoji == "view_zoom");
    assert(seen.back().first_frame == 0 && seen.back().last_frame == 17);
    state.set_face(true); // ASR followed by MCP must not restart the move.
    state.set_emoji("speaking");
    state.insert_animation("happy", 3000, 300);
    state.tick(1299, render);
    assert(seen.size() == 1);
    state.tick(1300, render);
    assert(seen.back().emoji == "face_speaking");
    assert(seen.back().animation == "face_happy" && seen.back().animation_ms == 2000);
    state.set_face(true);
    state.tick(1800, render);
    assert(seen.size() == 2);
    state.tick(3300, render);
    assert(seen.back().emoji == "face_speaking" && seen.back().animation.empty());
    const auto before_repeat = seen.size();
    state.set_emoji("speaking");
    state.tick(3350, render);
    assert(seen.size() == before_repeat); // Status polling must not starve the blink loop.
    state.set_face(false);
    state.tick(3400, render);
    assert(seen.back().first_frame == 18 && seen.back().last_frame == 35);
    state.set_emoji("thinking"); // DSH/voice events keep their latest intent.
    state.tick(4600, render);
    assert(seen.back().emoji == "thinking");

    State queued;
    seen.clear();
    queued.set_face(true);
    queued.tick(0, render);
    queued.set_face(false); // Finish this move, then reverse without jumping.
    queued.tick(500, render);
    assert(seen.size() == 1);
    queued.tick(1200, render);
    assert(seen.size() == 2 && seen.back().first_frame == 18);
    queued.tick(2400, render);
    assert(seen.back().emoji == "neutral");

    State retry;
    retry.set_face(true);
    assert(!retry.tick(0, [](const Render &) { return false; }));
    seen.clear();
    retry.tick(1000, render);
    retry.insert_animation("sad", 100, 1050);
    retry.tick(2200, render);
    assert(seen.size() == 2 && seen.back().emoji == "face_neutral");
    assert(seen.back().animation.empty()); // Never resurrect expired emotion.

    State lively;
    seen.clear();
    lively.set_face(true);
    lively.tick(0, render);
    lively.tick(1200, render);
    assert(seen.back().emoji == "face_neutral");
    lively.set_emoji("neutral"); // Polling must not reset the idle performance.
    lively.tick(4499, render);
    assert(seen.size() == 2);
    lively.tick(4500, render);
    assert(seen.back().emoji == "face_idle_smile");
    lively.tick(5900, render);
    assert(seen.back().emoji == "face_neutral");
    lively.tick(10100, render);
    assert(seen.back().emoji == "face_idle_curious");
    // A real conversation state preempts an idle gesture in the very next tick.
    lively.set_emoji("speaking");
    lively.tick(10200, render);
    assert(seen.back().emoji == "face_speaking");
    const auto speaking_count = seen.size();
    lively.tick(60000, render);
    assert(seen.size() == speaking_count);
    lively.set_emoji("neutral");
    lively.tick(60100, render);
    assert(seen.back().emoji == "face_neutral");
    lively.tick(63400, render);
    assert(seen.back().emoji == "face_idle_smile");
    lively.insert_animation("sad", 1000, 63500);
    lively.tick(63500, render);
    assert(seen.back().animation == "face_sad");
    lively.tick(64500, render);
    assert(seen.back().emoji == "face_neutral" && seen.back().animation.empty());
    lively.set_face(false);
    lively.tick(65000, render);
    lively.tick(66200, render);
    const auto full_count = seen.size();
    lively.tick(100000, render);
    assert(seen.size() == full_count && seen.back().emoji == "neutral");

    State delayed;
    seen.clear();
    delayed.set_face(true);
    delayed.tick(0, render);
    delayed.tick(1200, render);
    assert(!delayed.tick(4500, [](const Render &) { return false; }));
    delayed.tick(4600, render);
    assert(seen.back().emoji == "face_idle_smile");
    const auto delayed_count = seen.size();
    delayed.tick(1000000, render);
    assert(seen.size() == delayed_count + 1 && seen.back().emoji == "face_neutral");

    State cycle;
    seen.clear();
    cycle.set_face(true);
    for (int64_t now = 0; now <= 32000; now += 100) {
        cycle.set_emoji("neutral");
        cycle.tick(now, render);
    }
    std::set<std::string> expressions;
    for (const auto &command : seen) expressions.insert(command.emoji);
    assert(expressions.count("face_idle_smile"));
    assert(expressions.count("face_idle_curious"));
    assert(expressions.count("face_idle_drowsy"));
    assert(seen.back().emoji == "face_neutral"); // The next cycle starts calmly.
    std::cout << "character view checks passed\n";
}
