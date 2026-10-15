import { useEffect, useMemo, useRef, useState, type ReactNode, type PointerEvent } from 'react';
import { moduleIds, WorkspaceModuleRegistry, type ModuleId } from './WorkspaceModuleRegistry';
import {
  effectiveLayout,
  canonicalLayout,
  resizePreset,
  visibleIds,
  type WorkbenchLayout,
} from './WorkspaceLayoutEngine';
import DockableWorkspaceModule from './DockableWorkspaceModule';
import type { DropTarget } from './useModuleDrag';
import {
  createWorkspaceResize,
  workspaceDividers,
  type WorkspaceDivider,
  type WorkspaceResize,
} from './WorkspaceSplitLayoutEngine';
import { ResizeSessionController, type ResizeFrame } from './ResizeSessionController';

type Props = {
  layout: WorkbenchLayout;
  onCommit: (layout: WorkbenchLayout) => void;
  renderModule: (id: ModuleId, visible: boolean) => ReactNode;
  onChange: (id: ModuleId, patch: Partial<WorkbenchLayout['modules'][ModuleId]>) => void;
  drag: ModuleId | null;
  target: DropTarget | null;
};
type Gesture = {
  controller: ResizeSessionController;
  calculator: WorkspaceResize;
  next: WorkbenchLayout;
  source: WorkbenchLayout;
  painted: WorkbenchLayout;
  cells: Map<ModuleId, HTMLElement>;
  dividers: Map<string, HTMLElement>;
  height: number;
};
const heightOf = (layout: WorkbenchLayout) =>
  Math.max(
    240,
    ...visibleIds(layout).map((id) => layout.modules[id].y + layout.modules[id].height),
  );
const dividerStyle = (d: WorkspaceDivider) =>
  d.axis === 'x'
    ? { left: d.position - 4, top: d.start, width: 8, height: d.end - d.start }
    : { left: d.start, top: d.position - 4, width: d.end - d.start, height: 8 };

