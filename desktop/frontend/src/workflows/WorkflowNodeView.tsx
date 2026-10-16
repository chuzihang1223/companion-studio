import { memo, type PointerEvent as ReactPointer } from 'react';
import type { WorkflowNode } from './types';
import { nodeTypes, stateLabels } from './model';

const NodeView = memo(
  function NodeView({
    node,
    selected,
    state,
    readOnly,
    onDown,
    onPort,
    onInspect,
    onPortDown,
  }: {
    node: WorkflowNode;
    selected: boolean;
    state?: string;
    readOnly: boolean;
    onDown: (event: ReactPointer, node: WorkflowNode) => void;
    onPort: (node: WorkflowNode, port: string, output: boolean) => void;
    onInspect: (id: string) => void;
    onPortDown: (event: ReactPointer, node: WorkflowNode, port: string) => void;
  }) {
    return (
      <article
        className={`wf-node ${selected ? 'is-selected' : ''}`}
        data-node-id={node.node_id}
        data-state={state || 'DRAFT'}
        style={{ left: node.position.x, top: node.position.y }}
      >
        <button
          type="button"
          className="wf-node-title"
          onPointerDown={(e) => onDown(e, node)}
          onClick={(e) => {
            if (e.detail === 0) onInspect(node.node_id);
          }}
          title={node.title}
        >
          {node.title}
        </button>
        <small>{nodeTypes.find((t) => t.type === node.type)?.title}</small>
        <span className="wf-node-state">{state ? stateLabels[state] || state : '草稿'}</span>
        {node.type !== 'manual_trigger' &&
          Object.keys(node.input_ports).map((port, i) => (
            <button
              type="button"
              key={port}
              className="wf-port is-input"
              style={{ top: 54 + i * 20 }}
              data-input-node={node.node_id}
              data-port={port}
              aria-label={`${node.title} 输入 ${port}`}
              disabled={readOnly}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onPort(node, port, false)}
            >
              {port}
            </button>
          ))}
        {node.type !== 'output' &&
          Object.keys(node.output_ports).map((port, i) => (
            <button
              type="button"
              key={port}
              className="wf-port is-output"
              style={{ top: 54 + i * 20 }}
              data-output-node={node.node_id}
              data-port={port}
              aria-label={`${node.title} 输出 ${port}`}
              disabled={readOnly}
              onPointerDown={(e) => onPortDown(e, node, port)}
              onClick={(e) => {
                if (e.detail === 0) onPort(node, port, true);
              }}
            >
              {port}
            </button>
          ))}
      </article>
    );
  },
  (a, b) =>
    a.node === b.node &&
    a.selected === b.selected &&
    a.state === b.state &&
    a.readOnly === b.readOnly,
);

export default NodeView;
