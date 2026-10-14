import { useEffect, useRef, useState, type ReactNode } from 'react';
import AppPanel from './AppPanel';
import Icon, { type IconName } from './Icon';
import PersonalizationPanel, { type ProfileSection } from './PersonalizationPanel';
import CharacterEditor from './CharacterEditor';
import {
  activateCharacter,
  characterLibrary,
  createCustomCharacter,
  importCharacterCard,
  updateCharacter,
  type CharacterCard,
  type CharacterDraft,
} from './api';
import { PresenceIndicator, type Presence } from './PresenceService';
import logo from './assets/companion-logo.svg';
import './character-studio.css';

const categories: Array<[ProfileSection | 'library' | 'voice' | 'avatar', string, IconName]> = [
  ['library', '角色管理', 'user'],
  ['personality', '人格调整', 'conversation'],
  ['relationship', '关系与称呼', 'network'],
  ['user', '关于我', 'user'],
  ['style', '回复风格', 'edit'],
  ['memory', '记忆', 'package'],
  ['voice', '角色声音', 'wave'],
  ['avatar', '头像', 'eye'],
  ['advanced', '高级规则', 'settings'],
];

export default function CharacterPage({
  id,
  name = '通用助手',
  identity,
  avatar,
  tagline,
  presence,
  voice,
  onAvatar,
  onBack,
  onActive,
}: {
  id: string;
  name?: string;
  identity?: string;
  avatar: string;
  tagline?: string;
  presence: Presence;
  voice: ReactNode;
  onAvatar: () => void;
  onBack: () => void;
  onActive: (card: CharacterCard) => Promise<void>;
}) {
  const [section, setSection] = useState<(typeof categories)[number][0]>('library');
  const [cards, setCards] = useState<CharacterCard[]>([]);
  const [selectedId, setSelectedId] = useState(id);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState('');
  const [base, setBase] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const current = cards.find((card) => card.id === selectedId);
  async function refresh(select?: string) {
    const library = await characterLibrary();
    setCards(library.characters);
    if (select) setSelectedId(select);
  }
  useEffect(() => {
    let live = true;
    void characterLibrary()
      .then((data) => {
        if (live) {
          setCards(data.characters);
          setSelectedId(data.active_id);
        }
      })
      .catch((e) => {
        if (live) setNotice(`加载角色失败：${String(e)}`);
      });
    void window.desktopAPI?.getBackendUrl().then((url) => {
      if (live) setBase(url);
    });
    return () => {
      live = false;
    };
  }, []);
  async function run(action: () => Promise<CharacterCard>, success: string) {
    if (working) return;
    setWorking(true);
    setNotice('');
    try {
      const saved = await action();
      const active = await activateCharacter(saved.id);
      await onActive(active);
      await refresh(active.id);
      setEditing(false);
      setCreating(false);
      setNotice(success);
    } catch (e) {
      setNotice(`操作失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setWorking(false);
    }
  }
  function save(draft: CharacterDraft) {
    void run(
      () =>
        creating || !current ? createCustomCharacter(draft) : updateCharacter(current.id, draft),
      '角色已保存并启用',
    );
  }
  const category = categories.find(([key]) => key === section)!;
  const previewAvatar = current?.avatar_url ? base + current.avatar_url : logo;
  return (
    <section className="app-page character-page" aria-label="角色管理">
      <div className="page-heading">
        <button type="button" className="page-back" onClick={onBack}>
          <Icon name="back" />
          返回
        </button>
        <div>
          <h1>角色与个性化</h1>
          <p>导入角色卡、创建自己的角色，或直接使用通用助手</p>
        </div>
      </div>
      <div className="character-page-header">
        <img src={avatar} alt={name} />
        <div>
          <h2>
            {name} <PresenceIndicator presence={presence} />
          </h2>
          <p>{tagline || '当前使用的角色'}</p>
          <small>{id === 'assistant' ? '通用助手 · 无预设角色' : '用户配置的角色'}</small>
        </div>
      </div>
      <div className="settings-layout">
        <nav className="settings-navigation" aria-label="角色管理分类">
          {categories.map(([key, title, icon]) => (
            <button
              type="button"
              className={section === key ? 'active' : ''}
              aria-current={section === key ? 'page' : undefined}
              key={key}
              onClick={() => setSection(key)}
            >
              <Icon name={icon} />
              {title}
            </button>
          ))}
        </nav>
        <div className="settings-content">
          <AppPanel title={category[1]} icon={category[2]}>
            {section === 'library' ? (
              <div className="character-library">
                <div className="character-actions">
                  <button
                    type="button"
                    disabled={working}
                    onClick={() => inputRef.current?.click()}
                  >
                    <Icon name="folder" />
                    导入角色卡文件
                  </button>
                  <button
                    type="button"
                    disabled={working}
                    onClick={() => {
                      setCreating(true);
                      setEditing(true);
                      setNotice('');
                    }}
                  >
                    <Icon name="plus" />
                    创建自定义角色
                  </button>
                </div>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".json,.png"
                  hidden
                  aria-label="选择角色卡文件"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    if (file) void run(() => importCharacterCard(file), '角色卡已导入并启用');
                  }}
                />
                <p className="setting-note">
                  支持 JSON 角色配置，以及带角色卡信息的 Tavern V2 / V3 JSON 或
                  PNG。普通图片请在「头像」中选择。
                </p>
                <div className="character-library-list" role="list" aria-label="已保存角色">
                  {cards.map((card) => (
                    <button
                      type="button"
                      role="listitem"
                      key={card.id}
                      className={selectedId === card.id ? 'selected' : ''}
                      disabled={working}
                      onClick={() => {
                        setSelectedId(card.id);
                        setEditing(false);
                        setCreating(false);
                      }}
                    >
                      <img src={card.avatar_url ? base + card.avatar_url : logo} alt="" />
                      <span>
                        <strong>{card.name}</strong>
                        <small>
                          {card.id === 'assistant'
                            ? '无角色卡 · 通用助手'
                            : card.tagline || '自定义角色'}
                        </small>
                      </span>
                      {card.id === id && <small className="character-active">使用中</small>}
                    </button>
                  ))}
                </div>
                {editing ? (
                  <CharacterEditor
                    key={creating ? 'new' : current?.id}
                    card={creating ? null : current}
                    working={working}
                    onSave={save}
                    onCancel={() => {
                      setEditing(false);
                      setCreating(false);
                    }}
                  />
                ) : (
                  current && (
                    <div className="character-card-detail">
                      <img src={previewAvatar} alt="" />
                      <div>
                        <h3>{current.name}</h3>
                        <p>{current.profile.identity || '没有预设身份或背景。'}</p>
                        <p className="setting-note">
                          {current.tagline ||
                            (current.id === 'assistant'
                              ? '不需要角色卡也可以直接聊天和工作。'
                              : '角色设置由你管理。')}
                        </p>
                        {current.import_warnings?.map((note, index) => (
                          <p className="setting-note" key={index}>
                            {note}
                          </p>
                        ))}
                        <div className="character-actions">
                          <button
                            type="button"
                            className="character-primary"
                            disabled={working || current.id === id}
                            onClick={() =>
                              void run(async () => current, '已切换角色；历史记录按角色分别保留')
                            }
                          >
                            {current.id === id ? '当前角色' : '使用此角色'}
                          </button>
                          {current.id !== 'assistant' && (
                            <button
                              type="button"
                              disabled={working}
                              onClick={() => {
                                setCreating(false);
                                setEditing(true);
                              }}
                            >
                              编辑角色
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                )}
                {notice && (
                  <p className="setting-note" role="status">
                    {notice}
                  </p>
                )}
              </div>
            ) : section === 'voice' ? (
              voice
            ) : section === 'avatar' ? (
              <>
                <img className="profile-avatar-preview" src={avatar} alt="当前头像" />
                <button type="button" onClick={onAvatar}>
                  选择自定义头像
                </button>
                <p className="setting-note">
                  新安装只使用应用图标；头像仅由你自行选择或导入角色卡提供。
                </p>
              </>
            ) : (
              <PersonalizationPanel
                key={id}
                name={name}
                identity={identity}
                characterId={id}
                tagline={tagline}
                section={section}
                avatarSrc={avatar}
              />
            )}
          </AppPanel>
        </div>
      </div>
    </section>
  );
}
