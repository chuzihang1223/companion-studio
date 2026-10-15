import { useEffect, useState } from 'react';
import Icon from './Icon';
export default function WindowControls() {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    void window.desktopAPI?.windowState().then((s) => setMaximized(s.maximized));
    return window.desktopAPI?.onWindowState((s) => setMaximized(s.maximized));
  }, []);
  return (
    <div className="window-controls" aria-label="窗口控制">
      <button
        type="button"
        aria-label="最小化窗口"
        onClick={() => void window.desktopAPI?.windowControl('minimize')}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3 8h10" fill="none" stroke="currentColor" />
        </svg>
      </button>
      <button
        type="button"
        aria-label={maximized ? '恢复窗口' : '最大化窗口'}
        onClick={() => void window.desktopAPI?.windowControl('toggleMaximize')}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d={maximized ? 'M5 3h8v8H5ZM3 5v8h8' : 'M3 3h10v10H3Z'}
            fill="none"
            stroke="currentColor"
          />
        </svg>
      </button>
      <button
        type="button"
        className="window-close"
        aria-label="关闭窗口"
        onClick={() => void window.desktopAPI?.windowControl('close')}
      >
        <Icon name="close" size={16} />
      </button>
    </div>
  );
}
