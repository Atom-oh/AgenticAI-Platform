export type Role = 'owner' | 'planner' | 'designer' | 'developer';
export type DocumentRecord = {
  id: string; version: number; title: string; kind: string; graphRef: string | null;
  createdBy: string; projectId: string | null; readRoles: Role[]; aclVersion: number;
  status: 'active' | 'archived'; latestRevisionId: string; approvedRevisionId: string | null;
  provenance: 'uploaded' | 'synthetic_sample'; createdAt: number; updatedAt: number;
};
export type Revision = {
  id: string; documentId: string; version: number; revision: number; name: string;
  size: number; sha256: string; versionLabel: string; effectiveDate: string | null;
  createdBy: string; status: 'uploading' | 'processing' | 'draft' | 'in_review' | 'approved' | 'rejected' | 'failed';
  parseStatus: string; textHash: string | null; paragraphCount: number; pages: number;
  warnings: string[]; createdAt: number; updatedAt: number; jobId?: string;
  reviewedBy?: string; reviewedAt?: number; reviewNote?: string; error?: string;
};
export type Paragraph = { id: string; text: string; page: number | null; sha256: string };
export type Job = {
  id: string; status: string; error?: string; stopReason?: string;
  progress: number | { percent?: number; stage?: string; message?: string };
};
export type LibraryConfig = {
  roles: Role[]; maxFileBytes: number; chunkBytes: number; extensions: string[];
  role: Role; actorId: string; projectId: string | null;
};
export type Project = { id: string; name: string; version: number; members: Record<string, { role: Role; displayName?: string }> };
export type Reference = { id: string; label: 'Regulation' | 'Document'; title: string; version?: string; effectiveDate?: string };
export type DocumentDetail = {
  document: DocumentRecord; revisions: Revision[];
  capabilities: { read: boolean; edit: boolean; review: boolean; manage: boolean };
};
export type SourceView = { document: DocumentRecord; revision: Revision; paragraphs: Paragraph[]; cursor?: string; totalParagraphs: number };
export type Evidence = {
  id: string; documentId: string; revisionId: string; paragraphId: string; title: string;
  revision: number; versionLabel: string; originalSha256: string; textHash: string;
  quote: string; page: number | null; provenance: 'uploaded' | 'synthetic_sample';
};
export type Candidate = { id: string; label: string; name: string };
export type Analysis = {
  id: string; version: number; query: string; regulationRef: string; modelId: string;
  createdBy: string; projectId: string | null; status: string; jobId: string;
  createdAt: number; updatedAt: number; error?: string; decisionCount: number;
};
export type Decision = { id: string; nodeId: string; decision: 'change_required' | 'unaffected' | 'needs_review'; note: string; actorId: string; actorRole: Role; createdAt: number };
export type AnalysisResult = {
  regulation: { id: string; title: string; article?: string; registryVersion?: string; effectiveDate?: string };
  counts: Record<string, number>; candidates: Record<string, Candidate[]>;
  sources: { documentId: string; revisionId: string; graphRef: string; title: string; revision: number;
    versionLabel: string; sha256: string; textHash: string; provenance: string }[];
  evidence: Evidence[]; findings: { nodeId: string; reason: string; citationIds: string[] }[];
  summary: string; coverage: { graphBackend: string; linkedSources: number; unavailableSources: number;
    sourceLimitReached: boolean; contextCharacters?: number; evidenceParagraphs?: number;
    availableParagraphs?: number; truncated?: boolean; candidateContextsOmitted?: number; candidateOmissions?: Record<string, number>;
    uncheckedSources?: number; sourceResolution?: { graphRef: string; status: string; reason?: string }[];
    graphTraversalLimited?: boolean; graphCountsExact?: boolean };
  verification: { sourceIntegrity: string; references: string; semantic: string; outputPolicy?: string };
  model: { invoked: boolean; requestedId?: string; modelId?: string | null; usage?: { inputTokens?: number; outputTokens?: number } };
};
export type AnalysisView = { analysis: Analysis; result?: AnalysisResult; staleSources: string[]; canDecide: boolean; decisions: Decision[]; decisionsTruncated: boolean };
