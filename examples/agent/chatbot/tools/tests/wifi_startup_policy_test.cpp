/* SPDX-License-Identifier: Apache-2.0 */
#include <cassert>
#include <iostream>
#include "modules/wifi_startup_policy.hpp"

int main()
{
    using namespace wifi_startup;
    // At the startup deadline, a connection still in flight must not be
    // interrupted by starting SoftAP (which disconnects STA).
    assert(decide("Connecting") == Decision::Wait);
    assert(decide("Starting") == Decision::Wait);
    assert(decide("Initing") == Decision::Wait);
    assert(decide("") == Decision::Wait); // failed state query is not failure evidence
    assert(decide("Connected") == Decision::Connected); // covers a missed event
    assert(decide("Started") == Decision::Provision); // retries ended without a connection
    for (const auto *state : {"Stopping", "Inited", "Idle", "Deiniting"}) {
        assert(decide(state) == Decision::Stop); // respect an explicit WiFi stop
    }
    assert(GRACE_MS >= 60000 && CHECK_MS >= 1000);
    std::cout << "wifi startup policy checks passed\n";
}
