import type { Saved } from './types';
import { labels, shownTime } from './types';

export default function ScheduleList({
  items,
  busy,
  edit,
  select,
  state,
  run,
}: {
  items: Saved[];
  busy: boolean;
  edit: (row: Saved) => void;
  select: (id: string) => void;
  state: (row: Saved, value: 'ENABLED' | 'PAUSED' | 'DISABLED' | 'ARCHIVED') => void;
  run: (row: Saved) => void;
}) {
  return (
    <section className="scheduler-card">
      <h3>任务列表</h3>
      {!items.length && <p>没有默认计划。点击“创建定时任务”添加。</p>}
      {items.map((row) => (
        <article key={row.definition.schedule_id} data-schedule-id={row.definition.schedule_id}>
          <h4>
            {row.definition.name} · {labels[row.state]}
          </h4>
          <p>
            {labels[row.definition.trigger_type]} · 下次 {shownTime(row.next_due)} · 固定版本{' '}
            {row.definition.workflow_version_id.slice(0, 8)}
          </p>
          <small>
            {row.snapshot.may_use_models ? '包含模型，需本次确认' : '按当前权限检查'} ·
            不授予未来工具权限
          </small>
          <div className="scheduler-actions">
            <button disabled={busy} onClick={() => edit(row)}>
              编辑
            </button>
            <button
              disabled={busy}
              onClick={() => state(row, row.state === 'ENABLED' ? 'PAUSED' : 'ENABLED')}
            >
              {row.state === 'ENABLED' ? '暂停计划' : '启用计划'}
            </button>
            <button disabled={busy} onClick={() => state(row, 'DISABLED')}>
              禁用
            </button>
            <button disabled={busy} onClick={() => run(row)}>
              立即运行一次
            </button>
            <button onClick={() => select(row.definition.schedule_id)}>查看历史</button>
            <button disabled={busy} onClick={() => state(row, 'ARCHIVED')}>
              删除计划
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}
