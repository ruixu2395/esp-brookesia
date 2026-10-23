/* SPDX-License-Identifier: Apache-2.0 */
#include <cassert>
#include <cstring>
#include <iostream>
#include "modules/dsh_pet_state.hpp"

using namespace dsh_pet_sync;

static void expect(const char *actual, const char *expected)
{
    assert(actual && std::strcmp(actual, expected) == 0);
}

int main()
{
    PetState pet;
    assert(pet.next_emoji(0) == nullptr);
    assert(!pet.available(0));
    assert(pet.receive("publisher-a", 1, true, WorkState::Working, "task-a", 100));
    expect(pet.next_emoji(100), "speaking");
    assert(pet.next_emoji(101) == nullptr); // Never restart a clip per heartbeat.
    assert(!pet.receive("publisher-a", 1, true, WorkState::Error, "old", 200));
    assert(!pet.receive("publisher-a", 0, true, WorkState::Error, "old", 200));
    assert(pet.task() == "task-a");

    pet.set_voice_active(true);
    assert(pet.receive("publisher-a", 2, true, WorkState::Waiting, "task-b", 300));
    assert(pet.next_emoji(400) == nullptr); // DSH cannot interrupt the voice session.
    pet.local_emoji_changed(400);
    pet.set_voice_active(false);
    assert(pet.next_emoji(1000) == nullptr); // Local feedback lease.
    expect(pet.next_emoji(1900), "confused"); // Restore the latest, not the old state.

    assert(pet.receive("publisher-a", 3, true, WorkState::Success, "done", 2000));
    expect(pet.next_emoji(2000), "happy");
    assert(pet.receive("publisher-a", 4, true, WorkState::Success, "done", 6000));
    assert(pet.next_emoji(6000) == nullptr);
    expect(pet.next_emoji(7000), "neutral"); // Heartbeats do not extend celebration.
    assert(pet.available(7000)); // Display lifecycle differs from source freshness.
    assert(pet.receive("publisher-a", 5, true, WorkState::Working, "new", 8000));
    expect(pet.next_emoji(8000), "speaking");
    assert(!pet.available(23000));
    expect(pet.next_emoji(23000), "neutral"); // TTL boundary, not just after it.
    assert(pet.receive("publisher-a", 6, true, WorkState::Working, "new", 24000));
    expect(pet.next_emoji(24000), "speaking");
    assert(pet.receive("publisher-a", 7, false, WorkState::Idle, "never-export", 25000));
    expect(pet.next_emoji(25000), "neutral");
    assert(!pet.available(25000));
    assert(pet.task().empty());

    assert(pet.receive("publisher-b", 1, true, WorkState::Error, "restart", 26000));
    expect(pet.next_emoji(26000), "sad"); // Publisher restart permits sequence reset.
    pet.network_lost();
    assert(!pet.available(26001));
    expect(pet.next_emoji(26001), "neutral");
    assert(pet.receive("publisher-b", 2, true, WorkState::Thinking, "resume", 27000));
    expect(pet.next_emoji(27000), "thinking");
    pet.retry_render();
    expect(pet.next_emoji(27001), "thinking");

    for (auto state : {WorkState::Thinking, WorkState::Working, WorkState::Result,
                       WorkState::Waiting, WorkState::Success, WorkState::Error}) {
        assert(parse_state(state_name(state)) == state);
    }
    assert(!parse_state("unknown"));
    assert(!parse_state("idle")); // Wire idle is JSON null, not a made-up state.
    std::cout << "DSH pet arbitration, freshness, replay and lifecycle checks passed\n";
}
