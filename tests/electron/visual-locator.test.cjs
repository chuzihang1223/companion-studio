const assert = require('node:assert/strict');
const { candidates, publicCandidate } = require('../../desktop/electron/visual-locator.cjs');
const frame = {
  observation_id: 'observation',
  created_ms: 1000,
  elements: [
    {
      identity: 'one',
      automation_id: 'go',
      type: 'Button',
      name: 'Apply',
      visible: true,
      enabled: true,
      bounds: [-300, 20, 80, 40],
    },
  ],
  ocr: {
    lines: [
      { text: 'Duplicate', bounds: [10, 10, 20, 20] },
      { text: 'Duplicate', bounds: [10, 40, 20, 20] },
    ],
  },
};
assert.equal(
  candidates(frame, { description: 'Apply', source: 'auto', matching: 'exact' }, 'target', 1000)[0]
    .source,
  'uia',
);
assert.equal(
  candidates(frame, { description: 'Duplicate', source: 'auto', matching: 'exact' }, 'target', 1000)
    .length,
  2,
);
assert.throws(() => candidates(frame, { description: 'Apply' }, 'target', 10000), /EXPIRED/);
assert.equal(
  candidates(
    frame,
    { description: 'Apply', automation_id: 'missing', source: 'auto' },
    'target',
    1000,
  ).length,
  0,
);
assert.equal(
  candidates(frame, { description: 'applf', matching: 'limited_fuzzy' }, 'target', 1000)[0]
    .match_quality,
  0.8,
);
const result = publicCandidate(
  candidates(frame, { description: 'Apply', matching: 'exact' }, 'target', 1000)[0],
);
assert.throws(
  () => candidates({ ...frame, tree_truncated: true }, { description: 'Apply' }, 'target', 1000),
  /INCOMPLETE/,
);
assert.ok(!result.native);
assert.ok(!JSON.stringify(result).includes('Apply'));
console.log('Visual locator: unique/ambiguous/stale/negative coordinates/fuzzy privacy PASS');
