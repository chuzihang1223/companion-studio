// Owned synthetic fixture; all input flows through the product's original
// ComputerController and ComputerUseCoordinator, never a test-side click API.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const { ComputerController } = require('../../desktop/electron/computer-controller.cjs');
const { ComputerUseCoordinator } = require('../../desktop/electron/computer-use.cjs');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function run(root = path.resolve('.cache/visual-native')) {
  fs.mkdirSync(root, { recursive: true });
  const base = path.join(process.env.WINDIR, 'Microsoft.NET', 'Framework64', 'v4.0.30319');
  const executable = path.join(root, 'VisualFixture.exe');
  const compilation = spawnSync(path.join(base, 'csc.exe'), ['/nologo','/target:winexe','/r:System.Xaml.dll',
    ...['PresentationFramework','PresentationCore','WindowsBase','UIAutomationTypes'].map(n => '/r:' + path.join(base,'WPF',n+'.dll')),
    '/out:'+executable,path.resolve('desktop/electron/automation/VisualFixture.cs')], { windowsHide: true });
  if (compilation.status !== 0) throw Error('FIXTURE_COMPILATION_FAILED');
  const fixture = spawn(executable, [], { stdio: 'ignore' });
  const registry = { detect: () => ({ installed: true, name: 'Owned visual fixture', path: executable }) };
  const controller = new ComputerController(registry, root);
  const scope = { conversation_id: randomUUID(), character_id: 'assistant' };
  const computer = new ComputerUseCoordinator(controller, registry, async value => assert.deepEqual(value, scope));
  const checks = [], started = Date.now();
  const check = (name, value) => { assert.ok(value, name);checks.push({ name, status: 'PASS' }); };
  try {
    let windows = [];
    for (let i=0;i<30&&!windows.length;i++) { await delay(100);windows = await computer.windows(scope, 'fixture'); }
    check('window discovery', windows.length === 1);
    const state = await computer.enable(scope, windows[0].choice_id, { preview: true });
    const target_id = state.targets[0].target_id;
    const execute = (operation, args = {}) => computer.execute(operation, { target_id, ...args }, scope);
    check('focus verifies actual window', (await execute('focus_window')).verified);
    const observation = await execute('observe');check('UIA tree and memory capture', observation.element_count >= 20 && observation.capture_available);
    const frame = computer.state(scope).targets[0].observation;
    check('password excluded and masked', !frame.elements.some(r => r.automation_id === 'ProtectedInput') && frame.image.redacted_regions >= 1);
    check('Chinese local OCR', frame.ocr.lines.some(r => r.text.replace(/\s/g,'').includes('本地视觉测试')));
    check('English local OCR', frame.ocr.lines.some(r => r.text === 'Orchard QA'));
    check('OCR confidence unavailable is honest', frame.ocr.lines.every(r => r.confidence === null));
    const find = async id => {
      const result = await execute('find_element', { locator: { description: id, automation_id: id, source: 'uia', matching: 'exact' } });
      assert.equal(result.candidates.length, 1, id);return { observation_id: result.observation_id, candidate_id: result.candidates[0].candidate_id };
    };
    check('semantic text input verified', (await execute('type_text', { ...await find('SampleInput'), text: 'Local QA 中文' })).verified);
    check('semantic button result verified', (await execute('click', { ...await find('ApplySample'), verification: { kind: 'text_equals', automation_id: 'SampleResult', expected: 'Local QA 中文' } })).verified);
    const old = await find('SampleInput');
    check('rebuild verifies replacement', (await execute('click', { ...await find('RebuildControl'), verification: { kind: 'element_present', automation_id: 'SampleInput' } })).verified);
    check('old observation cannot be replayed', !(await execute('type_text', { ...old, text: 'wrong' })).verified);
    check('fresh replacement accepts text', (await execute('type_text', { ...await find('SampleInput'), text: 'Replacement' })).verified);
    check('scroll checks actual percent', (await execute('scroll', { ...await find('SampleList'), direction: 'down' })).verified);
    const duplicate = await execute('find_element', { locator: { description: 'Duplicate', source: 'ocr', matching: 'exact' } });
    check('duplicate OCR requires review', duplicate.status === 'NEEDS_REVIEW' && duplicate.candidates.length === 2);
    const wrong = await computer.execute('observe', { target_id }, { ...scope, conversation_id: randomUUID() }).catch(() => null);
    check('cross-conversation denied', !wrong);
    computer.control(scope,target_id,'pause');
    check('pause discards snapshots', computer.state(scope).targets[0].observation === null);
    check('paused input refused', !(await execute('observe').catch(() => null)));
    computer.control(scope,target_id,'resume');
    check('resume requires fresh observation', !(await execute('type_text', { ...old, text: 'wrong' })).verified);
    const metrics = { duration_ms: Date.now()-started, observation_ms: observation.duration_ms, rss_bytes: process.memoryUsage().rss,
      monitor_count: frame.geometry.monitors.length, window_dpi: frame.geometry.window_dpi, monitor_dpi: frame.geometry.monitor_dpi };
    const report = { status:'PASS', kind:'real_native_gui', model_requests:0, checks, metrics };
    fs.writeFileSync(path.join(root,'native-report.json'),JSON.stringify(report,null,2));return report;
  } finally { computer.close();controller.stop();fixture.kill(); }
}
module.exports = { run };
if (require.main === module) run(process.argv[2]).then(r => console.log(JSON.stringify(r))).catch(e => { console.error(e);process.exitCode=1; });
