'use strict';

const fs = require('node:fs');
const path = require('node:path');

class SchedulerBackground {
  constructor({ Tray, Menu, Notification, icon, userData, request, window, quit, audit }) {
    Object.assign(this, { Tray, Menu, Notification, icon, userData, request, window, quit, audit });
    this.enabled = false;
    this.quitting = false;
    this.timer = null;
    this.tray = null;
    this.pendingNavigation = null;
    this.activeNotifications = new Set();
    try {
      const saved = JSON.parse(
        fs.readFileSync(path.join(userData, 'scheduler-background.json'), 'utf8'),
      );
      this.enabled = saved.enabled === true;
    } catch {
      /* Default is complete exit, never silent background residency. */
    }
  }
  state() {
    return { enabled: this.enabled, tray_available: !!this.tray, startup_supported: false };
  }
  start() {
    try {
      this.tray = new this.Tray(this.icon);
      this.tray.setToolTip('Companion Studio · 定时任务');
      this.tray.on('double-click', () => this.open());
      this.menu = this.Menu.buildFromTemplate([
        { label: '打开 Companion Studio', click: () => this.open() },
        { label: '定时任务', click: () => this.navigate() },
        {
          label: '暂停全部计划',
          click: () =>
            void this.request('/api/scheduler/pause', { paused: true }).catch(() =>
              this.audit('scheduler.log', 'pause_failed'),
            ),
        },
        {
          label: '恢复计划触发',
          click: () =>
            void this.request('/api/scheduler/pause', { paused: false }).catch(() =>
              this.audit('scheduler.log', 'resume_failed'),
            ),
        },
        { label: '最近执行结果', click: () => this.navigate({ recent: true }) },
        { type: 'separator' },
        {
          label: '完全退出',
          click: () => {
            this.quitting = true;
            this.quit();
          },
        },
      ]);
      this.tray.setContextMenu(this.menu);
    } catch {
      this.tray = null;
      this.enabled = false;
      this.audit('scheduler.log', 'tray_unavailable');
    }
    this.timer = setInterval(() => void this.poll(), 3000);
    this.timer.unref();
  }
  configure(enabled) {
    if (enabled && !this.tray) throw Error('系统托盘不可用，保持关闭即退出');
    const file = path.join(this.userData, 'scheduler-background.json');
    const temporary = file + '.' + process.pid + '.tmp';
    fs.writeFileSync(temporary, JSON.stringify({ enabled }));
    fs.renameSync(temporary, file);
    this.enabled = enabled;
    return this.state();
  }
  hideOnClose() {
    return this.enabled && !!this.tray && !this.quitting;
  }
  hide() {
    if (!this.hideOnClose()) throw Error('请先明确启用托盘后台模式');
    this.window()?.hide();
    this.audit('scheduler.log', 'window_hidden_to_tray');
    return this.state();
  }
  open() {
    const window = this.window();
    if (!window || window.isDestroyed()) return;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  }
  navigate(target = {}) {
    if (this.quitting) return;
    this.pendingNavigation = target;
    this.open();
    this.window()?.webContents.send('scheduler-navigate', target);
  }
  takeNavigation() {
    const target = this.pendingNavigation;
    this.pendingNavigation = null;
    return target;
  }
  async poll() {
    if (this.quitting || this.polling) return;
    this.polling = true;
    try {
      const { items } = await this.request('/api/scheduler/notifications/pending');
      for (const row of items) {
        if (this.quitting) break;
        const claimed = await this.request(
          '/api/scheduler/notifications/' + row.notification_id + '/claim',
          {},
        );
        if (this.quitting) break;
        await this.show(claimed);
      }
    } catch {
      this.audit('scheduler.log', 'notification_poll_unavailable');
    } finally {
      this.polling = false;
    }
  }
  async show(row) {
    const endpoint = '/api/scheduler/notifications/' + row.notification_id + '/delivery';
    if (!this.Notification.isSupported()) {
      await this.request(endpoint, { result: 'UNAVAILABLE' });
      return;
    }
    const native = new this.Notification({
      title: 'Companion Studio · 定时任务',
      body:
        '状态：' +
        row.kind +
        ' · ' +
        new Date(row.created_at).toLocaleTimeString() +
        '\n点击查看安全摘要。',
      silent: true,
    });
    this.activeNotifications.add(native);
    if (this.activeNotifications.size > 16) {
      const oldest = this.activeNotifications.values().next().value;
      oldest?.close();
      this.activeNotifications.delete(oldest);
    }
    native.on('click', () =>
      this.navigate({
        conversation_id: row.conversation_id,
        schedule_id: row.schedule_id,
        occurrence_id: row.occurrence_id,
      }),
    );
    const result = await new Promise((resolve) => {
      let complete = false;
      const finish = (value) => {
        if (!complete) {
          complete = true;
          clearTimeout(timeout);
          resolve(value);
        }
      };
      const timeout = setTimeout(() => finish('UNAVAILABLE'), 8000);
      native.once('show', () => finish('SHOWN'));
      native.once('failed', () => finish('FAILED'));
      native.once('close', () => this.activeNotifications.delete(native));
      try {
        native.show();
      } catch {
        finish('FAILED');
      }
    });
    if (result !== 'SHOWN') {
      native.close();
      this.activeNotifications.delete(native);
    }
    await this.request(endpoint, { result });
    this.audit('scheduler.log', 'notification_' + result.toLowerCase());
  }
  async close() {
    this.quitting = true;
    clearInterval(this.timer);
    for (const notification of this.activeNotifications) notification.close();
    this.activeNotifications.clear();
    this.tray?.destroy();
    this.tray = null;
    try {
      await this.request('/api/scheduler/shutdown', {});
      this.audit('scheduler.log', 'scheduler_drained');
    } catch {
      this.audit('scheduler.log', 'scheduler_shutdown_unconfirmed');
    }
  }
}
module.exports = { SchedulerBackground };
