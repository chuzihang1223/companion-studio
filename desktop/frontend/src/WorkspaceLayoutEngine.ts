import { moduleIds, WorkspaceModuleRegistry, type ModuleId } from './WorkspaceModuleRegistry';
import { compactLayout, resolveCollisions, findNearestFreeSlot } from './LayoutCollisionResolver';
import {
  resizeWorkspaceSplit,
  withWorkspaceSplits,
  type WorkspaceSplits,
} from './WorkspaceSplitLayoutEngine';
/** 持久化矩形使用 canvasWidth 基准坐标；网格字段保留供旧布局迁移。 */
export type ModulePlacement = {
  module_type: ModuleId;
  module_instance_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  pinned_position: boolean;
  preferred_zone?: { x: number; y: number };
  column: number;
  row: number;
  columnSpan: number;
  rowSpan: number;
  position: 'main' | 'side';
  size: 'normal' | 'maximized';
  dockSide: 'left' | 'right' | null;
  collapsed: boolean;
  minimized: boolean;
  closed: boolean;
  order: number;
};
export type WorkbenchLayout = {
  version: 3;
  active: ModuleId;
  locked: boolean;
  canvasWidth: number;
  gap?: number;
  gapX?: number;
  splitLayout?: WorkspaceSplits;
  columns: Array<{ id: string; width: number; minWidth: number }>;
  rows: Array<{ id: string; height: number; minHeight: number }>;
  modules: Record<ModuleId, ModulePlacement>;
  legacy?: unknown;
  history?: WorkbenchLayout[];
};
export type LayoutTemplate = 'default' | 'coding' | 'browser' | 'system' | 'blank';
/**
 * 旧文件允许缺少 V3 字段。此类型描述迁移输入，不替代主进程校验；
 * V2 的轨道和网格索引由旧格式约定提供，V1 只含部分模块状态。
 */
type SavedWorkbenchLayout = {
  version?: number;
  active?: unknown;
  locked?: unknown;
  canvasWidth?: unknown;
  modules?: Partial<Record<ModuleId, Partial<ModulePlacement>>>;
  columns?: Array<{ width: number }>;
  rows?: Array<{ height: number }>;
  legacy?: unknown;
  history?: unknown;
};
export const GAP = 10;
export const visibleIds = (layout: WorkbenchLayout) =>
  moduleIds.filter((id) => !layout.modules[id].closed && !layout.modules[id].minimized);
export const overlap = (a: ModulePlacement, b: ModulePlacement) =>
  a.x < b.x + b.width - 0.1 &&
  b.x < a.x + a.width - 0.1 &&
  a.y < b.y + b.height - 0.1 &&
  b.y < a.y + a.height - 0.1;
const clone = (layout: WorkbenchLayout): WorkbenchLayout => structuredClone(layout);
const finite = (v: unknown, fallback: number) =>
  Number.isFinite(Number(v)) ? Number(v) : fallback;
