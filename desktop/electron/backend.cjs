const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const crypto = require('node:crypto');
const paths = require('./paths.cjs');
const brand = require('./brand.cjs');

/** 当前产品拥有的后端配置；不从另一产品的数据目录恢复设置。 */
const envFile = app.isPackaged
  ? path.join(paths.userData, '.env')
  : path.join(paths.projectRoot, '.env');
const contents = fs.existsSync(envFile) ? fs.readFileSync(envFile, 'utf8') : '';
function readEnv(key, fallback) {
  const match = contents.match(new RegExp(`^${key}=(.*)$`, 'm'));
  return process.env[key] || (match ? match[1].trim() : fallback);
}
function ownedEnv(key, fallback = '') {
  return contents.match(new RegExp(`^${key}=(.*)$`, 'm'))?.[1]?.trim() || fallback;
}
const host = '127.0.0.1';
const port = Number(readEnv('BACKEND_PORT', '8000'));
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid BACKEND_PORT');
const address = { host, port, url: `http://${host}:${port}` };
/** 单次启动的控制凭据，只传给本进程启动的后端，不暴露给 preload。 */
const controlToken = crypto.randomBytes(32).toString('hex');
let child = null;
let stopping = false;
let lastError = '';

/** @returns {Promise<boolean>} 回环 HTTP 健康探针；失败作为未就绪处理。 */
async function isHealthy() {
  try {
    const response = await fetch(`${address.url}/health`, { signal: AbortSignal.timeout(1000) });
    const body = await response.json();
    return response.ok && body.status === 'ok';
  } catch {
    return false;
  }
}

/**
 * 启动本产品后端并等待健康。发行版不接管已占用端口的其他后端。
 * @param {() => void} onUnexpectedExit 非主动关闭时通知主进程退出。
 * @returns {Promise<boolean>} 是否由本管理器创建后端子进程。
 */
async function startBackend(onUnexpectedExit = () => {}) {
  if (await isHealthy()) {
    if (app.isPackaged)
      throw new Error(
        `Port ${port} is already in use. Close the other application or change BACKEND_PORT in UserData\\.env.`,
      );
    return false;
  }
  const executable = app.isPackaged
    ? path.join(process.resourcesPath, 'companion-backend', 'companion-backend.exe')
    : path.join(paths.projectRoot, '.venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(executable)) throw new Error(`Backend executable missing: ${executable}`);
  const args = app.isPackaged ? [] : [path.join(paths.projectRoot, 'backend', 'run.py')];
  stopping = false;
  lastError = '';
  child = spawn(executable, args, {
    cwd: app.isPackaged ? paths.home : paths.projectRoot,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      ...(brand.clean
        ? {
            COMPANION_CLEAN_DISTRIBUTION: '1',
            COMPANION_DEFAULT_CHARACTER: 'assistant',
            COMPANION_APP_NAME: brand.appName,
            LLM_PROVIDER: 'openai',
            OPENAI_API_KEY: '',
            OPENAI_MODEL: '',
            OPENAI_BASE_URL: 'https://api.openai.com/v1',
            FISH_API_KEY: '',
            FISH_REFERENCE_ID: '',
            VOICE_PREFETCH_ENABLED: 'false',
            VOICE_INDEX_MODEL_DIR: ownedEnv('VOICE_INDEX_MODEL_DIR'),
            VOICE_MODEL_DIR: ownedEnv('VOICE_MODEL_DIR'),
            VOICE_REFERENCES_DIR: ownedEnv('VOICE_REFERENCES_DIR'),
          }
        : {}),
      BACKEND_PORT: String(port),
      COMPANION_DATA_DIR: paths.userData,
      COMPANION_PARENT_PID: String(process.pid),
      COMPANION_CONTROL_TOKEN: controlToken,
      AGENT_PYTHON: require('./python-runtime.cjs')({
        env: process.env,
        home: paths.home,
        backend: executable,
        packaged: app.isPackaged,
      }),
      VOICE_WORKER_SOURCE_DIR: app.isPackaged
        ? path.join(process.resourcesPath, 'backend-source')
        : path.join(paths.projectRoot, 'backend'),
    },
  });
  child.stdout.on('data', (data) => {
    if (!app.isPackaged) process.stdout.write(data);
  });
  child.stderr.on('data', (data) => {
    if (!app.isPackaged) process.stderr.write(data);
  });
  child.on('error', (error) => {
    lastError = error.message;
    console.error('Backend process error:', error);
  });
  child.on('exit', (code) => {
    child = null;
    if (!stopping) {
      console.error(`Backend exited with code ${code}: ${lastError}`);
      onUnexpectedExit();
    }
  });

  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (!child)
      throw new Error(
        `Backend exited during startup. Check ${path.join(paths.logs, 'backend.log')}. ${lastError}`,
      );
    if (await isHealthy()) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  stopBackend();
  throw new Error(
    `Backend did not become healthy within 20 seconds. Check ${path.join(paths.logs, 'backend.log')}.`,
  );
}

/** 仅结束本管理器拥有的 child；不按名称关闭其他产品的后端。 */
function stopBackend() {
  stopping = true;
  if (child) child.kill();
}

module.exports = { address, controlToken, isHealthy, startBackend, stopBackend };
