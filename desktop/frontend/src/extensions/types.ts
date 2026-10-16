export type ServerConfig = {
  display_name: string;
  transport: 'stdio' | 'http';
  endpoint_or_command: string;
  args: string[];
  enabled: boolean;
  allow_loopback: boolean;
  environment_names: string[];
  credential_reference: boolean;
  connect_timeout: number;
  call_timeout: number;
  protocol_mode: 'auto' | 'legacy' | '2026-07-28';
};
export type Server = {
  server_id: string;
  config: ServerConfig;
  connection_status: string;
  protocol_version: string | null;
  capabilities: Record<string, unknown>;
  tool_count: number;
  last_error: string | null;
  last_connected_at: string | null;
  recent_calls: Array<{
    tool_id: string;
    status: string;
    error_code: string | null;
    duration_ms: number;
  }>;
};
export type ExternalTool = {
  id: string;
  server_id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown> | null;
  enabled: boolean;
  risk_level: string;
  schema_fingerprint: string;
};
export type Skill = {
  id: string;
  name: string;
  description: string;
  version: string;
  source: string;
  enabled: boolean;
  trusted_state: string;
  content_hash: string;
  required_tools: string[];
  required_capabilities: string[];
  compatibility?: string;
  files: Array<{ path: string; size: number; sha256: string }>;
  instructions?: string;
  import_token?: string;
};
export type ExtensionReads = {
  servers: { items: Server[] };
  tools: { items: ExternalTool[] };
  catalog: {
    resources: Array<Record<string, unknown>>;
    prompts: Array<Record<string, unknown>>;
    capabilities: Record<string, unknown>;
  };
  skills: { items: Skill[] };
  skill: Skill;
  'skill-file': { text: string };
  template: Record<string, unknown>;
};
export type ExtensionControl =
  | 'skill-example'
  | 'template-select'
  | 'template-export'
  | 'approve-plan'
  | 'server-save'
  | 'connect'
  | 'disconnect'
  | 'refresh'
  | 'server-remove'
  | 'credentials-save'
  | 'tool-save'
  | 'preview'
  | 'skill-select-folder'
  | 'skill-import'
  | 'skill-update'
  | 'skill-create'
  | 'skill-edit'
  | 'skill-toggle'
  | 'skill-remove'
  | 'skill-export'
  | 'grant';

export function api() {
  if (!window.desktopAPI) throw Error('扩展管理需要桌面版');
  return window.desktopAPI;
}
