export type Purpose = 'reference' | 'component' | 'token' | 'skill' | 'guide' | 'prototype' | 'archive';
export type Model = { id: string; label: string; provider?: string };
export type WorkspaceConfig = { models: Model[]; defaultModel: string; maxFileBytes: number; chunkBytes: number; extensions: string[] };
export type Preview = { page: number; mime: string; width?: number; height?: number; ocrStatus?: string };
export type Asset = {
  // Internal optimistic database revision; never the designer-facing import version.
  id: string; version: number; name: string; size: number; sha256: string; purpose: Purpose; parentId?: string;
  importRevision?: number; lineageId?: string;
  uploadStatus: 'uploading' | 'processing' | 'stored' | 'failed';
  parseStatus: 'pending' | 'complete' | 'partial' | 'unsupported' | 'failed';
  previews?: Preview[]; warnings?: string[]; error?: string; archived?: boolean; jobId?: string;
};
export type Analysis = { text?: string; format?: string; pages?: number; warnings?: string[]; resources?: unknown; parseStatus?: string };
export type Job = {
  id: string; task: 'finalize' | 'propose' | 'run'; status: 'queued' | 'running' | 'completed' | 'failed';
  progress?: number | string | { percent?: number; message?: string };
  result?: { assetId?: string; contractId?: string; runId?: string; bestRound?: number; passed?: boolean }; error?: string;
};
export type StyleProperty = 'color' | 'backgroundColor' | 'fontSize' | 'fontWeight' | 'fontFamily' | 'borderRadius' |
  'padding' | 'margin' | 'gap' | 'minHeight' | 'height' | 'width' | 'borderColor' | 'borderWidth' | 'display';
export type Action = 'fill' | 'click' | 'check' | 'select' | 'press' | 'expectText' | 'expectValue' | 'expectVisible' | 'expectEnabled' | 'expectChecked' | 'expectStyle';
export type Step = { action: Action; target: string; targetLabel: string; value?: string | boolean; match?: 'contains' | 'equals'; property?: StyleProperty };
export type Rule = {
  id: string; title: string; required: boolean;
  source: { kind: 'explicit' | 'inferred' | 'manual'; assetId?: string; quote?: string; page?: number };
  steps: Step[];
};
export type EditableContract = {
  schemaVersion?: number; title: string; brief: string; assetIds: string[];
  viewport: { width: number; height: number }; rules: Rule[]; unresolved: string[];
  bindings?: Record<string, string>;
};
export type Contract = EditableContract & {
  id: string; version: number; status: string; hash?: string; approval?: unknown;
};
export type Round = {
  number: number; passed: boolean; artifactSha256: string;
  hasHtml?: boolean; hasScreenshot?: boolean; hasDiff?: boolean; hasReport?: boolean;
  checks?: { pass: number; fail: number; incomplete: number }; blockingFindings?: unknown[]; functionalStatus?: string; visualStatus?: string;
};
export type Run = {
  id: string; version: number; contractId: string; contractVersion: number; contractHash: string;
  assetSnapshots?: { id: string; sha256: string; name: string }[];
  model?: string; mode?: 'generate' | 'verify'; sourceAssetId?: string; status: string; bestRound?: number; rounds: Round[];
  contract?: EditableContract; referenceAssetId?: string; referencePage?: number; visualTolerance?: number; jobId?: string;
  variant?: string; baseRunId?: string;
  contextWarnings?: string[];
  functionalStatus?: string; visualStatus?: string;
  approval?: { round?: number; artifactSha256?: string; actor?: string; at?: number };
};
