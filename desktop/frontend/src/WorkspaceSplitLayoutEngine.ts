import { moduleIds, type ModuleId } from './WorkspaceModuleRegistry';
import type { WorkbenchLayout, ModulePlacement } from './WorkspaceLayoutEngine';

export type WorkspaceDivider = {
  id: string;
  axis: 'x' | 'y';
  position: number;
  start: number;
  end: number;
  before: ModuleId[];
  after: ModuleId[];
  ratio: number;
};
export type WorkspaceSplits = {
  version: 1;
  kind: 'shared-boundaries';
  dividers: WorkspaceDivider[];
};
const visible = (l: WorkbenchLayout) =>
  moduleIds.filter((id) => !l.modules[id].closed && !l.modules[id].minimized);
const near = (a: number, b: number) => Math.abs(a - b) < 0.15;
const interval = (p: ModulePlacement, axis: 'x' | 'y') =>
  axis === 'x' ? [p.y, p.y + p.height] : [p.x, p.x + p.width];
const size = (p: ModulePlacement, axis: 'x' | 'y') => (axis === 'x' ? p.width : p.height);
const origin = (p: ModulePlacement, axis: 'x' | 'y') => (axis === 'x' ? p.x : p.y);

/** 从现有自由矩形建立边界图，保留 T 形连接。
 * 同一边界的连通窗格共享分割线，不把分离的边缘合成同一手势。
 */
export function workspaceDividers(l: WorkbenchLayout): WorkspaceDivider[] {
  const ids = visible(l),
    result: WorkspaceDivider[] = [];
  for (const axis of ['x', 'y'] as const) {
    const gap = axis === 'x' ? (l.gapX ?? l.gap ?? 10) : (l.gap ?? 10);
    const pairs: Array<{
      before: ModuleId;
      after: ModuleId;
      position: number;
      start: number;
      end: number;
    }> = [];
    for (const a of ids)
      for (const b of ids) {
        if (a === b) continue;
        const p = l.modules[a],
          q = l.modules[b],
          pa = interval(p, axis),
          qa = interval(q, axis),
          start = Math.max(pa[0], qa[0]),
          end = Math.min(pa[1], qa[1]);
        if (end - start > 0.1 && near(origin(p, axis) + size(p, axis) + gap, origin(q, axis)))
          pairs.push({
            before: a,
            after: b,
            position: origin(p, axis) + size(p, axis) + gap / 2,
            start,
            end,
          });
      }
    while (pairs.length) {
      const component = [pairs.shift()!];
      let expanded = true;
      while (expanded) {
        expanded = false;
        for (let i = pairs.length - 1; i >= 0; i--) {
          const p = pairs[i];
          if (
            component.some(
              (q) =>
                near(p.position, q.position) &&
                (p.before === q.before ||
                  p.after === q.after ||
                  Math.min(p.end, q.end) - Math.max(p.start, q.start) >= -gap),
            )
          ) {
            component.push(...pairs.splice(i, 1));
            expanded = true;
          }
        }
      }
      const before = [...new Set(component.map((p) => p.before))].sort(),
        after = [...new Set(component.map((p) => p.after))].sort(),
        position = component[0].position;
      const low = Math.min(...before.map((id) => origin(l.modules[id], axis))),
        high = Math.max(
          ...after.map((id) => origin(l.modules[id], axis) + size(l.modules[id], axis)),
        );
      result.push({
        id: axis + ':' + before.join(',') + '|' + after.join(','),
        axis,
        position,
        start: Math.min(...component.map((p) => p.start)),
        end: Math.max(...component.map((p) => p.end)),
        before,
        after,
        ratio: (position - low) / (high - low),
      });
    }
  }
  return result;
}
export function withWorkspaceSplits(l: WorkbenchLayout): WorkbenchLayout {
  l.splitLayout = { version: 1, kind: 'shared-boundaries', dividers: workspaceDividers(l) };
  return l;
}
type AxisPlan = { axis: 'x' | 'y'; before: Set<ModuleId>; after: Set<ModuleId> };
type DividerPlan = { divider: WorkspaceDivider; pairs: Array<[ModuleId, ModuleId]> };
export type WorkspaceResize = {
  dividers: WorkspaceDivider[];
  update: (dx: number, dy: number) => WorkbenchLayout;
};

function prepareAxis(
  dividers: WorkspaceDivider[],
  id: ModuleId,
  edge: string,
  axis: 'x' | 'y',
): AxisPlan {
  const leading = edge === 'w' || edge === 'n',
    divider = dividers.find((d) => d.axis === axis && (leading ? d.after : d.before).includes(id));
  return {
    axis,
    before: new Set(divider?.before ?? (leading ? [] : [id])),
    after: new Set(divider?.after ?? (leading ? [id] : [])),
  };
}

