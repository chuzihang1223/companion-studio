const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');

/** 源码/安装位置只用于推导当前产品 home；运行档案和缓存不随源码发布。 */
const projectRoot = path.resolve(__dirname, '../..');
const home =
  process.env.COMPANION_HOME ||
  (app.isPackaged
    ? path.resolve(path.dirname(process.execPath), '../..')
    : path.basename(projectRoot).toLowerCase() === 'source'
      ? path.dirname(projectRoot)
      : path.join(projectRoot, '..'));
const userData = path.join(home, 'UserData');
const cache = path.join(home, 'Cache');
const logs = path.join(userData, 'logs');
const exportsDirectory = path.join(home, 'Exports');
for (const dir of [
  userData,
  cache,
  logs,
  exportsDirectory,
  path.join(userData, 'session'),
  path.join(cache, 'chromium'),
])
  fs.mkdirSync(dir, { recursive: true });

// 在创建窗口和浏览器 session 前设置路径，保证 Chromium 与应用档案归属一致。
app.setPath('userData', userData);
app.setPath('sessionData', path.join(userData, 'session'));
// Trace JSON downloads follow this edition's home, including isolated reviews.
app.setPath('downloads', exportsDirectory);
const crashDumps = path.join(cache, 'crash-dumps');
fs.mkdirSync(crashDumps, { recursive: true });
app.setPath('crashDumps', crashDumps);
process.env.TEMP = cache;
process.env.TMP = cache;
app.commandLine.appendSwitch('disk-cache-dir', path.join(cache, 'chromium'));

module.exports = { projectRoot, home, userData, cache, logs };
