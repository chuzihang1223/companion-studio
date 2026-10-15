import { useEffect, useState } from 'react';
import PermissionsPanel from './PermissionsPanel';
export default function PermissionSettings() {
  const [prefs, setPrefs] = useState<WorkPreferences | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    void window.desktopAPI
      ?.workPreferences()
      .then(setPrefs)
      .catch((e) => setError(String(e)));
  }, []);
  return (
    <>
      {prefs && (
        <PermissionsPanel
          disabled={false}
          mode={prefs.permission_mode || 'smart'}
          onChange={(mode) =>
            void window.desktopAPI
              ?.workPreferences({ permission_mode: mode })
              .then(setPrefs)
              .catch((e) => setError(String(e)))
          }
        />
      )}
      <p className="setting-note">
        读取及普通桌面操作遵循当前权限模式。运行代码、删除文件等高风险操作仍需确认。
      </p>
      {error && <p role="alert">{error}</p>}
    </>
  );
}
