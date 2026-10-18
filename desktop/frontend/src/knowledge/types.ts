export type Owner = { conversation_id: string; character_id: string };
export type Collection = {
  id: string;
  name: string;
  description: string;
  revision: number;
  index_version: number;
  status: string;
  document_count: number;
  chunk_count: number;
  source_roots: Array<{ id: string; path: string }>;
  config: {
    allowed_types: string[];
    excluded_names: string[];
    include_globs: string[];
    exclude_globs: string[];
    embedding_mode: string;
    max_file_bytes: number;
    max_documents: number;
    max_chunks: number;
    chunk_characters: number;
    overlap: number;
  };
};
export type Citation = {
  collection_id: string;
  collection_name: string;
  document_id: string;
  document_version: number;
  content_hash: string;
  index_version: number;
  chunk_id: string;
  relative_path: string;
  file_type: string;
  excerpt: string;
  verification: string;
  location: {
    line_start?: number;
    line_end?: number;
    page?: number;
    paragraph?: number;
    heading?: string;
    symbol?: string;
  };
};
export type SearchResult = {
  results: Citation[];
  mode: string;
  requested_mode: string;
  warnings: string[];
  duration_ms: number;
};
export type Job = {
  id: string;
  state: string;
  progress: Record<string, number>;
  last_error?: string;
};
export type Scope = 'GLOBAL_ASSISTANT' | 'CHARACTER' | 'CONVERSATION' | 'WORKSPACE';
export type Memory = {
  memory_id: string;
  scope: Scope;
  owner_id: string;
  content: string;
  category: 'fact' | 'preference' | 'task';
  enabled: boolean;
  status: string;
  expires_at: string | null;
};
export type Reads = {
  collections: { collections: Collection[] };
  jobs: { jobs: Job[] };
  selection: {
    collection_ids: string[];
    preference: { rag_enabled: boolean; memory_enabled: boolean };
    transfer_approved: boolean;
  };
  targets: {
    targets: Array<{ role: string; fingerprint: string; base_url: string; model: string }>;
  };
  evidence: { citations: Citation[] };
  memories: { memories: Memory[] };
};
export type Operation =
  | 'embedding'
  | 'create'
  | 'update'
  | 'add-root'
  | 'remove-root'
  | 'delete'
  | 'index'
  | 'job-control'
  | 'selection'
  | 'preferences'
  | 'search'
  | 'reveal-source'
  | 'memory-save'
  | 'memory-remove';
