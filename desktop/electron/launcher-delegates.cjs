const fs = require('node:fs'),
  path = require('node:path');
const { windowsPath } = require('./application-record.cjs');
// Some installed bootstrappers signal an existing platform instead of spawning a child.
// Match only known registered executable paths in bounded, recent installed diagnostics.
// Never return or log diagnostic text, account values, command arguments or tokens.
function findLauncherDelegate(record, records, now = Date.now()) {
  if (!record.launch_candidates?.some((c) => c.launch_type === 'launcher')) return null;
  const root = windowsPath(record.install_location);
  if (!root || root.length <= 3) return null;
  const files = [];
  for (const folder of ['log', 'logs']) {
    const directory = path.win32.join(root, folder);
    try {
      const resolved = fs.realpathSync(directory).toLowerCase(),
        allowed = fs.realpathSync(root).toLowerCase() + '\\';
      if (!resolved.startsWith(allowed)) continue;
      for (const file of fs.readdirSync(directory, { withFileTypes: true }).slice(0, 80)) {
        if (!file.isFile()) continue;
        const target = path.win32.join(directory, file.name),
          stat = fs.statSync(target);
        if (stat.size <= 512 * 1024 && now - stat.mtimeMs < 24 * 60 * 60 * 1000)
          files.push({ target, modified: stat.mtimeMs });
      }
    } catch {}
  }
  const candidates = records
    .filter((r) => r.app_id !== record.app_id && r.installed && r.source !== 'custom')
    .flatMap((r) =>
      (r.launch_candidates || [])
        .filter((c) => ['exe', 'launcher'].includes(c.launch_type))
        .map((c) => ({ record: r, path: windowsPath(c.target) })),
    );
  const matches = new Map();
  for (const file of files.sort((a, b) => b.modified - a.modified).slice(0, 2)) {
    const text = fs.readFileSync(file.target, 'utf8').toLowerCase().replace(/\\\\/g, '\\');
    for (const candidate of candidates) {
      if (!candidate.path || candidate.path.toLowerCase().startsWith(root.toLowerCase() + '\\'))
        continue;
      if (
        /\\(?:windows|system32|syswow64)\\|\\(?:cmd|powershell|pwsh|explorer|rundll32|svchost|msedge|chrome)\.exe$/i.test(
          candidate.path,
        )
      )
        continue;
      const at = text.indexOf(candidate.path.toLowerCase()),
        next = text[at + candidate.path.length];
      if (at >= 0 && (!next || /[\s"';,)\]>]/.test(next)))
        matches.set(candidate.record.app_id, candidate);
    }
  }
  if (matches.size !== 1) return null;
  const selected = [...matches.values()][0];
  return {
    app_id: selected.record.app_id,
    name: selected.record.name,
    path: selected.path,
    evidence: 'registered_executable_in_recent_launcher_diagnostic',
  };
}
module.exports = { findLauncherDelegate };
