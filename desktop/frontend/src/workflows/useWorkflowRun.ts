import { useCallback, useEffect, useRef, useState } from 'react';
import type { WorkflowRun, WorkflowEvent, ResumePreview } from './types';

export function useWorkflowRun(
  owner: string | null,
  visible: boolean,
  onError: (error: string) => void,
) {
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [history, setHistory] = useState<WorkflowRun[]>([]);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [preview, setPreview] = useState<ResumePreview | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const latest = useRef({ owner, run, visible, onError });
  latest.current = { owner, run, visible, onError };
  const generation = useRef(0);
  const cursor = useRef(0);
  const refreshing = useRef(false);
  const again = useRef(false);
  const read = window.desktopAPI?.readWorkflows;
  const activeRunId = run?.run_id;
  const reloadHistory = useCallback(async () => {
    if (!owner || !read) {
      setHistory([]);
      return;
    }
    const result = await read('runs', { conversation_id: owner });
    if (latest.current.owner === owner) setHistory(result.items);
  }, [owner, read]);
  const refresh = useCallback(async () => {
    if (refreshing.current) {
      again.current = true;
      return;
    }
    refreshing.current = true;
    const current = latest.current,
      stamp = generation.current;
    try {
      if (!current.owner || !current.run || !read) return;
      const query = { conversation_id: current.owner, run_id: current.run.run_id };
      const [data, messages] = await Promise.all([
        read('run', query),
        read('events', { ...query, after: cursor.current }),
      ]);
      if (generation.current !== stamp || latest.current.run?.run_id !== current.run.run_id) return;
      setRun(data);
      if (messages.items.length) {
        cursor.current = messages.items.at(-1)!.cursor;
        setEvents((old) =>
          [
            ...old,
            ...messages.items.filter((item) => !old.some((e) => e.cursor === item.cursor)),
          ].slice(-500),
        );
      }
      if (['COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED', 'NEEDS_REVIEW'].includes(data.state))
        void reloadHistory();
    } catch (e) {
      if (generation.current === stamp) latest.current.onError(String(e));
    } finally {
      refreshing.current = false;
      if (again.current) {
        again.current = false;
        queueMicrotask(() => {
          void refresh();
        });
      }
    }
  }, [read, reloadHistory]);
  useEffect(() => {
    generation.current++;
    cursor.current = 0;
    setRun(null);
    setEvents([]);
    setPreview(null);
    setDisconnected(false);
    void reloadHistory().catch((e) => latest.current.onError(String(e)));
  }, [owner, reloadHistory]);
  useEffect(() => {
    const api = window.desktopAPI;
    if (!api || !owner || !activeRunId || !visible) return;
    const id = activeRunId;
    const off = api.onWorkflowProgress((event) => {
      if (
        event.conversation_id !== latest.current.owner ||
        event.run_id !== latest.current.run?.run_id
      )
        return;
      if (event.disconnected) setDisconnected(true);
      // Events are node transitions, never token deltas; coalesce slow snapshots.
      void refresh();
    });
    void api
      .watchWorkflow({ run_id: id, conversation_id: owner, after: cursor.current })
      .catch((e) => latest.current.onError(String(e)));
    void refresh();
    return () => {
      off();
      void api.watchWorkflow(null);
    };
  }, [owner, activeRunId, visible, refresh]);
  async function select(id: string) {
    if (!owner || !read) return null;
    generation.current++;
    const stamp = generation.current;
    cursor.current = 0;
    setPreview(null);
    setEvents([]);
    setDisconnected(false);
    const data = await read('run', { run_id: id, conversation_id: owner });
    if (latest.current.owner !== owner || generation.current !== stamp) return null;
    setRun(data);
    return data;
  }
  async function action(
    operation: 'control' | 'approve' | 'resume',
    options: Record<string, unknown>,
  ) {
    if (!run || !owner || !window.desktopAPI) return;
    await window.desktopAPI.controlWorkflows(operation, {
      run_id: run.run_id,
      conversation_id: owner,
      ...options,
    });
    await refresh();
  }
  async function checkRecovery() {
    if (run && owner && read)
      setPreview(await read('resume-preview', { run_id: run.run_id, conversation_id: owner }));
  }
  return {
    run,
    history,
    events,
    preview,
    disconnected,
    select,
    refresh,
    reloadHistory,
    action,
    checkRecovery,
  };
}
