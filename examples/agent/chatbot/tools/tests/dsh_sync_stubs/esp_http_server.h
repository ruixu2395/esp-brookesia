// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <algorithm>
#include <cstring>
#include <map>
#include <string>
using esp_err_t = int;
using httpd_handle_t = void *;
constexpr esp_err_t ESP_OK = 0;
constexpr esp_err_t ESP_FAIL = -1;
constexpr int HTTP_POST = 1;
struct httpd_req_t {
    httpd_handle_t handle = nullptr;
    size_t content_len = 0;
    std::map<std::string, std::string> headers;
    std::string input;
    size_t offset = 0;
    std::string response_status;
    std::string response;
    int recv_chunk = 7; // Exercise fragmented body reads.
};
struct httpd_config_t {
    int server_port = 80, ctrl_port = 32768, stack_size = 4096, max_open_sockets = 7, max_uri_handlers = 8;
    bool lru_purge_enable = false;
    int recv_wait_timeout = 5, send_wait_timeout = 5;
};
#define HTTPD_DEFAULT_CONFIG() httpd_config_t{}
struct httpd_uri_t { const char *uri; int method; esp_err_t (*handler)(httpd_req_t *); };
inline esp_err_t httpd_start(httpd_handle_t *server, const httpd_config_t *) { *server = reinterpret_cast<void *>(1); return ESP_OK; }
inline esp_err_t httpd_stop(httpd_handle_t) { return ESP_OK; }
inline esp_err_t httpd_register_uri_handler(httpd_handle_t, const httpd_uri_t *) { return ESP_OK; }
inline esp_err_t httpd_resp_set_status(httpd_req_t *req, const char *status) { req->response_status = status; return ESP_OK; }
inline esp_err_t httpd_resp_set_type(httpd_req_t *, const char *) { return ESP_OK; }
inline esp_err_t httpd_resp_set_hdr(httpd_req_t *, const char *, const char *) { return ESP_OK; }
inline esp_err_t httpd_resp_send(httpd_req_t *req, const char *data, size_t size) { req->response.assign(data, size); return ESP_OK; }
inline esp_err_t httpd_sess_trigger_close(httpd_handle_t, int) { return ESP_OK; }
inline int httpd_req_to_sockfd(httpd_req_t *) { return 1; }
inline size_t httpd_req_get_hdr_value_len(httpd_req_t *req, const char *key) { return req->headers[key].size(); }
inline esp_err_t httpd_req_get_hdr_value_str(httpd_req_t *req, const char *key, char *out, size_t size)
{
    const auto &value = req->headers[key];
    if (value.empty() || value.size() >= size) return ESP_FAIL;
    std::memcpy(out, value.c_str(), value.size() + 1);
    return ESP_OK;
}
inline int httpd_req_recv(httpd_req_t *req, char *out, size_t size)
{
    auto count = std::min({size, req->input.size() - req->offset, static_cast<size_t>(req->recv_chunk)});
    std::memcpy(out, req->input.data() + req->offset, count);
    req->offset += count;
    return static_cast<int>(count);
}
