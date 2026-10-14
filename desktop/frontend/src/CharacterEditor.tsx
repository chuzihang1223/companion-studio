import { useEffect, useState } from 'react';
import type { CharacterCard, CharacterDraft } from './api';

const blank: CharacterDraft = {
  name: '',
  identity: '',
  personality: [],
  speech_style: [],
  behavior_rules: [],
  relationship: {},
  system_prompt: '',
  welcome_message: '',
  tagline: '',
};
export function draftFromCard(card?: CharacterCard | null): CharacterDraft {
  return card
    ? {
        name: card.name,
        identity: card.profile.identity,
        personality: card.profile.personality,
        speech_style: card.profile.speech_style,
        behavior_rules: card.profile.behavior_rules,
        relationship: card.profile.relationship,
        system_prompt: card.system_prompt,
        welcome_message: card.welcome_message,
        tagline: card.tagline,
      }
    : { ...blank };
}
const lines = (value: string[]) => value.map((line) => line.trim()).filter(Boolean);

export default function CharacterEditor({
  card,
  working,
  onSave,
  onCancel,
}: {
  card?: CharacterCard | null;
  working: boolean;
  onSave: (draft: CharacterDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<CharacterDraft>(() => draftFromCard(card));
  useEffect(() => setDraft(draftFromCard(card)), [card]);
  function field(
    key: 'name' | 'identity' | 'tagline' | 'welcome_message' | 'system_prompt',
    label: string,
    max: number,
    multiline = false,
  ) {
    return (
      <label className="profile-field">
        <span>{label}</span>
        {multiline ? (
          <textarea
            rows={key === 'system_prompt' ? 5 : 3}
            maxLength={max}
            value={draft[key]}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
          />
        ) : (
          <input
            maxLength={max}
            value={draft[key]}
            required={key === 'name'}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
          />
        )}
      </label>
    );
  }
  return (
    <form
      className="character-editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          ...draft,
          name: draft.name.trim(),
          personality: lines(draft.personality),
          speech_style: lines(draft.speech_style),
          behavior_rules: lines(draft.behavior_rules),
        });
      }}
    >
      <h3>{card ? '编辑角色' : '创建自定义角色'}</h3>
      <p className="setting-note">只需填写名称即可创建。其他设置可留空，之后随时调整。</p>
      {field('name', '角色名称（必填）', 100)}
      {field('tagline', '简短介绍', 300)}
      {field('identity', '身份与背景', 4000, true)}
      {(['personality', 'speech_style', 'behavior_rules'] as const).map((key, index) => (
        <label className="profile-field" key={key}>
          <span>{['性格（每行一项）', '说话风格（每行一项）', '行为规则（每行一项）'][index]}</span>
          <textarea
            rows={3}
            maxLength={4000}
            value={draft[key].join('\n')}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value.split('\n') })}
          />
        </label>
      ))}
      <label className="profile-field">
        <span>与用户的关系</span>
        <input
          maxLength={300}
          value={draft.relationship.user_role || ''}
          onChange={(e) =>
            setDraft({
              ...draft,
              relationship: { ...draft.relationship, user_role: e.target.value },
            })
          }
        />
      </label>
      {field('welcome_message', '新对话欢迎语', 2000, true)}
      <details>
        <summary>高级角色提示词</summary>
        {field('system_prompt', '角色提示词', 16000, true)}
        <p className="setting-note">用于描述角色；本机操作权限仍由工作模式的权限设置管理。</p>
      </details>
      <div className="character-actions">
        <button
          className="character-primary"
          type="submit"
          disabled={working || !draft.name.trim()}
        >
          保存并使用
        </button>
        <button type="button" disabled={working} onClick={onCancel}>
          取消
        </button>
      </div>
    </form>
  );
}
