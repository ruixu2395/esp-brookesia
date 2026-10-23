# SPDX-License-Identifier: Apache-2.0
"""Network and data contract tests for the DSH-to-XiaoZhi bridge."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dsh_bridge as bridge


def event(kind='state', timestamp=1000, **fields):
    return {'protocolVersion': 1, 'kind': kind, 'timestamp': timestamp, **fields}


@contextmanager
def serving(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.store = bridge.StatusStore(clock=lambda: self.now)
        self.store.begin_connection()

    def test_real_protocol_progress_and_unknown_progress(self):
        self.store.apply(event(sessionId='a', state='WORKING', project='机器人'))
        self.store.apply(event('task', timestamp=1001, sessionId='a', task='验证驱动',
                               project='机器人', progress={'completed': 2, 'total': 5, 'current': 3}))
        result = self.store.snapshot()
        self.assertTrue(result['available'])
        self.assertIn('待办完成 2/5', result['summary'])
        self.assertEqual(result['current']['project'], '机器人')
        self.store.apply(event(timestamp=1002, sessionId='a', state='THINKING'))
        self.assertIsNone(self.store.snapshot()['current']['progress'])
        self.assertIn('无法计算', self.store.snapshot()['summary'])

    def test_snapshot_replay_never_restores_old_session_todo(self):
        self.store.apply(event(timestamp=2000, sessionId='new', state='WORKING', project='新项目'))
        self.store.apply(event('task', timestamp=1000, sessionId='old', task='旧待办',
                               progress={'completed': 8, 'total': 9}))
        self.assertEqual(self.store.snapshot()['current']['session_id'], 'new')
        self.assertIsNone(self.store.snapshot()['current']['task'])
        self.store.apply(event('task', timestamp=1999, sessionId='new', task='上一轮待办',
                               progress={'completed': 3, 'total': 4}))
        self.assertIsNone(self.store.snapshot()['current']['progress'])

    def test_multiple_sessions_and_list_clear(self):
        self.store.apply(event(sessionId='a', state='WORKING', project='项目 A',
                               progress={'completed': 1, 'total': 3}))
        self.store.apply(event(timestamp=1001, sessionId='b', state='THINKING', project='项目 B'))
        self.store.apply(event('tasks', timestamp=1002, tasks=[
            {'sessionId': 'a', 'state': 'WORKING', 'project': '项目 A'},
            {'sessionId': 'b', 'state': 'THINKING', 'project': '项目 B'},
        ]))
        result = self.store.snapshot()
        # Counts cached from a different selected session may be from an old
        # turn. The source's task list itself does not supply those counts.
        self.assertIsNone(result['tasks'][0]['progress'])
        self.assertIsNone(result['tasks'][1]['progress'])
        self.store.apply(event('tasks', timestamp=1003, tasks=[]))
        self.assertEqual(self.store.snapshot()['tasks'], [])

    def test_disconnect_stale_data_and_reconnect(self):
        self.assertFalse(self.store.snapshot()['available'])
        self.store.apply(event(sessionId='a', state='WORKING'))
        self.now += 46
        self.assertFalse(self.store.snapshot()['available'])
        self.store.apply(event('pulse', timestamp=2000, state='WORKING'))
        self.assertTrue(self.store.snapshot()['available'])
        self.store.disconnect('source_http_404')
        result = self.store.snapshot()
        self.assertFalse(result['available'])
        self.assertTrue(result['stale'])
        self.assertIn('不能作为当前状态', result['summary'])
        self.store.begin_connection()
        self.assertIsNone(self.store.snapshot()['current'])
        self.assertFalse(self.store.snapshot()['available'])

    def test_fields_are_allowlisted_bounded_and_malformed_events_are_ignored(self):
        for message in (None, [], {}, event(state={}), event(state=[]),
                        event(protocolVersion=True, state='WORKING'), event(timestamp=float('nan'))):
            self.store.apply(message)
        self.assertFalse(self.store.snapshot()['available'])
        self.store.apply(event(sessionId='a', state='WORKING', project='项' * 3000,
                               task='任' * 3000, token='never-export', config={'secret': 'never-export'},
                               progress={'completed': True, 'total': 5}))
        self.store.apply(event('tasks', tasks=[
            {'sessionId': str(i), 'state': 'WORKING', 'task': '任' * 3000, 'project': '项' * 3000}
            for i in range(100)
        ]))
        payload = bridge.status_payload(self.store)
        self.assertLessEqual(len(payload), bridge.MAX_RESPONSE_BYTES)
        self.assertNotIn('never-export', payload.decode())
        self.assertLessEqual(len(json.loads(payload)['tasks']), bridge.MAX_TASKS)
        self.assertIsNone(json.loads(payload)['current']['progress'])


class WireTest(unittest.TestCase):
    def test_sse_comments_multiline_invalid_and_size_limit(self):
        wire = (b': comment\r\nretry: 2000\r\n\r\ndata: invalid\n\n'
                b'data: {"kind": "state",\ndata: "state": "WORKING"}\n\n')
        self.assertEqual(list(bridge.iter_sse(io.BytesIO(wire))), [{'kind': 'state', 'state': 'WORKING'}])
        with self.assertRaises(ValueError):
            list(bridge.iter_sse(io.BytesIO(b'data: ' + b'x' * bridge.MAX_EVENT_BYTES + b'\n\n')))

    def test_real_http_source_to_authenticated_device_query(self):
        snapshot = event(sessionId='desktop-1', state='WORKING', project='esp-brookesia',
                         task='验证音频', progress={'completed': 2, 'total': 4})

        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if self.path == bridge.CONFIG_PATH:
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"enabled":true}')
                    return
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(('data: ' + json.dumps(snapshot) + '\n\n').encode())

            def log_message(self, *args):
                pass

        store = bridge.StatusStore()
        token = 'test-only-' + 'a' * 32
        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Source)) as source:
            bridge.read_source(bridge.source_endpoint(source), store, threading.Event(), once=True)
        with serving(bridge.make_server(('127.0.0.1', 0), store, token)) as address:
            with self.assertRaises(HTTPError) as failure:
                urlopen(address + '/v1/status', timeout=2)
            self.assertEqual(failure.exception.code, 401)
            request = Request(address + '/v1/status', headers={'Authorization': 'Bearer ' + token})
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
            self.assertTrue(result['available'])
            self.assertEqual(result['current']['project'], 'esp-brookesia')
            self.assertEqual(result['current']['progress'], {'completed': 2, 'total': 4})
            for method, path, status in (('POST', '/v1/status', 405), ('GET', '/other', 404)):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(address + path, method=method,
                                    headers={'Authorization': 'Bearer ' + token}), timeout=2)
                self.assertEqual(failure.exception.code, status)

    def test_wrong_source_http_and_html_are_reported(self):
        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == bridge.CONFIG_PATH:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"enabled":true}')
                    return
                self.send_response(404 if self.path.startswith('/missing') else 200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()

            def log_message(self, *args):
                pass

        store = bridge.StatusStore()
        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Source)) as source:
            bridge.read_source(source + '/missing', store, threading.Event(), once=True)
            self.assertEqual(store.snapshot()['connection_error'], 'source_http_404')
            bridge.read_source(source + '/html', store, threading.Event(), once=True)
            self.assertFalse(store.snapshot()['available'])

    def test_desktop_host_cookie_and_disabled_plugin(self):
        enabled = [True]
        paths = []

        class Desktop(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.headers.get('Cookie') != 'host-session=test-only':
                    self.send_response(401)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json' if self.path == bridge.CONFIG_PATH
                                 else 'text/event-stream')
                self.end_headers()
                if self.path == bridge.CONFIG_PATH:
                    self.wfile.write(json.dumps({'enabled': enabled[0]}).encode())
                else:
                    self.wfile.write(('data: ' + json.dumps(event(state='IDLE')) + '\n\n').encode())

            def log_message(self, *args):
                pass

        store = bridge.StatusStore()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'host-cookie.txt'
            path.write_text('host-session=test-only\n', encoding='ascii')
            with serving(ThreadingHTTPServer(('127.0.0.1', 0), Desktop)) as address:
                endpoint = bridge.source_endpoint(address)
                bridge.read_source(endpoint, store, threading.Event(), once=True)
                self.assertEqual(store.snapshot()['connection_error'], 'source_http_401')
                bridge.read_source(endpoint, store, threading.Event(), once=True, cookie_file=path)
                self.assertTrue(store.snapshot()['available'])
                self.assertNotIn('host-session', bridge.status_payload(store).decode())
                enabled[0] = False
                paths.clear()
                bridge.read_source(endpoint, store, threading.Event(), once=True, cookie_file=path)
                self.assertFalse(store.snapshot()['available'])
                self.assertEqual(store.snapshot()['connection_error'], 'source_plugin_disabled')
                self.assertEqual(paths, [bridge.CONFIG_PATH])
            path.write_text('cookie=abc\nInjected: header', encoding='ascii')
            with self.assertRaises(ValueError):
                bridge.read_cookie(path)

    def test_source_must_be_local_and_token_persists(self):
        for url in ('https://example.com', 'http://192.168.1.2:3080',
                    'http://127.0.0.1@evil.example', 'http://localhost:3080/?x=1'):
            with self.assertRaises(ValueError):
                bridge.source_endpoint(url)
        self.assertEqual(bridge.source_endpoint('http://127.0.0.1:3080/'),
                         'http://127.0.0.1:3080/plugins/dsh-dafeiyu/events')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'token.txt'
            token = bridge.load_token(path)
            self.assertEqual(token, bridge.load_token(path))
            path.write_text('invalid\n', encoding='ascii')
            with self.assertRaises(ValueError):
                bridge.load_token(path)


if __name__ == '__main__':
    unittest.main()
