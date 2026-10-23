// SPDX-License-Identifier: Apache-2.0
const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { mkdtempSync, readFileSync, rmSync, existsSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { spawnSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { Forwarder, configuration, workSnapshot, request, apply } = require('../index.cjs');

const token = 'test-only-' + 'a'.repeat(32);
const snapshot = (state = 'working', task = '验证音频', counter = 1000) => ({ counter,
  sections: { workStatus: { counter, data: { state, task } } }, pets: {} });
const response = (body, status = 200) => ({ status, headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body) });

function make(extra = {}) {
  return new Forwarder({ deviceUrl: 'http://192.168.1.80:8766', token,
    sourceOrigin: () => 'http://127.0.0.1:3080', authenticatedUrl: origin => origin + '/?token=test-only', ...extra });
}

async function listen(handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: 'http://127.0.0.1:' + server.address().port };
}

async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}

async function until(predicate) {
  for (let i = 0; i < 100; ++i) {
    if (predicate()) return;
    await delay(10);
  }
  assert.fail('Expected state was not reached');
}

test('allowlisted work status contract includes null idle and result', () => {
  for (const state of [null, 'thinking', 'working', 'result', 'waiting', 'success', 'error']) {
    const value = snapshot(state);
    value.sections.balance = { secret: 'never-export' };
    value.pets.main = { say: { text: 'never-export' } };
    value.sections.workStatus.data.project = 'never-export';
    const result = workSnapshot(value);
    assert.deepEqual(result, { available: true, state, task: '验证音频' });
    assert(!JSON.stringify(result).includes('never-export'));
  }
  assert.deepEqual(workSnapshot({ counter: 0, sections: { workStatus: { counter: 0, data: null } } }),
    { available: false, state: null, task: null });
  assert.equal(Array.from(workSnapshot(snapshot('working', '😀'.repeat(300))).task).length, 120);
});

test('malformed source snapshots are rejected', () => {
  for (const body of [null, {}, snapshot('invalid'), snapshot([]), snapshot('working', 1),
    snapshot('working', null, -1), { counter: true }, { counter: 2, sections: {} },
    { counter: 1, sections: { workStatus: { counter: 2, data: null } } }]) {
    assert.throws(() => workSnapshot(body));
  }
});

test('device configuration limits the destination to a private LAN and validates tokens', () => {
  assert.equal(configuration({ deviceUrl: 'http://192.168.1.80:8766', token }).deviceUrl,
    'http://192.168.1.80:8766');
  for (const deviceUrl of ['http://example.com', 'http://8.8.8.8', 'http://127.0.0.1',
    'http://192.168.1.80/path', 'http://user:pass@192.168.1.80', 'https://192.168.1.80']) {
    assert.throws(() => configuration({ deviceUrl, token }));
  }
  assert.throws(() => configuration({ deviceUrl: 'http://10.0.0.2', token: 'short' }));
});

test('source long polling uses the top counter; unchanged data refreshes freshness', async () => {
  const calls = [];
  let now = 0;
  const f = make({ clock: () => now, transport: async (url, options) => {
    calls.push({ url, options });
    return response(snapshot());
  } });
  await f.readOnce();
  now = 50000;
  await f.readOnce();
  assert.equal(calls[1].url, 'http://127.0.0.1:3080/dsh-pet-7340/state?since=1000&timeout=10000');
  assert.equal(f.lastRead, 50000);
  assert.equal(f.effectiveSnapshot().state, 'working');
});

