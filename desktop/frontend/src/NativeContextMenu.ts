/** One handler covers search, textarea and editable controls in both modes. */
export function installNativeContextMenus() {
  let lastInput: HTMLElement | null = null;
  const history = new WeakMap<HTMLElement, { undo: boolean; redo: boolean }>();
  const input = (event: Event) => {
    if (event.target instanceof HTMLElement) {
      history.set(event.target, { undo: true, redo: false });
      lastInput = event.target;
    }
  };
  const handler = (event: MouseEvent) => {
    if (!window.desktopAPI) return;
    const target =
      event.target instanceof Element
        ? event.target.closest('input,textarea,[contenteditable="true"]')
        : null;
    if (!target && event.target instanceof Element && event.target.closest('.message-row')) return;
    event.preventDefault();
    let selected = window.getSelection()?.toString() || '';
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)
      selected = target.value.slice(target.selectionStart || 0, target.selectionEnd || 0);
    const editable = target instanceof HTMLElement;
    if (editable) {
      target.focus();
      lastInput = target;
    }
    void window.desktopAPI.showContextMenu(editable ? 'input' : 'text', {
      selectedText: selected,
      hasSelection: !!selected,
      x: event.clientX,
      y: event.clientY,
      readOnly:
        editable &&
        ('readOnly' in target
          ? target.readOnly || ('disabled' in target && target.disabled)
          : false),
      canUndo: editable && (document.queryCommandEnabled('undo') || !!history.get(target)?.undo),
      canRedo: editable && (document.queryCommandEnabled('redo') || !!history.get(target)?.redo),
    });
  };
  const key = (e: KeyboardEvent) => {
    if (lastInput && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z')
      history.set(lastInput, { undo: !!e.shiftKey, redo: !e.shiftKey });
  };
  document.addEventListener('input', input);
  document.addEventListener('contextmenu', handler);
  document.addEventListener('keydown', key);
  return () => {
    document.removeEventListener('input', input);
    document.removeEventListener('contextmenu', handler);
    document.removeEventListener('keydown', key);
  };
}
