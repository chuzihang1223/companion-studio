'use strict';
const { randomUUID, randomBytes } = require('node:crypto');
const path = require('node:path');
const { candidates, publicCandidate } = require('./visual-locator.cjs');
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const DENIED =
  /^(?:powershell|pwsh|cmd|conhost|windowsterminal|consent|lockapp|credentialuibroker|securityhealthui|taskmgr|regedit|mmc|keepass|keepassxc|1password|bitwarden|wechat|weixin|qq|tim|telegram|signal|discord|codex|chatgpt|companionstudio|chrome|msedge|firefox)\.exe$/i;
const SENSITIVE =
  /密码|密钥|验证码|支付|付款|银行卡|sign.?in|password|windows security|user account control/i;
const OPERATIONS = new Set([
  'observe',
  'find_element',
  'click',
  'type_text',
  'scroll',
  'focus_window',
  'wait_for_state',
]);
function owner(value) {
  if (
    !value ||
    !UUID.test(value.conversation_id) ||
    typeof value.character_id !== 'string' ||
    !value.character_id ||
    value.character_id.length > 100
  )
    throw Error('COMPUTER_OWNER_INVALID');
  return { conversation_id: value.conversation_id, character_id: value.character_id };
}
function sameOwner(a, b) {
  return a.conversation_id === b.conversation_id && a.character_id === b.character_id;
}
function object(value, keys) {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    JSON.stringify(value).length > 14000 ||
    Object.keys(value).some((k) => !keys.includes(k))
  )
    throw Error('COMPUTER_ARGUMENT_INVALID');
}

/** In-memory window capabilities. Model-visible IDs carry no executable, HWND,
 * screenshots or permission. Every dispatch still uses the original controller. */
