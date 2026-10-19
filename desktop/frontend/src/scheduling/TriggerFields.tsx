import type { Definition, Source, Trigger, TriggerKind } from './types';
import { labels, newDefinition } from './types';
export default function TriggerFields({
  definition,
  sources,
  change,
}: {
  definition: Definition;
  sources: Source[];
  change: (value: Partial<Definition>) => void;
}) {
  const config = definition.trigger_config;
  const update = (next: Partial<Trigger>) => change({ trigger_config: { ...config, ...next } });
  return (
    <>
      <label>
        触发方式
        <select
          aria-label="触发方式"
          value={definition.trigger_type}
          onChange={(e) => {
            const kind = e.target.value as TriggerKind;
            change({
              trigger_type: kind,
              trigger_config:
                kind === 'ONCE'
                  ? newDefinition(definition.conversation_id).trigger_config
                  : kind === 'INTERVAL'
                    ? { interval_seconds: 300 }
                    : kind === 'CONDITION'
                      ? {
                          condition_type: 'FILE_CHANGE',
                          poll_interval: 30,
                          debounce: 5,
                          cooldown: 60,
                        }
                      : { time: '09:00', weekdays: [0] },
            });
          }}
        >
          {(['ONCE', 'DAILY', 'WEEKLY', 'INTERVAL', 'CONDITION'] as const).map((kind) => (
            <option key={kind} value={kind}>
              {labels[kind]}
            </option>
          ))}
        </select>
      </label>
      <label>
        时区（IANA 或受支持的 Windows 名称）
        <input
          aria-label="调度时区"
          value={definition.timezone}
          onChange={(e) => change({ timezone: e.target.value })}
        />
      </label>
      {definition.trigger_type === 'ONCE' && (
        <label>
          执行日期与时间
          <input
            aria-label="执行日期与时间"
            type="datetime-local"
            step="1"
            value={config.at || ''}
            onChange={(e) => update({ at: e.target.value })}
          />
        </label>
      )}
      {['DAILY', 'WEEKLY'].includes(definition.trigger_type) && (
        <label>
          本地时间
          <input
            type="time"
            aria-label="每天执行时间"
            value={config.time || '09:00'}
            onChange={(e) => update({ time: e.target.value })}
          />
        </label>
      )}
      {definition.trigger_type === 'WEEKLY' && (
        <fieldset>
          <legend>星期（周一至周日）</legend>
          {['一', '二', '三', '四', '五', '六', '日'].map((name, day) => (
            <label key={day}>
              <input
                type="checkbox"
                checked={(config.weekdays || []).includes(day)}
                onChange={(e) =>
                  update({
                    weekdays: e.target.checked
                      ? [...(config.weekdays || []), day]
                      : (config.weekdays || []).filter((value) => value !== day),
                  })
                }
              />
              {name}
            </label>
          ))}
        </fieldset>
      )}
      {definition.trigger_type === 'INTERVAL' && (
        <label>
          间隔分钟（至少 5）
          <input
            type="number"
            min="5"
            max="43200"
            aria-label="间隔分钟"
            value={(config.interval_seconds || 300) / 60}
            onChange={(e) => update({ interval_seconds: Number(e.target.value) * 60 })}
          />
        </label>
      )}
      {definition.trigger_type === 'CONDITION' && (
        <>
          <label>
            受控来源
            <select
              aria-label="条件来源"
              value={config.source_id || ''}
              onChange={(e) => {
                const source = sources.find((row) => row.source_id === e.target.value);
                update({ source_id: source?.source_id, condition_type: source?.condition_type });
              }}
            >
              <option value="">先添加并授权来源</option>
              {sources
                .filter((row) => row.enabled)
                .map((row) => (
                  <option key={row.source_id} value={row.source_id}>
                    {row.condition_type === 'FILE_CHANGE'
                      ? '当前选定项目文件变化'
                      : '所选工作流状态'}{' '}
                    · {row.source_id.slice(0, 8)}
                  </option>
                ))}
            </select>
          </label>
          {config.condition_type === 'WORKFLOW_STATE' && (
            <label>
              目标状态
              <select
                value={config.expected_value || 'COMPLETED'}
                onChange={(e) =>
                  update({ expected_value: e.target.value as 'COMPLETED' | 'FAILED' })
                }
              >
                <option value="COMPLETED">完成</option>
                <option value="FAILED">失败</option>
              </select>
            </label>
          )}
          <p>
            仅变化边沿触发；首次检查建立基线。文件至少稳定 5 秒，默认冷却 60 秒；来源有效 24 小时。
          </p>
        </>
      )}
    </>
  );
}
