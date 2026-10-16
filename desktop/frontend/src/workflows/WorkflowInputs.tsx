import type { WorkflowDefinition } from './types';

export default function WorkflowInputs({
  graph,
  value,
  onChange,
}: {
  graph: WorkflowDefinition;
  value: string;
  onChange: (value: string) => void;
}) {
  let data: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) data = parsed;
  } catch {
    /* Advanced JSON may be temporarily incomplete. */
  }
  const properties = graph.input_schema.properties;
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return null;
  const required = new Set(
    Array.isArray(graph.input_schema.required) ? (graph.input_schema.required as string[]) : [],
  );
  function set(name: string, item: unknown) {
    const next = { ...data };
    if (item === undefined) delete next[name];
    else next[name] = item;
    onChange(JSON.stringify(next, null, 2));
  }
  return (
    <div className="wf-input-form">
      {Object.entries(properties).map(([name, raw]) => {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const schema = raw as Record<string, unknown>,
          type = schema.type;
        const file = graph.nodes.some(
          (n) => n.type === 'input' && n.config.name === name && n.config.type === 'file_reference',
        );
        const title = `${name}${required.has(name) ? ' *' : ''}`;
        if (file)
          return (
            <label key={name}>
              {title} · 项目内文件
              <input
                aria-label={`运行参数 ${name}`}
                placeholder="例如 docs/report.md（不会读取整个文件夹）"
                value={String((data[name] as { path?: string })?.path || '')}
                onChange={(e) =>
                  set(name, { kind: 'selected_workspace_file', path: e.target.value })
                }
              />
            </label>
          );
        if (Array.isArray(schema.enum))
          return (
            <label key={name}>
              {title}
              <select
                value={data[name] === undefined ? '' : JSON.stringify(data[name])}
                onChange={(e) =>
                  set(name, e.target.value === '' ? undefined : JSON.parse(e.target.value))
                }
              >
                <option value="">请选择</option>
                {schema.enum.map((item) => (
                  <option key={JSON.stringify(item)} value={JSON.stringify(item)}>
                    {String(item)}
                  </option>
                ))}
              </select>
            </label>
          );
        if (type === 'boolean')
          return (
            <label key={name}>
              {title}
              <select
                aria-label={`运行参数 ${name}`}
                value={data[name] === undefined ? '' : String(data[name])}
                onChange={(e) =>
                  set(name, e.target.value === '' ? undefined : e.target.value === 'true')
                }
              >
                <option value="">未填写</option>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
          );
        if (type === 'number' || type === 'integer')
          return (
            <label key={name}>
              {title}
              <input
                type="number"
                aria-label={`运行参数 ${name}`}
                value={typeof data[name] === 'number' ? data[name] : ''}
                onChange={(e) =>
                  set(name, e.target.value === '' ? undefined : Number(e.target.value))
                }
              />
            </label>
          );
        if (type === 'string')
          return (
            <label key={name}>
              {title}
              <input
                aria-label={`运行参数 ${name}`}
                value={typeof data[name] === 'string' ? data[name] : ''}
                maxLength={typeof schema.maxLength === 'number' ? schema.maxLength : 8000}
                onChange={(e) => set(name, e.target.value)}
              />
            </label>
          );
        return (
          <p key={name}>
            {title} · {String(type || 'JSON')}：在下方 JSON 输入中填写。
          </p>
        );
      })}
    </div>
  );
}
