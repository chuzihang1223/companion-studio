import { useEffect, useState } from 'react';

export default function FirstRunGuide({
  refresh,
  onConfigure,
}: {
  refresh: unknown;
  onConfigure: () => void;
}) {
  const [needsKey, setNeedsKey] = useState(false);
  useEffect(() => {
    let live = true;
    void window.desktopAPI?.listApiPresets().then((data) => {
      if (live) setNeedsKey(!data.presets.some((p) => p.id === data.active_id && p.has_key));
    });
    return () => {
      live = false;
    };
  }, [refresh]);
  if (!needsKey) return null;
  return (
    <div className="reply-preview" role="status">
      <span>
        欢迎使用。请在设置的「模型
        API」填写自己的服务地址、模型和密钥。语音可稍后配置；角色管理支持导入 JSON / PNG
        角色卡，也可直接创建自定义角色。
      </span>
      <button type="button" onClick={onConfigure}>
        打开设置
      </button>
    </div>
  );
}
