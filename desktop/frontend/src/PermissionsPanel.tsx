type Props = {
  mode: WorkPreferences['permission_mode'];
  disabled: boolean;
  onChange: (mode: WorkPreferences['permission_mode']) => void;
};

const descriptions: Record<WorkPreferences['permission_mode'], string> = {
  automatic:
    '自动执行普通桌面操作和工作文件创建。运行代码、删除、敏感数据外发与系统级操作仍需确认。',
  smart:
    '打开和控制应用、读取当前工作文件夹、浏览普通网页可自动执行。删除、运行代码及其他较高风险操作会先询问。',
  ask: '任何会改变电脑状态的操作都先询问。你可以仅为当前任务批准同类普通操作。',
};

export default function PermissionsPanel({ mode, disabled, onChange }: Props) {
  return (
    <section className="permissions-settings" aria-label="工作权限">
      <label>
        权限模式
        <select
          aria-label="权限模式"
          value={mode}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value as WorkPreferences['permission_mode'])}
        >
          <option value="automatic">自动授权</option>
          <option value="smart">智能授权（推荐）</option>
          <option value="ask">始终询问</option>
        </select>
      </label>
      <p className="setting-note">{descriptions[mode]}</p>
      <p className="setting-note">需要管理员权限时仍由 Windows 请求授权。</p>
    </section>
  );
}
