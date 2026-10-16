import { memo, useCallback, useEffect, useRef, useState } from 'react';
import type { Comparison, Dataset, EvaluationResult, EvaluationRun, ModelChoice } from './types';
import { modelLabel, terminal } from './presentation';
import EvaluationMetrics from './EvaluationMetrics';
import EvaluationEvidence from './EvaluationEvidence';
import EvaluationComparison from './EvaluationComparison';
import EvaluationSetup, { initialForm } from './EvaluationSetup';
import EvaluationResults from './EvaluationResults';
import EvaluationTrend from './EvaluationTrend';
import './evaluation.css';

// All progress, paging and metrics state stays in this memoized module. Evaluation
// SSE does not invalidate the parent Workbench or its geometry on every case.
export default memo(function EvaluationDashboard({ visible }: { visible: boolean }) {
  const api = window.desktopAPI;
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [evidenceDataset, setEvidenceDataset] = useState<Dataset | null>(null);
  const [models, setModels] = useState<ModelChoice[]>([]);
  const [form, setForm] = useState(initialForm);
  const {
    datasetId,
    mode,
    role,
    caseId,
    repetitions,
    concurrency,
    timeout,
    duration,
    requestLimit,
    tokenBudget,
    consent,
  } = form;
  const [history, setHistory] = useState<EvaluationRun[]>([]);
  const [historyCursor, setHistoryCursor] = useState<number | null>(null);
  const [selected, setSelected] = useState('');
  const [run, setRun] = useState<EvaluationRun | null>(null);
  const [rows, setRows] = useState<EvaluationResult[]>([]);
  const [cursor, setCursor] = useState(0);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<EvaluationResult | null>(null);
  const [comparisonId, setComparisonId] = useState('');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = useRef(true);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  const loadHistory = useCallback(
    async (after = 0) => {
      if (!api) return;
      const page = await api.readEvaluation('runs', { cursor: after, limit: 20 });
      if (!active.current) return;
      setHistory((old) => (after ? [...old, ...page.items].slice(0, 100) : page.items));
      setHistoryCursor(page.next_cursor);
      setSelected((old) => old || page.items[0]?.run_id || '');
    },
    [api],
  );
  useEffect(() => {
    if (!api || !visible) return;
    let current = true;
    void Promise.all([
      api.readEvaluation('dataset', { dataset_id: datasetId }),
      api.readEvaluation('models'),
      loadHistory(),
    ])
      .then(([data, choices]) => {
        if (current) {
          setDataset(data);
          setModels(choices.items);
        }
      })
      .catch((e: unknown) => {
        if (current) setError(String(e));
      });
    return () => {
      current = false;
    };
  }, [api, visible, loadHistory, datasetId]);

  useEffect(() => {
    setCursor(0);
    setRows([]);
    setEvidence(null);
    setRun(null);
    setComparison(null);
    setPicked([]);
  }, [selected]);
  useEffect(() => {
    if (!api || !selected || !visible) return;
    let current = true;
    void Promise.all([
      api.readEvaluation('detail', { run_id: selected }),
      api.readEvaluation('results', { run_id: selected, cursor, limit: 20 }),
    ])
      .then(([detail, page]) => {
        if (!current) return;
        setRun(detail);
        void api
          .readEvaluation('dataset', {
            dataset_id: detail.snapshot.dataset_id as 'studio-core' | 'recovery-core',
          })
          .then((value) => {
            if (current) setEvidenceDataset(value);
          })
          .catch((e: unknown) => {
            if (current) setError(String(e));
          });
        setRows(page.items);
        setNextCursor(page.next_cursor);
        if (terminal(detail.status))
          void loadHistory().catch((e: unknown) => {
            if (current) setError(String(e));
          });
      })
      .catch((e: unknown) => {
        if (current) setError(String(e));
      });
    return () => {
      current = false;
    };
  }, [api, selected, cursor, revision, visible, loadHistory]);

  useEffect(() => {
    if (!api || !selected || !visible) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = api.onEvaluationProgress((event) => {
      if (event.run_id !== selected || timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        setRevision((v) => v + 1);
      }, 500);
    });
    void api.watchEvaluation(selected).catch((e: unknown) => setError(String(e)));
    // Bounded fallback after a lost stream; no API execution is performed by reads.
    const polling = setInterval(() => setRevision((v) => v + 1), 3000);
    return () => {
      unsubscribe();
      clearTimeout(timer);
      clearInterval(polling);
      void api.watchEvaluation(null).catch(() => {});
    };
  }, [api, selected, visible]);

  const start = async (ids?: string[]) => {
    if (!api || busy) return;
    if (
      mode === 'live_model' &&
      (!consent ||
        !window.confirm(
          `将使用所选模型，最多 ${requestLimit} 次请求；可能计费。只运行合成测试，是否开始？`,
        ))
    )
      return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const created = await api.startEvaluation({
        dataset_id:
          ids && run ? (run.snapshot.dataset_id as 'studio-core' | 'recovery-core') : datasetId,
        case_ids: ids || (caseId ? [caseId] : []),
        mode,
        model_role: role,
        repetitions,
        concurrency: ['live_model', 'windows_gui'].includes(mode) ? 1 : concurrency,
        timeout_seconds: timeout,
        max_duration_seconds: duration,
        max_requests: mode === 'live_model' ? requestLimit : 0,
        token_budget: mode === 'live_model' ? tokenBudget : 0,
        live_consent: mode === 'live_model' && consent,
      });
      if (active.current) {
        setSelected(created.run_id);
        setRevision((v) => v + 1);
        await loadHistory();
      }
    } catch (e) {
      if (active.current) setError(String(e));
    } finally {
      if (active.current) setBusy(false);
    }
  };
  const control = async (action: 'pause' | 'resume' | 'cancel') => {
    if (!api || !run) return;
    const id = run.run_id;
    try {
      const updated = await api.controlEvaluation(id, action);
      if (active.current && selectedRef.current === id) {
        setRun(updated);
        setRevision((v) => v + 1);
      }
    } catch (e) {
      if (active.current) setError(String(e));
    }
  };
  const isRunning = !!run && !terminal(run.status);
  const completed = Object.values(run?.metrics.counts || {}).reduce((sum, n) => sum + n, 0);
  if (!api) return <p>请在桌面版工作台运行评测。</p>;
  return (
    <section className="evaluation-dashboard" aria-label="Agent Evaluation">
      <h3>Agent Evaluation · 评测</h3>
      <p className="evaluation-note">
        内置合成用例 · 独立档案与 SQLite · 默认不联网。Mock 通过率不代表真实 AI 任务成功率。
      </p>
      <EvaluationSetup
        form={form}
        dataset={dataset}
        models={models}
        change={(patch) => setForm((old) => ({ ...old, ...patch }))}
      />
      <div className="evaluation-actions">
        <button
          onClick={() => void start()}
          disabled={busy || isRunning || (mode === 'live_model' && !consent)}
        >
          开始评测
        </button>
        <button disabled={busy || isRunning || !picked.length} onClick={() => void start(picked)}>
          重跑选中项（{picked.length}）
        </button>
        <button
          disabled={!isRunning}
          onClick={() => void control(run?.status === 'PAUSED' ? 'resume' : 'pause')}
        >
          {run?.status === 'PAUSED' ? '继续排队' : '暂停排队'}
        </button>
        <button disabled={!isRunning} onClick={() => void control('cancel')}>
          取消运行
        </button>
        <button onClick={() => setRevision((v) => v + 1)}>刷新结果</button>
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <div className="evaluation-history">
        <label>
          历史评测
          <select
            aria-label="评测历史"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">尚未选择</option>
            {history.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.created_at.slice(0, 19)} · {r.mode} · {r.status} · {r.run_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        {historyCursor != null && (
          <button
            onClick={() =>
              void loadHistory(historyCursor).catch((e: unknown) => setError(String(e)))
            }
          >
            更多历史
          </button>
        )}
      </div>
      <EvaluationTrend
        values={history
          .filter((r) => r.snapshot.dataset_id === datasetId && r.mode === mode)
          .map((r) => r.metrics.average_duration_ms)
          .filter((v): v is number => v != null)}
      />
      {!run ? (
        <p>尚无评测成绩。点击「开始评测」生成真实执行记录；此处不会填充示例分数。</p>
      ) : (
        <>
          <p role="status" className="evaluation-progress">
            {run.status} · {completed} / {run.total_cases} · {run.mode} · {modelLabel(run)} ·{' '}
            {run.snapshot.app_version}
          </p>
          <progress max={run.total_cases} value={completed} />
          <EvaluationMetrics run={run} />
          <div className="evaluation-actions">
            {(['json', 'csv', 'markdown'] as const).map((format) => (
              <button
                key={format}
                onClick={() => {
                  void api
                    .exportEvaluation(run.run_id, format)
                    .then((r) =>
                      setNotice(
                        `已导出到本实例 Exports/${r.filename}${r.partial ? '（运行中快照）' : ''}`,
                      ),
                    )
                    .catch((e: unknown) => setError(String(e)));
                }}
              >
                导出 {format.toUpperCase()}
              </button>
            ))}
            <button
              disabled={isRunning}
              onClick={() => {
                if (window.confirm('删除这次评测及其隔离 Trace？正式聊天不会改变。'))
                  void api
                    .deleteEvaluation(run.run_id)
                    .then(() => {
                      setSelected('');
                      setRun(null);
                      return loadHistory();
                    })
                    .catch((e: unknown) => setError(String(e)));
              }}
            >
              删除评测历史
            </button>
          </div>
          <EvaluationResults
            rows={rows}
            dataset={dataset}
            run={run}
            picked={picked}
            setPicked={setPicked}
            onEvidence={setEvidence}
            cursor={cursor}
            nextCursor={nextCursor}
            onCursor={setCursor}
          />
          {evidence && (
            <EvaluationEvidence
              key={evidence.attempt_id}
              row={evidence}
              visible={visible}
              caseInfo={evidenceDataset?.cases?.find((c) => c.case_id === evidence.case_id)}
            />
          )}
          <div className="evaluation-actions">
            <label>
              对比历史
              <select
                aria-label="对比评测"
                value={comparisonId}
                onChange={(e) => setComparisonId(e.target.value)}
              >
                <option value="">选择另一次运行</option>
                {history
                  .filter((r) => r.run_id !== selected)
                  .map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {r.run_id.slice(0, 8)} · {r.mode} · {modelLabel(r)}
                    </option>
                  ))}
              </select>
            </label>
            <button
              disabled={!comparisonId}
              onClick={() => {
                void api
                  .readEvaluation('compare', { left: selected, right: comparisonId })
                  .then((result) => {
                    if (active.current && selectedRef.current === selected) setComparison(result);
                  })
                  .catch((e: unknown) => setError(String(e)));
              }}
            >
              比较条件与结果
            </button>
          </div>
          {comparison && <EvaluationComparison comparison={comparison} />}
        </>
      )}
    </section>
  );
});
