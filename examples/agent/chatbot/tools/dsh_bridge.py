#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read local dsh-dafeiyu SSE snapshots and serve a bounded status API to XiaoZhi.

Python 3.10+, standard library only. No model API keys, task writes or screen
capture are used. An optional desktop Host Cookie stays on the PC.
MCP tool registration lives in the ESP32 firmware.
"""

import argparse
import copy
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


EVENT_PATH = '/plugins/dsh-dafeiyu/events'
CONFIG_PATH = '/plugins/dsh-dafeiyu/config'
MAX_EVENT_BYTES = 65536
MAX_RESPONSE_BYTES = 8192
MAX_TASKS = 6
STATES = {
    'IDLE': '空闲', 'THINKING': '思考中', 'WORKING': '执行中',
    'WAITING': '等待确认', 'SUCCESS': '已完成', 'ERROR': '发生错误',
    'DISCONNECTED': '已断开',
}
LOG = logging.getLogger('dsh-bridge')


def text(value, limit=120):
    if not isinstance(value, str):
        return None
    return ' '.join(value.split())[:limit] or None


def progress(value):
    if not isinstance(value, dict):
        return None
    completed, total = value.get('completed'), value.get('total')
    if (type(completed) is not int or type(total) is not int
            or not 0 <= completed <= total <= 1000000 or total == 0):
        return None
    result = {'completed': completed, 'total': total}
    current = value.get('current')
    if type(current) is int and 1 <= current <= total:
        result['current'] = current
    return result


def task_record(value):
    """Allowlist protocol fields; never forward config, balance or arbitrary JSON."""
    state = value.get('state')
    return {
        'session_id': text(value.get('sessionId'), 64),
        'state': state if isinstance(state, str) and state in STATES else 'UNKNOWN',
        'project': text(value.get('project'), 80),
        'stage': text(value.get('stage'), 40),
        'task': text(value.get('task')),
        'progress': progress(value.get('progress')),
    }


def event_timestamp(message):
    value = message.get('timestamp')
    if type(value) not in (int, float) or not 0 <= value <= 253402300799000:
        return None
    return value


class StatusStore:
    def __init__(self, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.connected = False
        self.current = None
        self.sessions = {}
        self.tasks = []
        self.last_sync = None
        self.updated_at = None
        self.connection_error = 'waiting_for_dsh'

    def begin_connection(self):
        with self.lock:
            # The source replays independent state/task snapshots. Rebuild each
            # connection so old sessions from an earlier host cannot leak in.
            self.connected = True
            self.current = None
            self.sessions = {}
            self.tasks = []
            self.last_sync = None
            self.updated_at = None
            self.connection_error = 'waiting_for_state'

    def disconnect(self, reason='source_disconnected'):
        with self.lock:
            self.connected = False
            self.connection_error = reason

    def apply(self, message):
        if (not isinstance(message, dict) or type(message.get('protocolVersion')) is not int
                or message['protocolVersion'] != 1):
            return
        kind = message.get('kind')
        stamp = event_timestamp(message)
        if kind not in ('state', 'task', 'tasks', 'shutdown', 'closed', 'hello',
                        'pulse', 'config', 'balance', 'ping', 'pong') or stamp is None:
            return
        with self.lock:
            if kind in ('shutdown', 'closed'):
                self.connected = False
                self.connection_error = 'dsh_stopped'
                return
            if self.current is not None:
                self.last_sync = self.clock()
            if kind not in ('state', 'task', 'tasks'):
                return
            if kind == 'tasks':
                values = message.get('tasks')
                if not isinstance(values, list):
                    return
                self.tasks = [task_record(item) for item in values[:MAX_TASKS]
                              if isinstance(item, dict)]
                return
            state = message.get('state')
            if kind == 'state' and (not isinstance(state, str) or state not in STATES):
                return
            record = task_record(message)
            session_id = record['session_id']
            if kind == 'task' and session_id is None:
                return
            previous = self.sessions.get(session_id)
            # A replayed TASK can predate the latest STATE, including a new
            # turn in the same session. Never resurrect that old todo progress.
            if previous and stamp < previous[0]:
                return
            if kind == 'task':
                if previous:
                    merged = dict(previous[1])
                    for key in ('project', 'task', 'progress'):
                        merged[key] = record[key]
                    record = merged
            self.sessions[session_id] = (stamp, record)
            if len(self.sessions) > 64:
                oldest = min(self.sessions, key=lambda key: self.sessions[key][0])
                del self.sessions[oldest]
            if kind == 'state' or (self.current and self.current['session_id'] == session_id):
                self.current = record
                self.last_sync = self.clock()
                self.connection_error = None
                self.updated_at = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()

    def snapshot(self):
        with self.lock:
            age = None if self.last_sync is None else max(0, self.clock() - self.last_sync)
            available = self.connected and self.current is not None and age is not None and age < 45
            result = {
                'schema_version': 1,
                'source': 'dsh-dafeiyu',
                'connected': self.connected,
                'available': available,
                'stale': not available,
                'updated_at': self.updated_at,
                'sync_age_seconds': None if age is None else round(age, 1),
                'connection_error': self.connection_error,
                'current': copy.deepcopy(self.current),
                'tasks': [],
            }
            for item in self.tasks:
                item = dict(item)
                # The multi-task list omits progress and may include a new
                # turn in an older session. Counts are current only for the
                # selected session, whose full snapshot we actually receive.
                if (self.current and item['session_id'] == self.current['session_id']
                        and item['state'] == self.current['state']):
                    item['progress'] = self.current['progress']
                result['tasks'].append(item)
        current = result['current']
        if not available:
            result['summary'] = '暂时无法获取电脑 DSH 的实时状态；缓存信息不能作为当前状态。'
        elif current:
            parts = [f"DSH：{STATES.get(current['state'], '状态未知')}"]
            if current['project']:
                parts.append(f"项目：{current['project']}")
            if current['task']:
                parts.append(f"当前待办：{current['task']}")
            elif current['stage']:
                parts.append(f"阶段：{current['stage']}")
            todo = current['progress']
            parts.append(f"待办完成 {todo['completed']}/{todo['total']}" if todo
                         else 'DSH 未提供待办计数，无法计算完成百分比')
            result['summary'] = '；'.join(parts)
        return result


def status_payload(store):
    result = store.snapshot()
    payload = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_RESPONSE_BYTES:
        result['tasks'] = []
        result['tasks_truncated'] = True
        payload = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('status payload exceeds firmware limit')
    return payload


def iter_sse(stream):
    chunks, size = [], 0
    while True:
        line = stream.readline(MAX_EVENT_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_EVENT_BYTES:
            raise ValueError('oversized source event line')
        line = line.decode('utf-8').rstrip('\r\n')
        if not line:
            if chunks:
                try:
                    message = json.loads('\n'.join(chunks))
                except (ValueError, RecursionError):
                    message = None
                if isinstance(message, dict):
                    yield message
            chunks, size = [], 0
        elif line.startswith('data:'):
            value = line[5:].removeprefix(' ')
            size += len(value.encode('utf-8'))
            if size > MAX_EVENT_BYTES:
                raise ValueError('oversized source event')
            chunks.append(value)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def source_endpoint(base_url):
    url = urlsplit(base_url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
        raise ValueError('DSH source must be a loopback HTTP base URL, e.g. http://127.0.0.1:3080')
    return base_url.rstrip('/') + EVENT_PATH


def read_cookie(path):
    if path is None:
        return None
    value = Path(path).read_text(encoding='ascii').strip()
    if not value or len(value) > 4096 or any(not 32 <= ord(c) < 127 for c in value):
        raise ValueError('Host Cookie file must contain one nonempty ASCII header value')
    return value


def read_source(endpoint, store, stop, once=False, cookie_file=None):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    while not stop.is_set():
        try:
            headers = {}
            cookie = read_cookie(cookie_file)
            if cookie:
                headers['Cookie'] = cookie
            origin = urlsplit(endpoint)
            config_url = f'{origin.scheme}://{origin.netloc}{CONFIG_PATH}'
            config_request = Request(config_url, headers={**headers, 'Accept': 'application/json'})
            with opener.open(config_request, timeout=5) as response:
                body = response.read(8193)
                if response.headers.get_content_type() != 'application/json' or len(body) > 8192:
                    raise ValueError('invalid plugin configuration response')
                config = json.loads(body)
                if not isinstance(config, dict) or type(config.get('enabled')) is not bool:
                    raise ValueError('invalid plugin configuration schema')
            if not config['enabled']:
                store.disconnect('source_plugin_disabled')
                if once or stop.wait(1):
                    return
                continue
            request = Request(endpoint, headers={**headers, 'Accept': 'text/event-stream'})
            with opener.open(request, timeout=20) as response:
                if response.headers.get_content_type() != 'text/event-stream':
                    raise ValueError('source is not an SSE endpoint')
                store.begin_connection()
                for message in iter_sse(response):
                    if stop.is_set():
                        return
                    store.apply(message)
                    if once and store.snapshot()['available']:
                        return
            store.disconnect()
        except HTTPError as error:
            store.disconnect(f'source_http_{error.code}')
        except (OSError, URLError, ValueError, RecursionError):
            store.disconnect('source_unreachable_or_invalid')
        if once:
            return
        # The upstream SSE endpoint has no heartbeat. Reconnect after the read
        # timeout to revalidate the host and obtain a fresh snapshot even idle.
        if stop.wait(1):
            return


def make_server(address, store, token):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, payload):
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied.encode('utf-8'), ('Bearer ' + token).encode('ascii')):
                self.reply(401, b'{"error":"unauthorized"}')
            elif self.path != '/v1/status':
                self.reply(404, b'{"error":"not_found"}')
            else:
                self.reply(200, status_payload(store))

        def do_POST(self):
            self.reply(405, b'{"error":"read_only"}')

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, fmt, *args):
            # Do not log tokens or task contents.
            return

    return ThreadingHTTPServer(address, Handler)


def load_token(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open('x', encoding='ascii') as file:
            file.write(secrets.token_urlsafe(32) + '\n')
    token = path.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', token):
        raise ValueError('token file must contain 24-128 URL-safe ASCII characters')
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-url', required=True, help='Actual DSH desktop host loopback HTTP base URL')
    parser.add_argument('--bind', default='127.0.0.1', help='Use the PC LAN IP to allow the ESP32 to connect')
    parser.add_argument('--source-cookie-file', type=Path,
                        help='Optional local file containing the desktop Host Cookie header value')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--token-file', type=Path,
                        default=Path(__file__).resolve().parents[4] / '.cache/dsh-bridge/token.txt')
    parser.add_argument('--probe', action='store_true', help='Read one real source snapshot without serving LAN HTTP')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    try:
        endpoint = source_endpoint(args.source_url)
        read_cookie(args.source_cookie_file)
        store = StatusStore()
        stop = threading.Event()
        if args.probe:
            read_source(endpoint, store, stop, once=True, cookie_file=args.source_cookie_file)
            print(status_payload(store).decode('utf-8'))
            return 0 if store.snapshot()['available'] else 1
        token = load_token(args.token_file)
        server = make_server((args.bind, args.port), store, token)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    worker = threading.Thread(target=read_source, args=(endpoint, store, stop),
                              kwargs={'cookie_file': args.source_cookie_file}, daemon=True)
    worker.start()
    LOG.info('DSH status bridge: http://%s:%s/v1/status', args.bind, args.port)
    LOG.info('Token file: %s (copy its contents into the firmware configuration)', args.token_file)
    LOG.info('Read-only DSH source: %s', endpoint)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
