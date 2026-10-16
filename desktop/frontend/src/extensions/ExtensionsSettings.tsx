import { useState } from 'react';
import MCPServersPanel from './MCPServersPanel';
import SkillsPanel from './SkillsPanel';
import './extensions.css';

export default function ExtensionsSettings() {
  const [tab, setTab] = useState('servers');
  return (
    <section className="extensions-settings" aria-label="MCP 与 Skills 管理">
      <p>按需连接工具、加载任务说明。外部程序以你的 Windows 账号运行；Skill 不授予系统权限。</p>
      <nav aria-label="扩展类别">
        <button type="button" aria-pressed={tab === 'servers'} onClick={() => setTab('servers')}>
          MCP Servers
        </button>
        <button type="button" aria-pressed={tab === 'skills'} onClick={() => setTab('skills')}>
          Agent Skills
        </button>
      </nav>
      {tab === 'servers' ? <MCPServersPanel /> : <SkillsPanel />}
    </section>
  );
}