test('desktop Host auth uses launch cookie only for loopback, never for ESP32', async () => {
  const calls = [];
  const f = make({ transport: async (url, options) => {
    calls.push({ url: String(url), options });
    if (String(url).includes('?token=')) return { status: 303,
      headers: { 'set-cookie': ['host-session=test-only; HttpOnly; Path=/'] }, body: '' };
    if (options.method === 'POST') return response({ ok: true, sequence: options.body.sequence });
    if (String(url).includes('/state') && !options.headers.Cookie) return response({}, 401);
    return response(snapshot());
  } });
  await assert.rejects(f.readOnce(), /source_retry_after_auth/);
  await f.readOnce();
  await f.pushOnce();
  assert.equal(calls[2].options.headers.Cookie, 'host-session=test-only');
  assert.equal(calls[3].options.headers.Cookie, undefined);
  assert.equal(calls[3].options.headers.Authorization, 'Bearer ' + token);
  assert(!JSON.stringify(f.status()).includes(token));
  assert(!JSON.stringify(f.status()).includes('host-session'));
});

test('authentication URL must stay on the exact loopback origin', async () => {
  let count = 0;
  const f = make({ authenticatedUrl: () => 'http://evil.example/?token=secret',
    transport: async () => { ++count; return response({}, 401); } });
  await assert.rejects(f.readOnce(), /source_auth_origin/);
  assert.equal(count, 1);
});

test('real loopback source and ESP32 contract: POST ordering, task changes, heartbeat and cancellation', async () => {
  const posts = [];
  const paths = [];
  let state = 'working';
  let sourceDown = false;
  const source = await listen((req, res) => {
    paths.push(req.url);
    res.writeHead(sourceDown ? 503 : 200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(snapshot(state)));
  });
  const device = await listen(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    assert.equal(req.url, '/api/pet/state');
    assert.equal(req.headers.authorization, 'Bearer ' + token);
    assert.equal(req.headers.cookie, undefined);
    const body = JSON.parse(Buffer.concat(chunks));
    posts.push(body);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, sequence: body.sequence }));
  });
  // Test-only transport address injection; production configuration rejects loopback devices.
  const f = make({ sourceOrigin: () => source.origin, deviceUrl: device.origin, heartbeatMs: 40, retryMs: 10 });
  try {
    void f.start();
    await until(() => posts.filter(p => p.available && p.state === 'working').length >= 2);
    state = 'waiting';
    await until(() => posts.some(p => p.available && p.state === 'waiting'));
    sourceDown = true;
    await until(() => posts.at(-1).available === false);
    assert.equal(posts.at(-1).task, null);
    for (let i = 1; i < posts.length; ++i) assert(posts[i].sequence > posts[i - 1].sequence);
    assert.equal(new Set(posts.map(p => p.publisher_id)).size, 1);
    assert(paths.some(p => p.includes('?since=1000&timeout=10000')));
  } finally {
    f.stop();
    await f.running;
    await close(source.server);
    await close(device.server);
  }
  assert.equal(f.delays.size, 0);
});

test('device failure retries newest snapshot instead of replaying stale animation', async () => {
  let failed = true;
  const sent = [];
  const f = make({ clock: () => 1, transport: async (url, options) => {
    sent.push(options.body);
    return response(failed ? {} : { ok: true, sequence: options.body.sequence }, failed ? 401 : 200);
  } });
  f.lastRead = 1;
  f.latest = { available: true, state: 'working', task: '旧任务' };
  await assert.rejects(f.pushOnce(), /device_http_401/);
  failed = false;
  f.latest = { available: true, state: 'success', task: null };
  await f.pushOnce();
  assert.equal(sent[1].state, 'success');
  assert.equal(sent[1].task, null);
});

test('slow POST cannot lose a concurrent source change and never overlaps another POST', async () => {
  let count = 0;
  let inFlight = 0;
  const f = make({ clock: () => 1, heartbeatMs: 5000, transport: async (url, options) => {
    assert.equal(++inFlight, 1);
    await delay(10);
    if (++count === 1) f.latest = { available: true, state: 'waiting', task: null };
    else f.stop();
    --inFlight;
    return response({ ok: true, sequence: options.body.sequence });
  } });
  f.lastRead = 1;
  f.latest = { available: true, state: 'working', task: null };
  await f.pushLoop();
  assert.equal(count, 2);
});

