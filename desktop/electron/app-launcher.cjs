'use strict';
const fs = require('node:fs'),
  path = require('node:path'),
  { spawn } = require('node:child_process');
const { rankCandidates, windowsPath } = require('./application-record.cjs');
const { ProcessLifecycleWatcher } = require('./process-lifecycle-watcher.cjs');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/**
 * 从授权应用的观察结果选择可操作窗口，排除系统宿主和播放器工具提示。
 * @param {Record<string, unknown>} state 进程和窗口的最新观察快照。
 * @param {Record<string, unknown>} record 已登记目标应用及确认过的可执行路径。
 * @returns {Array<Record<string, unknown>>} 按主窗口可见性、归属和面积排序。
 */
function applicationWindows(state, record = {}) {
  const primary = new Set(
    [
      record.executable_path,
      record.executable,
      record.path,
      ...(record.verified_process_paths || []),
    ]
      .filter(Boolean)
      .map((value) => windowsPath(value).toLowerCase()),
  );
  const owners = new Map(
    (state.processes || []).map((row) => [row.pid, windowsPath(row.path).toLowerCase()]),
  );
  return (state.windows || [])
    .filter(
      (row) =>
        row.responding &&
        (row.visible !== false || row.minimized) &&
        row.bounds[2] >= 120 &&
        row.bounds[3] >= 80 &&
        !(row.class_name === 'Static' && /RAIL-|SDK|_WND|_WINDOW/i.test(row.title || '')),
    )
    .sort(
      (a, b) =>
        Number(b.visible !== false) - Number(a.visible !== false) ||
        Number(primary.has(owners.get(b.pid))) - Number(primary.has(owners.get(a.pid))) ||
        b.bounds[2] * b.bounds[3] - a.bounds[2] * a.bounds[3],
    );
}
/** 将登记参数按 Windows 引号/反斜杠规则解析成 argv，不交给 shell 求值。 */
function windowsArguments(raw) {
  const result = [];
  let part = '',
    quoted = false,
    slashes = 0,
    active = false;
  for (const char of String(raw || '')) {
    if (char === '\\') {
      slashes++;
      active = true;
      continue;
    }
    if (char === '"') {
      part += '\\'.repeat(Math.floor(slashes / 2));
      if (slashes % 2) part += '"';
      else quoted = !quoted;
      slashes = 0;
      active = true;
      continue;
    }
    part += '\\'.repeat(slashes);
    slashes = 0;
    if (/\s/.test(char) && !quoted) {
      if (active) {
        result.push(part);
        part = '';
        active = false;
      }
    } else {
      part += char;
      active = true;
    }
  }
  part += '\\'.repeat(slashes);
  if (active) result.push(part);
  if (quoted) throw Error('登记的启动参数引号不完整');
  return result;
}
/** 进程创建只是中间状态；窗口就绪和生命周期证据决定能否继续操作。 */
class AppLaunchVerifier {
  constructor(controller, record = {}) {
    this.controller = controller;
    this.record = record;
  }
  async observe(id) {
    return this.controller.observe_app(id);
  }
  async wait(id, timeout, onProgress, checkpoint = async () => {}) {
    const end = Date.now() + timeout;
    let processReported = false,
      last = { processes: [], windows: [] };
    const lifecycle = new ProcessLifecycleWatcher({
      graceMs: this.record.launch_candidates?.some((row) => row.launch_type === 'launcher')
        ? 4000
        : 1200,
    });
    do {
      await checkpoint();
      last = await this.observe(id);
      lifecycle.observe(last);
      if (last.processes?.length && !processReported) {
        processReported = true;
        await onProgress('process_detected', last);
      }
      const window = applicationWindows(last, this.record)[0];
      if (window) {
        await onProgress(window.visible === false ? 'window_detected' : 'window_ready', last);
        return { ...last, window, verified: true };
      }
      await sleep(200);
    } while (Date.now() < end);
    return { ...last, verified: !!last.processes?.length, window: null, window_ready: false };
  }
}
/** 在授权后的目标记录中尝试入口；成功提交后不会盲目重开已关闭的目标应用。 */
class AppLauncher {
  constructor({ discovery, shell, controller, audit = () => {}, spawnProcess = spawn }) {
    this.discovery = discovery;
    this.shell = shell;
    this.controller = controller;
    this.audit = audit;
    this.spawn = spawnProcess;
    this.pending = new Map();
  }
  /** 同应用/任务的在途启动复用，防止重复提交；结束后释放 pending。 */
  launch(record, options = {}) {
    const key = (record.app_id || record.id) + ':' + (options.taskId || 'direct');
    if (this.pending.has(key)) return this.pending.get(key);
    const promise = this.perform(record, options).finally(() => this.pending.delete(key));
    this.pending.set(key, promise);
    return promise;
  }
  async perform(
    record,
    { targetText, onProgress = async () => {}, checkpoint = async () => {} } = {},
  ) {
    const id = record.id || record.app_id,
      started = Date.now();
    this.discovery.discoverDelegate?.(record.app_id);
    record = { ...record, ...this.discovery.find?.(record.app_id) };
    const verifier = new AppLaunchVerifier(this.controller, record);
    const progress = async (stage, state = {}) => {
      this.audit(
        'app-launch.log',
        JSON.stringify({
          event: stage,
          app_id: id,
          elapsed_ms: Date.now() - started,
          pids: state.processes?.map((p) => p.pid),
          window_count: state.windows?.length,
        }),
      );
      await onProgress(stage, state);
    };
    const focus = async (window) => {
      await checkpoint();
      try {
        return (await this.controller.focus_window(id, window.handle)).focused !== false;
      } catch (error) {
        if (/task_cancelled|automation_paused|desktop_locked|user_interruption/.test(error.message))
          throw error;
        await progress('focus_unavailable');
        return false;
      }
    };
    const focusState = async (state) => {
      if (!state.window) return { ...state, focused: false, verified: false, window_ready: false };
      let focused, observed;
      try {
        focused = await focus(state.window);
        observed = await this.controller.verify_window_state(id, state.window.handle);
      } catch (error) {
        if (
          /TargetWindowNotFound|window.*not.*found|目标窗口|主窗口.*不存在/i.test(
            String(error.message),
          )
        )
          throw Error('TARGET_WINDOW_CLOSED');
        throw error;
      }
      const current = { ...state.window, ...observed };
      const ready = current.visible !== false && current.responding !== false;
      return {
        ...state,
        window: ready ? current : null,
        window_ready: ready,
        verified: ready,
        focused,
      };
    };
    const before = await verifier.observe(id),
      window = applicationWindows(before, record)[0];
    if (window) {
      const state = await focusState({ ...before, window });
      await progress('existing_window_observed', before);
      if (state.verified) return { ...record, ...state, status: 'running', already_running: true };
      await progress('existing_hidden_window');
    }
    if (before.processes?.length && !window) {
      const state = await verifier.wait(id, 2000, progress);
      if (state.window) {
        const ready = await focusState(state);
        if (ready.verified)
          return { ...record, ...ready, status: 'running', already_running: true };
      }
    }
    const failures = [];
    for (const entry of rankCandidates(record).slice(0, 4)) {
      let submitted = false;
      await checkpoint();
      if (Date.now() - started > 25000) break;
      await progress('launching');
      const target = ['exe', 'shortcut', 'launcher', 'custom', 'start_app'].includes(
        entry.launch_type,
      )
        ? windowsPath(entry.target)
        : entry.target;
      const exists = ['exe', 'shortcut', 'launcher', 'custom', 'start_app'].includes(
        entry.launch_type,
      )
        ? fs.existsSync(target)
        : null;
      this.audit(
        'app-launch.log',
        JSON.stringify({
          event: 'resolved_entry',
          app_id: id,
          resolved_path: target,
          launch_method: entry.launch_type,
          exists,
          is_file: exists ? fs.statSync(target).isFile() : false,
        }),
      );
      try {
        if (exists === false) throw Error('启动入口已经不存在');
        let pid = null;
        if (entry.launch_type === 'shortcut') {
          if (this.controller.launch_entry)
            pid = (await this.controller.launch_entry(id, entry.id)).pid;
          else {
            const error = await this.shell.openPath(target);
            if (error) throw Error(error);
          }
        } else if (['exe', 'launcher', 'custom', 'start_app'].includes(entry.launch_type)) {
          const cwd =
            entry.working_directory && fs.existsSync(entry.working_directory)
              ? entry.working_directory
              : path.win32.dirname(target);
          try {
            pid = await this.spawnEntry(target, windowsArguments(entry.arguments), cwd);
          } catch (error) {
            if (!['EACCES', 'EPERM'].includes(error.code) || !this.controller.launch_entry)
              throw error;
            pid = (await this.controller.launch_entry(id, entry.id)).pid;
            await progress('windows_shell_launch');
          }
        } else if (['app_user_model_id', 'shell_app'].includes(entry.launch_type)) {
          pid = await this.spawnEntry(
            path.win32.join(process.env.WINDIR || 'C:\\Windows', 'explorer.exe'),
            ['shell:AppsFolder\\' + target],
          );
        } else if (entry.launch_type === 'uri_protocol') {
          if (!/^[a-z][a-z0-9+.-]*:/i.test(target) || /^file:/i.test(target))
            throw Error('登记的协议无效');
          await this.shell.openExternal(target);
        } else throw Error('不支持的启动入口类型');
        await progress('launch_submitted', { processes: pid ? [{ pid }] : [] });
        submitted = true;
        const timeout =
          entry.launch_type === 'launcher' ||
          record.launch_candidates?.some((c) => c.launch_type === 'launcher')
            ? 18000
            : 10000;
        const state = await verifier.wait(
          id,
          Math.max(200, Math.min(timeout, 25000 - (Date.now() - started))),
          progress,
          checkpoint,
        );
        if (!state.verified) throw Error('未检测到该应用的进程或窗口');
        const ready = await focusState(state);
        if (!ready.verified) throw Error('应用进程存在，但主窗口未就绪');
        this.discovery.recordLaunch(record.app_id, entry.id, true, targetText, state.processes);
        return {
          ...record,
          ...ready,
          status: 'running',
          started_at: new Date().toISOString(),
          launch_method: entry.launch_type,
          launch_candidate_id: entry.id,
          pid: state.window?.pid || state.processes?.[0]?.pid || null,
        };
      } catch (error) {
        if (
          /task_cancelled|automation_paused|desktop_locked|user_interruption|APP_EXITED_BEFORE_READY|TARGET_WINDOW_CLOSED/.test(
            error.message,
          )
        )
          throw error;
        failures.push({
          launch_method: entry.launch_type,
          error: String(error.message || error).slice(0, 180),
        });
        this.discovery.recordLaunch(record.app_id, entry.id, false);
        // After Windows accepted the launch, a missing/closed window is an interruption,
        // not a reason to reopen the same app via every other registered candidate.
        if (submitted) {
          await progress('launch_failed');
          return {
            ...record,
            status: 'launch_failed',
            verified: false,
            error: '启动请求已提交，但应用已退出或窗口未就绪。',
            code: 'APP_NOT_READY',
            attempts: failures,
          };
        }
        await progress('candidate_failed');
      }
    }
    await progress('launch_failed');
    return {
      ...record,
      status: 'launch_failed',
      verified: false,
      error: '没有验证到应用启动：' + failures.map((item) => item.error).join('；'),
      attempts: failures,
    };
  }
  /** 创建独立应用进程；shell:false 防止登记参数成为命令文本。 */
  spawnEntry(target, args, cwd) {
    return new Promise((resolve, reject) => {
      const child = this.spawn(target, args, {
        cwd,
        windowsHide: true,
        detached: true,
        stdio: 'ignore',
        shell: false,
      });
      child.once('error', reject);
      child.once('spawn', () => {
        child.unref();
        resolve(child.pid);
      });
    });
  }
}
module.exports = { AppLauncher, AppLaunchVerifier, windowsArguments, applicationWindows };