/** 求解本轴移动的线性约束；顺序和浮点容差属于现有几何行为。 */
function applyAxis(
  input: WorkbenchLayout,
  id: ModuleId,
  ids: ModuleId[],
  plan: AxisPlan,
  delta: number,
): WorkbenchLayout {
  if (!Number.isFinite(delta) || !delta) return input;
  const { axis, before, after } = plan,
    minimum = axis === 'x' ? 64 : 40;
  let lower = -Infinity,
    upper = Infinity;
  for (const key of before) {
    const p = input.modules[key];
    lower = Math.max(lower, Math.min(minimum, size(p, axis)) - size(p, axis));
    if (axis === 'x') upper = Math.min(upper, input.canvasWidth - origin(p, axis) - size(p, axis));
  }
  for (const key of after) {
    const p = input.modules[key];
    upper = Math.min(upper, size(p, axis) - Math.min(minimum, size(p, axis)));
    lower = Math.max(lower, -origin(p, axis));
    if (p.pinned_position && key !== id) {
      lower = Math.max(lower, 0);
      upper = Math.min(upper, 0);
    }
  }
  // 仅移动的边缘参与碰撞约束；直接求线性上下界，避免逐帧深拷贝探测。
  const constrain = (left: ModuleId, right: ModuleId) => {
    const p = input.modules[left],
      q = input.modules[right],
      coefficient = Number(before.has(left)) - Number(after.has(right));
    if (!coefficient) return;
    const room = origin(q, axis) - origin(p, axis) - size(p, axis) + 0.01;
    if (coefficient > 0) upper = Math.min(upper, room);
    else lower = Math.max(lower, -room);
  };
  for (let a = 0; a < ids.length; a++)
    for (let b = a + 1; b < ids.length; b++) {
      const left = ids[a],
        right = ids[b],
        p = input.modules[left],
        q = input.modules[right],
        pi = interval(p, axis),
        qi = interval(q, axis);
      if (pi[0] >= qi[1] - 0.01 || qi[0] >= pi[1] - 0.01) continue;
      if (origin(p, axis) + size(p, axis) <= origin(q, axis) + 0.01) constrain(left, right);
      else if (origin(q, axis) + size(q, axis) <= origin(p, axis) + 0.01) constrain(right, left);
    }
  delta = Math.max(lower, Math.min(upper, delta));
  if (!delta) return input;
  const modules = { ...input.modules };
  for (const key of before) {
    const p = input.modules[key];
    modules[key] = {
      ...p,
      ...(axis === 'x' ? { width: p.width + delta } : { height: p.height + delta }),
    };
  }
  for (const key of after) {
    const p = input.modules[key];
    modules[key] = {
      ...p,
      ...(axis === 'x'
        ? { x: p.x + delta, width: p.width - delta }
        : { y: p.y + delta, height: p.height - delta }),
    };
  }
  return { ...input, modules };
}

function dividerGeometry(input: WorkbenchLayout, plan: DividerPlan): WorkspaceDivider | null {
  const d = plan.divider,
    axis = d.axis,
    pairs = plan.pairs
      .map(([before, after]) => {
        const p = interval(input.modules[before], axis),
          q = interval(input.modules[after], axis);
        return { start: Math.max(p[0], q[0]), end: Math.min(p[1], q[1]) };
      })
      .filter((p) => p.end - p.start > 0.1);
  if (!pairs.length) return null;
  const gap = axis === 'x' ? (input.gapX ?? input.gap ?? 10) : (input.gap ?? 10);
  const position =
    origin(input.modules[d.before[0]], axis) + size(input.modules[d.before[0]], axis) + gap / 2;
  const low = Math.min(...d.before.map((id) => origin(input.modules[id], axis))),
    high = Math.max(
      ...d.after.map((id) => origin(input.modules[id], axis) + size(input.modules[id], axis)),
    );
  return {
    ...d,
    position,
    start: Math.min(...pairs.map((p) => p.start)),
    end: Math.max(...pairs.map((p) => p.end)),
    ratio: (position - low) / (high - low),
  };
}

/** 每次手势只准备一次邻接关系，dx/dy 始终相对于开始时的几何。
 * 帧更新只复制变化窗格，保留 history/legacy 引用；下一次手势再重算拓扑。
 */
export function createWorkspaceResize(
  input: WorkbenchLayout,
  id: ModuleId,
  edge: string,
): WorkspaceResize {
  const modules = Object.fromEntries(
    moduleIds.map((key) => [key, { ...input.modules[key] }]),
  ) as WorkbenchLayout['modules'];
  const start = { ...input, modules },
    ids = visible(start),
    dividers = workspaceDividers(start);
  const x =
    edge.includes('e') || edge.includes('w')
      ? prepareAxis(dividers, id, edge.includes('w') ? 'w' : 'e', 'x')
      : null;
  const y =
    edge.includes('n') || edge.includes('s')
      ? prepareAxis(dividers, id, edge.includes('n') ? 'n' : 's', 'y')
      : null;
  const plans: DividerPlan[] = dividers.map((divider) => ({
    divider,
    pairs: divider.before.flatMap((a) =>
      divider.after
        .filter((b) => {
          const p = start.modules[a],
            q = start.modules[b],
            pi = interval(p, divider.axis),
            qi = interval(q, divider.axis),
            gap = divider.axis === 'x' ? (start.gapX ?? start.gap ?? 10) : (start.gap ?? 10);
          return (
            near(origin(p, divider.axis) + size(p, divider.axis) + gap, origin(q, divider.axis)) &&
            Math.min(pi[1], qi[1]) - Math.max(pi[0], qi[0]) > 0.1
          );
        })
        .map((b) => [a, b] as [ModuleId, ModuleId]),
    ),
  }));
  return {
    dividers,
    update(dx, dy) {
      let next = start;
      if (x) next = applyAxis(next, id, ids, x, dx);
      if (y) next = applyAxis(next, id, ids, y, dy);
      const p = next.modules[id];
      return {
        ...next,
        modules: {
          ...next.modules,
          [id]: { ...p, size: 'normal', preferred_zone: { x: p.x, y: p.y } },
        },
        splitLayout: {
          version: 1,
          kind: 'shared-boundaries',
          dividers: plans
            .map((plan) => dividerGeometry(next, plan))
            .filter((d): d is WorkspaceDivider => !!d),
        },
      };
    },
  };
}
export function resizeWorkspaceSplit(
  input: WorkbenchLayout,
  id: ModuleId,
  edge: string,
  dx: number,
  dy: number,
) {
  return createWorkspaceResize(input, id, edge).update(dx, dy);
}
