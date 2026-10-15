import { useAppTheme, type BackgroundConfig } from './AppThemeProvider';
export default function BackgroundSettings() {
  const { config, update, choose, error } = useAppTheme();
  const change = (key: 'chat' | 'work', patch: Partial<BackgroundConfig>) => {
    void update({ [key]: { ...config[key], ...patch } }).catch(() => {});
  };
  return (
    <section className="background-settings">
      <h3>外观 · 模式背景</h3>
      {(['chat', 'work'] as const).map((key) => {
        const value = config[key],
          work = key === 'work';
        return (
          <fieldset key={key}>
            <legend>{work ? '工作模式背景' : '聊天模式背景'}</legend>
            {work && (
              <label>
                背景来源
                <select
                  aria-label="工作背景来源"
                  value={value.follow_chat ? 'follow' : 'independent'}
                  onChange={(e) => change(key, { follow_chat: e.target.value === 'follow' })}
                >
                  <option value="follow">跟随聊天模式</option>
                  <option value="independent">单独设置</option>
                </select>
              </label>
            )}
            {(!work || !value.follow_chat) && (
              <>
                <label>
                  背景类型
                  <select
                    aria-label={`${key}背景类型`}
                    value={value.mode}
                    onChange={(e) =>
                      change(key, { mode: e.target.value as BackgroundConfig['mode'] })
                    }
                  >
                    <option value="default">默认背景</option>
                    <option value="color">纯色</option>
                    <option value="gradient">渐变</option>
                    <option value="image">自定义图片</option>
                  </select>
                </label>
                {value.mode === 'color' && (
                  <label>
                    背景颜色
                    <input
                      aria-label={`${key}背景颜色`}
                      type="color"
                      value={value.color}
                      onChange={(e) => change(key, { color: e.target.value })}
                    />
                  </label>
                )}
                {value.mode === 'gradient' && (
                  <label>
                    渐变颜色
                    <input
                      type="color"
                      value={value.gradient_start}
                      onChange={(e) => change(key, { gradient_start: e.target.value })}
                    />
                    <input
                      type="color"
                      value={value.gradient_end}
                      onChange={(e) => change(key, { gradient_end: e.target.value })}
                    />
                  </label>
                )}
                <button type="button" onClick={() => void choose(key).catch(() => {})}>
                  选择{work ? '工作' : '聊天'}背景图片
                </button>
                {(
                  [
                    ['brightness', '背景亮度', 40, 150],
                    ['blur', '背景模糊', 0, 24],
                    ['mask', '遮罩透明度', 0, 100],
                    ['panel', '面板不透明度', 55, 100],
                  ] as const
                ).map(([field, label, min, max]) => (
                  <label key={field}>
                    {label}
                    <input
                      type="range"
                      aria-label={`${key}${label}`}
                      min={min}
                      max={max}
                      value={value[field]}
                      onChange={(e) => change(key, { [field]: Number(e.target.value) })}
                    />
                    <span>
                      {value[field]}
                      {field === 'blur' ? 'px' : '%'}
                    </span>
                  </label>
                ))}
              </>
            )}
          </fieldset>
        );
      })}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