/** 绘制可见坐标；缩放帧直接更新 DOM，仅指针松开后提交 React/持久布局。 */
export default function WorkspaceGrid({
  layout,
  onCommit,
  renderModule,
  onChange,
  drag,
  target,
}: Props) {
  const grid = useRef<HTMLDivElement>(null),
    [width, setWidth] = useState(750),
    pendingWidth = useRef(750);
  const gesture = useRef<Gesture | null>(null),
    latest = useRef({ layout, onCommit });
  latest.current = { layout, onCommit };
  const effective = useMemo(() => effectiveLayout(layout, width), [layout, width]),
    view = effective.layout,
    viewRef = useRef(view);
  viewRef.current = view;
  const visible = visibleIds(view),
    maximized = visible.find((id) => view.modules[id].size === 'maximized'),
    canvasHeight = heightOf(view);
  const dividers = useMemo(() => workspaceDividers(view), [view]);
  const shared = (id: ModuleId, edge: string) =>
    dividers.some(
      (d) =>
        d.axis === (edge === 'e' || edge === 'w' ? 'x' : 'y') &&
        (edge === 'w' || edge === 'n' ? d.after : d.before).includes(id),
    );
  function setResizing(active: boolean, container: HTMLElement) {
    grid.current?.classList.toggle('is-resizing', active);
    window.dispatchEvent(
      new CustomEvent('companion-workspace-resize', { detail: { active, container } }),
    );
  }
  function log(state: Gesture, reason: string) {
    void window.desktopAPI
      ?.logDiagnostic('workbench-ui.log', state.controller.metrics(reason))
      .catch(() => {});
  }
  function paint(value: WorkbenchLayout, state: Gesture) {
    let changed = false;
    for (const [id, element] of state.cells) {
      const before = state.painted.modules[id],
        next = value.modules[id];
      for (const [field, css] of [
        ['x', 'left'],
        ['y', 'top'],
        ['width', 'width'],
        ['height', 'height'],
      ] as const)
        if (Math.abs(before[field] - next[field]) > 0.001) {
          element.style[css] = next[field] + 'px';
          changed = true;
        }
      const compact = String(
        next.width < WorkspaceModuleRegistry[id].intrinsicMinSize.width || next.height < 100,
      );
      if (element.dataset.compact !== compact) element.dataset.compact = compact;
    }
    const previous = new Map(state.painted.splitLayout?.dividers.map((d) => [d.id, d]));
    for (const d of value.splitLayout?.dividers || []) {
      const element = state.dividers.get(d.id);
      if (!element) continue;
      const before = previous.get(d.id),
        nextStyle = dividerStyle(d),
        beforeStyle = before && dividerStyle(before);
      for (const css of ['left', 'top', 'width', 'height'] as const)
        if (!beforeStyle || Math.abs(nextStyle[css] - beforeStyle[css]) > 0.001)
          element.style[css] = nextStyle[css] + 'px';
    }
    const height = heightOf(value);
    if (grid.current && Math.abs(state.height - height) > 0.001) {
      grid.current.style.height = height + 'px';
      state.height = height;
      changed = true;
    }
    state.painted = value;
    return changed;
  }
  function update(frame: ResizeFrame) {
    const state = gesture.current;
    if (!state) return { scrollTop: frame.scrollTop, changed: false };
    let scroll = frame.scrollTop + frame.scrollDelta,
      next = state.calculator.update(frame.dx, frame.dy + frame.scrollDelta);
    // 先计算允许消耗的滚动，再唯一绘制；约束重算不应增加 paint 或排队帧。
    const allowed = frame.engine.clampScroll(scroll, heightOf(next));
    if (Math.abs(allowed - scroll) > 0.001) {
      scroll = allowed;
      next = state.calculator.update(frame.dx, frame.dy + scroll - frame.scrollTop);
    }
    state.next = next;
    return { scrollTop: scroll, changed: paint(next, state) };
  }
  function cancel(reason = 'cancel') {
    const state = gesture.current;
    if (!state) return;
    gesture.current = null;
    state.controller.stop();
    paint(viewRef.current, state);
    setResizing(false, state.controller.options.container);
    log(state, reason);
    if (pendingWidth.current !== width) setWidth(pendingWidth.current);
  }
  // ResizeObserver 不在手势中触发 React 尺寸重建，改为刷新控制器几何。
  useEffect(() => {
    const element = grid.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const observed = entries.find((entry) => entry.target === element);
      if (!observed) return;
      const next = observed.contentRect.width;
      if (Math.abs(next - pendingWidth.current) < 0.25) return;
      pendingWidth.current = next;
      if (gesture.current) gesture.current.controller.invalidateGeometry();
      else setWidth(next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  // 原生监听仅注册一次；gesture/latest/viewRef 提供当前会话与提交对象。
  // 不机械加入每次渲染变化的依赖，以免清理函数中断正在进行的手势。
  useEffect(() => {
    const escape = (e: KeyboardEvent) => {
        if (e.key === 'Escape') cancel('escape');
      },
      blur = () => cancel('blur'),
      resize = () => gesture.current?.controller.invalidateGeometry();
    window.addEventListener('keydown', escape);
    window.addEventListener('blur', blur);
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('keydown', escape);
      window.removeEventListener('blur', blur);
      window.removeEventListener('resize', resize);
      cancel('unmount');
    };
  }, []);
  useEffect(() => {
    if (gesture.current && layout !== gesture.current.source) cancel('layout_changed');
  }, [layout]);
  function down(e: PointerEvent, id: ModuleId, edge: string, dividerId: string | null = null) {
    if (e.button !== 0) return;
    const element = grid.current,
      container = element?.closest<HTMLElement>('.workbench');
    if (!element || !container) return;
    e.preventDefault();
    e.stopPropagation();
    cancel('replaced');
    const handle = e.currentTarget as HTMLElement;
    handle.setPointerCapture(e.pointerId);
    const calculator = createWorkspaceResize(viewRef.current, id, edge);
    const state: Gesture = {
      controller: null!,
      calculator,
      next: viewRef.current,
      source: latest.current.layout,
      painted: viewRef.current,
      cells: new Map(),
      dividers: new Map(),
      height: heightOf(viewRef.current),
    };
    element
      .querySelectorAll<HTMLElement>('[data-cell-module]')
      .forEach((node) => state.cells.set(node.dataset.cellModule as ModuleId, node));
    element
      .querySelectorAll<HTMLElement>('[data-divider-id]')
      .forEach((node) => state.dividers.set(node.dataset.dividerId!, node));
    state.controller = new ResizeSessionController({
      id,
      dividerId,
      edge,
      layoutVersion: layout.version,
      pointerId: e.pointerId,
      handle,
      container,
      grid: element,
      clientX: e.clientX,
      clientY: e.clientY,
      startSize: { width: view.modules[id].width, height: view.modules[id].height },
      update,
    });
    gesture.current = state;
    setResizing(true, container);
  }
  function move(e: PointerEvent) {
    const state = gesture.current;
    if (!state || state.controller.options.pointerId !== e.pointerId) return;
    e.preventDefault();
    state.controller.pointer(e.clientX, e.clientY);
  }
  function up(e: PointerEvent) {
    const state = gesture.current;
    if (!state || state.controller.options.pointerId !== e.pointerId) return;
    // 最后位置刷新 → 停止/释放捕获 → 网页恢复 → 转回持久坐标并提交。
    state.controller.flush(e.clientX, e.clientY);
    gesture.current = null;
    state.controller.stop();
    setResizing(false, state.controller.options.container);
    log(state, 'pointer_up');
    latest.current.onCommit(canonicalLayout(state.next, state.source));
    if (pendingWidth.current !== width) setWidth(pendingWidth.current);
  }
  return (
    <div
      className={'workspace-grid workspace-free ' + (drag ? 'is-dragging ' : '')}
      ref={grid}
      data-responsive={effective.responsive}
      style={{ height: canvasHeight }}
    >
      {moduleIds
        .filter((id) => visible.includes(id) || id === 'browser')
        .map((id) => {
          const p = view.modules[id],
            shown = visible.includes(id) && (!maximized || maximized === id),
            compact =
              p.width < WorkspaceModuleRegistry[id].intrinsicMinSize.width || p.height < 100;
          return (
            <div
              className={'workspace-grid-cell ' + (id === layout.active ? 'module-focused' : '')}
              data-cell-module={id}
              data-compact={compact}
              hidden={!shown}
              key={id}
              style={{
                left: maximized === id ? 0 : p.x,
                top: maximized === id ? 0 : p.y,
                width: maximized === id ? width : p.width,
                height: maximized === id ? canvasHeight : p.height,
              }}
            >
              <DockableWorkspaceModule
                id={id}
                placement={p}
                className={compact ? 'module-compact' : ''}
                onChange={(patch) => onChange(id, patch)}
                onSize={(preset, w, h) =>
                  onCommit(canonicalLayout(resizePreset(view, id, preset, w, h), layout))
                }
                resizeHandles={(maximized
                  ? []
                  : ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].filter(
                      (edge) => edge.length > 1 || !shared(id, edge),
                    )
                ).map((edge) => (
                  <div
                    key={edge}
                    className={'module-resize-handle resize-' + edge}
                    data-resize-edge={edge}
                    role="separator"
                    aria-label={WorkspaceModuleRegistry[id].title + '调整' + edge + '边界'}
                    onPointerDown={(e) => down(e, id, edge)}
                    onPointerMove={move}
                    onPointerUp={up}
                    onPointerCancel={() => cancel('pointer_cancel')}
                    onLostPointerCapture={() => {
                      if (gesture.current) cancel('lost_capture');
                    }}
                  />
                ))}
              >
                {shown || id === 'browser'
                  ? renderModule(id, shown && !p.collapsed && !compact && !drag)
                  : null}
              </DockableWorkspaceModule>
            </div>
          );
        })}
      {!maximized &&
        dividers.map((d) => (
          <div
            key={d.id}
            className={'workspace-resize-divider divider-' + d.axis}
            data-divider-id={d.id}
            data-divider-axis={d.axis}
            data-divider-before={d.before.join(',')}
            data-divider-after={d.after.join(',')}
            role="separator"
            aria-orientation={d.axis === 'x' ? 'vertical' : 'horizontal'}
            aria-label={
              d.before.map((id) => WorkspaceModuleRegistry[id].title).join('、') +
              '与' +
              d.after.map((id) => WorkspaceModuleRegistry[id].title).join('、') +
              '的共享分割线'
            }
            style={dividerStyle(d)}
            onPointerDown={(e) => down(e, d.before[0], d.axis === 'x' ? 'e' : 's', d.id)}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={() => cancel('pointer_cancel')}
            onLostPointerCapture={() => {
              if (gesture.current) cancel('lost_capture');
            }}
          />
        ))}
      {drag && (
        <div className="workspace-drop-zones free-drop-zones">
          {visible
            .filter((id) => id !== drag)
            .map((id) => {
              const p = view.modules[id];
              return (
                <div
                  className="free-drop-pane"
                  key={id}
                  style={{ left: p.x, top: p.y, width: p.width, height: p.height }}
                >
                  {['left', 'right', 'top', 'bottom', 'replace'].map((edge) => (
                    <div
                      key={edge}
                      data-drop-module={id}
                      data-drop-edge={edge}
                      className={
                        'workspace-drop-zone free-drop-' +
                        edge +
                        (target?.module === id && target.edge === edge ? ' drop-active' : '')
                      }
                    >
                      <span>
                        {
                          {
                            left: '左侧分割',
                            right: '右侧分割',
                            top: '上方分割',
                            bottom: '下方分割',
                            replace: '替换位置',
                          }[edge]
                        }
                      </span>
                    </div>
                  ))}
                </div>
              );
            })}
          <div
            data-drop-edge="row"
            className="workspace-drop-zone free-drop-row"
            style={{ top: canvasHeight }}
          >
            紧邻新增行
          </div>
        </div>
      )}
    </div>
  );
}
