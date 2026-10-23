/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "boost/json/object.hpp"

namespace dsh_pet_sync {

// The server is started by the worker only after STA Wi-Fi is connected.
bool start();
void set_network_connected(bool connected);
void set_voice_active(bool active);
void set_local_emoji(const char *name);
boost::json::object status();

} // namespace dsh_pet_sync
