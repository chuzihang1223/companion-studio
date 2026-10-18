import type { IconName } from './Icon';
export type ModuleId =
  | 'conversation'
  | 'workflow'
  | 'collaboration'
  | 'code'
  | 'diff'
  | 'terminal'
  | 'browser'
  | 'files'
  | 'application'
  | 'media'
  | 'system'
  | 'preview'
  | 'artifacts'
  | 'trace'
  | 'evaluation'
  | 'workflow_builder'
  | 'knowledge'
  | 'visual_observer'
  | 'permissions';
export type ModuleDefinition = {
  id: ModuleId;
  title: string;
  icon: IconName;
  defaultDock: 'main' | 'side';
  canMinimize: boolean;
  canCollapse: boolean;
  canClose: boolean;
  canMaximize: boolean;
  minWidth: number;
  minHeight: number;
  intrinsicMinSize: { width: number; height: number };
  allowMultiple: boolean;
};
const definitions: Array<[ModuleId, string, IconName, 'main' | 'side']> = [
  ['conversation', '协作对话', 'conversation', 'main'],
  ['workflow', '工作流', 'workflow', 'side'],
  ['collaboration', '助手 · 协作流', 'network', 'side'],
  ['code', '代码', 'code', 'main'],
  ['diff', '代码变更', 'diff', 'main'],
  ['terminal', '终端', 'terminal', 'main'],
  ['browser', '网页', 'globe', 'main'],
  ['files', '项目文件', 'folder', 'main'],
  ['application', '应用观察', 'app', 'main'],
  ['media', '媒体播放', 'music', 'main'],
  ['system', '系统状态', 'activity', 'main'],
  ['preview', '预览', 'eye', 'main'],
  ['artifacts', '产物', 'package', 'main'],
  ['permissions', '操作授权', 'check', 'side'],
  ['trace', 'Agent Trace · 执行追踪', 'activity', 'main'],
  ['evaluation', 'Agent Evaluation · 评测', 'activity', 'main'],
  ['workflow_builder', 'Visual Workflow · 流程设计', 'workflow', 'main'],
  ['knowledge', '本地知识与记忆', 'folder', 'main'],
  ['visual_observer', '屏幕观察 · Visual Observer', 'eye', 'main'],
];
export const WorkspaceModuleRegistry = Object.fromEntries(
  definitions.map(([id, title, icon, defaultDock]) => [
    id,
    {
      id,
      title,
      icon,
      defaultDock,
      canMinimize: true,
      canCollapse: true,
      canClose: id !== 'conversation',
      canMaximize: true,
      minWidth: 64,
      minHeight: 40,
      intrinsicMinSize: {
        width: id === 'conversation' ? 240 : ['terminal', 'code', 'diff'].includes(id) ? 220 : 180,
        height: defaultDock === 'side' ? 120 : 160,
      },
      allowMultiple: false,
    },
  ]),
) as Record<ModuleId, ModuleDefinition>;
export const moduleIds = definitions.map(([id]) => id);
