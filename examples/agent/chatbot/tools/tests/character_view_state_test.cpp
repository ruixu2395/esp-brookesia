/* SPDX-License-Identifier: Apache-2.0 */
#include <cassert>
#include <iostream>
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
    assert(seen.back().first_frame == 0 && seen.back().last_frame == 11);
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
    state.set_face(false);
    state.tick(3400, render);
    assert(seen.back().first_frame == 12 && seen.back().last_frame == 23);
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
    assert(seen.size() == 2 && seen.back().first_frame == 12);
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
    std::cout << "character view checks passed\n";
}
