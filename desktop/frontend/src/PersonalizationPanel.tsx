import { useEffect, useState } from 'react';
import {
  changeManualMemory,
  createManualMemory,
  loadPersonalization,
  removeManualMemory,
  resetPersonalization,
  savePersonalization,
  type PersonalizationData,
  type PersonalizationSettings,
} from './api';
import logo from './assets/companion-logo.svg';

export type ProfileSection =
  | 'character'
  | 'relationship'
  | 'memory'
  | 'personalization'
  | 'personality'
  | 'user'
  | 'style'
  | 'advanced';
type Tab = ProfileSection;
const tabs: Array<{ key: Tab; title: string }> = [
  { key: 'character', title: '角色' },
  { key: 'relationship', title: '关系' },
  { key: 'memory', title: '记忆' },
  { key: 'personalization', title: '个性化' },
];
const errorText = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

export default function PersonalizationPanel({
  characterId,
  name = '通用助手',
  identity,
  tagline,
  section,
  avatarSrc = logo,
}: {
  characterId: string;
  name?: string;
  identity?: string;
  tagline?: string;
  section?: ProfileSection;
  avatarSrc?: string;
}) {
  const [localTab, setTab] = useState<Tab>('character');
  const tab = section || localTab;
  const [data, setData] = useState<PersonalizationData | null>(null);
  const [draft, setDraft] = useState<PersonalizationSettings | null>(null);
  const [newMemory, setNewMemory] = useState('');
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState(false);

  async function reload() {
    const fresh = await loadPersonalization(characterId);
    setData(fresh);
    setDraft(fresh.settings);
  }
  useEffect(() => {
    void reload().catch((cause) => setNotice(`加载个性化失败：${errorText(cause)}`));
  }, [characterId]);
  function change(key: keyof PersonalizationSettings, value: string) {
    setDraft((old) => old && { ...old, [key]: value });
    setNotice('尚未保存');
  }
  async function run(action: () => Promise<void>, success: string) {
    if (working) return;
    setWorking(true);
    setNotice('');
    try {
      await action();
      await reload();
      setNotice(success);
    } catch (cause) {
      setNotice(`操作失败：${errorText(cause)}`);
    } finally {
      setWorking(false);
    }
  }
  const field = (
    key: keyof PersonalizationSettings,
    label: string,
    hint: string,
    multiline = false,
  ) => (
    <label className="profile-field" key={key}>
      <span>{label}</span>
      {multiline ? (
        <textarea
          value={draft?.[key] ?? ''}
          onChange={(event) => change(key, event.target.value)}
          placeholder={hint}
          maxLength={1000}
          rows={3}
        />
      ) : (
        <input
          value={draft?.[key] ?? ''}
          onChange={(event) => change(key, event.target.value)}
          placeholder={hint}
          maxLength={300}
        />
      )}
    </label>
  );

  return (
    <div className="personalization-panel">
      {!section && (
        <nav className="profile-tabs" aria-label="角色档案分页">
          {tabs.map((item) => (
            <button
              type="button"
              key={item.key}
              className={tab === item.key ? 'selected' : ''}
              onClick={() => setTab(item.key)}
            >
              {item.title}
            </button>
          ))}
        </nav>
      )}
      {!draft && <p className="setting-note">正在加载…</p>}
      {draft && tab === 'character' && (
        <div className="profile-card character-detail">
          <img src={avatarSrc} alt={name} />
          <h3>{name}</h3>
          <p>{tagline}</p>
          <p>{identity}</p>
          <p>在「角色管理」中编辑角色设定；下面的个性化设置按角色分别保存。</p>
        </div>
      )}
      {draft && tab === 'relationship' && (
        <div className="profile-card">
          {field('relationship', `我与${name}的关系`, '例如：朋友、学习搭档；可留空', true)}
          {field('appellation', '角色怎么称呼我', '例如：名字、昵称；可留空')}
          <label className="profile-field">
            <span>亲密程度</span>
            <select
              value={draft.intimacy}
              onChange={(event) => change('intimacy', event.target.value)}
            >
              <option value="natural">自然</option>
              <option value="close">亲密</option>
              <option value="very_close">非常亲密</option>
            </select>
          </label>
        </div>
      )}
      {draft && tab === 'memory' && (
        <div className="profile-card">
          <h3>我希望角色记住的事</h3>
          <p className="setting-note">
            手动记忆优先于聊天中提取的信息。停用或删除后，下一条消息就不再使用。
          </p>
          <div className="memory-add">
            <input
              aria-label="新增记忆"
              value={newMemory}
              onChange={(event) => setNewMemory(event.target.value)}
              placeholder="例如：我最近正在准备面试"
              maxLength={500}
            />
            <button
              type="button"
              disabled={working || !newMemory.trim()}
              onClick={() =>
                void run(async () => {
                  await createManualMemory(characterId, newMemory.trim());
                  setNewMemory('');
                }, '已添加记忆')
              }
            >
              添加
            </button>
          </div>
          {data?.manual_memories.map((memory) => (
            <div className="memory-row" key={memory.id}>
              <label>
                <input
                  type="checkbox"
                  checked={memory.enabled}
                  disabled={working}
                  onChange={(event) =>
                    void run(
                      () =>
                        changeManualMemory(characterId, memory.id, {
                          enabled: event.target.checked,
                        }).then(() => {}),
                      '已更新记忆',
                    )
                  }
                />
                启用
              </label>
              <span>{memory.content}</span>
              <button
                type="button"
                disabled={working}
                onClick={() => {
                  const content = window.prompt('修改这条记忆', memory.content)?.trim();
                  if (content && content !== memory.content)
                    void run(
                      () => changeManualMemory(characterId, memory.id, { content }).then(() => {}),
                      '已修改记忆',
                    );
                }}
              >
                编辑
              </button>
              <button
                type="button"
                disabled={working}
                onClick={() => {
                  if (window.confirm('删除这条手动记忆？'))
                    void run(() => removeManualMemory(characterId, memory.id), '已删除记忆');
                }}
              >
                删除
              </button>
            </div>
          ))}
          {!data?.manual_memories.length && <p className="setting-note">还没有手动记忆。</p>}
          <h3>聊天中记住的事</h3>
          <p className="setting-note">以下内容来自你曾说过的话，由系统自动提取，可能随聊天变化。</p>
          {data?.conversation_memories
            .filter((item) => item.startsWith('用户曾说'))
            .map((item, index) => (
              <p className="setting-note" key={index}>
                {item}
              </p>
            ))}
        </div>
      )}
      {draft && ['personalization', 'personality'].includes(tab) && (
        <div className="profile-card">
          {field('personality', `${name}的个性调整`, '例如：更爱开玩笑，晚上说话柔一点', true)}
        </div>
      )}
      {draft && ['personalization', 'user'].includes(tab) && (
        <div className="profile-card">
          {field('user_name', '我的名字', '可选')}
          {field('user_nickname', '我的昵称', '可选')}
          {field('user_status', '工作或学习', '例如：正在准备面试')}
          {field('user_interests', '兴趣', '例如：画画、旅行')}
          {field('user_games', '喜欢的游戏', '可选')}
          {field('user_foods', '喜欢的食物', '可选')}
          {field('user_dislikes', '不喜欢的事', '可选')}
          {field('user_notes', '关于我的其他信息', '希望角色知道的背景', true)}
        </div>
      )}
      {draft && ['personalization', 'style'].includes(tab) && (
        <div className="profile-card">
          <label className="profile-field">
            <span>回复风格</span>
            <select
              value={draft.reply_style}
              onChange={(event) => change('reply_style', event.target.value)}
            >
              <option value="short">简短</option>
              <option value="natural">自然</option>
              <option value="detailed">详细</option>
            </select>
          </label>
          <p className="setting-note">
            预览：简短偏一两句，自然偏日常一小段，详细在你需要时展开。保存不会发送模型请求。
          </p>
        </div>
      )}
      {draft && ['personalization', 'advanced'].includes(tab) && (
        <div className="profile-card">
          {field('custom_rules', '自定义规则', '例如：少用反问句', true)}
        </div>
      )}
      {draft && tab !== 'character' && (
        <div className="profile-actions">
          <button
            type="button"
            disabled={working}
            onClick={() =>
              void run(() => savePersonalization(characterId, draft), '已保存，下一条消息立即生效')
            }
          >
            保存个性化设置
          </button>
          <button
            type="button"
            disabled={working}
            onClick={() => {
              if (
                window.confirm('恢复默认个性化设置？') &&
                window.confirm('再次确认：这将清除自定义设置与手动记忆，聊天记录仍会保留。')
              )
                void run(() => resetPersonalization(characterId), '已恢复默认设置');
            }}
          >
            恢复默认设置
          </button>
        </div>
      )}
      {notice && (
        <p className="setting-note" role="status">
          {notice}
        </p>
      )}
    </div>
  );
}
