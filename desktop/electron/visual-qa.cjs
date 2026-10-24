'use strict';
const fs = require('node:fs'),
  path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { randomUUID, createHash } = require('node:crypto');
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/** Review-profile-only original fixture. No caller supplies a program, script,
 * path or coordinates. The real controller and normal window grant are reused. */
class VisualQA {
  constructor(computer, paths, ownerWindow = () => null) {
    this.computer = computer;
    this.paths = paths;
    this.consents = new Map();
    this.instances = new Map();
    this.ownerWindow = ownerWindow;
  }
  authorize() {
    if (process.env.COMPANION_VISUAL_QA_PROFILE !== '1')
      throw Error('VISUAL_QA_ISOLATED_PROFILE_REQUIRED');
    const marker = path.join(this.paths.home, '.companion-visual-qa');
    if (
      !fs.existsSync(marker) ||
      fs.statSync(marker).size > 1024 ||
      JSON.parse(fs.readFileSync(marker, 'utf8')).purpose !== 'companion-visual-qa'
    )
      throw Error('VISUAL_QA_ISOLATED_PROFILE_REQUIRED');
    const id = randomUUID();
    this.consents.set(id, Date.now() + 15 * 60000);
    return id;
  }
  async perform(operation, args) {
    if (process.env.COMPANION_VISUAL_QA_PROFILE !== '1') throw Error('VISUAL_QA_PERMISSION_DENIED');
    // Revoked/expired consent prevents new work, but must not strand this
    // runner's existing fixture during cancellation or timeout cleanup.
    if (operation === 'close_owner') {
      for (const item of [...this.instances.values()])
        if (
          item.consent === args.consent_id &&
          item.scope.conversation_id === args.owner.conversation_id &&
          item.scope.character_id === args.owner.character_id
        )
          this.close(item);
      return { verified: true };
    }
    const instance = this.instances.get(args.fixture_id);
    if (operation === 'close') {
      if (
        !instance ||
        instance.consent !== args.consent_id ||
        instance.scope.conversation_id !== args.owner.conversation_id ||
        instance.scope.character_id !== args.owner.character_id
      )
        throw Error('VISUAL_QA_FIXTURE_NOT_FOUND');
      this.close(instance);
      return { verified: true };
    }
    if ((this.consents.get(args.consent_id) || 0) < Date.now())
      throw Error('VISUAL_QA_PERMISSION_DENIED');
    if (operation === 'open') return this.open(args);
    if (
      !instance ||
      instance.consent !== args.consent_id ||
      instance.scope.conversation_id !== args.owner.conversation_id ||
      instance.scope.character_id !== args.owner.character_id
    )
      throw Error('VISUAL_QA_FIXTURE_NOT_FOUND');
    if (operation === 'close') {
      this.close(instance);
      return { verified: true };
    }
    if (operation === 'close_window') {
      await this.computer.controller.call(instance.app, 'close', {
        handle: instance.window.handle,
      });
      return { verified: true };
    }
    if (operation === 'foreground_studio') {
      const window = this.ownerWindow();
      if (!window || window.isDestroyed()) throw Error('VISUAL_QA_UI_UNAVAILABLE');
      window.show();
      window.focus();
      await wait(100);
      if (!window.isFocused()) throw Error('VISUAL_QA_UI_FOCUS_FAILED');
      return { verified: true };
    }
    if (operation === 'blur') {
      const other = spawn(instance.exe, [], { stdio: 'ignore' });
      instance.children.push(other);
      let window;
      for (let i = 0; i < 30 && !window; i++) {
        await wait(100);
        window = (await this.computer.controller.list_windows(instance.app)).find(
          (w) => w.pid === other.pid && w.visible,
        );
      }
      if (!window) throw Error('VISUAL_QA_AUXILIARY_UNAVAILABLE');
      await this.computer.controller.focus_window(instance.app, window.handle);
      return { verified: true };
    }
    if (operation === 'inspect') {
      const state = this.computer.state(instance.scope),
        target = state.targets.find((t) => t.target_id === instance.target);
      const frame = target?.observation;
      if (!frame) throw Error('VISUAL_QA_OBSERVATION_EXPIRED');
      const types = Object.fromEntries(
        ['Button', 'Edit', 'Text', 'List', 'Menu', 'Tree'].map((type) => [
          type,
          frame.elements.filter((e) => e.type === type).length,
        ]),
      );
      return {
        verified: true,
        types,
        element_count: frame.elements.length,
        mask_count: frame.image?.redacted_regions || 0,
        password_excluded: !frame.elements.some((e) => e.automation_id === 'ProtectedInput'),
        image_available: !!frame.image,
        capture_error: frame.capture_error,
        rebuild_generation:
          frame.elements.find((e) => e.automation_id === 'RebuildGeneration')?.value ||
          frame.elements.find((e) => e.automation_id === 'RebuildGeneration')?.name,
        input_bounds: frame.elements.find((e) => e.automation_id === 'SampleInput')?.bounds,
        chinese:
          frame.ocr?.lines.some((l) => l.text.replace(/\s/g, '').includes('本地视觉测试')) || false,
        english: frame.ocr?.lines.some((l) => l.text === 'Orchard QA') || false,
        ocr_boxes_valid:
          frame.ocr?.lines.every(
            (l) =>
              l.bounds.length === 4 &&
              l.bounds.every(Number.isFinite) &&
              l.bounds[2] > 0 &&
              l.bounds[3] > 0,
          ) || false,
        ocr_confidence_null: frame.ocr?.lines.every((l) => l.confidence === null) || false,
        custom_text_absent_from_uia: !frame.elements.some((e) => e.name.includes('本地视觉测试')),
        window_dpi: frame.geometry.window_dpi,
        monitor_dpi: frame.geometry.monitor_dpi,
        monitor_count: frame.geometry.monitors.length,
        window_bounds: frame.geometry.bounds,
        uia_ms: frame.uia_duration_ms,
        ocr_ms: frame.ocr_duration_ms,
        capture_ms: frame.capture_duration_ms,
        native_memory_bytes: frame.window.memory_bytes,
        image_bytes: frame.image ? Buffer.byteLength(frame.image.data_uri) : 0,
      };
    }
    throw Error('VISUAL_QA_OPERATION_UNSUPPORTED');
  }
  async open(args) {
    const automation = path.join(__dirname.replace('app.asar', 'app.asar.unpacked'), 'automation');
    const source = path.join(automation, 'VisualFixture.cs');
    const digest = createHash('sha256').update(fs.readFileSync(source)).digest('hex');
    const root = path.join(this.paths.cache, 'visual-qa', digest);
    fs.mkdirSync(root, { recursive: true });
    const exe = path.join(root, 'VisualFixture.exe');
    if (!fs.existsSync(exe)) {
      const base = path.join(process.env.WINDIR, 'Microsoft.NET', 'Framework64', 'v4.0.30319');
      const result = spawnSync(
        path.join(base, 'csc.exe'),
        [
          '/nologo',
          '/target:winexe',
          '/r:System.Xaml.dll',
          '/r:System.Windows.Forms.dll',
          '/r:System.Drawing.dll',
          ...['PresentationFramework', 'PresentationCore', 'WindowsBase', 'UIAutomationTypes'].map(
            (n) => '/r:' + path.join(base, 'WPF', n + '.dll'),
          ),
          '/out:' + exe,
          source,
        ],
        { windowsHide: true, timeout: 15000, env: { ...process.env, TEMP: root, TMP: root } },
      );
      if (result.status !== 0) throw Error('VISUAL_QA_COMPILER_UNAVAILABLE');
    }
    const id = randomUUID(),
      app = 'visual_fixture_' + id;
    const fixture = spawn(exe, [], { stdio: 'ignore' });
    this.computer.controller.fixtureEntries.set(app, {
      installed: true,
      path: exe,
      name: 'Companion Visual QA',
    });
    const instance = {
      id,
      app,
      exe,
      children: [fixture],
      scope: args.owner,
      consent: args.consent_id,
    };
    this.instances.set(id, instance);
    try {
      let choices = [];
      for (let i = 0; i < 30 && !choices.length; i++) {
        if (!this.instances.has(id)) throw Error('VISUAL_QA_CANCELLED');
        await wait(100);
        choices = await this.computer.windows(args.owner, app);
      }
      if (choices.length !== 1) throw Error('VISUAL_QA_WINDOW_NOT_READY');
      const state = await this.computer.enable(args.owner, choices[0].choice_id, {
        preview: true,
        coordinate_fallback: false,
      });
      if (!this.instances.has(id)) throw Error('VISUAL_QA_CANCELLED');
      instance.target = state.targets[0].target_id;
      instance.window = this.computer.get(args.owner, instance.target).window;
      return { fixture_id: id, target_id: instance.target, verified: true };
    } catch (error) {
      this.close(instance);
      throw error;
    }
  }
  close(instance) {
    this.computer.stopOwner(instance.scope);
    for (const child of instance.children) child.kill();
    this.computer.controller.fixtureEntries.delete(instance.app);
    this.instances.delete(instance.id);
  }
  stop() {
    for (const instance of [...this.instances.values()]) this.close(instance);
    this.consents.clear();
  }
}
module.exports = { VisualQA };
