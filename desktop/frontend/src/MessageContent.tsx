import CopyCode from './CopyCode';
/** Fenced blocks have their own copy action; the message action keeps raw text. */
export default function MessageContent({ text }: { text: string }) {
  const parts = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (!part.startsWith('```'))
          return (
            <span key={i}>
              {part.split(/(\[K:[a-f0-9-]{36}\])/gi).map((value, key) =>
                /^\[K:[a-f0-9-]{36}\]$/i.test(value) ? (
                  <button
                    className="knowledge-inline-citation"
                    key={key}
                    title="查看检索来源"
                    onClick={(event) => {
                      const source = event.currentTarget
                        .closest('article')
                        ?.querySelector<HTMLDetailsElement>(
                          `[data-knowledge-source="${value.slice(3, -1)}"]`,
                        );
                      if (source) {
                        source.open = true;
                        source.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                      }
                    }}
                  >
                    〔来源〕
                  </button>
                ) : (
                  value
                ),
              )}
            </span>
          );
        const code = part.slice(part.indexOf('\n') + 1, -3).replace(/\n$/, '');
        return (
          <div key={i} className="message-code">
            <CopyCode text={code} />
            <pre>
              <code>{code}</code>
            </pre>
          </div>
        );
      })}
    </>
  );
}
