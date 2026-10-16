'use strict';
const ids = [
  'conversation',
  'workflow',
  'collaboration',
  'code',
  'diff',
  'terminal',
  'browser',
  'files',
  'application',
  'media',
  'system',
  'preview',
  'artifacts',
  'permissions',
  'trace',
  'evaluation',
  'workflow_builder',
];
const finite = (v, f) => (Number.isFinite(Number(v)) ? Number(v) : f);
/**
 * 验证自由布局与共享分割线，拒绝重叠或无效结构，避免覆盖已有有效布局。
 * history=false 用于单层历史快照，防止递归复制全部撤销历史。
 */
function cleanFree(value, history = true) {
  if (
    value.version !== 3 ||
    !ids.includes(value.active) ||
    !value.modules ||
    JSON.stringify(value).length > 256000
  )
    throw Error('无效自由布局');
  const canvasWidth = Math.max(64, finite(value.canvasWidth, 1000)),
    modules = {};
  for (const [order, id] of ids.entries()) {
    const p = value.modules[id] || {},
      width = Math.min(canvasWidth, Math.max(1, finite(p.width, 360)));
    modules[id] = {
      module_type: id,
      module_instance_id: id,
      x: Math.max(0, Math.min(canvasWidth - width, finite(p.x, 0))),
      y: Math.max(0, finite(p.y, 0)),
      width,
      height: Math.max(40, finite(p.height, 220)),
      pinned_position: !!p.pinned_position,
      position: p.position === 'side' ? 'side' : 'main',
      size: p.size === 'maximized' ? 'maximized' : 'normal',
      dockSide: ['left', 'right'].includes(p.dockSide) ? p.dockSide : null,
      collapsed: !!p.collapsed,
      minimized: !!p.minimized,
      closed:
        id === 'conversation'
          ? false
          : ['trace', 'evaluation', 'workflow_builder'].includes(id) && p.closed === undefined
            ? true
            : !!p.closed,
      order: finite(p.order, order),
      column: 0,
      row: 0,
      columnSpan: 1,
      rowSpan: 1,
    };
    if (p.preferred_zone)
      modules[id].preferred_zone = {
        x: Math.max(0, finite(p.preferred_zone.x, 0)),
        y: Math.max(0, finite(p.preferred_zone.y, 0)),
      };
  }
  const visible = Object.values(modules).filter((p) => !p.closed && !p.minimized);
  for (let a = 0; a < visible.length; a++)
    for (let b = a + 1; b < visible.length; b++) {
      const p = visible[a],
        r = visible[b];
      if (
        p.x < r.x + r.width - 0.1 &&
        r.x < p.x + p.width - 0.1 &&
        p.y < r.y + r.height - 0.1 &&
        r.y < p.y + p.height - 0.1
      )
        throw Error('布局存在重叠，未覆盖已保存布局');
    }
  let splitLayout;
  if (value.splitLayout) {
    const s = value.splitLayout;
    if (
      s.version !== 1 ||
      s.kind !== 'shared-boundaries' ||
      !Array.isArray(s.dividers) ||
      s.dividers.length > 196
    )
      throw Error('无效共享分割线布局');
    const dividers = s.dividers.map((d) => {
      if (
        !['x', 'y'].includes(d.axis) ||
        typeof d.id !== 'string' ||
        d.id.length > 300 ||
        ![d.position, d.start, d.end, d.ratio].every(Number.isFinite) ||
        d.start < 0 ||
        d.end <= d.start ||
        d.ratio <= 0 ||
        d.ratio >= 1 ||
        !['before', 'after'].every(
          (k) => Array.isArray(d[k]) && d[k].length && d[k].every((id) => ids.includes(id)),
        ) ||
        d.before.some((id) => d.after.includes(id))
      )
        throw Error('无效共享分割线');
      return {
        id: d.id,
        axis: d.axis,
        position: d.position,
        start: d.start,
        end: d.end,
        ratio: d.ratio,
        before: [...d.before],
        after: [...d.after],
      };
    });
    splitLayout = { version: 1, kind: 'shared-boundaries', dividers };
  }
  return {
    version: 3,
    active: value.active,
    locked: !!value.locked,
    canvasWidth,
    columns: [],
    rows: [],
    modules,
    ...(splitLayout ? { splitLayout } : {}),
    ...(value.legacy ? { legacy: value.legacy } : {}),
    ...(history
      ? {
          history: (value.history || [])
            .slice(-10)
            .map((v) => cleanFree({ ...v, legacy: undefined, history: undefined }, false)),
        }
      : {}),
  };
}
module.exports = { cleanFree };
