import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgentTrace, TraceEvent, TraceQuery, TraceStatus, TraceSummary } from './types';
import {
  eventNames,
  isFinished,
  MAX_EVENTS,
  orderedEvents,
  replayDelay,
  ROW_HEIGHT,
  traceStatuses,
  virtualWindow,
} from './replay';
import './trace.css';

import TraceDetails, { formatMs as ms } from './TraceDetails';
const date = (value: string) => new Date(value).toLocaleString();
type Props = {
  conversationId: string | null;
  sessionId: string | null;
  revision: number;
  visible: boolean;
  source?: Pick<
    NonNullable<Window['desktopAPI']>,
    'readAgentTraces' | 'exportAgentTrace' | 'deleteAgentTrace'
  >;
  readOnly?: boolean;
};

export default function TraceViewer(props: Props) {
  // Remount on work-session changes as well as conversation changes. Late IPC
  // responses belong to the retired component and cannot select another task.
  return <TraceSession key={`${props.conversationId}:${props.sessionId}`} {...props} />;
}

function TraceSession({ conversationId, sessionId, revision, visible, source, readOnly }: Props) {
  const [traces, setTraces] = useState<AgentTrace[]>([]);
  const [selected, setSelected] = useState('');
  const [eventRows, setEvents] = useState<TraceEvent[]>([]);
  const [summaryRow, setSummary] = useState<TraceSummary | null>(null);
  const events = useMemo(
    () => eventRows.filter((row) => row.trace_id === selected),
    [eventRows, selected],
  );
  const summary = summaryRow?.trace_id === selected ? summaryRow : null;
  const [status, setStatus] = useState<TraceStatus | ''>('');
  const [tool, setTool] = useState('');
  const [task, setTask] = useState('');
  const [since, setSince] = useState('');
  const [onlySession, setOnlySession] = useState(false);
  const [historyCursor, setHistoryCursor] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [integrity, setIntegrity] = useState({ pending: 0, needs_review: 0, failed: 0 });
  const [exporting, setExporting] = useState(false);
  const [exportName, setExportName] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(300);
  const [renderMs, setRenderMs] = useState(0);
  const viewport = useRef<HTMLDivElement>(null);
  const live = useRef(true);
  const pendingHistory = useRef(false);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const api = source || window.desktopAPI;
  const scope = useCallback(
    (): TraceQuery => ({
      conversation_id: conversationId || '',
      ...(onlySession && sessionId ? { work_session_id: sessionId } : {}),
    }),
    [conversationId, onlySession, sessionId],
  );
  const filters = useCallback(
    (): TraceQuery => ({
      ...scope(),
      status: status || undefined,
      tool_name: tool || undefined,
      task_id: task || undefined,
      since: since ? new Date(since).toISOString() : undefined,
    }),
    [scope, status, tool, task, since],
  );
  const filterKey = JSON.stringify(filters());
  const latestFilter = useRef(filterKey);
  latestFilter.current = filterKey;

  useEffect(() => {
    if (!visible || !conversationId || !api) return;
    let active = true;
    // Batch bursts from the shared WorkEvent SSE into one bounded history read.
    const timer = setTimeout(() => {
      void api
        .readAgentTraces('list', filters())
        .then((page) => {
          if (!active) return;
          setTraces(page.items);
          setHistoryCursor(page.next_cursor);
          setIntegrity(page.integrity || { pending: 0, needs_review: 0, failed: 0 });
          setError('');
          setSelected((old) =>
            page.items.some((row) => row.trace_id === old) ? old : page.items[0]?.trace_id || '',
          );
        })
        .catch((e: unknown) => {
          if (active) setError(String(e));
        });
    }, 150);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, conversationId, visible, filters, revision, refresh]);

  const trace = traces.find((row) => row.trace_id === selected);
  useEffect(() => {
    if (!api || !visible || !selected) return;
    let active = true;
    const load = async () => {
      const options = { ...scope(), trace_id: selected };
      const [totals, current] = await Promise.all([
        api.readAgentTraces('summary', options),
        api.readAgentTraces('detail', options),
      ]);
      const rows: TraceEvent[] = [];
      let after = 0;
      for (let page = 0; page < MAX_EVENTS / 250; page++) {
        const result = await api.readAgentTraces('events', { ...options, after, limit: 250 });
        if (!active) return;
        rows.push(...result.items);
        if (!result.has_more || result.next_cursor == null || result.next_cursor <= after) break;
        after = result.next_cursor;
      }
      if (active) {
        setEvents(orderedEvents(rows));
        setSummary(totals);
        setTraces((old) => old.map((row) => (row.trace_id === selected ? current : row)));
      }
    };
    void load().catch((e: unknown) => {
      if (active) setError(String(e));
    });
    return () => {
      active = false;
    };
  }, [api, visible, selected, scope, revision, refresh]);

  useEffect(() => {
    if (!visible || !trace || isFinished(trace.status)) return;
    const timer = setInterval(() => setRefresh((n) => n + 1), 3000);
    return () => clearInterval(timer);
  }, [visible, trace]);
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setHeight(element.clientHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, [selected]);
  useEffect(() => {
    if (!playing || !visible || index >= events.length - 1) return;
    const timer = setTimeout(
      () => setIndex((n) => Math.min(n + 1, events.length - 1)),
      replayDelay(events[index], events[index + 1], speed),
    );
    return () => clearTimeout(timer);
  }, [playing, visible, index, speed, events]);
  useEffect(() => {
    if (!playing || !viewport.current) return;
    viewport.current.scrollTop = Math.max(0, index * ROW_HEIGHT - height / 2);
  }, [index, playing, height]);
  useEffect(() => {
    const started = performance.now();
    const frame = requestAnimationFrame(() => setRenderMs(performance.now() - started));
    return () => cancelAnimationFrame(frame);
  }, [events]);

  const choose = (id: string) => {
    setPlaying(false);
    setSelected(id);
    setIndex(0);
    setEvents([]);
    setSummary(null);
    setScrollTop(0);
    if (viewport.current) viewport.current.scrollTop = 0;
  };
  const selectedEvent = events[Math.min(index, events.length - 1)];
  const range = virtualWindow(events.length, scrollTop, height);
  const exportTrace = async () => {
    if (!api || exporting || !selected) return;
    setExporting(true);
    try {
      const result = await api.exportAgentTrace({ ...scope(), trace_id: selected });
      if (live.current) setExportName(result.filename);
    } catch (error) {
      if (live.current) setError(String(error));
    } finally {
      if (live.current) setExporting(false);
    }
  };
  const moreHistory = async () => {
    if (!api || historyCursor == null || pendingHistory.current) return;
    pendingHistory.current = true;
    const oldScope = JSON.stringify(filters());
    try {
      const page = await api.readAgentTraces('list', { ...filters(), cursor: historyCursor });
      if (live.current && oldScope === latestFilter.current) {
        setTraces((old) =>
          [...new Map([...old, ...page.items].map((row) => [row.trace_id, row])).values()].slice(
            0,
            1000,
          ),
        );
        setHistoryCursor(page.next_cursor);
      }
    } catch (e) {
      if (live.current) setError(String(e));
    } finally {
      pendingHistory.current = false;
    }
  };
  if (!conversationId) return <p>开始一个工作会话后可查看执行追踪。</p>;
  if (!api) return <p>请在桌面版中查看执行追踪。</p>;
  return (
    <section className="trace-viewer" aria-label="Agent Trace 执行追踪">
      <p className="trace-note">
        只读回放 · 不会重新操作电脑或调用模型。默认保留 30 天、最多 1000 个任务；每任务最多 5000
        条事件。
      </p>
      {(integrity.pending > 0 || integrity.needs_review > 0 || integrity.failed > 0) && (
        <p role="status" className="trace-warning">
          追踪记录尚未全部写入：{integrity.pending} 条等待恢复，
          {integrity.needs_review + integrity.failed} 条需要检查。历史可能不完整。
        </p>
      )}
      {!readOnly && (
        <div className="trace-filters">
          <select
            aria-label="轨迹状态"
            value={status}
            onChange={(e) => setStatus(e.target.value as TraceStatus | '')}
          >
            <option value="">全部状态</option>
            {Object.entries(traceStatuses)
              .filter(([key]) => key !== 'denied')
              .map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
          </select>
          <input
            aria-label="工具名称"
            placeholder="工具，如 app.launch"
            value={tool}
            onChange={(e) => setTool(e.target.value)}
            maxLength={80}
          />
          <input
            aria-label="任务编号筛选"
            placeholder="任务 UUID"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            maxLength={36}
          />
          <input
            aria-label="起始时间"
            type="datetime-local"
            value={since}
            onChange={(e) => setSince(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={onlySession}
              onChange={(e) => {
                setOnlySession(e.target.checked);
                choose('');
              }}
            />
            本次工作任务
          </label>
          <button onClick={() => setRefresh((n) => n + 1)}>刷新</button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {exportName && (
        <p className="trace-note" data-trace-export={exportName}>
          已导出到本实例 Exports/{exportName}
        </p>
      )}
      <div className="trace-history">
        <select aria-label="历史执行轨迹" value={selected} onChange={(e) => choose(e.target.value)}>
          <option value="">选择任务（旧任务不会补造轨迹）</option>
          {traces.map((row) => (
            <option key={row.trace_id} value={row.trace_id}>
              {date(row.started_at)} · {traceStatuses[row.status]} · {row.task_id.slice(0, 8)}
            </option>
          ))}
        </select>
        {historyCursor != null && traces.length < 1000 && (
          <button onClick={() => void moreHistory()}>更早的任务</button>
        )}
      </div>
      {trace && (
        <>
          <div className="trace-totals">
            <strong>{traceStatuses[trace.status]}</strong>
            <span>总耗时 {ms(summary?.task_total_ms)}</span>
            <span>
              模型 {summary?.model_requests || 0} 次 / {ms(summary?.model_ms)}
            </span>
            <span>
              工具 {summary?.tools_executed || 0}/{summary?.tool_requests || 0} 次 /{' '}
              {ms(summary?.tool_ms)}
            </span>
            <span>授权 {ms(summary?.permission_wait_ms)}</span>
            <span>调度 {ms(summary?.scheduler_ms)}</span>
            <span>恢复 {ms(summary?.recovery_ms)}</span>
            <span>重试 {summary?.retries || 0}</span>
          </div>
          {(trace.incomplete || trace.event_count > events.length) && (
            <p className="trace-note">
              轨迹不完整或尚在加载（{events.length}/{trace.event_count}
              ），缺失部分不会重新执行补齐。
            </p>
          )}
          <div className="trace-player">
            <button
              disabled={!events.length}
              onClick={() => {
                setIndex(0);
                setPlaying(true);
              }}
            >
              从头回放
            </button>
            <button disabled={!events.length} onClick={() => setPlaying((v) => !v)}>
              {playing && index < events.length - 1 ? '暂停回放' : '继续回放'}
            </button>
            <select
              aria-label="回放速度"
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
            >
              {[0.5, 1, 2, 4].map((n) => (
                <option key={n} value={n}>
                  {n}×
                </option>
              ))}
            </select>
            <input
              aria-label="回放进度"
              type="range"
              min={0}
              max={Math.max(0, events.length - 1)}
              value={Math.min(index, Math.max(0, events.length - 1))}
              onChange={(e) => {
                setPlaying(false);
                setIndex(Number(e.target.value));
              }}
            />
            <small>
              {events.length ? index + 1 : 0}/{events.length} · 空闲间隔最长 2 秒
            </small>
            {!readOnly && (
              <button
                onClick={() => void exportTrace()}
                disabled={readOnly || !events.length || exporting}
              >
                导出脱敏 JSON
              </button>
            )}
          </div>
          <div className="trace-body">
            <div
              className="trace-timeline"
              ref={viewport}
              onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
              role="list"
              aria-label="执行事件时间轴"
            >
              <div style={{ height: events.length * ROW_HEIGHT, position: 'relative' }}>
                {events.slice(range.start, range.end).map((row, offset) => {
                  const at = range.start + offset;
                  return (
                    <button
                      className={`trace-row ${at === index ? 'selected' : ''}`}
                      role="listitem"
                      key={row.sequence}
                      style={{ top: at * ROW_HEIGHT, height: ROW_HEIGHT }}
                      onClick={() => {
                        setPlaying(false);
                        setIndex(at);
                      }}
                      aria-current={at === index ? 'step' : undefined}
                    >
                      <span>
                        {row.sequence}. {eventNames[row.event_type] || row.event_type}{' '}
                        {row.span?.tool_name || row.span?.model_name || ''}
                      </span>
                      <small>
                        {traceStatuses[row.status] || row.status} · {ms(row.duration_ms)} ·{' '}
                        {new Date(row.created_at).toLocaleTimeString()}
                      </small>
                    </button>
                  );
                })}
              </div>
            </div>
            <TraceDetails trace={trace} event={selectedEvent} />
          </div>
          <small className="trace-note">
            {events.length} 条事件 · 已渲染 {Math.max(0, range.end - range.start)} 行 · 更新至下一帧{' '}
            {renderMs.toFixed(1)}{' '}
            ms（含帧调度，非纯渲染耗时）。各类耗时可能嵌套，不能相加作为总耗时。
          </small>
          {!readOnly && isFinished(trace.status) && (
            <button
              className="trace-delete"
              onClick={() => {
                if (!window.confirm('删除这条执行轨迹？原聊天和任务记录会保留。')) return;
                void api
                  .deleteAgentTrace({ ...scope(), trace_id: selected })
                  .then(() => {
                    if (live.current) {
                      choose('');
                      setRefresh((n) => n + 1);
                    }
                  })
                  .catch((e: unknown) => {
                    if (live.current) setError(String(e));
                  });
              }}
            >
              删除本条轨迹
            </button>
          )}
        </>
      )}
      {!trace && !error && <p>当前筛选下没有执行轨迹。执行新工作任务后，过程会自动保存在本机。</p>}
    </section>
  );
}