class ComputerUseCoordinator {
  constructor(controller, registry, validateOwner) {
    this.controller = controller;
    this.registry = registry;
    this.validateOwner = validateOwner;
    this.grants = new Map();
    this.choices = new Map();
    this.queue = Promise.resolve();
    this.cleanupTimer = setInterval(() => {
      const now = Date.now();
      for (const [id, grant] of this.grants) {
        if (grant.expires < now) {
          this.revoke(grant);
          this.grants.delete(id);
          continue;
        }
        for (const [key, frame] of grant.frames)
          if (now - frame.created_ms >= 8000) grant.frames.delete(key);
        if (grant.latest && now - grant.latest.created_ms >= 8000) grant.latest = null;
      }
      for (const [id, choice] of this.choices)
        if (now - choice.created > 30000) this.choices.delete(id);
    }, 1000);
    this.cleanupTimer.unref();
  }
  revoke(grant) {
    this.controller.cancel_visual?.(grant.nonce);
    this.onRevoke?.(grant);
  }
  allowedEntry(app) {
    const entry = this.controller.fixtureEntries?.get(app) || this.registry.detect(app),
      exe = entry?.path || entry?.executable;
    if (!entry?.installed || !exe || DENIED.test(path.basename(exe)))
      throw Error('COMPUTER_SENSITIVE_OR_UNAVAILABLE_TARGET');
    return { entry, exe };
  }
  async windows(scope, app) {
    await this.validateOwner(owner(scope));
    const { entry, exe } = this.allowedEntry(app);
    this.choices.clear();
    const rows = await this.controller.list_windows(app);
    return rows
      .filter((w) => w.visible && !w.minimized && w.responding && !SENSITIVE.test(w.title))
      .slice(0, 24)
      .map((window) => {
        const id = randomUUID();
        this.choices.set(id, { scope: owner(scope), app, exe, window, created: Date.now() });
        return {
          choice_id: id,
          application: entry.name || app,
          title: window.title.slice(0, 120),
          bounds: window.bounds,
        };
      });
  }
  choice(scope, id) {
    const choice = this.choices.get(id);
    if (!choice || !sameOwner(choice.scope, owner(scope)) || Date.now() - choice.created > 30000)
      throw Error('COMPUTER_WINDOW_SELECTION_EXPIRED');
    return choice;
  }
  async enable(scope, choiceId, settings) {
    await this.validateOwner(owner(scope));
    const choice = this.choice(scope, choiceId),
      current = await this.controller.verify_window_state(choice.app, choice.window.handle);
    if (
      current.pid !== choice.window.pid ||
      current.started_at !== choice.window.started_at ||
      !current.visible ||
      current.minimized ||
      SENSITIVE.test(current.title)
    )
      throw Error('WINDOW_IDENTITY_CHANGED');
    this.stopOwner(scope);
    if (this.grants.size >= 16) throw Error('COMPUTER_CAPACITY_LIMIT');
    const id = randomUUID();
    this.grants.set(id, {
      id,
      scope: owner(scope),
      app: choice.app,
      exe: choice.exe,
      window: current,
      nonce: randomBytes(32).toString('hex'),
      preview: settings.preview === true,
      coordinate: settings.coordinate_fallback === true,
      status: 'enabled',
      expires: Date.now() + 15 * 60000,
      frames: new Map(),
      latest: null,
      last: null,
    });
    this.choices.delete(choiceId);
    return this.state(scope);
  }
  stopOwner(scope) {
    for (const [id, grant] of this.grants)
      if (sameOwner(grant.scope, owner(scope))) {
        this.revoke(grant);
        grant.frames.clear();
        grant.latest = null;
        this.grants.delete(id);
      }
  }
  get(scope, id, allowPaused = false) {
    const grant = this.grants.get(id);
    if (!grant || !sameOwner(grant.scope, owner(scope)) || grant.expires < Date.now()) {
      if (grant && grant.expires < Date.now()) {
        this.revoke(grant);
        this.grants.delete(id);
      }
      throw Error('COMPUTER_PERMISSION_DENIED');
    }
    if (!allowPaused && grant.status !== 'enabled') throw Error('COMPUTER_PAUSED');
    return grant;
  }
  control(scope, id, action) {
    const grant = this.get(scope, id, true);
    this.revoke(grant);
    grant.frames.clear();
    grant.latest = null;
    if (['stop', 'takeover'].includes(action)) {
      this.grants.delete(id);
      return this.state(scope);
    }
    if (!['pause', 'resume'].includes(action)) throw Error('COMPUTER_CONTROL_INVALID');
    grant.status = action === 'pause' ? 'paused' : 'enabled';
    if (action === 'resume') grant.nonce = randomBytes(32).toString('hex');
    return this.state(scope);
  }
  state(scope) {
    owner(scope);
    const grants = [];
    for (const grant of this.grants.values()) {
      if (!sameOwner(grant.scope, scope)) continue;
      if (grant.expires < Date.now()) {
        this.revoke(grant);
        this.grants.delete(grant.id);
        continue;
      }
      for (const [id, frame] of grant.frames)
        if (Date.now() - frame.created_ms > 8000) grant.frames.delete(id);
      if (grant.latest && Date.now() - grant.latest.created_ms > 8000) grant.latest = null;
      grants.push({
        target_id: grant.id,
        application: grant.app,
        title: grant.window.title.slice(0, 120),
        status: grant.status,
        preview_enabled: grant.preview,
        coordinate_fallback: grant.coordinate,
        expires_at: grant.expires,
        observation: grant.latest,
        last_result: grant.last,
      });
    }
    return {
      enabled: grants.length > 0,
      cloud_transfer: false,
      persistent_screenshots: false,
      targets: grants,
    };
  }
  save(grant, frame) {
    if (
      !frame ||
      !UUID.test(frame.observation_id) ||
      !Array.isArray(frame.elements) ||
      frame.elements.length > 600
    )
      throw Error('COMPUTER_OBSERVATION_INVALID');
    grant.frames.clear(); // one observation version; no permanent coordinate ledger
    frame.candidates = [];
    grant.frames.set(frame.observation_id, frame);
    grant.latest = { ...frame, ...(grant.preview ? {} : { image: undefined }) };
    return frame;
  }
  async execute(operation, args, scope) {
    if (!OPERATIONS.has(operation)) throw Error('COMPUTER_OPERATION_UNSUPPORTED');
    const run = async () => {
      await this.validateOwner(owner(scope));
      const grant = this.get(scope, args.target_id),
        started = Date.now();
      const native = {
        handle: grant.window.handle,
        expected_pid: grant.window.pid,
        expected_started_at: grant.window.started_at,
        expected_executable: grant.exe,
        visual_nonce: grant.nonce,
        expected_expires_ms: grant.expires,
        visual_operation: 'observe',
        visual_capture: grant.preview,
        ocr: grant.preview,
        language: 'auto',
        capture_scope: args.capture_scope || 'window',
        crop: args.region
          ? [args.region.x, args.region.y, args.region.width, args.region.height]
          : undefined,
        display_id: args.display_id,
        verification: null,
      };
      try {
        let frame,
          found = [],
          result;
        if (operation === 'find_element') {
          if (args.locator.source === 'dom') throw Error('COMPUTER_DOM_USE_BROWSER_TOOLS');
          if (args.locator.source === 'ocr' && !grant.preview)
            throw Error('COMPUTER_CAPTURE_PERMISSION_REQUIRED');
          native.visual_capture = grant.preview;
          native.ocr = grant.preview && args.locator.source !== 'uia';
          frame = this.save(grant, await this.controller.call(grant.app, 'visual', native));
          frame.locator = { ...args.locator };
          found = candidates(frame, args.locator, grant.id);
          frame.candidates = found;
          result = {
            status: found.length === 1 ? 'SUCCESS' : found.length ? 'NEEDS_REVIEW' : 'FAILED',
            code:
              found.length === 1
                ? undefined
                : found.length
                  ? 'ELEMENT_AMBIGUOUS'
                  : 'ELEMENT_NOT_FOUND',
            verified: found.length === 1,
          };
        } else if (['click', 'type_text', 'scroll'].includes(operation)) {
          const old = grant.frames.get(args.observation_id);
          if (!old || Date.now() - old.created_ms > 8000)
            throw Error('COMPUTER_OBSERVATION_EXPIRED');
          const selected = old.candidates.find((row) => row.candidate_id === args.candidate_id);
          if (!selected || old.candidates.length !== 1 || selected.match_quality !== 1)
            throw Error('ELEMENT_AMBIGUOUS');
          // Consume before dispatch. Timeout/cancel/restart can never retry this version.
          grant.frames.clear();
          grant.latest = null;
          result = await this.controller.call(grant.app, 'visual', {
            ...native,
            visual_operation: operation,
            observation_id: args.observation_id,
            selected_candidate: selected.native,
            text: args.text,
            direction: args.direction,
            verification: args.verification || null,
            timeout_seconds: args.timeout_seconds || 5,
            coordinate_fallback: grant.coordinate,
            visual_capture: selected.source === 'ocr',
            ocr: selected.source === 'ocr',
          });
          frame = result.observation;
        } else {
          result = await this.controller.call(grant.app, 'visual', {
            ...native,
            visual_operation: operation,
            verification: args.verification || null,
            timeout_seconds: args.timeout_seconds || 5,
          });
          if (operation === 'observe') {
            frame = result;
            result = { status: 'SUCCESS', verified: true };
          } else frame = result.observation;
        }
        // A pause or takeover while the serial worker ran revokes even a late response.
        if (this.get(scope, grant.id) !== grant) throw Error('COMPUTER_PERMISSION_DENIED');
        if (frame && operation !== 'find_element') this.save(grant, frame);
        if (
          ['UNKNOWN', 'NEEDS_REVIEW'].includes(result.status) &&
          ['click', 'type_text', 'scroll'].includes(operation)
        ) {
          grant.status = 'paused';
          grant.frames.clear();
          grant.latest = null;
          this.revoke(grant);
        }
        const summary = {
          target_id: grant.id,
          window_id: grant.id,
          observation_id: frame?.observation_id,
          status: result.status,
          verified: result.verified === true,
          executed: result.executed === true,
          code: result.code,
          source: found[0]?.source || 'uia',
          candidate_count: found.length,
          candidates: found.map(publicCandidate),
          element_count: frame?.elements.length || 0,
          ocr_count: frame?.ocr?.lines?.length || 0,
          capture_available: Boolean(frame?.image),
          capture_error: frame?.capture_error || null,
          ocr_error: frame?.ocr_error || null,
          duration_ms: Date.now() - started,
          evidence_refs: [],
        };
        grant.last = summary;
        return summary;
      } catch (error) {
        grant.frames.clear();
        grant.latest = null;
        const code = /^[A-Z_]{3,80}$/.test(error.message) ? error.message : 'ACTION_STATE_UNKNOWN';
        grant.last = {
          status: ['click', 'type_text', 'scroll'].includes(operation) ? 'NEEDS_REVIEW' : 'FAILED',
          verified: false,
          code,
          executed: code === 'ACTION_STATE_UNKNOWN' ? null : false,
          duration_ms: Date.now() - started,
        };
        if (code === 'ACTION_STATE_UNKNOWN') grant.status = 'paused';
        return { ...grant.last, target_id: grant.id, window_id: grant.id, evidence_refs: [] };
      }
    };
    const current = this.queue.then(run, run);
    this.queue = current.catch(() => {});
    return current;
  }
  close() {
    clearInterval(this.cleanupTimer);
    for (const grant of this.grants.values()) {
      this.revoke(grant);
      grant.frames.clear();
      grant.latest = null;
    }
    this.grants.clear();
    this.choices.clear();
  }
}
module.exports = { ComputerUseCoordinator, owner, object, OPERATIONS };
