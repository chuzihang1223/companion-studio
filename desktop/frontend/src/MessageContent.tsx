import CopyCode from './CopyCode';
/** Fenced blocks have their own copy action; the message action keeps raw text. */
export default function MessageContent({ text }: { text: string }) {
  const parts = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (!part.startsWith('```')) return <span key={i}>{part}</span>;
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