test('old cached source data produces unavailable heartbeats', async () => {
  const f = make({ clock: () => 30000, transport: async (url, options) => {
    assert.equal(options.body.available, false);
    assert.equal(options.body.task, null);
    return response({ ok: true, sequence: options.body.sequence });
  } });
  f.lastRead = 1;
  f.latest = { available: true, state: 'working', task: 'old' };
  await f.pushOnce();
  assert.equal(f.sentKey, JSON.stringify(f.effectiveSnapshot()));
});

test('transport enforces response bounds, wall timeout and abort; redirects are not followed', async () => {
  let calls = 0;
  const target = await listen((req, res) => {
    ++calls;
    if (req.url === '/slow') return;
    if (req.url === '/redirect') { res.writeHead(302, { Location: '/unexpected' }); res.end(); return; }
    res.end('x'.repeat(500));
  });
  try {
    await assert.rejects(request(target.origin, { maxBytes: 100 }), /response_too_large/);
    await assert.rejects(request(target.origin + '/slow', { timeout: 30 }), /request_timeout/);
    const controller = new AbortController();
    const pending = request(target.origin + '/slow', { signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, /abort/iu);
    const before = calls;
    assert.equal((await request(target.origin + '/redirect')).status, 302);
    assert.equal(calls, before + 1);
  } finally { await close(target.server); }
});

test('Cordis entry rejects missing config without leaking its contents', () => {
  let message;
  apply({ logger: { warn: text => { message = text; } } }, { configFile: '/nonexistent/dsh-config.json' });
  assert(message.includes('esp32-sync.json'));
});

test('DSH ESM loader sees the Cordis named exports', async () => {
  const plugin = await import('../index.cjs');
  assert.equal(plugin.name, 'dsh-esp32-sync');
  assert.deepEqual(plugin.inject, ['webServer', 'connection']);
  assert.equal(typeof plugin.apply, 'function');
});

test('plugin unload aborts an in-flight source long poll and clears heartbeat timers', async () => {
  let reading = false;
  const source = await listen(() => { reading = true; });
  const f = make({ sourceOrigin: () => source.origin, transport: async (url, options) => {
    if (options.method === 'POST') return response({ ok: true, sequence: options.body.sequence });
    return request(url, options);
  } });
  try {
    void f.start();
    await until(() => reading);
    f.stop();
    await Promise.race([f.running, delay(300).then(() => { throw new Error('unload did not abort'); })]);
    assert.equal(f.delays.size, 0);
  } finally { f.stop(); await close(source.server); }
});

test('configuration generator writes matching secrets, reuses them and prints only paths', () => {
  const directory = mkdtempSync(join(tmpdir(), 'dsh-esp32-config-'));
  const config = join(directory, 'esp32-sync.json');
  const firmware = join(directory, 'sdkconfig.defaults');
  const run = device => spawnSync(process.execPath, [resolve(__dirname, '../configure.cjs'),
    '--device-url', device, '--config', config, '--firmware-config', firmware], { encoding: 'utf8' });
  try {
    assert.equal(run('http://192.168.1.80').status, 1);
    assert(!existsSync(config));
    const result = run('http://192.168.1.80:8766');
    assert.equal(result.status, 0);
    const data = JSON.parse(readFileSync(config));
    assert.match(data.token, /^[A-Za-z0-9_-]{43}$/u);
    assert(!result.stdout.includes(data.token));
    assert(readFileSync(firmware, 'utf8').includes('CONFIG_EXAMPLE_DSH_PET_SYNC_TOKEN="' + data.token + '"'));
    assert.equal(run('http://192.168.1.81:8767').status, 0);
    assert.equal(JSON.parse(readFileSync(config)).token, data.token);
    assert(readFileSync(firmware, 'utf8').includes('CONFIG_EXAMPLE_DSH_PET_SYNC_PORT=8767'));
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
