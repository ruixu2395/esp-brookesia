/* SPDX-License-Identifier: Apache-2.0 */
// Execute the real receiver/parser with small doubles for ESP-IDF hardware APIs.
#include <cassert>
#include <iostream>
#include <boost/json.hpp>
#include "../../main/modules/dsh_pet_sync.cpp"

static httpd_req_t post(boost::json::object body)
{
    httpd_req_t req;
    req.headers["Authorization"] = std::string("Bearer ") + CONFIG_EXAMPLE_DSH_PET_SYNC_TOKEN;
    req.headers["Content-Type"] = "application/json";
    req.input = boost::json::serialize(body);
    req.content_len = req.input.size();
    return req;
}

int main()
{
    assert(dsh_pet_sync::start());
    assert(start_server());
    boost::json::object frame{{"schema_version", 1}, {"source", "dsh-pet"},
        {"publisher_id", "00000000-0000-4000-8000-000000000001"}, {"sequence", 1},
        {"available", true}, {"state", "working"}, {"task", "Validate audio"}};
    auto req = post(frame);
    assert(receive_state(&req) == ESP_OK);
    assert(req.response_status == "200 OK");
    assert(boost::json::parse(req.response).as_object().at("sequence").as_int64() == 1);
    assert(dsh_pet_sync::status().at("available").as_bool());
    assert(dsh_pet_sync::status().at("current").as_object().at("task").as_string() == "Validate audio");

    auto rejected = [&](httpd_req_t value, const char *expected) {
        assert(receive_state(&value) == ESP_OK);
        assert(value.response_status == expected);
    };
    rejected(post(frame), "409 Conflict");
    req = post(frame);
    req.headers["Authorization"] = "Bearer wrong-token";
    rejected(req, "401 Unauthorized");
    req = post(frame);
    req.content_len = 1025;
    rejected(req, "413 Payload Too Large");
    req = post(frame);
    req.headers["Content-Type"] = "text/html";
    rejected(req, "415 Unsupported Media Type");
    req = post(frame);
    req.input = "{invalid";
    req.content_len = req.input.size();
    rejected(req, "400 Bad Request");
    req = post(frame);
    req.input.resize(1); // Body closes before Content-Length bytes arrive.
    assert(receive_state(&req) == ESP_FAIL);

    for (const char *field : {"schema_version", "source", "publisher_id", "sequence", "available", "state", "task"}) {
        auto invalid = frame;
        invalid.erase(field);
        rejected(post(invalid), "400 Bad Request");
    }
    frame["sequence"] = true;
    rejected(post(frame), "400 Bad Request");
    frame["sequence"] = 2;
    frame["state"] = "unsupported";
    rejected(post(frame), "400 Bad Request");
    frame["state"] = "error";
    frame["available"] = false;
    rejected(post(frame), "400 Bad Request");
    frame["state"] = nullptr;
    frame["task"] = nullptr;
    req = post(frame);
    assert(receive_state(&req) == ESP_OK);
    assert(req.response_status == "200 OK");
    assert(!dsh_pet_sync::status().at("available").as_bool());

    frame["sequence"] = 3;
    frame["available"] = true;
    req = post(frame);
    assert(receive_state(&req) == ESP_OK); // Explicit null state is live idle.
    assert(dsh_pet_sync::status().at("available").as_bool());
    dsh_pet_sync::set_voice_active(true);
    assert(dsh_pet_sync::status().at("voice_active").as_bool());
    dsh_pet_sync::set_local_emoji("listening");
    assert(esp_brookesia::service::helper::ExpressionEmote::last_emoji == "listening");
    test_time_us += 15000000;
    assert(!dsh_pet_sync::status().at("available").as_bool());
    assert(dsh_pet_sync::status().at("current").is_null());
    std::cout << "Receiver parser, authentication and cached MCP status checks passed\n";
}
