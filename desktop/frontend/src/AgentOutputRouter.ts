import type { ChatMessage, WorkSession } from './api';
import type { ModuleId } from './WorkspaceModuleRegistry';
export type AgentOutputType =
  | 'character_message'
  | 'code'
  | 'diff'
  | 'terminal'
  | 'file'
  | 'browser'
  | 'application'
  | 'media'
  | 'system'
  | 'tool_event';
export type RoutedOutput = {
  type: AgentOutputType;
  module: ModuleId;
  id: string;
  title: string;
  data: Record<string, unknown>;
};
const kinds: Record<string, [AgentOutputType, ModuleId]> = {
  code: ['code', 'code'],
  diff: ['diff', 'diff'],
  terminal: ['terminal', 'terminal'],
  file: ['file', 'files'],
  files: ['file', 'files'],
  web: ['browser', 'browser'],
  app: ['application', 'application'],
  media: ['media', 'media'],
  system: ['system', 'system'],
  preview: ['tool_event', 'preview'],
  artifacts: ['tool_event', 'artifacts'],
};
/** 将结果路由至模块，并为协作对话生成展示副本；不修改持久消息原文。 */
export class AgentOutputRouter {
  route(
    session: WorkSession | null,
    messages: ChatMessage[],
  ): { outputs: RoutedOutput[]; conversation: ChatMessage[] } {
    const outputs: RoutedOutput[] = [];
    for (const tab of session?.tabs || []) {
      const [type, module] = kinds[tab.kind] || ['tool_event', 'artifacts'];
      outputs.push({ type, module, id: tab.id, title: tab.title, data: tab.data });
      if (tab.kind === 'code' && tab.data.diff)
        outputs.push({
          type: 'diff',
          module: 'diff',
          id: tab.id + '-diff',
          title: tab.title,
          data: tab.data,
        });
    }
    // 对话中隐藏代码/工具噪声，但代码产物仍带回复 ID 放入代码模块。
    const conversation = messages.map((row) => {
      if (row.role !== 'assistant') return row;
      let found = false;
      const content = row.content.replace(
        /```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g,
        (_whole, language, code) => {
          found = true;
          outputs.push({
            type: 'code',
            module: 'code',
            id: row.id + '-code-' + outputs.length,
            title: language?.trim() || '代码片段',
            data: { content: code.trim(), language },
          });
          return '';
        },
      );
      const natural = content
        .split('\n')
        .filter(
          (line) =>
            !/^\s*(?:Traceback|File "|\s*at\s+\S+\(|\{\s*"|PS [A-Z]:|\$ (?:npm|python|git)|[+-]{3} )/.test(
              line,
            ),
        )
        .join('\n')
        .trim();
      return {
        ...row,
        content:
          natural ||
          (found ? '代码放在工作区了，可以在代码模块查看。' : '工作结果已保存在对应模块。'),
      };
    });
    return { outputs, conversation };
  }
}
