'use strict';
// Production controllers with synthetic device/IPC boundaries. No real microphone,
// native consent, cloud call or playback is claimed by this regression group.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../../desktop/node_modules/typescript');
const root = path.resolve(__dirname, '../../desktop/frontend/src');
global.localStorage = global.sessionStorage = { getItem: () => null, setItem() {} };
global.window = { desktopAPI: undefined };
global.requestAnimationFrame = (fn) => fn();
class FailedCapture {
  async start() { throw Error('synthetic device disconnected'); }
  async stop() {}
}
const modules = new Map();
function load(name) {
  if (modules.has(name)) return modules.get(name);
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, name + '.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInThisContext('(function(require,module,exports){' + code + '\n})', { filename: name })(
    (id) => id === './AudioCapture' ? { AudioCapture: FailedCapture } : id === './api' ? {} : id.startsWith('./') ? load(path.posix.normalize(path.posix.join(path.posix.dirname(name), id))) : require(id),
    module, module.exports,
  );
  modules.set(name, module.exports);
  return module.exports;
}
const pause = () => new Promise((resolve) => setTimeout(resolve, 20));
async function run() {
  const { VoiceController } = load('realtimeVoice/VoiceController');
  const calls = [];
  const identity = { voice_session_id: 'session', conversation_id: 'A', state: 'IDLE' };
  const sdk = {
    realtimeVoiceStart: async () => identity,
    realtimeVoiceControl: async (action) => {
      calls.push(action);
      return action === 'begin' ? { ...identity, turn_id: 'turn', generation_id: 'generation', state: 'INITIALIZING' } : identity;
    },
  };
  const controller = new VoiceController(() => {}, async () => {}, async () => {}, sdk);
  await controller.start({});
  await controller.record('');
  assert.deepEqual(calls, ['interrupt', 'begin', 'interrupt']);
  assert.equal(controller.view.busy, false);
  assert.equal(controller.view.capturing, false);
  assert.equal(controller.view.identity.state, 'IDLE');
  assert.match(controller.view.error, /device disconnected/);

  const { ConversationSubmissionService } = load('ConversationSubmissionService');
  const { ConversationRuntimeStore } = load('ConversationRuntimeStore');
  const store = new ConversationRuntimeStore();
  let current = true;
  const streams = [], replies = [];
  const api = {
    routeAgent: async () => 'agent', loadConversation: async () => [],
    loadWorkSession: async () => ({ id: 'work' }),
    planAgent: async (_character, id) => ({ plan_id: id, work_session_id: 'work', calls: [{ requires_confirmation: true }] }),
    stopWorkSession: async () => {},
    streamAgent: async function* (...args) {
      streams.push(args);
      yield { event: 'assistant_started', data: { message_id: 'reply', conversation_id: args[0] } };
      yield { event: 'completed', data: { message_id: 'reply', content: 'fixture' } };
    },
  };
  const service = new ConversationSubmissionService(store, { stop() {} }, { visible: () => true, created() {}, messages() {}, work() {}, changed() {} }, api);
  const request = (id) => ({ conversationId: id, character: { id: 'assistant' }, text: 'fixture', mode: 'agent', folder: null, attachments: [], voice: true, expression: 'natural', speed: 1, volume: 1, prefetch: true, autoWork: false, voiceContext: { voice_session_id: 'voice', turn_id: id, generation_id: 'generation' }, stillCurrent: () => current, onVoiceReply: (id) => replies.push(id) });
  await service.submit(request('A'));
  await pause();
  service.approve('A', ['approved'], { ...request('A'), voice: false, voiceContext: undefined, onVoiceReply: undefined });
  await pause();
  assert.equal(streams.length, 1);
  assert.equal(streams[0][2], true);
  assert.deepEqual(streams[0][6], request('A').voiceContext);
  assert.deepEqual(replies, ['reply']);
  await service.submit(request('B'));
  await pause();
  current = false;
  service.approve('B', ['approved'], { ...request('B'), stillCurrent: undefined });
  await pause();
  assert.equal(streams.length, 1, 'Old voice approval cannot send using ordinary settings');
  assert.match(store.get('B').error, /语音会话已停止/);
  console.log(JSON.stringify({ capture_failure_cleanup: true, native_approval_preserves_voice_snapshot: true, stale_approval_denied: true, physical_microphone: false, actual_playback: false }));
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
