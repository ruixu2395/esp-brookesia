// SPDX-License-Identifier: Apache-2.0
// Single-file Cordis Host plugin. No build step, npm dependencies or model calls.
const http = require('node:http');
const { randomUUID } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { homedir } = require('node:os');
const { join } = require('node:path');

const STATES = new Set(['thinking', 'working', 'result', 'waiting', 'success', 'error']);
const STATE_PATH = '/dsh-pet-7340/state';
const DEVICE_PATH = '/api/pet/state';
const MAX_SOURCE_BYTES = 256 * 1024;

function deviceOrigin(value) {
  const url = new URL(value);
  const parts = url.hostname.split('.').map(Number);
  const privateIP = parts.length === 4 && parts.every(n => Number.isInteger(n) && n >= 0 && n <= 255)
    && (parts[0] === 10 || (parts[0] === 192 && parts[1] === 168)
        || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31));
  if (url.protocol !== 'http:' || !privateIP || url.username || url.password
      || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('deviceUrl must be an HTTP private LAN IPv4 origin, e.g. http://192.168.1.80:8766');
  }
  return url.origin;
}

function configuration(raw) {
  if (!raw || typeof raw !== 'object' || typeof raw.token !== 'string'
      || !/^[A-Za-z0-9_-]{24,128}$/.test(raw.token)) {
    throw new Error('Configure deviceUrl and a 24-128 character URL-safe token in esp32-sync.json');
  }
  return { deviceUrl: deviceOrigin(raw.deviceUrl), token: raw.token };
}

function workSnapshot(body) {
  const leaf = body?.sections?.workStatus;
  if (!Number.isSafeInteger(body?.counter) || body.counter < 0 || !leaf
      || !Number.isSafeInteger(leaf.counter) || leaf.counter < 0 || leaf.counter > body.counter
      || !Object.hasOwn(leaf, 'data')) throw new Error('source_schema');
  if (leaf.data === null) return { available: false, state: null, task: null };
  const data = leaf.data;
  if (!data || typeof data !== 'object' || !Object.hasOwn(data, 'state')
      || (data.state !== null && !STATES.has(data.state))
      || !Object.hasOwn(data, 'task') || (data.task !== null && typeof data.task !== 'string')) {
    throw new Error('source_schema');
  }
  return { available: true, state: data.state,
    task: data.task === null ? null : Array.from(data.task.replace(/\s+/gu, ' ').trim()).slice(0, 120).join('') };
}

function request(url, { method = 'GET', headers = {}, body, signal, timeout = 15000,
                        maxBytes = MAX_SOURCE_BYTES } = {}) {
  // Native http: no system proxy, redirects, automatic cookies or browser APIs.
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
    const req = http.request(url, { method, signal, headers: {
      Accept: 'application/json', ...headers,
      ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': payload.length } : {}),
    } }, res => {
      let length = 0;
      const chunks = [];
      res.on('data', chunk => {
        length += chunk.length;
        if (length > maxBytes) res.destroy(new Error('response_too_large'));
        else chunks.push(chunk);
      });
      res.on('error', reject);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8') }));
    });
    // Wall-clock deadline, not just a socket-idle timeout.
    const timer = setTimeout(() => req.destroy(new Error('request_timeout')), timeout);
    req.on('close', () => clearTimeout(timer));
    req.on('error', reject);
    req.end(payload);
  });
}

class Forwarder {
  constructor({ deviceUrl, token, sourceOrigin, authenticatedUrl, transport = request,
                clock = Date.now, heartbeatMs = 5000, retryMs = 1000 }) {
    this.deviceUrl = deviceUrl;
    this.token = token;
    this.sourceOrigin = sourceOrigin;
    this.authenticatedUrl = authenticatedUrl;
    this.transport = transport;
    this.clock = clock;
    this.heartbeatMs = heartbeatMs;
    this.retryMs = retryMs;
    this.publisher = randomUUID();
    this.sequence = 0;
    this.controller = new AbortController();
    this.cookie = null;
    this.origin = null;
    this.counter = undefined;
    this.latest = { available: false, state: null, task: null };
    this.lastRead = null;
    this.lastPush = null;
    this.sentKey = null;
    this.sourceError = 'waiting_for_source';
    this.deviceError = 'waiting_for_device';
    this.wakePush = null;
    this.delays = new Map();
  }

  options(extra = {}) { return { signal: this.controller.signal, ...extra }; }

  delay(ms, push = false) {
    if (this.controller.signal.aborted) return Promise.resolve();
    return new Promise(resolve => {
      const done = () => {
        clearTimeout(timer);
        this.delays.delete(timer);
        if (this.wakePush === done) this.wakePush = null;
        resolve();
      };
      const timer = setTimeout(done, ms);
      this.delays.set(timer, done);
      if (push) this.wakePush = done;
    });
  }

  async authenticate() {
    const launch = new URL(this.authenticatedUrl(this.origin));
    if (launch.origin !== this.origin || launch.pathname !== '/') throw new Error('source_auth_origin');
    const reply = await this.transport(launch, this.options({ maxBytes: 64 * 1024, timeout: 3000 }));
    const cookies = reply.headers['set-cookie'];
    if (reply.status !== 303 || !Array.isArray(cookies) || cookies.length === 0) {
      throw new Error('source_auth_failed');
    }
    this.cookie = cookies.map(value => value.split(';', 1)[0]).join('; ');
  }

