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
    state.tick(0, render);
    assert(seen.back().first_frame == 0 && seen.back().last_frame == VIEW_FRAMES - 1);
    state.set_emoji("speaking");
    state.insert_animation("happy", 3000, 300);
    state.tick(1199, render);
    assert(seen.size() == 1);
    state.tick(1200, render);
    assert(seen.size() == 2 && seen.back().emoji == "face_neutral");
    assert(seen.back().animation.empty());
    // All competing expression sources must leave the running blink untouched.
    for (int64_t now = 1300; now < 9000; now += 100) {
        state.set_emoji(now % 200 ? "thinking" : "speaking");
        state.insert_animation("sad", 100, now);
        state.tick(now, render);
    }
    assert(seen.size() == 2);
    state.set_face(false);
    state.tick(9000, render);
    assert(seen.size() == 2);
    const auto exit_at = 1200 + ((9000 - 1200 + FACE_LOOP_MS - 1) / FACE_LOOP_MS) * FACE_LOOP_MS;
    state.tick(exit_at - 1, render);
    assert(seen.size() == 2);
    state.tick(exit_at, render);
    assert(seen.back().first_frame == VIEW_FRAMES && seen.back().last_frame == 2 * VIEW_FRAMES - 1);
    state.set_emoji("thinking");
    state.tick(exit_at + TRANSITION_MS, render);
    assert(seen.back().emoji == "thinking" && seen.back().animation.empty());

    State cancelled;
    seen.clear();
    cancelled.set_face(true);
    cancelled.tick(0, render);
    cancelled.tick(1200, render);
    cancelled.set_face(false);
    cancelled.tick(1400, render);
    cancelled.set_face(true);
    cancelled.tick(1500, render);
    cancelled.tick(1200 + FACE_LOOP_MS, render);
    assert(seen.size() == 2); // Cancelling exit must not restart the video.

    State queued;
    seen.clear();
    queued.set_face(true);
    queued.tick(0, render);
    queued.set_face(false);
    queued.tick(1200, render);
    assert(seen.size() == 2 && seen.back().first_frame == VIEW_FRAMES);
    queued.tick(2400, render);
    assert(seen.back().emoji == "neutral");

    State retry;
    retry.set_face(true);
    assert(!retry.tick(0, [](const Render &) { return false; }));
    seen.clear();
    retry.tick(1000, render);
    assert(!retry.tick(2200, [](const Render &) { return false; }));
    retry.tick(2300, render);
    assert(seen.back().emoji == "face_neutral");
    retry.tick(100000, render);
    assert(seen.size() == 2);
    std::cout << "character view checks passed\n";
}
