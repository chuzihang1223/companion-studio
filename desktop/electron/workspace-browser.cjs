const { WebContentsView, session, dialog, shell } = require('electron');
const fs = require('node:fs'),
  path = require('node:path'),
  crypto = require('node:crypto');
const paths = require('./paths.cjs');
const {
  SITES,
  domReady,
  fillSearch,
  readPage,
  searchResults,
} = require('./browser-automation.cjs');

/**
 * 主进程拥有的隔离网页标签管理器；网页没有应用 preload/Node 能力。
 * 标签由任务 ID 关联，URL 与导航经注入策略检查，结果须带实际 DOM 证据。
 */
class WorkspaceBrowserManager {
  constructor(window, compatibility, safeUrl, audit) {
    this.window = window;
    this.compatibility = compatibility;
    this.safeUrl = safeUrl;
    this.audit = audit;
    this.tabs = new Map();
    this.active = null;
    this.rect = { x: 250, y: 150, width: 700, height: 450 };
    this.session = session.fromPartition('persist:companion-workspace-browser');
    this.session.setPermissionRequestHandler((_wc, _p, cb) => cb(false));
    this.session.on('will-download', async (_e, item) => {
      item.pause();
      const result = await dialog.showMessageBox(window, {
        title: '确认下载',
        message: `下载 ${item.getFilename()}？`,
        detail: '文件保存在应用用户数据的 downloads 文件夹，不会自动打开或执行。',
        buttons: ['取消', '下载'],
        defaultId: 0,
        cancelId: 0,
      });
      if (result.response !== 1) {
        item.cancel();
        return;
      }
      const folder = path.join(paths.userData, 'downloads');
      fs.mkdirSync(folder, { recursive: true });
      item.setSavePath(
        path.join(
          folder,
          crypto.randomUUID() +
            '-' +
            path.basename(item.getFilename()).replace(/[<>:"/\\|?*]/g, '_'),
        ),
      );
      item.resume();
    });
    this.session.webRequest.onCompleted(({ url, statusCode, resourceType }) => {
      if (resourceType === 'mainFrame') {
        this.log('response', { url, statusCode });
        for (const tab of this.tabs.values())
          if (url === tab.url || url === tab.view.webContents.getURL())
            tab.responseStatus = statusCode;
      }
    });
  }
  /** 诊断日志去除 URL 查询/凭据及常见秘密字段，不保存完整网页正文。 */
  log(event, detail = {}) {
    const scrub = (value) =>
      typeof value === 'string'
        ? value
            .replace(/https?:\/\/[^\s"<>]+/g, (raw) => {
              try {
                const u = new URL(raw);
                u.username = '';
                u.password = '';
                u.search = '';
                u.hash = '';
                return u.toString();
              } catch {
                return '[url]';
              }
            })
            .replace(
              /(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+/gi,
              '$1[redacted]',
            )
        : value;
    this.audit(
      'workspace-browser.log',
      event,
      JSON.stringify(detail, (_key, value) => scrub(value)),
    );
  }
  state(tab) {
    return {
      id: tab.id,
      url: tab.url,
      title: tab.title,
      error: tab.error,
      status: tab.status,
      visible: this.active === tab.id,
      ...tab.failure,
    };
  }
  publish(tab) {
    if (!this.window.isDestroyed())
      this.window.webContents.send('workspace-browser-state', this.state(tab));
  }
  bounds(rect) {
    const s = this.window.getContentBounds(),
      x = Math.max(0, Math.round(Number(rect?.x) || 0)),
      y = Math.max(0, Math.round(Number(rect?.y) || 0));
    this.rect = {
      x: Math.min(x, s.width - 10),
      y: Math.min(y, s.height - 10),
      width: Math.max(1, Math.min(s.width - x, Math.round(Number(rect?.width) || 700))),
      height: Math.max(1, Math.min(s.height - y, Math.round(Number(rect?.height) || 400))),
    };
    const tab = this.tabs.get(this.active);
    if (tab && !tab.view.webContents.isDestroyed()) tab.view.setBounds(this.rect);
  }
  create(id) {
    const view = new WebContentsView({
      webPreferences: {
        partition: 'persist:companion-workspace-browser',
        sandbox: true,
        nodeIntegration: false,
        contextIsolation: true,
        webSecurity: true,
      },
    });
    const tab = { id, view, url: '', title: '新网页', error: '', status: 'idle' };
    this.tabs.set(id, tab);
    const wc = view.webContents;
    wc.setWindowOpenHandler(({ url }) => {
      try {
        tab.popup_url = this.safeUrl(url);
        this.window.webContents.send('workspace-browser-state', {
          ...this.state(tab),
          popup_url: tab.popup_url,
        });
      } catch {}
      return { action: 'deny' };
    });
    wc.on('will-navigate', (event, url) => {
      try {
        this.safeUrl(url);
      } catch {
        event.preventDefault();
      }
    });
    wc.on('will-redirect', (event, url) => {
      try {
        this.safeUrl(url);
      } catch {
        event.preventDefault();
      }
    });
    for (const event of [
      'did-start-loading',
      'did-stop-loading',
      'did-start-navigation',
      'did-redirect-navigation',
      'did-navigate',
      'did-navigate-in-page',
    ])
      wc.on(event, (_e, url) => {
        this.log(event, { id, url: typeof url === 'string' ? url : tab.url });
        if (event === 'did-start-loading') {
          tab.status = 'loading';
          this.publish(tab);
        }
      });
    wc.on('did-finish-load', () => {
      tab.status = 'loaded';
      tab.error = '';
      tab.failure = undefined;
      tab.url = wc.getURL();
      tab.title = wc.getTitle() || tab.url;
      this.log('did-finish-load', { id, url: tab.url });
      this.publish(tab);
    });
    const fail = (event, code, description, url, isMainFrame) => {
      this.log(event, { id, url, code, description, isMainFrame });
      if (isMainFrame && code !== -3) {
        tab.status = 'failed';
        tab.error = `${description} (${code})`;
        tab.failure = { code, description, isMainFrame };
        this.publish(tab);
        if (this.active === id) this.hide();
      }
    };
    wc.on('did-fail-load', (_e, ...args) => fail('did-fail-load', ...args));
    wc.on('did-fail-provisional-load', (_e, ...args) => fail('did-fail-provisional-load', ...args));
    wc.on('render-process-gone', (_e, detail) => {
      tab.status = 'failed';
      tab.error = `网页进程退出：${detail.reason}`;
      this.log('render-process-gone', { id, ...detail });
      this.publish(tab);
      if (this.active === id) this.hide();
    });
    wc.on('certificate-error', (_e, url, error) =>
      this.log('certificate-error', { id, url, error }),
    );
    wc.on('console-message', (_e, detail) =>
      this.log('console-message', {
        id,
        level: detail?.level,
        message: String(detail?.message || '').slice(0, 500),
      }),
    );
    return tab;
  }
  hide() {
    const tab = this.tabs.get(this.active);
    if (tab) {
      try {
        this.window.contentView.removeChildView(tab.view);
      } catch {}
    }
    this.active = null;
  }
  show(id) {
    const tab = this.tabs.get(id);
    if (!tab || tab.view.webContents.isDestroyed()) return;
    this.hide();
    this.active = id;
    this.window.contentView.addChildView(tab.view);
    tab.view.setBounds(this.rect);
  }
  /** 创建/复用标签并等待主页面就绪；网络或渲染失败返回 failed 状态。 */
  async open(url, rect, context = {}) {
    const safe = this.safeUrl(url);
    const id = String(context.tabId || crypto.randomUUID());
    let tab = this.tabs.get(id);
    if (!tab || tab.view.webContents.isDestroyed()) tab = this.create(id);
    tab.url = safe;
    tab.error = '';
    tab.responseStatus = null;
    tab.popup_url = null;
    tab.status = 'loading';
    this.bounds(rect);
    tab.view.setBounds(this.rect);
    if (!context.background) this.show(id);
    this.publish(tab);
    this.log('load-request', {
      id,
      url: safe,
      proxy: await this.session.resolveProxy(safe),
      sandbox: true,
    });
    let timer;
    try {
      const loading = tab.view.webContents.loadURL(safe);
      loading.catch(() => {});
      await Promise.race([
        loading,
        domReady(tab.view.webContents, 20000, safe),
        new Promise((_r, reject) => {
          timer = setTimeout(() => reject(Error('网页加载超时')), 24000);
        }),
      ]);
      if (tab.status === 'failed') throw Error(tab.error);
      if (tab.responseStatus >= 400) throw Error('网站返回 HTTP ' + tab.responseStatus);
      tab.status = 'loaded';
      tab.url = tab.view.webContents.getURL();
      tab.title = tab.view.webContents.getTitle();
      this.publish(tab);
    } catch (error) {
      if (tab.status !== 'failed') {
        tab.status = 'failed';
        tab.error = String(error.message);
        this.log('load-error', { id, url: safe, error: tab.error });
        this.publish(tab);
        if (this.active === id) this.hide();
      }
    } finally {
      clearTimeout(timer);
    }
    return this.state(tab);
  }
  async action(action, id) {
    const tab = this.tabs.get(id || this.active);
    if (action === 'hide') {
      this.hide();
      return { visible: false, url: '', title: '' };
    }
    if (!tab) return { url: '', title: '', visible: false };
    const wc = tab.view.webContents;
    if (action === 'close') {
      if (this.active === tab.id) this.hide();
      this.tabs.delete(tab.id);
      wc.close();
      return { id: tab.id, url: tab.url, title: tab.title, visible: false, closed: true };
    }
    if (wc.isDestroyed()) return this.state(tab);
    if (action === 'show') {
      if (tab.status === 'failed') {
        this.hide();
        return this.state(tab);
      }
      this.show(tab.id);
    }
    if (action === 'reload') return this.open(tab.url, this.rect, { tabId: tab.id });
    if (action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
    if (action === 'forward' && wc.navigationHistory.canGoForward())
      wc.navigationHistory.goForward();
    if (action === 'external') await shell.openExternal(this.safeUrl(tab.url));
    return this.state(tab);
  }
  async navigate(url, tabId) {
    let state = await this.open(url, this.rect, { tabId, background: true });
    if (state.status === 'failed') {
      const tab = this.tabs.get(tabId);
      if (tab) {
        if (this.active === tabId) this.hide();
        tab.view.webContents.close();
        this.tabs.delete(tabId);
      }
      state = await this.open(url, this.rect, { tabId, background: true });
    }
    if (state.status !== 'loaded') throw Error(state.error || '网页未完成加载');
    await domReady(this.tabs.get(tabId).view.webContents);
    return state;
  }
  /**
   * 搜索须确认查询反映、导航及结果链接；验证码/登录未通过不宣告完成。
   * @param {string} operation 已批准的 browser 操作。
   * @param {{query?: string, site?: string, url?: string}} args
   * @param {string} taskId 隔离任务标签的归属标识。
   */
  async automate(operation, args, taskId) {
    if (operation === 'browser.observe') {
      const tab = this.tabs.get('agent-browser-' + taskId);
      if (!tab || tab.view.webContents.isDestroyed())
        return { verified: false, code: 'WINDOW_NOT_FOUND' };
      const content = await readPage(tab.view.webContents);
      return {
        verified: true,
        url: content.url,
        ready: content.ready,
        dom_fingerprint: crypto
          .createHash('sha256')
          .update(
            JSON.stringify({
              url: content.url,
              ready: content.ready,
              text: content.text,
              links: content.links,
            }),
          )
          .digest('hex'),
        observed_count: content.links.length,
      };
    }
    const tabId = 'agent-browser-' + taskId,
      query = String(args.query || '')
        .trim()
        .slice(0, 160),
      site = String(args.site || '');
    const definition = SITES[site],
      url =
        operation === 'browser.search'
          ? 'https://www.bing.com/search?q=' + encodeURIComponent(query)
          : definition?.home || args.url;
    if (!url) throw Error('请提供网站 HTTPS 地址');
    const search = operation === 'browser.search_site' || operation === 'browser.search';
    if (search && !query) throw Error('搜索内容为空');
    let state,
      method = 'navigation',
      selector = null;
    if (search && definition) {
      state = await this.navigate(definition.search(query), tabId);
      method = 'search_url';
    } else {
      state = await this.navigate(url, tabId);
      if (operation === 'browser.search_site') {
        const tab = this.tabs.get(tabId),
          wc = tab.view.webContents,
          end = Date.now() + 8000;
        do {
          selector = await fillSearch(wc, query);
          if (!selector) await new Promise((r) => setTimeout(r, 200));
        } while (!selector && Date.now() < end);
        if (!selector) throw Error('网页未找到语义搜索框，请使用该站搜索地址或在系统浏览器打开。');
        if (!selector.submitted) {
          wc.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' });
          wc.sendInputEvent({ type: 'keyUp', keyCode: 'Enter' });
        }
        await new Promise((r) => setTimeout(r, 350));
        if (tab.popup_url) await this.navigate(tab.popup_url, tabId);
        method = 'semantic_dom';
      }
    }
    const wc = this.tabs.get(tabId).view.webContents;
    const content = search ? await searchResults(wc, site, query, url) : await readPage(wc, site);
    this.lastDiagnostic = {
      url: content.url,
      ready: content.ready,
      method,
      selector: selector?.selector,
      verified: search ? content.verified : true,
      proxy: await this.session.resolveProxy(content.url),
      partition: 'persist:companion-workspace-browser',
      webSecurity: true,
    };
    this.log('automation_verified', this.lastDiagnostic);
    return {
      ...this.state(this.tabs.get(tabId)),
      ...content,
      tabId,
      query,
      method,
      selector: selector?.selector,
      verified: search ? content.verified : true,
      action: 'desktop_completed',
      summary: search ? '网页搜索结果已验证' : '网页内容已读取',
      diagnostic: this.lastDiagnostic,
    };
  }
  async readPage(wc, site) {
    return readPage(wc, site);
  }
  /** 主窗口关闭时释放当前 manager 拥有的网页视图。 */
  dispose() {
    this.hide();
    for (const tab of this.tabs.values())
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    this.tabs.clear();
  }
}
module.exports = { WorkspaceBrowserManager };
