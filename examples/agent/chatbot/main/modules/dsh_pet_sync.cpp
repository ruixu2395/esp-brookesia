/* SPDX-License-Identifier: Apache-2.0 */
#include "sdkconfig.h"
#include "dsh_pet_sync.hpp"
#include "character_view.hpp"
#include "brookesia/service_helper.hpp"

#if CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE
#include <algorithm>
#include <atomic>
#include <mutex>
#include <string>
#include "boost/json.hpp"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "dsh_pet_state.hpp"

namespace {
constexpr const char *TAG = "DshPetSync";
constexpr size_t MAX_BODY_BYTES = 1024;
std::mutex state_mutex;
dsh_pet_sync::PetState pet;
std::atomic_bool network_connected{false};
TaskHandle_t worker_handle = nullptr;
httpd_handle_t server = nullptr;

int64_t now_ms() { return esp_timer_get_time() / 1000; }

bool token_character(unsigned char c)
{
    return ((c >= 'a') && (c <= 'z')) || ((c >= 'A') && (c <= 'Z')) ||
           ((c >= '0') && (c <= '9')) || c == '-' || c == '_';
}

esp_err_t reply(httpd_req_t *req, const char *status, const std::string &body)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    auto result = httpd_resp_send(req, body.data(), body.size());
    httpd_sess_trigger_close(req->handle, httpd_req_to_sockfd(req));
    return result;
}

bool authorized(httpd_req_t *req)
{
    const std::string expected = std::string("Bearer ") + CONFIG_EXAMPLE_DSH_PET_SYNC_TOKEN;
    size_t length = httpd_req_get_hdr_value_len(req, "Authorization");
    if (length != expected.size()) return false;
    char value[136]{};
    if (httpd_req_get_hdr_value_str(req, "Authorization", value, sizeof(value)) != ESP_OK) return false;
    unsigned char difference = 0;
    for (size_t i = 0; i < length; ++i) difference |= value[i] ^ expected[i];
    return difference == 0;
}

esp_err_t receive_state(httpd_req_t *req)
{
    // Close the connection after each POST, including rejected requests with an
    // unread body. This prevents leftover bytes being interpreted as a request.
    httpd_resp_set_hdr(req, "Connection", "close");
    if (!authorized(req)) return reply(req, "401 Unauthorized", "{\"error\":\"unauthorized\"}");
    if (req->content_len == 0 || req->content_len > MAX_BODY_BYTES) {
        return reply(req, "413 Payload Too Large", "{\"error\":\"body_size\"}");
    }
    char content_type[64]{};
    if (httpd_req_get_hdr_value_str(req, "Content-Type", content_type, sizeof(content_type)) != ESP_OK ||
            (std::string(content_type) != "application/json" &&
             std::string(content_type).rfind("application/json;", 0) != 0)) {
        return reply(req, "415 Unsupported Media Type", "{\"error\":\"content_type\"}");
    }
    std::string body(req->content_len, '\0');
    size_t offset = 0;
    while (offset < body.size()) {
        int count = httpd_req_recv(req, body.data() + offset, body.size() - offset);
        if (count <= 0) return ESP_FAIL;
        offset += static_cast<size_t>(count);
    }
    boost::system::error_code error;
    auto value = boost::json::parse(body, error);
    if (error || !value.is_object()) return reply(req, "400 Bad Request", "{\"error\":\"invalid_json\"}");
    auto &object = value.as_object();
    auto version = object.if_contains("schema_version");
    auto source = object.if_contains("source");
    auto publisher = object.if_contains("publisher_id");
    auto sequence = object.if_contains("sequence");
    auto available = object.if_contains("available");
    auto state = object.if_contains("state");
    auto task = object.if_contains("task");
    if (!version || !version->is_int64() || version->as_int64() != 1 ||
            !source || !source->is_string() || source->as_string() != "dsh-pet" ||
            !publisher || !publisher->is_string() || publisher->as_string().size() != 36 ||
            !std::all_of(publisher->as_string().begin(), publisher->as_string().end(), token_character) ||
            !sequence || !sequence->is_int64() || sequence->as_int64() < 1 ||
            sequence->as_int64() > 9007199254740991LL || !available || !available->is_bool() ||
            !state || (!state->is_null() && !state->is_string()) ||
            !task || (!task->is_null() && !task->is_string()) ||
            (task->is_string() && task->as_string().size() > 480)) {
        return reply(req, "400 Bad Request", "{\"error\":\"invalid_schema\"}");
    }
    auto parsed = state->is_null() ? std::optional(dsh_pet_sync::WorkState::Idle) :
                  dsh_pet_sync::parse_state(std::string_view(state->as_string().data(), state->as_string().size()));
    if (!parsed || (!available->as_bool() && (!state->is_null() || !task->is_null()))) {
        return reply(req, "400 Bad Request", "{\"error\":\"invalid_state\"}");
    }
    bool accepted;
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        accepted = pet.receive(std::string(publisher->as_string()), sequence->as_int64(),
                               available->as_bool(), *parsed, task->is_string() ? std::string(task->as_string()) : "",
                               now_ms());
    }
    if (!accepted) return reply(req, "409 Conflict", "{\"error\":\"old_sequence\"}");
    return reply(req, "200 OK", "{\"ok\":true,\"sequence\":" + std::to_string(sequence->as_int64()) + "}");
}

