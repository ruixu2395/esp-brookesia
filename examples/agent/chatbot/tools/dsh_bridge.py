#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read local dsh-pet state snapshots and serve a bounded status API to XiaoZhi.

Python 3.10+, standard library only. No model API keys, task writes or screen
capture are used. An optional desktop Host Cookie stays on the PC.
MCP tool registration lives in the ESP32 firmware.
"""

import argparse
import copy
from datetime import datetime, timezone
import hmac
from http.client import HTTPException
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


PLUGIN_PATH = '/dsh-pet-7340'
STATE_PATH = PLUGIN_PATH + '/state'
MAX_SOURCE_BYTES = 262144
MAX_RESPONSE_BYTES = 8192
POLL_TIMEOUT_MS = 10000
STATES = {
    'IDLE': '空闲', 'THINKING': '思考中', 'WORKING': '执行中',
    'RESULT': '整理结果中',
    'WAITING': '等待确认', 'SUCCESS': '已完成', 'ERROR': '发生错误',
}
SOURCE_STATES = {state.lower(): state for state in STATES if state != 'IDLE'}
LOG = logging.getLogger('dsh-bridge')


def text(value, limit=120):
    if not isinstance(value, str):
        return None
    return ' '.join(value.split())[:limit] or None


def is_counter(value):
    return type(value) is int and 0 <= value <= 9223372036854775807


class StatusStore:
    def __init__(self, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.connected = False
        self.current = None
        self.last_sync = None
        self.updated_at = None
        self.connection_error = 'waiting_for_dsh'

    def disconnect(self, reason='source_disconnected'):
        with self.lock:
            self.connected = False
            self.connection_error = reason

    def apply(self, message):
        """Validate the public /state contract; copy only workStatus.state/task.

        A counter is a revision, not a heartbeat. Even unchanged snapshots prove
        the source is reachable. No workStatus data means unknown, not idle.
        """
        if not isinstance(message, dict) or not is_counter(message.get('counter')):
            raise ValueError('invalid source counter')
        sections = message.get('sections')
        leaf = sections.get('workStatus') if isinstance(sections, dict) else None
        if (not isinstance(leaf, dict) or not is_counter(leaf.get('counter'))
                or leaf['counter'] > message['counter'] or 'data' not in leaf):
            raise ValueError('invalid workStatus leaf')
        data = leaf['data']
        record = None
        if data is not None:
            if (not isinstance(data, dict) or 'state' not in data or 'task' not in data
                    or (data['state'] is not None and
                        (not isinstance(data['state'], str) or data['state'] not in SOURCE_STATES))
                    or (data['task'] is not None and not isinstance(data['task'], str))):
                raise ValueError('invalid workStatus data')
            record = {
                'session_id': None,
                'state': 'IDLE' if data['state'] is None else SOURCE_STATES[data['state']],
                'project': None,
                'stage': None,
                'task': text(data['task']),
                'progress': None,
            }
        stamp = leaf['counter']
        updated_at = (datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
                      if 0 < stamp <= 253402300799000 else None)
        with self.lock:
            self.connected = True
            self.current = record
            self.last_sync = self.clock()
            self.connection_error = None if record is not None else 'waiting_for_work_status'
            self.updated_at = updated_at
        return message['counter']

    def snapshot(self):
        with self.lock:
            age = None if self.last_sync is None else max(0, self.clock() - self.last_sync)
            available = self.connected and self.current is not None and age is not None and age < 45
            result = {
                'schema_version': 1,
                'source': 'dsh-pet',
                'capabilities': {'work_status': True, 'current_task': True,
                                 'project': False, 'todo_counts': False, 'task_list': False},
                'connected': self.connected,
                'available': available,
                'stale': not available,
                'updated_at': self.updated_at,
                'sync_age_seconds': None if age is None else round(age, 1),
                'connection_error': self.connection_error,
                'current': copy.deepcopy(self.current),
                'tasks': [],
            }
        current = result['current']
        result['source_note'] = ('dsh-pet 发布的工作状态可能被其他客户端覆盖；'
                                '该接口不提供项目名、待办计数或多任务列表。')
        if not available:
            result['summary'] = '暂时无法获取电脑 DSH 的实时状态；缓存信息不能作为当前状态。'
        elif current:
            parts = [f"DSH：{STATES.get(current['state'], '状态未知')}"]
            if current['task']:
                parts.append(f"当前任务：{current['task']}")
            parts.append('dsh-pet 未提供项目名及待办计数，无法计算完成百分比')
            result['summary'] = '；'.join(parts)
        return result


def status_payload(store):
    result = store.snapshot()
    payload = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('status payload exceeds firmware limit')
    return payload


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def source_endpoint(base_url):
    url = urlsplit(base_url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment
            or url.path.rstrip('/') not in ('', PLUGIN_PATH) or url.port == 0):
        raise ValueError('DSH source must be a loopback HTTP Host or dsh-pet base URL')
    return f'{url.scheme}://{url.netloc}{STATE_PATH}'


def read_cookie(path):
    if path is None:
        return None
    value = Path(path).read_text(encoding='ascii').strip()
    if not value or len(value) > 4096 or any(not 32 <= ord(c) < 127 for c in value):
        raise ValueError('Host Cookie file must contain one nonempty ASCII header value')
    return value


def read_source(endpoint, store, stop, once=False, cookie_file=None):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    counter = None
    while not stop.is_set():
        try:
            headers = {}
            cookie = read_cookie(cookie_file)
            if cookie:
                headers['Cookie'] = cookie
            url = endpoint if counter is None else f'{endpoint}?since={counter}&timeout={POLL_TIMEOUT_MS}'
            request = Request(url, headers={**headers, 'Accept': 'application/json'})
            with opener.open(request, timeout=15) as response:
                body = response.read(MAX_SOURCE_BYTES + 1)
                if response.headers.get_content_type() != 'application/json' or len(body) > MAX_SOURCE_BYTES:
                    raise ValueError('invalid or oversized dsh-pet state response')
                counter = store.apply(json.loads(body))
            if once:
                return
            # Bound request rate if other pet leaves change continuously or a
            # source ignores long polling. Every reply is still a full snapshot.
            if stop.wait(0.1):
                return
            continue
        except HTTPError as error:
            store.disconnect(f'source_http_{error.code}')
        except (OSError, URLError, HTTPException, ValueError, RecursionError):
            store.disconnect('source_unreachable_or_invalid')
        if once:
            return
        counter = None
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
    parser.add_argument('--source-url', required=True,
                        help='Actual DSH Host loopback HTTP URL, optionally ending in /dsh-pet-7340')
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
