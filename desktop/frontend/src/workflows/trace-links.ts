import type { TraceEvent } from '../trace/types';

/** Ready events use the task span: never expand from that root into sibling nodes. */
export function nodeTraceEvents(events: TraceEvent[], executionId?: string): TraceEvent[] {
  if (!executionId) return events;
  const spans = new Set(
    events
      .filter(
        (e) =>
          e.span?.kind === 'workflow_node' && e.span.metadata.node_execution_id === executionId,
      )
      .map((e) => e.span_id!),
  );
  let changed = true;
  while (changed) {
    changed = false;
    for (const event of events)
      if (
        event.span?.parent_span_id &&
        spans.has(event.span.parent_span_id) &&
        !spans.has(event.span.span_id)
      ) {
        spans.add(event.span.span_id);
        changed = true;
      }
  }
  return events.filter(
    (e) =>
      e.metadata.node_execution_id === executionId ||
      e.span?.metadata.node_execution_id === executionId ||
      (e.span_id !== null && spans.has(e.span_id)),
  );
}