  async readOnce() {
    const origin = this.sourceOrigin();
    const parsed = new URL(origin);
    if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1'
        || parsed.username || parsed.password || parsed.origin !== origin) throw new Error('source_origin');
    if (this.origin !== origin) {
      this.origin = origin;
      this.cookie = null;
      this.counter = undefined;
    }
    const query = this.counter === undefined ? '' : `?since=${this.counter}&timeout=10000`;
    const reply = await this.transport(origin + STATE_PATH + query, this.options({
      headers: this.cookie ? { Cookie: this.cookie } : {},
    }));
    if (reply.status === 401 || reply.status === 403) {
      this.cookie = null;
      await this.authenticate();
      throw new Error('source_retry_after_auth');
    }
    if (reply.status !== 200) throw new Error(`source_http_${reply.status}`);
    if (!/^application\/json(?:;|$)/iu.test(reply.headers['content-type'] ?? '')) {
      throw new Error('source_content_type');
    }
    const body = JSON.parse(reply.body);
    const latest = workSnapshot(body);
    const changed = JSON.stringify(latest) !== JSON.stringify(this.latest);
    this.latest = latest;
    this.counter = body.counter;
    this.lastRead = this.clock();
    this.sourceError = latest.available ? null : 'waiting_for_work_status';
    if (changed) this.wakePush?.();
  }

  async readLoop() {
    while (!this.controller.signal.aborted) {
      try {
        await this.readOnce();
        await this.delay(100);
      } catch (error) {
        if (this.controller.signal.aborted) break;
        // Never forward stale task text after a source failure.
        this.latest = { available: false, state: null, task: null };
        this.counter = undefined;
        this.sourceError = /^source_[a-z0-9_]+$/u.test(error.message) ? error.message : 'source_unavailable';
        this.wakePush?.();
        await this.delay(this.retryMs);
      }
    }
  }

  effectiveSnapshot() {
    return this.lastRead === null || this.clock() - this.lastRead >= 20000
      ? { available: false, state: null, task: null } : this.latest;
  }

  async pushOnce() {
    const latest = this.effectiveSnapshot();
    const key = JSON.stringify(latest);
    const body = { schema_version: 1, source: 'dsh-pet', publisher_id: this.publisher,
      sequence: ++this.sequence, ...latest };
    const reply = await this.transport(this.deviceUrl + DEVICE_PATH, this.options({
      method: 'POST', headers: { Authorization: 'Bearer ' + this.token }, body,
      timeout: 3000, maxBytes: 4096,
    }));
    if (reply.status !== 200) throw new Error(`device_http_${reply.status}`);
    if (!/^application\/json(?:;|$)/iu.test(reply.headers['content-type'] ?? '')) {
      throw new Error('device_content_type');
    }
    const ack = JSON.parse(reply.body);
    if (ack.ok !== true || ack.sequence !== body.sequence) throw new Error('device_protocol');
    this.lastPush = this.clock();
    this.sentKey = key;
    this.deviceError = null;
  }

  async pushLoop() {
    while (!this.controller.signal.aborted) {
      try {
        await this.pushOnce();
        // A source change during POST must be sent immediately, not lost until
        // the next heartbeat. Exactly one POST is ever in flight.
        if (this.sentKey !== JSON.stringify(this.effectiveSnapshot())) continue;
        await this.delay(this.heartbeatMs, true);
      } catch (error) {
        if (this.controller.signal.aborted) break;
        this.deviceError = /^device_[a-z0-9_]+$/u.test(error.message) ? error.message : 'device_unreachable';
        await this.delay(this.retryMs);
      }
    }
  }

  start() { this.running = Promise.all([this.readLoop(), this.pushLoop()]); return this.running; }

  stop() {
    this.controller.abort();
    for (const done of [...this.delays.values()]) done();
  }

  status() {
    return { schema_version: 1, source: 'dsh-pet', source_error: this.sourceError,
      device_error: this.deviceError, last_read_at: this.lastRead, last_push_at: this.lastPush };
  }
}

function apply(ctx, pluginConfig = {}) {
  const path = pluginConfig.configFile || process.env.DSH_ESP32_CONFIG || join(homedir(), '.dsh', 'esp32-sync.json');
  let config;
  try { config = configuration(JSON.parse(readFileSync(path, 'utf8'))); }
  catch {
    ctx.logger?.warn?.('[dsh-esp32-sync] Configure deviceUrl/token in ~/.dsh/esp32-sync.json, then restart DSH.');
    return;
  }
  const forwarder = new Forwarder({ ...config,
    sourceOrigin: () => {
      const port = ctx.webServer.port;
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('source_port_not_ready');
      return `http://127.0.0.1:${port}`;
    },
    authenticatedUrl: origin => ctx.connection.authenticatedUrl(origin),
  });
  ctx.effect(() => {
    void forwarder.start();
    return () => forwarder.stop();
  });
  ctx.effect(() => ctx.webServer.register({ kind: 'prefix', path: '/dsh-esp32-sync', handler: (req, res) => {
    const ok = req.method === 'GET' && req.url?.split('?', 1)[0] === '/dsh-esp32-sync/status';
    const body = JSON.stringify(ok ? forwarder.status() : { error: 'not_found' });
    res.writeHead(ok ? 200 : 404, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(body);
  } }));
  ctx.logger?.info?.('[dsh-esp32-sync] Direct ESP32 state forwarding started.');
}

// Explicit assignments expose named exports to both require() and DSH's ESM
// loader; an object-literal module.exports only exposes `default` in Node ESM.
exports.name = 'dsh-esp32-sync';
exports.inject = ['webServer', 'connection'];
exports.apply = apply;
exports.Forwarder = Forwarder;
exports.configuration = configuration;
exports.workSnapshot = workSnapshot;
exports.request = request;
