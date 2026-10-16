import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointer } from 'react';
import { fitView, nodeTypes, arrangeNodes } from './model';
import NodeView from './WorkflowNodeView';
import type { NodeType, WorkflowDefinition, WorkflowNode, NodeRun } from './types';

type View = { x: number; y: number; scale: number };
type Drag = {
  id: number;
  kind: 'nodes' | 'pan' | 'select' | 'connect';
  start: { x: number; y: number };
  view: View;
  origins: Record<string, { x: number; y: number }>;
  moved: boolean;
};
type Link = { node: string; port: string };

export default function WorkflowCanvas({
  graph,
  selected,
  onSelect,
  onChange,
  onConnect,
  onAdd,
  onRemoveEdge,
  records,
  readOnly = false,
}: {
  graph: WorkflowDefinition;
  selected: string[];
  onSelect: (ids: string[]) => void;
  onChange: (graph: WorkflowDefinition) => void;
  onConnect: (a: string, port: string, b: string, input?: string) => void;
  onAdd: (type: NodeType, position: { x: number; y: number }) => void;
  onRemoveEdge: (id: string) => void;
  records: NodeRun[];
  readOnly?: boolean;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 600, height: 400 });
  const [view, setView] = useState<View>({ x: 35, y: 35, scale: 1 });
  const [grid, setGrid] = useState(true);
  const [preview, setPreview] = useState<Record<string, { x: number; y: number }>>({});
  const [selectionBox, setSelectionBox] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  const [link, setLink] = useState<Link | null>(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const gesture = useRef<Drag | null>(null);
  const frame = useRef(0);
  const positions = useRef<Record<string, { x: number; y: number }>>({});
  const graphRef = useRef(graph);
  graphRef.current = graph;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const viewRef = useRef(view);
  viewRef.current = view;
  const linkRef = useRef(link);
  linkRef.current = link;
  const actions = useRef({ onChange, onSelect, onConnect });
  actions.current = { onChange, onSelect, onConnect };
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ width: r.width, height: r.height });
    });
    observer.observe(element);
    const preventScroll = (event: WheelEvent) => event.preventDefault();
    element.addEventListener('wheel', preventScroll, { passive: false });
    return () => {
      observer.disconnect();
      element.removeEventListener('wheel', preventScroll);
    };
  }, []);
  useEffect(() => {
    // Native subscriptions use current refs; cleanup never commits a cancelled gesture.
    function abort() {
      const drag = gesture.current;
      gesture.current = null;
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
      positions.current = {};
      setPreview({});
      setSelectionBox(null);
      linkRef.current = null;
      setLink(null);
      if (drag && viewport.current?.hasPointerCapture(drag.id))
        viewport.current.releasePointerCapture(drag.id);
    }
    function key(e: KeyboardEvent) {
      if (e.key === 'Escape') abort();
    }
    window.addEventListener('blur', abort);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('blur', abort);
      window.removeEventListener('keydown', key);
      if (frame.current) cancelAnimationFrame(frame.current);
      gesture.current = null;
    };
  }, []);
  function point(clientX: number, clientY: number) {
    const box = viewport.current!.getBoundingClientRect();
    const current = viewRef.current;
    return {
      x: (clientX - box.left - current.x) / current.scale,
      y: (clientY - box.top - current.y) / current.scale,
    };
  }
  function begin(e: ReactPointer, kind: Drag['kind'], node?: WorkflowNode) {
    e.stopPropagation();
    if ((e.button !== 0 && e.button !== 1) || (readOnly && kind !== 'pan')) return;
    viewport.current?.focus();
    let ids = selectedRef.current;
    if (node) {
      ids = e.shiftKey
        ? [...new Set([...ids, node.node_id])]
        : ids.includes(node.node_id)
          ? ids
          : [node.node_id];
      actions.current.onSelect(ids);
    } else if (kind === 'select') actions.current.onSelect([]);
    gesture.current = {
      id: e.pointerId,
      kind,
      start: { x: e.clientX, y: e.clientY },
      view: viewRef.current,
      origins: Object.fromEntries(
        graphRef.current.nodes
          .filter((n) => ids.includes(n.node_id))
          .map((n) => [n.node_id, { ...n.position }]),
      ),
      moved: false,
    };
    viewport.current?.setPointerCapture(e.pointerId);
    e.preventDefault();
  }
  function move(e: ReactPointer) {
    const p = point(e.clientX, e.clientY);
    if (linkRef.current) setPointer(p);
    const drag = gesture.current;
    if (!drag || drag.id !== e.pointerId) return;
    e.stopPropagation();
    const dx = e.clientX - drag.start.x,
      dy = e.clientY - drag.start.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    if (drag.kind === 'connect') return;
    if (drag.kind === 'nodes') {
      positions.current = Object.fromEntries(
        Object.entries(drag.origins).map(([id, origin]) => [
          id,
          {
            x: Math.max(-10000, Math.min(10000, origin.x + dx / drag.view.scale)),
            y: Math.max(-10000, Math.min(10000, origin.y + dy / drag.view.scale)),
          },
        ]),
      );
      if (!frame.current)
        frame.current = requestAnimationFrame(() => {
          frame.current = 0;
          setPreview(positions.current);
        });
    } else if (drag.kind === 'pan')
      setView({ ...drag.view, x: drag.view.x + dx, y: drag.view.y + dy });
    else {
      const a = point(drag.start.x, drag.start.y);
      setSelectionBox({
        x: Math.min(a.x, p.x),
        y: Math.min(a.y, p.y),
        width: Math.abs(p.x - a.x),
        height: Math.abs(p.y - a.y),
      });
    }
  }
  function finish(e: ReactPointer, cancelled = false) {
    const drag = gesture.current;
    if (!drag || drag.id !== e.pointerId) return;
    e.stopPropagation();
    gesture.current = null;
    if (drag.kind === 'connect') {
      const target = document
        .elementFromPoint(e.clientX, e.clientY)
        ?.closest('[data-input-node]') as HTMLElement | null;
      if (!cancelled && target && linkRef.current) {
        actions.current.onConnect(
          linkRef.current.node,
          linkRef.current.port,
          target.dataset.inputNode!,
          target.dataset.port,
        );
        linkRef.current = null;
        setLink(null);
      } else if (cancelled) {
        linkRef.current = null;
        setLink(null);
      }
    }
    if (!cancelled && drag.moved && drag.kind === 'nodes')
      actions.current.onChange({
        ...graphRef.current,
        nodes: graphRef.current.nodes.map((n) =>
          positions.current[n.node_id] ? { ...n, position: positions.current[n.node_id] } : n,
        ),
      });
    if (!cancelled && drag.moved && drag.kind === 'select') {
      const a = point(drag.start.x, drag.start.y),
        b = point(e.clientX, e.clientY);
      actions.current.onSelect(
        graphRef.current.nodes
          .filter(
            (n) =>
              n.position.x + 210 >= Math.min(a.x, b.x) &&
              n.position.x <= Math.max(a.x, b.x) &&
              n.position.y + 126 >= Math.min(a.y, b.y) &&
              n.position.y <= Math.max(a.y, b.y),
          )
          .map((n) => n.node_id),
      );
    }
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = 0;
    positions.current = {};
    setPreview({});
    setSelectionBox(null);
    if (viewport.current?.hasPointerCapture(e.pointerId))
      viewport.current.releasePointerCapture(e.pointerId);
  }
  function port(node: WorkflowNode, name: string, output: boolean) {
    if (readOnly) return;
    if (output) {
      linkRef.current = { node: node.node_id, port: name };
      setLink(linkRef.current);
      setPointer({ x: node.position.x + 260, y: node.position.y + 65 });
    } else if (linkRef.current) {
      actions.current.onConnect(linkRef.current.node, linkRef.current.port, node.node_id, name);
      linkRef.current = null;
      setLink(null);
    }
  }
  function beginLink(e: ReactPointer, node: WorkflowNode, name: string) {
    e.stopPropagation();
    if (readOnly || e.button !== 0) return;
    port(node, name, true);
    gesture.current = {
      id: e.pointerId,
      kind: 'connect',
      start: { x: e.clientX, y: e.clientY },
      view: viewRef.current,
      origins: {},
      moved: false,
    };
    viewport.current?.setPointerCapture(e.pointerId);
    e.preventDefault();
  }
  const shown = useMemo(
    () => graph.nodes.map((n) => (preview[n.node_id] ? { ...n, position: preview[n.node_id] } : n)),
    [graph.nodes, preview],
  );
  const byId = new Map(shown.map((n) => [n.node_id, n]));
  const stateById = new Map(records.map((r) => [r.node_id, r.state]));
  function startPoint(node: WorkflowNode, port: string) {
    return {
      x: node.position.x + 210,
      y: node.position.y + 65 + Math.max(0, Object.keys(node.output_ports).indexOf(port)) * 20,
    };
  }
  function curve(a: { x: number; y: number }, b: { x: number; y: number }) {
    const d = Math.max(60, Math.abs(b.x - a.x) / 2);
    return `M${a.x},${a.y} C${a.x + d},${a.y} ${b.x - d},${b.y} ${b.x},${b.y}`;
  }
  return (
    <div className="wf-canvas-shell">
      <div className="wf-canvas-tools">
        <button
          disabled={readOnly || !graph.nodes.length}
          onClick={() => {
            const next = arrangeNodes(graph);
            onChange(next);
            setView(fitView(next.nodes, size.width, size.height));
          }}
        >
          整理节点
        </button>
        <button onClick={() => setView(fitView(graph.nodes, size.width, size.height))}>
          适应画布
        </button>
        <button
          aria-label="缩小画布"
          onClick={() => setView((v) => ({ ...v, scale: Math.max(0.2, v.scale / 1.2) }))}
        >
          −
        </button>
        <output>{Math.round(view.scale * 100)}%</output>
        <button
          aria-label="放大画布"
          onClick={() => setView((v) => ({ ...v, scale: Math.min(2.5, v.scale * 1.2) }))}
        >
          ＋
        </button>
        <button aria-pressed={grid} onClick={() => setGrid((v) => !v)}>
          网格
        </button>
        <span>
          {link
            ? '选择目标节点的输入端口；Esc 取消'
            : '拖动节点标题 · 拖动空白平移 · Shift 框选 · 滚轮缩放'}
        </span>
      </div>
      <div
        className={`wf-canvas ${grid ? 'has-grid' : ''}`}
        ref={viewport}
        tabIndex={0}
        role="application"
        aria-label="工作流画布"
        style={{
          backgroundSize: `${24 * view.scale}px ${24 * view.scale}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
        onPointerDown={(e) => {
          if (
            e.target === e.currentTarget ||
            (e.target as Element).classList.contains('wf-world') ||
            (e.target as Element).tagName === 'svg'
          )
            begin(e, e.shiftKey ? 'select' : 'pan');
          else e.stopPropagation();
        }}
        onPointerMove={move}
        onPointerUp={(e) => finish(e)}
        onPointerCancel={(e) => finish(e, true)}
        onLostPointerCapture={(e) => finish(e, true)}
        onWheel={(e) => {
          e.stopPropagation();
          const box = e.currentTarget.getBoundingClientRect();
          setView((v) => {
            const scale = Math.max(0.2, Math.min(2.5, v.scale * Math.exp(-e.deltaY * 0.0015)));
            const x = e.clientX - box.left,
              y = e.clientY - box.top;
            return {
              x: x - ((x - v.x) * scale) / v.scale,
              y: y - ((y - v.y) * scale) / v.scale,
              scale,
            };
          });
        }}
        onDragOver={(e) => {
          if (!readOnly) {
            e.preventDefault();
            e.stopPropagation();
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const type = e.dataTransfer.getData('application/studio-workflow');
          if (!readOnly && nodeTypes.some((n) => n.type === type))
            onAdd(type as NodeType, point(e.clientX, e.clientY));
        }}
      >
        <div
          className="wf-world"
          style={{ transform: `translate(${view.x}px,${view.y}px) scale(${view.scale})` }}
        >
          <svg
            className="wf-edges"
            width="20000"
            height="20000"
            viewBox="-10000 -10000 20000 20000"
            aria-label="节点连接"
          >
            <defs>
              <marker
                id="wf-arrow"
                markerWidth="8"
                markerHeight="8"
                refX="7"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L0,6 L8,3 z" />
              </marker>
            </defs>
            {graph.edges.map((edge) => {
              const a = byId.get(edge.source_node_id),
                b = byId.get(edge.target_node_id);
              if (!a || !b) return null;
              const d = curve(startPoint(a, edge.source_port), {
                x: b.position.x,
                y:
                  b.position.y +
                  65 +
                  Math.max(0, Object.keys(b.input_ports).indexOf(edge.target_port)) * 20,
              });
              return (
                <g key={edge.edge_id} className="wf-edge" data-edge-id={edge.edge_id}>
                  <path d={d} markerEnd="url(#wf-arrow)" />
                  <path
                    className="wf-edge-hit"
                    d={d}
                    role="button"
                    tabIndex={readOnly ? -1 : 0}
                    aria-label={`删除连接 ${a.title} ${edge.source_port} 到 ${b.title}`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={() => !readOnly && onRemoveEdge(edge.edge_id)}
                    onKeyDown={(e) => {
                      if (!readOnly && ['Enter', 'Delete'].includes(e.key))
                        onRemoveEdge(edge.edge_id);
                    }}
                  />
                </g>
              );
            })}
            {link && byId.has(link.node) && (
              <path
                className="wf-connecting"
                d={curve(startPoint(byId.get(link.node)!, link.port), pointer)}
              />
            )}
          </svg>
          {shown.map((node) => (
            <NodeView
              key={node.node_id}
              node={node}
              selected={selected.includes(node.node_id)}
              state={stateById.get(node.node_id)}
              readOnly={readOnly}
              onDown={(e, n) => begin(e, 'nodes', n)}
              onPort={port}
              onInspect={(id) => actions.current.onSelect([id])}
              onPortDown={beginLink}
            />
          ))}
          {selectionBox && (
            <div
              className="wf-selection-box"
              style={{
                left: selectionBox.x,
                top: selectionBox.y,
                width: selectionBox.width,
                height: selectionBox.height,
              }}
            />
          )}
        </div>
        {!graph.nodes.length && (
          <div className="wf-empty">
            从左侧添加「手动触发」和「输出结果」。
            <br />
            点击输出端口，再点击下一个节点的输入端口连接。
            <br />
            也可从模板开始；保存草稿无需先填写全部配置。
          </div>
        )}
      </div>
    </div>
  );
}
