#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Generate matching DSH and firmware configuration without displaying tokens.
const { randomBytes } = require('node:crypto');
const { existsSync, mkdirSync, readFileSync, writeFileSync } = require('node:fs');
const { homedir } = require('node:os');
const { dirname, join, resolve } = require('node:path');
const { parseArgs } = require('node:util');
const { configuration } = require('./index.cjs');

try {
  const { values } = parseArgs({ options: {
    'device-url': { type: 'string' },
    config: { type: 'string', default: join(homedir(), '.dsh', 'esp32-sync.json') },
    'firmware-config': { type: 'string', default: resolve('.cache/dsh-esp32/sdkconfig.defaults') },
  } });
  if (!values['device-url']) throw new Error('Pass --device-url http://ESP32-LAN-IP:8766');
  const path = resolve(values.config);
  const previous = existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : null;
  const token = previous?.token ?? randomBytes(32).toString('base64url');
  const config = configuration({ deviceUrl: values['device-url'], token });
  const port = new URL(config.deviceUrl).port || '80';
  if (Number(port) < 1024) throw new Error('Use a device TCP port between 1024 and 65535');
  const firmwarePath = resolve(values['firmware-config']);
  if (path === firmwarePath) throw new Error('DSH and firmware configuration paths must differ');
  for (const file of [path, firmwarePath]) mkdirSync(dirname(file), { recursive: true });
  writeFileSync(path, JSON.stringify(config, null, 2) + '\n', { mode: 0o600 });
  writeFileSync(firmwarePath, 'CONFIG_EXAMPLE_DSH_PET_SYNC_ENABLE=y\n'
    + `CONFIG_EXAMPLE_DSH_PET_SYNC_PORT=${port}\n`
    + `CONFIG_EXAMPLE_DSH_PET_SYNC_TOKEN="${config.token}"\n`, { mode: 0o600 });
  console.log('DSH configuration: ' + path);
  console.log('Firmware configuration fragment: ' + firmwarePath);
  console.log('Token saved in those files; it was not printed. Restart DSH after configuration changes.');
} catch (error) {
  // JSON parser error text may contain a credential. Use only known safe messages.
  console.error(error instanceof SyntaxError ? 'Invalid existing configuration JSON; correct it locally.' : error.message);
  process.exitCode = 1;
}