export function defaultLayout(template: LayoutTemplate = 'default'): WorkbenchLayout {
  const modules = Object.fromEntries(
    moduleIds.map((id, order) => [
      id,
      {
        module_type: id,
        module_instance_id: id,
        x: 0,
        y: 0,
        width: 400,
        height: 220,
        pinned_position: false,
        column: 0,
        row: 0,
        columnSpan: 1,
        rowSpan: 1,
        position: WorkspaceModuleRegistry[id].defaultDock,
        size: 'normal',
        dockSide: null,
        collapsed: false,
        minimized: false,
        closed: true,
        order,
      },
    ]),
  ) as Record<ModuleId, ModulePlacement>;
  const layout: WorkbenchLayout = {
    version: 3,
    active: 'conversation',
    locked: false,
    canvasWidth: 1000,
    columns: [],
    rows: [],
    modules,
  };
  const slots: Array<[ModuleId, number, number, number, number]> =
    template === 'coding'
      ? [
          ['code', 0, 0, 690, 280],
          ['workflow', 700, 0, 300, 170],
          ['conversation', 0, 290, 350, 220],
          ['diff', 360, 290, 330, 220],
          ['terminal', 700, 180, 300, 330],
        ]
      : template === 'browser'
        ? [
            ['browser', 0, 0, 690, 470],
            ['conversation', 700, 0, 300, 260],
            ['workflow', 700, 270, 300, 200],
          ]
        : template === 'system'
          ? [
              ['system', 0, 0, 650, 230],
              ['conversation', 0, 240, 650, 230],
              ['workflow', 660, 0, 340, 470],
            ]
          : template === 'blank'
            ? [['conversation', 0, 0, 1000, 240]]
            : [
                ['conversation', 0, 0, 650, 470],
                ['workflow', 660, 0, 340, 220],
                ['collaboration', 660, 230, 340, 240],
              ];
  for (const [id, x, y, width, height] of slots)
    Object.assign(modules[id], { x, y, width, height, closed: false });
  return withWorkspaceSplits(layout);
}
/** 迁移旧状态；合法 V3 几何准确恢复，不以整理布局的方式改变位置。 */
export function migrateLayout(saved: unknown): WorkbenchLayout {
  const value = saved as SavedWorkbenchLayout,
    layout = defaultLayout();
  if (!value?.modules) return layout;
  layout.active = moduleIds.includes(value.active as ModuleId)
    ? (value.active as ModuleId)
    : 'conversation';
  layout.locked = !!value.locked;
  // 保留旧版缺少轨道时由 NaN 回退到默认宽度的行为。
  layout.canvasWidth =
    value.version === 3
      ? Math.max(64, finite(value.canvasWidth, 1000))
      : (value.columns?.reduce((n, c) => n + finite(c.width, 320), 0) as number) +
          ((value.columns?.length as number) - 1) * GAP || 1000;
  layout.legacy = value.version === 3 ? value.legacy : value;
  for (const id of moduleIds) {
    const old = value.modules[id];
    if (!old) continue;
    const p = layout.modules[id];
    Object.assign(p, old, {
      module_type: id,
      module_instance_id: id,
      pinned_position: !!old.pinned_position,
    });
    // 这里仅收窄 V2 字段类型，沿用原轨道相加及间距计算，不新增运行时修复。
    if (value.version === 2) {
      p.x = value.columns!.slice(0, old.column).reduce((n, c) => n + c.width + GAP, 0);
      p.y = value.rows!.slice(0, old.row).reduce((n, r) => n + r.height + GAP, 0);
      p.width =
        value
          .columns!.slice(old.column, old.column! + old.columnSpan!)
          .reduce((n, c) => n + c.width, 0) +
        (old.columnSpan! - 1) * GAP;
      p.height =
        value.rows!.slice(old.row, old.row! + old.rowSpan!).reduce((n, r) => n + r.height, 0) +
          (old.rowSpan! - 1) * GAP || 220;
    }
    p.width = Math.max(
      value.version === 3 ? 1 : 64,
      Math.min(layout.canvasWidth, finite(p.width, 360)),
    );
    p.x = Math.max(0, Math.min(layout.canvasWidth - p.width, finite(p.x, 0)));
    p.y = Math.max(0, finite(p.y, 0));
    p.height = p.collapsed ? 40 : Math.max(40, finite(p.height, 220));
    if (id === 'conversation') p.closed = false;
  }
  // 间距式重排会改变错位共享窗格；仅修复实际重叠，保留合法自由布局。
  const visible = visibleIds(layout),
    hasOverlap = visible.some((id, i) =>
      visible.slice(i + 1).some((other) => overlap(layout.modules[id], layout.modules[other])),
    );
  const result =
    value.version === 3 ? (hasOverlap ? resolveCollisions(layout) : layout) : compactLayout(layout);
  result.history = Array.isArray(value.history)
    ? value.history.slice(-10).map((h: unknown) => {
        const v = migrateLayout({ ...(h as object), history: undefined });
        delete v.history;
        delete v.legacy;
        return v;
      })
    : [];
  return withWorkspaceSplits(result);
}
export const pack = (input: WorkbenchLayout, priority?: ModuleId) => compactLayout(input, priority);
export function openModule(input: WorkbenchLayout, id: ModuleId, focus = true) {
  const layout = clone(input),
    p = layout.modules[id],
    anchor =
      id === 'terminal'
        ? 'code'
        : id === 'workflow'
          ? 'conversation'
          : id === 'code' || id === 'browser'
            ? 'conversation'
            : layout.active;
  if (p.closed || p.minimized) {
    Object.assign(p, {
      closed: false,
      minimized: false,
      collapsed: false,
      dockSide: null,
      size: 'normal',
    });
    const viewport = typeof document === 'undefined' ? null : document.querySelector('.workbench'),
      grid = typeof document === 'undefined' ? null : document.querySelector('.workspace-grid'),
      viewportTop =
        viewport && grid
          ? Math.max(0, viewport.getBoundingClientRect().top - grid.getBoundingClientRect().top)
          : 0;
    const a = layout.modules[anchor],
      hint = {
        ...(p.preferred_zone || { x: a.x, y: id === 'terminal' ? a.y + a.height + GAP : a.y }),
        viewportTop,
        viewportBottom: viewportTop + (viewport?.clientHeight || Infinity),
      };
    Object.assign(p, findNearestFreeSlot(layout, id, hint));
  }
  if (focus) layout.active = id;
  return resolveCollisions(layout);
}
export function moveModule(input: WorkbenchLayout, id: ModuleId, x: number, y: number) {
  const layout = clone(input),
    p = layout.modules[id];
  Object.assign(p, {
    x: Math.max(0, Math.min(layout.canvasWidth - p.width, x)),
    y: Math.max(0, y),
    minimized: false,
    closed: false,
    dockSide: null,
    size: 'normal',
  });
  p.preferred_zone = { x: p.x, y: p.y };
  layout.active = id;
  return resolveCollisions(layout, id);
}
export function splitModule(
  input: WorkbenchLayout,
  id: ModuleId,
  target: ModuleId | undefined,
  edge: string,
) {
  const layout = clone(input),
    p = layout.modules[id];
  Object.assign(p, {
    closed: false,
    minimized: false,
    collapsed: false,
    dockSide: null,
    size: 'normal',
  });
  layout.active = id;
  if (!target || edge === 'row') {
    p.x = 0;
    p.y = Math.max(
      0,
      ...visibleIds(layout)
        .filter((v) => v !== id)
        .map((v) => layout.modules[v].y + layout.modules[v].height + GAP),
    );
    return resolveCollisions(layout, id);
  }
  const t = layout.modules[target];
  if (target === id) return layout;
  Object.assign(p, { x: t.x, y: t.y, width: t.width, height: t.height });
  if (edge === 'replace') {
    t.minimized = true;
    t.dockSide = 'left';
  } else if (edge === 'left' || edge === 'right') {
    p.width = t.width = Math.max(64, (t.width - GAP) / 2);
    if (edge === 'left') t.x += p.width + GAP;
    else p.x += t.width + GAP;
  } else {
    p.height = t.height = Math.max(40, (t.height - GAP) / 2);
    if (edge === 'top') t.y += p.height + GAP;
    else p.y += t.height + GAP;
  }
  p.preferred_zone = { x: p.x, y: p.y };
  return resolveCollisions(layout, id);
}
/** 持久坐标转容器像素；正常视口只缩放横向，窄视口改为纵向排列。 */
export function effectiveLayout(
  input: WorkbenchLayout,
  width: number,
): { layout: WorkbenchLayout; responsive: boolean } {
  const layout = clone(input),
    scale = Math.max(64, width) / input.canvasWidth;
  layout.canvasWidth = Math.max(64, width);
  layout.gap = input.gap ?? GAP;
  layout.gapX = (input.gap ?? GAP) * scale;
  const responsive =
    width < 480 || visibleIds(input).some((id) => input.modules[id].width * scale < 64);
  for (const id of moduleIds) {
    const p = layout.modules[id];
    p.x *= scale;
    if (p.preferred_zone) p.preferred_zone.x *= scale;
    p.width = Math.min(
      layout.canvasWidth,
      Math.max(Math.min(64, layout.canvasWidth), p.width * scale),
    );
    p.x = Math.min(p.x, layout.canvasWidth - p.width);
  }
  if (responsive) {
    let y = 0;
    for (const id of visibleIds(layout).sort(
      (a, b) =>
        layout.modules[a].y - layout.modules[b].y || layout.modules[a].x - layout.modules[b].x,
    )) {
      const p = layout.modules[id];
      p.x = 0;
      p.width = layout.canvasWidth;
      p.y = y;
      y += p.height + GAP;
    }
  }
  return { layout: withWorkspaceSplits(layout), responsive };
}
/** 手势提交时还原横向基准坐标，避免下一次渲染再次缩放同一结果。 */
export function canonicalLayout(view: WorkbenchLayout, original: WorkbenchLayout) {
  const result = clone(view),
    scale = original.canvasWidth / view.canvasWidth;
  result.canvasWidth = original.canvasWidth;
  result.gap = original.gap ?? GAP;
  delete result.gapX;
  for (const id of moduleIds) {
    result.modules[id].x *= scale;
    result.modules[id].width *= scale;
    if (result.modules[id].preferred_zone) result.modules[id].preferred_zone!.x *= scale;
  }
  return withWorkspaceSplits(result);
}
export function resizeTracks(
  input: WorkbenchLayout,
  id: ModuleId,
  edge: string,
  dx: number,
  dy: number,
) {
  return resizeWorkspaceSplit(input, id, edge, dx, dy);
}
export function resizePreset(
  input: WorkbenchLayout,
  id: ModuleId,
  preset: string,
  width?: number,
  height?: number,
) {
  const layout = clone(input),
    p = layout.modules[id],
    sizes: Record<string, [number, number]> = {
      compact: [180, 100],
      default: [360, 240],
      wide: [layout.canvasWidth * 0.8, 280],
      large: [layout.canvasWidth, 500],
    };
  const [w, h] =
    preset === 'custom'
      ? [finite(width, p.width), finite(height, p.height)]
      : sizes[preset] || [p.width, p.height];
  p.collapsed = false;
  return resizeWorkspaceSplit(
    layout,
    id,
    'se',
    Math.max(64, w) - p.width,
    Math.max(40, h) - p.height,
  );
}
export { findNearestFreeSlot, compactLayout, resolveCollisions };