bool start_server()
{
    auto config = HTTPD_DEFAULT_CONFIG();
    config.server_port = CONFIG_EXAMPLE_DSH_PET_SYNC_PORT;
    // Keep the default server's UDP control socket free for Wi-Fi provisioning.
    config.ctrl_port = 32769;
    config.stack_size = 6144;
    config.max_open_sockets = 2;
    config.max_uri_handlers = 1;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 2;
    config.send_wait_timeout = 2;
    if (httpd_start(&server, &config) != ESP_OK) return false;
    httpd_uri_t route{};
    route.uri = "/api/pet/state";
    route.method = HTTP_POST;
    route.handler = receive_state;
    if (httpd_register_uri_handler(server, &route) != ESP_OK) {
        httpd_stop(server);
        server = nullptr;
        return false;
    }
    ESP_LOGI(TAG, "Listening for DSH status on TCP %d", config.server_port);
    return true;
}

void worker(void *)
{
    int64_t retry_at = 0;
    while (true) {
        const auto now = now_ms();
        if (network_connected.load() && !server && now >= retry_at) {
            if (!start_server()) {
                ESP_LOGW(TAG, "Could not start receiver; retrying in 5 seconds");
                retry_at = now + 5000;
            }
        } else if (!network_connected.load() && server) {
            httpd_stop(server);
            server = nullptr;
        }
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            if (const auto emoji = pet.next_emoji(now)) {
                if (!character_view::set_emoji(emoji)) pet.retry_render();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}
} // namespace
#endif

bool dsh_pet_sync::start()
{
#if CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE
    const std::string token = CONFIG_EXAMPLE_DSH_PET_SYNC_TOKEN;
    if (token.size() < 24 || token.size() > 128 ||
            !std::all_of(token.begin(), token.end(), token_character)) {
        ESP_LOGE(TAG, "Set a 24-128 character URL-safe DSH sync token in menuconfig");
        return false;
    }
    if (worker_handle) return true;
    return xTaskCreate(worker, "DshPetSync", 4096, nullptr, 2, &worker_handle) == pdPASS;
#else
    return true;
#endif
}

void dsh_pet_sync::set_network_connected(bool connected)
{
#if CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE
    network_connected.store(connected);
    if (!connected) {
        std::lock_guard<std::mutex> lock(state_mutex);
        pet.network_lost();
    }
#else
    (void)connected;
#endif
}

void dsh_pet_sync::set_voice_active(bool active)
{
#if CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE
    std::lock_guard<std::mutex> lock(state_mutex);
    pet.set_voice_active(active);
#else
    (void)active;
#endif
}

void dsh_pet_sync::set_local_emoji(const char *name)
{
#if CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE
    std::lock_guard<std::mutex> lock(state_mutex);
    pet.local_emoji_changed(now_ms());
#endif
    character_view::set_emoji(name);
}

boost::json::object dsh_pet_sync::status()
{
    boost::json::object result{{"schema_version", 1}, {"source", "dsh-pet"}, {"transport", "direct_push"},
        {"available", false}, {"stale", true}, {"current", nullptr},
        {"summary", "DSH direct push is unavailable. Check DSH plugin, Wi-Fi and device token."}};
#if CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE
    std::lock_guard<std::mutex> lock(state_mutex);
    const auto now = now_ms();
    const bool available = pet.available(now);
    result["available"] = available;
    result["stale"] = !available;
    result["voice_active"] = pet.voice_active();
    result["sync_age_seconds"] = pet.received() ? boost::json::value((now - pet.received_at()) / 1000.0) :
                                 boost::json::value(nullptr);
    result["capabilities"] = boost::json::object{{"work_status", true}, {"current_task", true},
        {"project", false}, {"todo_counts", false}, {"task_list", false}};
    if (available) {
        result["current"] = boost::json::object{{"state", state_name(pet.state())}, {"task", pet.task()},
            {"project", nullptr}, {"progress", nullptr}};
        result["summary"] = std::string("DSH state: ") + state_name(pet.state()) + ". Task: " + pet.task();
    }
#endif
    return result;
}
