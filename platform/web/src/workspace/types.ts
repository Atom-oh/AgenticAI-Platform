export type Purpose = 'reference' | 'component' | 'token' | 'skill' | 'guide' | 'prototype' | 'archive';
export type Model = { id: string; label: string; provider?: string };
export type ComponentCatalogSummary = { id: string; version: string; label: string; hash: string };
export type ComponentCatalog = ComponentCatalogSummary & { components: {
  name: string; description: string; props: Record<string, string>; variationAxes: string[];
}[] };
export type WorkspaceConfig = { actorId?: string; componentCatalog?: ComponentCatalogSummary; models: Model[]; defaultModel: string; maxFileBytes: number; chunkBytes: number; extensions: string[] };
export type Preview = { page: number; mime: string; width?: number; height?: number; ocrStatus?: string };
export type Asset = {
  // Internal optimistic database revision; never the designer-facing import version.
  id: string; version: number; name: string; size: number; sha256: string; purpose: Purpose; parentId?: string;
  importRevision?: number; lineageId?: string;
  system?: boolean; projectId?: string; productId?: string; guidelineId?: string;
  uploadStatus: 'uploading' | 'processing' | 'stored' | 'failed';
  parseStatus: 'pending' | 'complete' | 'partial' | 'unsupported' | 'failed';
  previews?: Preview[]; warnings?: string[]; error?: string; archived?: boolean; jobId?: string;
};
export type Analysis = { text?: string; format?: string; pages?: number; warnings?: string[]; resources?: unknown; parseStatus?: string };
export type Job = {
  id: string; task: 'finalize' | 'propose' | 'run' | 'release' | 'git'; status: 'queued' | 'running' | 'completed' | 'failed';
  progress?: number | string | { percent?: number; message?: string };
  result?: { assetId?: string; contractId?: string; runId?: string; releaseId?: string; bestRound?: number; passed?: boolean }; error?: string;
};
export type StyleProperty = 'color' | 'backgroundColor' | 'fontSize' | 'fontWeight' | 'fontFamily' | 'borderRadius' |
  'padding' | 'margin' | 'gap' | 'minHeight' | 'height' | 'width' | 'borderColor' | 'borderWidth' | 'display';
export type Action = 'fill' | 'click' | 'check' | 'select' | 'press' | 'expectText' | 'expectValue' | 'expectVisible' | 'expectEnabled' | 'expectChecked' | 'expectStyle';
export type Step = { action: Action; target: string; targetLabel: string; value?: string | boolean; match?: 'contains' | 'equals'; property?: StyleProperty; normalizeWhitespace?: boolean };
export type Rule = {
  id: string; title: string; required: boolean;
  source: { kind: 'explicit' | 'inferred' | 'manual'; assetId?: string; quote?: string; page?: number };
  steps: Step[];
};
export type EditableContract = {
  projectId?: string; productId?: string; guidelineId?: string; guidelineAssetId?: string; ontologyHash?: string; catalogHash?: string;
  schemaVersion?: number; title: string; brief: string; assetIds: string[];
  viewport: { width: number; height: number }; rules: Rule[]; unresolved: string[];
  bindings?: Record<string, string>;
};
export type Contract = EditableContract & {
  id: string; version: number; status: string; hash?: string; approval?: unknown;
};
export type BuildEvidence = {
  ok?: boolean; build?: BuildEvidence;
  gates?: Partial<Record<'policy' | 'types' | 'build' | 'components', { status?: string }>>;
  sourceHash?: string; bundleHash?: string; catalogHash?: string;
  diagnostics?: unknown[];
};
export type Round = BuildEvidence & {
  number: number; passed: boolean; artifactSha256: string;
  hasHtml?: boolean; hasScreenshot?: boolean; hasDiff?: boolean; hasReport?: boolean;
  checks?: { pass: number; fail: number; incomplete: number }; blockingFindings?: unknown[]; functionalStatus?: string; visualStatus?: string;
  pageSources?: { pageId: string; path: string }[]; hasSource?: boolean; hasDist?: boolean; hasCandidate?: boolean;
  releaseId?: string;
};
export type Run = {
  projectId?: string; productId?: string; guidelineId?: string; guidelineAssetId?: string; ontologyHash?: string; catalogHash?: string;
  outputType?: 'react' | 'html'; batchId?: string; needsRevalidation?: boolean;
  visualPolicy?: 'exact' | 'variation-review';
  generationMode?: 'creative' | 'guided';
  id: string; version: number; contractId: string; contractVersion: number; contractHash: string;
  assetSnapshots?: { id: string; sha256: string; name: string }[];
  model?: string; mode?: 'generate' | 'verify'; sourceAssetId?: string; status: string; bestRound?: number; rounds: Round[];
  contract?: EditableContract; referenceAssetId?: string; referencePage?: number; visualTolerance?: number; jobId?: string;
  variant?: string; baseRunId?: string;
  contextWarnings?: string[];
  functionalStatus?: string; visualStatus?: string;
  approval?: { round?: number; artifactSha256?: string; contractVersion?: number; contractHash?: string; actor?: string; at?: number;
    sourceHash?: string; bundleHash?: string; catalogHash?: string; guidelineId?: string; acceptedVariation?: boolean };
};
export type Role = 'owner' | 'planner' | 'designer' | 'developer';
export type Project = { id: string; name: string; version: number; members: Record<string, { role: Role; displayName: string }> };
export type Product = {
  id: string; projectId: string; version: number; title: string; description: string;
  conditions: { id: string; text: string }[]; steps: { id: string; title: string; description: string }[];
  notices: { id: string; title: string; content: string; required: boolean }[];
  publishedGuidelineId?: string; publishedRevision?: number; ontologyHash?: string; guideAssetId?: string;
};
export type Ontology = { schemaVersion: number; projectId: string; productId: string; guidelineId: string;
  revision: number; hash: string; nodes: Record<string, unknown>[]; edges: Record<string, unknown>[] };
export type Anchor = { productId?: string; guidelineId?: string; runId?: string; round?: number; pageId?: string };
export type Discussion = { id: string; text: string; anchor: Anchor; author: string; createdAt?: number };
export type Selection = { run: Run; round?: Round; pageId?: string } | null;
export type Batch = { id: string; mode: 'creative' | 'guided'; baselineRunId?: string; runIds: string[];
  variationCount?: number; contractHash: string; catalogHash: string; contractId?: string; contractVersion?: number; status?: string;
  slots?: { index: number; variant: string; role: string; runId?: string; jobId?: string; error?: string; errorCode?: string }[] };
export type Release = {
  id: string; runId: string; round: number; status: string; sourceHash?: string; bundleHash?: string;
  catalogHash?: string; contractHash?: string; guidelineId?: string; approval?: Run['approval'];
  rebuildEvidence?: Record<string, unknown>; hasSource?: boolean; hasDist?: boolean; hasManifest?: boolean; hasReport?: boolean;
  build?: BuildEvidence; rebuiltAt?: number;
  verification?: { functionalStatus?: string; accessibility?: { status?: string };
    visual?: { status?: string; tolerance?: number; changedRatio?: number } };
  jobId?: string; error?: string;
  git?: { status: string; branch?: string; commitSha?: string; commitUrl?: string | null; filesUrl?: string | null; baseSha?: string;
    error?: string; exportId?: string; jobId?: string; criteriaCurrent?: boolean | null; criteriaStatus?: string };
};
