'use strict';
const { randomUUID } = require('node:crypto');
const normalize = (text) =>
  String(text || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/\s+/g, '');
function similarity(left, right) {
  if (left === right) return 1;
  if (Math.abs(left.length - right.length) > 2 || Math.min(left.length, right.length) < 5) return 0;
  let previous = Array.from({ length: right.length + 1 }, (_, i) => i);
  for (let i = 1; i <= left.length; i++) {
    const next = [i];
    for (let j = 1; j <= right.length; j++)
      next[j] = Math.min(
        next[j - 1] + 1,
        previous[j] + 1,
        previous[j - 1] + (left[i - 1] === right[j - 1] ? 0 : 1),
      );
    previous = next;
  }
  return 1 - previous[right.length] / Math.max(left.length, right.length);
}
function candidates(frame, locator, targetId, now = Date.now()) {
  if (frame.tree_truncated) throw Error('COMPUTER_OBSERVATION_INCOMPLETE');
  if (now - frame.created_ms > 8000 || now < frame.created_ms - 1000)
    throw Error('COMPUTER_OBSERVATION_EXPIRED');
  const match = (name) => {
    if (locator.matching === 'exact') return name === locator.description ? 1 : 0;
    const a = normalize(name),
      b = normalize(locator.description);
    return locator.matching === 'limited_fuzzy' ? similarity(a, b) : a === b ? 1 : 0;
  };
  const safeBox = (box) =>
    Array.isArray(box) &&
    box.length === 4 &&
    box.every(Number.isFinite) &&
    box[2] > 0 &&
    box[3] > 0;
  const rows = (frame.elements || []).filter(
    (row) =>
      row.visible &&
      row.enabled &&
      safeBox(row.bounds) &&
      (!locator.automation_id || row.automation_id === locator.automation_id) &&
      (!locator.control_type || row.type === locator.control_type) &&
      (locator.automation_id || match(row.name) >= 0.8),
  );
  const wrap = (row, source, name, quality) => ({
    candidate_id: randomUUID(),
    source,
    window_id: targetId,
    observation_id: frame.observation_id,
    element_identity: row.identity || null,
    bounding_box: row.bounds,
    confidence: source === 'ocr' ? null : 1,
    match_quality: quality,
    is_enabled: row.enabled !== false,
    is_visible: true,
    capture_timestamp: frame.created_ms,
    risk: 'requires_action_approval',
    // Native execution consumes this server-owned observation, never model coordinates.
    native: {
      identity: row.identity,
      automation_id: row.automation_id,
      type: row.type,
      bounds: row.bounds,
      name,
      source,
    },
  });
  if (locator.source === 'dom') return []; // DOM is handled by the existing owned browser adapter.
  if (rows.length && ['auto', 'uia'].includes(locator.source || 'auto'))
    return rows
      .slice(0, 32)
      .map((r) => wrap(r, 'uia', r.name, locator.automation_id ? 1 : match(r.name)));
  if (locator.source === 'uia' || locator.automation_id || locator.control_type) return [];
  return (frame.ocr?.lines || [])
    .filter((r) => safeBox(r.bounds) && match(r.text) >= 0.8)
    .slice(0, 32)
    .map((r) => wrap(r, 'ocr', r.text, match(r.text)));
}
const publicCandidate = ({ native, element_identity, ...rest }) => ({
  ...rest,
  element_identity: rest.candidate_id,
});
module.exports = { normalize, similarity, candidates, publicCandidate };
