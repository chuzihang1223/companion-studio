import type { RoutedOutput } from './AgentOutputRouter';
export default function TerminalModule({ outputs }: { outputs: RoutedOutput[] }) {
  return (
    <div className="terminal-module">
      {outputs.map((row) => (
        <article key={row.id}>
          <p>{String(row.data.command || row.title)}</p>
          <pre>{String(row.data.output || '等待命令输出…')}</pre>
          <small>退出码 · {String(row.data.exit_code ?? '运行中')}</small>
        </article>
      ))}
    </div>
  );
}
