import { useEffect, useState } from 'react';
import { api, type Skill } from './types';

const sample =
  '---\nname: my-skill\ndescription: 描述任务用途与适用场景\nmetadata:\n  version: "1.0.0"\n---\n# 任务说明\n先读取用户指定的资料，说明结果与局限。所有操作遵守当前工具权限。\n';

export default function SkillsPanel() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Skill | null>(null);
  const [imported, setImported] = useState<Skill | null>(null);
  const [name, setName] = useState('my-skill');
  const [text, setText] = useState(sample);
  const [editor, setEditor] = useState(false);
  const [file, setFile] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [page, setPage] = useState(0);
  useEffect(() => {
    let active = true;
    void api()
      .readExtensions('skills')
      .then((value) => {
        if (active) setSkills(value.items);
      })
      .catch((e) => {
        if (active) setNotice(String(e));
      });
    return () => {
      active = false;
    };
  }, []);
  async function run(operation: () => Promise<unknown>, message = '操作已完成') {
    if (busy) return;
    setBusy(true);
    setNotice('');
    try {
      await operation();
      setSkills((await api().readExtensions('skills')).items);
      setNotice(message);
    } catch (e) {
      setNotice(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function choose(skill: Skill) {
    setSelected(await api().readExtensions('skill', { skill_id: skill.id }));
    setFile('');
    setEditor(false);
  }
  return (
    <div className="extension-manager">
      <p>
        导入 Agent Skills 标准目录；不支持压缩包。不会执行 scripts。allowed-tools
        只是依赖提示，不是权限。
      </p>
      <div className="extension-actions">
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const value = (await api().controlExtensions('skill-select-folder')) as Skill | null;
              setImported(value);
            }, '请审阅内容和文件清单后导入')
          }
        >
          导入 Skill 文件夹
        </button>
        <button
          disabled={busy}
          onClick={() => {
            setSelected(null);
            setImported(null);
            setName('my-skill');
            setText(sample);
            setEditor(true);
          }}
        >
          创建 Skill
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => setSkills((await api().readExtensions('skills')).items))
          }
        >
          刷新 Skills
        </button>
      </div>
      <p role="status">{notice}</p>
      <details>
        <summary>随附安全示例（MIT）</summary>
        {['project-review', 'markdown-report', 'workspace-search'].map((example) => (
          <button
            key={example}
            disabled={busy}
            onClick={() =>
              void run(
                async () =>
                  setImported(
                    (await api().controlExtensions('skill-example', { name: example })) as Skill,
                  ),
                '请审阅示例后导入；不会自动启用',
              )
            }
          >
            {example}
          </button>
        ))}
      </details>
      {imported && (
        <article className="extension-card" aria-label="Skill 导入预览">
          <h3>{imported.name}</h3>
          <p>{imported.description}</p>
          <p>来源：本地导入 · 信任：未信任 · 导入后默认禁用</p>
          <pre>{imported.instructions}</pre>
          <details>
            <summary>文件清单与摘要</summary>
            <pre>{JSON.stringify(imported.files, null, 2)}</pre>
          </details>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const item = (await api().controlExtensions('skill-import', {
                  import_token: imported.import_token,
                })) as Skill;
                setImported(null);
                await choose(item);
              }, 'Skill 已导入，尚未启用')
            }
          >
            确认导入
          </button>
          {selected && (
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api().controlExtensions('skill-update', {
                    import_token: imported.import_token,
                    skill_id: selected.id,
                  });
                  setImported(null);
                  setSelected(null);
                }, '快照已更新，需要重新启用')
              }
            >
              更新当前同名 Skill
            </button>
          )}
          <button disabled={busy} onClick={() => setImported(null)}>
            取消导入
          </button>
        </article>
      )}
      <div className="extension-list" aria-label="Skills 列表">
        {skills.slice(page * 20, (page + 1) * 20).map((skill) => (
          <article key={skill.id} className="extension-card">
            <button
              disabled={busy}
              onClick={() => void run(() => choose(skill), '已加载所选 Skill 说明')}
            >
              {skill.name}
            </button>
            <span>
              {' '}
              · {skill.version} · {skill.source} · {skill.trusted_state}
            </span>
            <p>{skill.description}</p>
            <label>
              <input
                type="checkbox"
                checked={skill.enabled}
                disabled={busy}
                onChange={(e) =>
                  void run(
                    async () => {
                      await api().controlExtensions('skill-toggle', {
                        skill_id: skill.id,
                        enabled: e.target.checked,
                      });
                      if (selected?.id === skill.id) await choose(skill);
                    },
                    e.target.checked ? 'Skill 已启用，仅匹配任务时加载' : 'Skill 已禁用',
                  )
                }
              />
              启用
            </label>
          </article>
        ))}
        {!skills.length && (
          <p>尚未导入 Skill。可以创建自己的任务说明，或导入含 SKILL.md 的文件夹。</p>
        )}
      </div>
      {skills.length > 20 && (
        <div className="extension-actions">
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>
            上一页
          </button>
          <span>
            {page + 1} / {Math.ceil(skills.length / 20)}
          </span>
          <button disabled={(page + 1) * 20 >= skills.length} onClick={() => setPage(page + 1)}>
            下一页
          </button>
        </div>
      )}
      {selected && (
        <article className="extension-card" aria-label="Skill 详情">
          <h3>{selected.name}</h3>
          <p>
            版本 {selected.version} · 来源 {selected.source} · {selected.trusted_state}
          </p>
          <p>所需工具：{selected.required_tools?.join(', ') || '未声明'}</p>
          <p>所需能力：{selected.required_capabilities?.join(', ') || '未声明'}</p>
          <p>兼容性：{selected.compatibility || '未声明'}</p>
          <pre>{selected.instructions}</pre>
          <details>
            <summary>文件（仅查看，不能执行）</summary>
            {selected.files.map((f) => (
              <div key={f.path}>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () =>
                      setFile(
                        (
                          await api().readExtensions('skill-file', {
                            skill_id: selected.id,
                            file: f.path,
                          })
                        ).text,
                      ),
                    )
                  }
                >
                  {f.path}
                </button>
                <small> {f.size} 字节</small>
              </div>
            ))}
          </details>
          {file && <pre aria-label="Skill 文件内容">{file}</pre>}
          <div className="extension-actions">
            {selected.source === 'self-authored' && (
              <>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      setText(
                        (
                          await api().readExtensions('skill-file', {
                            skill_id: selected.id,
                            file: 'SKILL.md',
                          })
                        ).text,
                      );
                      setName(selected.name);
                      setEditor(true);
                    })
                  }
                >
                  编辑自建 Skill
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () => api().controlExtensions('skill-export', { skill_id: selected.id }),
                      '已导出到本应用 Exports/Skills',
                    )
                  }
                >
                  导出自建 Skill
                </button>
              </>
            )}
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api().controlExtensions('skill-remove', { skill_id: selected.id });
                  setSelected(null);
                }, '已删除导入记录，原文件夹保留')
              }
            >
              删除记录
            </button>
          </div>
        </article>
      )}
      {editor && (
        <fieldset disabled={busy}>
          <legend>{selected ? '编辑自建 Skill' : '创建 Skill'}</legend>
          <label>
            目录名称
            <input
              aria-label="Skill 名称"
              value={name}
              maxLength={64}
              disabled={!!selected}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            SKILL.md
            <textarea
              aria-label="SKILL.md 编辑器"
              className="skill-editor"
              value={text}
              maxLength={32000}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <button
            onClick={() =>
              void run(async () => {
                const value = (await api().controlExtensions(
                  selected ? 'skill-edit' : 'skill-create',
                  { name, text, skill_id: selected?.id },
                )) as Skill;
                await choose(value);
                setEditor(false);
              }, '已保存 Skill，默认禁用')
            }
          >
            保存 Skill
          </button>
          <button onClick={() => setEditor(false)}>取消编辑</button>
        </fieldset>
      )}
    </div>
  );
}
