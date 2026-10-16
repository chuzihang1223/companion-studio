'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { q, sleep } = require('./studio-smoke-driver.cjs');

module.exports = async function templates(driver, owner) {
  const read = (op, options = {}) =>
    driver.c.evaluate(`window.desktopAPI.readWorkflows(${q(op)},${q(options)})`);
  const control = (op, options = {}) =>
    driver.c.evaluate(`window.desktopAPI.controlWorkflows(${q(op)},${q(options)})`);
  const ext = (op, options = {}) =>
    driver.c.evaluate(`window.desktopAPI.controlExtensions(${q(op)},${q(options)})`);
  const preview = await ext('skill-example', { name: 'project-review' });
  const skill = await ext('skill-import', { import_token: preview.import_token });
  await ext('skill-toggle', { skill_id: skill.id, enabled: true });
  driver.check(
    skill.name === 'project-review',
    'Original MIT Skill is explicitly imported/enabled for the owned template fixture',
  );
  const project = path.resolve(__dirname, '../..');
  const command = driver.source
    ? path.join(project, '.venv/Scripts/python.exe')
    : path.join(path.dirname(driver.exe), 'resources/companion-backend/companion-backend.exe');
  const args = driver.source
    ? ['-B', path.join(project, 'backend/run.py'), '--mcp-demo']
    : ['--mcp-demo'];
  assert(fs.existsSync(command));
  const server = await ext('server-save', {
    config: {
      display_name: 'Owned Workflow Fixture',
      transport: 'stdio',
      endpoint_or_command: command,
      args,
      enabled: true,
    },
  });
  await ext('connect', { server_id: server.server_id });
  const tools = await driver.c.evaluate(
    `window.desktopAPI.readExtensions('tools',${q({ server_id: server.server_id })})`,
  );
  const echo = tools.items.find((t) => t.name === 'echo');
  assert(echo);
  await ext('tool-save', { tool_id: echo.id, enabled: true, risk_level: 'READ_ONLY_LOCAL' });
  for (const template_id of ['project-review', 'code-test-report', 'mcp-research']) {
    const graph = await read('template', { template_id });
    const saved = await control('save', { definition: graph, revision: 0 });
    const pub = await control('publish', {
      definition: saved.definition,
      revision: saved.revision,
    });
    const created = await control('start', {
      workflow_id: graph.workflow_id,
      version_id: pub.version_id,
      conversation_id: owner,
      inputs:
        template_id === 'mcp-research'
          ? { topic: 'Synthetic orchid data' }
          : { path: template_id === 'code-test-report' ? 'check_fixture.py' : 'fixture.txt' },
    });
    let result;
    const approved = new Set();
    for (let attempt = 0; attempt < 450; attempt++) {
      result = await read('run', { run_id: created.run_id, conversation_id: owner });
      const pending = result.nodes.find((n) => n.pending)?.pending;
      if (pending && !approved.has(pending.approval_id)) {
        approved.add(pending.approval_id);
        await control('approve', {
          run_id: result.run_id,
          conversation_id: owner,
          approval_id: pending.approval_id,
          decision: true,
        });
      }
      if (['COMPLETED', 'FAILED', 'NEEDS_REVIEW', 'CANCELLED'].includes(result.state)) break;
      await sleep(60);
    }
    driver.check(
      result?.state === 'COMPLETED' && result.output,
      `Actual ${template_id} template completes through original services`,
      {
        state: result?.state,
        error: result?.error_code,
        model_calls: result?.model_calls,
        tool_calls: result?.tool_calls,
      },
    );
    if (template_id === 'code-test-report')
      driver.check(
        result.nodes.find((n) => n.node_id === 'test').output.exit_code === 0 &&
          result.nodes.some((n) => n.state === 'SKIPPED'),
        'Actual approved Python test exits zero; inactive branches and Merge settle',
      );
    if (template_id === 'mcp-research')
      driver.check(
        result.nodes.find((n) => n.node_id === 'external').output.protocol_success === true &&
          approved.size === 1,
        'MCP template uses real SDK and original native-to-exact-grant authorization',
      );
    driver.report.templateRuns ||= {};
    driver.report.templateRuns[template_id] = {
      run_id: result.run_id,
      workflow_id: graph.workflow_id,
      version_id: result.version_id,
    };
  }
  await ext('disconnect', { server_id: server.server_id });
  driver.check(true, 'Owned template MCP server is explicitly disconnected after acceptance');
};
