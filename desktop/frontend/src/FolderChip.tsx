import { useEffect, useRef, useState } from 'react';
import Icon from './Icon';
import { ClipboardService } from './ClipboardService';
export default function FolderChip({
  root,
  disabled,
  onSelect,
  onOpen,
  onClear,
  onError,
}: {
  root: string | null;
  disabled?: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onClear: () => void;
  onError: (error: string) => void;
}) {
  const [open, setOpen] = useState(false),
    ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);
  const name = root
    ?.replace(/[\\/]$/, '')
    .split(/[\\/]/)
    .pop();
  return (
    <div className="folder-chip-wrap" ref={ref}>
      <button
        type="button"
        className="folder-chip"
        disabled={disabled}
        aria-label="项目文件夹"
        aria-expanded={open}
        title={root || '选择项目文件夹'}
        onClick={() => (root ? setOpen(!open) : onSelect())}
      >
        <Icon name="folder" />
        <span>{name || '选择项目文件夹'}</span>
      </button>
      {open && root && (
        <div className="folder-chip-menu" role="menu">
          <p>{root}</p>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onSelect();
            }}
          >
            更换文件夹
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onOpen();
            }}
          >
            在资源管理器中打开
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() =>
              void navigator.clipboard
                .writeText(root)
                .then(() => setOpen(false))
                .catch(() => onError('复制路径失败'))
            }
          >
            复制路径
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onClear();
            }}
          >
            清除选择
          </button>
        </div>
      )}
    </div>
  );
}
