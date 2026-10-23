# SPDX-License-Identifier: Apache-2.0
"""Public dsh-pet contract and loopback HTTP tests for the XiaoZhi bridge."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def snapshot(state='working', task='验证音频', counter=1000, leaf_counter=None):
    return {
        'counter': counter,
        'sections': {'workStatus': {
            'counter': counter if leaf_counter is None else leaf_counter,
            'data': {'state': state, 'task': task},
        }},
        'pets': {},
    }


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


@contextmanager
def source_server(respond):
    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            respond(self)

        def log_message(self, *args):
            pass

    with serving(ThreadingHTTPServer(('127.0.0.1', 0), Source)) as address:
        yield address


def reply(handler, value, code=200, content_type='application/json'):
    payload = value if isinstance(value, bytes) else json.dumps(value).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.store = bridge.StatusStore(clock=lambda: self.now)

    def test_public_states_including_result_and_idle(self):
        for source, expected in ((None, 'IDLE'), ('thinking', 'THINKING'),
                                 ('working', 'WORKING'), ('result', 'RESULT'),
                                 ('waiting', 'WAITING'), ('success', 'SUCCESS'), ('error', 'ERROR')):
            with self.subTest(state=source):
                self.store.apply(snapshot(source))
                result = self.store.snapshot()
                self.assertTrue(result['available'])
                self.assertEqual(result['source'], 'dsh-pet')
                self.assertEqual(result['current']['state'], expected)
                self.assertIn(bridge.STATES[expected], result['summary'])

    def test_unsupported_project_counts_and_task_list_are_explicit(self):
        value = snapshot()
        value['sections']['workStatus']['data'].update(
            project='not-in-public-contract', progress={'completed': 2, 'total': 4})
        value['tasks'] = [{'project': 'not-in-public-contract'}]
        self.store.apply(value)
        result = self.store.snapshot()
        for field in ('project', 'progress', 'session_id', 'stage'):
            self.assertIsNone(result['current'][field])
        self.assertEqual(result['tasks'], [])
        for field in ('project', 'todo_counts', 'task_list'):
            self.assertFalse(result['capabilities'][field])
        self.assertIn('无法计算', result['summary'])
        self.assertNotIn('not-in-public-contract', bridge.status_payload(self.store).decode())

    def test_unchanged_counter_is_fresh_while_source_is_reachable(self):
        value = snapshot()
        self.store.apply(value)
        updated_at = self.store.snapshot()['updated_at']
        self.now += 46
        self.assertFalse(self.store.snapshot()['available'])
        self.store.apply(value)
        result = self.store.snapshot()
        self.assertTrue(result['available'])
        self.assertEqual(result['sync_age_seconds'], 0)
        self.assertEqual(result['updated_at'], updated_at)

    def test_never_written_work_status_is_unknown_not_idle_and_clears_cache(self):
        self.store.apply(snapshot())
        self.store.apply({'counter': 10, 'sections': {'workStatus': {'counter': 0, 'data': None}}})
        result = self.store.snapshot()
        self.assertTrue(result['connected'])
        self.assertFalse(result['available'])
        self.assertIsNone(result['current'])
        self.assertIsNone(result['updated_at'])
        self.assertEqual(result['connection_error'], 'waiting_for_work_status')

    def test_disconnect_and_new_snapshot_replace_old_task(self):
        self.store.apply(snapshot(task='旧任务'))
        self.store.disconnect('source_http_404')
        result = self.store.snapshot()
        self.assertFalse(result['available'])
        self.assertTrue(result['stale'])
        self.assertIn('不能作为当前状态', result['summary'])
        # A restarted source's full snapshot is authoritative even if its
        # revision is lower; never merge text from the previous instance.
        self.store.apply(snapshot(state=None, task=None, counter=1))
        result = self.store.snapshot()
        self.assertTrue(result['available'])
        self.assertIsNone(result['current']['task'])
        self.assertIsNone(result['connection_error'])

    def test_malformed_contract_is_rejected_without_refreshing_cache(self):
        malformed = [None, [], {}, {'counter': True}, snapshot(state='unknown'),
                     snapshot(state=[]), snapshot(task=3), snapshot(counter=-1),
                     snapshot(counter=1, leaf_counter=2), snapshot(counter=2**63),
                     {'counter': 1, 'sections': []},
                     {'counter': 1, 'sections': {'workStatus': {'counter': 1}}},
                     {'counter': 1, 'sections': {'workStatus': {'counter': 1, 'data': {}}}}]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.apply(value)
        self.assertFalse(self.store.snapshot()['available'])

    def test_allowlist_bounds_and_work_timestamp_not_other_pet_timestamp(self):
        value = snapshot(task='任' * 3000, counter=9000, leaf_counter=1000)
        value['sections']['balance'] = {'data': {'secret': 'never-export'}}
        value['sections']['config'] = {'data': {'token': 'never-export'}}
        value['pets'] = {'main': {'say': {'data': {'text': 'never-export'}}}}
        self.store.apply(value)
        payload = bridge.status_payload(self.store)
        result = json.loads(payload)
        self.assertLessEqual(len(payload), bridge.MAX_RESPONSE_BYTES)
        self.assertNotIn('never-export', payload.decode())
        self.assertEqual(len(result['current']['task']), 120)
        self.assertEqual(result['updated_at'], '1970-01-01T00:00:01+00:00')
        self.store.apply(snapshot(counter=2**63 - 1))
        self.assertIsNone(self.store.snapshot()['updated_at'])


class WireTest(unittest.TestCase):
    def test_real_http_source_to_authenticated_device_query(self):
        paths = []

        def respond(handler):
            paths.append(handler.path)
            reply(handler, snapshot())

        store = bridge.StatusStore()
        token = 'test-only-' + 'a' * 32
        with source_server(respond) as source:
            bridge.read_source(bridge.source_endpoint(source), store, threading.Event(), once=True)
        self.assertEqual(paths, [bridge.STATE_PATH])
        with serving(bridge.make_server(('127.0.0.1', 0), store, token)) as address:
            for supplied in ('', 'Bearer wrong-token'):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(address + '/v1/status', headers={'Authorization': supplied}), timeout=2)
                self.assertEqual(failure.exception.code, 401)
            request = Request(address + '/v1/status', headers={'Authorization': 'Bearer ' + token})
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
            self.assertTrue(result['available'])
            self.assertEqual(result['current']['task'], '验证音频')
            self.assertIsNone(result['current']['progress'])
            for method, path, status in (('POST', '/v1/status', 405), ('GET', '/other', 404)):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(address + path, method=method,
                                    headers={'Authorization': 'Bearer ' + token}), timeout=2)
                self.assertEqual(failure.exception.code, status)

    def test_long_poll_uses_top_counter_and_updates_after_unchanged_reply(self):
        paths = []
        stop = threading.Event()

        def respond(handler):
            paths.append(handler.path)
            last = len(paths) == 3
            reply(handler, snapshot('result' if last else 'working', counter=1200 if last else 1000,
                                    leaf_counter=1100 if last else 800))
            if last:
                stop.set()

        store = bridge.StatusStore()
        with source_server(respond) as source:
            bridge.read_source(bridge.source_endpoint(source), store, stop)
        self.assertEqual(paths, [bridge.STATE_PATH] +
                         [bridge.STATE_PATH + '?since=1000&timeout=10000'] * 2)
        self.assertEqual(store.snapshot()['current']['state'], 'RESULT')

    def test_failure_retries_with_full_snapshot(self):
        paths = []
        stop = threading.Event()

        def respond(handler):
            paths.append(handler.path)
            if len(paths) == 2:
                reply(handler, {}, code=503)
                return
            reply(handler, snapshot(counter=len(paths)))
            if len(paths) == 3:
                stop.set()

        store = bridge.StatusStore()
        with source_server(respond) as source:
            bridge.read_source(bridge.source_endpoint(source), store, stop)
        self.assertEqual(paths, [bridge.STATE_PATH, bridge.STATE_PATH + '?since=1&timeout=10000',
                                bridge.STATE_PATH])
        self.assertTrue(store.snapshot()['available'])

    def test_http_html_invalid_json_and_oversized_source_are_unavailable(self):
        cases = [(b'{}', 404, 'application/json', 'source_http_404'),
                 (b'<html/>', 200, 'text/html', 'source_unreachable_or_invalid'),
                 (b'{invalid', 200, 'application/json', 'source_unreachable_or_invalid'),
                 (b'{}', 200, 'application/json', 'source_unreachable_or_invalid'),
                 (b'x' * (bridge.MAX_SOURCE_BYTES + 1), 200, 'application/json',
                  'source_unreachable_or_invalid')]
        for body, code, content_type, expected in cases:
            with self.subTest(code=code, size=len(body)):
                store = bridge.StatusStore()
                with source_server(lambda h: reply(h, body, code, content_type)) as source:
                    bridge.read_source(bridge.source_endpoint(source), store, threading.Event(), once=True)
                self.assertFalse(store.snapshot()['available'])
                self.assertEqual(store.snapshot()['connection_error'], expected)

    def test_optional_host_cookie_stays_on_pc(self):
        def respond(handler):
            if handler.headers.get('Cookie') != 'host-session=test-only':
                reply(handler, {}, code=401)
            else:
                reply(handler, snapshot())

        store = bridge.StatusStore()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'host-cookie.txt'
            path.write_text('host-session=test-only\n', encoding='ascii')
            with source_server(respond) as source:
                endpoint = bridge.source_endpoint(source)
                bridge.read_source(endpoint, store, threading.Event(), once=True)
                self.assertEqual(store.snapshot()['connection_error'], 'source_http_401')
                bridge.read_source(endpoint, store, threading.Event(), once=True, cookie_file=path)
                self.assertTrue(store.snapshot()['available'])
                self.assertNotIn('host-session', bridge.status_payload(store).decode())
            path.write_text('cookie=abc\nInjected: header', encoding='ascii')
            with self.assertRaises(ValueError):
                bridge.read_cookie(path)

    def test_interrupted_chunked_response_is_unavailable(self):
        def respond(handler):
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/json')
            handler.send_header('Transfer-Encoding', 'chunked')
            handler.end_headers()
            handler.wfile.write(b'20\r\n{')
            handler.close_connection = True

        store = bridge.StatusStore()
        store.apply(snapshot())
        with source_server(respond) as source:
            bridge.read_source(bridge.source_endpoint(source), store, threading.Event(), once=True)
        self.assertFalse(store.snapshot()['available'])
        self.assertEqual(store.snapshot()['connection_error'], 'source_unreachable_or_invalid')

    def test_redirect_is_rejected_without_forwarding_credentials(self):
        paths = []

        def respond(handler):
            paths.append(handler.path)
            handler.send_response(302)
            handler.send_header('Location', '/unexpected')
            handler.send_header('Content-Length', '0')
            handler.end_headers()

        store = bridge.StatusStore()
        with source_server(respond) as source:
            bridge.read_source(bridge.source_endpoint(source), store, threading.Event(), once=True)
        self.assertEqual(paths, [bridge.STATE_PATH])
        self.assertEqual(store.snapshot()['connection_error'], 'source_http_302')

    def test_source_urls_and_token_persistence(self):
        for url in ('https://example.com', 'http://192.168.1.2:3080',
                    'http://127.0.0.1@evil.example', 'http://localhost:3080/?x=1',
                    'http://localhost:3080/plugins/dsh-dafeiyu', 'http://localhost:99999',
                    'http://localhost:0', 'http://localhost:abc'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bridge.source_endpoint(url)
        for suffix in ('', '/', bridge.PLUGIN_PATH, bridge.PLUGIN_PATH + '/'):
            self.assertEqual(bridge.source_endpoint('http://127.0.0.1:3080' + suffix),
                             'http://127.0.0.1:3080/dsh-pet-7340/state')
        self.assertEqual(bridge.source_endpoint('http://[::1]:3080'),
                         'http://[::1]:3080/dsh-pet-7340/state')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'token.txt'
            token = bridge.load_token(path)
            self.assertEqual(token, bridge.load_token(path))
            path.write_text('invalid\n', encoding='ascii')
            with self.assertRaises(ValueError):
                bridge.load_token(path)


if __name__ == '__main__':
    unittest.main()
