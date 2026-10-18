export type Owner = { conversation_id: string; character_id: string };
export type Box = [number, number, number, number];
export type Element = {
  identity: string;
  automation_id: string;
  type: string;
  name: string;
  value: string;
  enabled: boolean;
  visible: boolean;
  bounds: Box;
};
export type Candidate = {
  candidate_id: string;
  source: string;
  observation_id: string;
  bounding_box: Box;
  match_quality: number;
  confidence: number | null;
};
export type Observation = {
  observation_id: string;
  created_ms: number;
  elements: Element[];
  window: { bounds: Box; foreground: boolean; memory_bytes: number };
  geometry: {
    window_dpi: number;
    monitor_dpi: number;
    coordinate_space: string;
    monitors: Array<{ id: string; bounds: Box }>;
  };
  ocr: { lines: Array<{ text: string; bounds: Box; confidence: null; language: string }> } | null;
  image?: {
    data_uri: string;
    width: number;
    height: number;
    origin: [number, number];
    scale: number;
    redacted_regions: number;
  };
  capture_error?: string | null;
  ocr_error?: string | null;
  duration_ms: number;
};
export type Result = {
  verified: boolean;
  status: string;
  code?: string;
  observation_id?: string;
  candidates?: Candidate[];
  task_id?: string;
  work_session_id?: string;
  duration_ms?: number;
};
export type Target = {
  target_id: string;
  application: string;
  title: string;
  status: string;
  preview_enabled: boolean;
  observation: Observation | null;
  last_result: Result | null;
};
export type State = {
  enabled: boolean;
  cloud_transfer: false;
  persistent_screenshots: false;
  targets: Target[];
};
export type Choice = { choice_id: string; application: string; title: string; bounds: Box };
export type Action = {
  operation:
    | 'observe'
    | 'find_element'
    | 'click'
    | 'type_text'
    | 'scroll'
    | 'focus_window'
    | 'wait_for_state';
  arguments: Record<string, unknown>;
};
