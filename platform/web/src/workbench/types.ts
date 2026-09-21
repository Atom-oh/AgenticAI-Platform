import type { Product, Project, Role } from '../workspace/types';

export type WorkbenchView = 'planning' | 'changes' | 'deliverables' | 'components' | 'development' |
  'knowledge' | 'ontology' | 'skills' | 'pension' | 'reports' | 'sources' | 'batches' | 'tools' | 'operations';
export type JsonRecord = Record<string, unknown>;
export type Job = { id: string; status: string; progress?: unknown; error?: string; result?: JsonRecord };
export type Overview = {
  actorId: string; role: Role; operator: boolean; capabilities: string[] | Record<string, boolean>;
  stats: Record<string, number>; tasks: Task[]; recentArtifacts: JsonRecord[]; readiness: JsonRecord;
  statsIncomplete?: string[];
};
export type Task = {
  id: string; changeId: string; impactHash: string; targetId: string; title: string; role: string;
  status: string; assigneeSub?: string; evidenceRefs: unknown[]; sourceRefs?: unknown[]; version: number;
};
export type Source = {
  id: string; name: string; kind: string; version: number; status?: string; description?: string;
  connectionId?: string; scope?: unknown; [key: string]: unknown;
};
export type Batch = {
  id: string; sourceId: string; status: string; jobId?: string; counts?: JsonRecord;
  generation?: string; error?: string; [key: string]: unknown;
};
export type GraphNode = { id: string; label: string; title: string; version?: string | number; role?: string; sourceRef?: unknown; provenance?: unknown };
export type GraphEdge = { src: string; rel: string; dst: string; sourceRef?: unknown; provenance?: unknown };
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[]; coverage?: unknown; generation?: string };
export type Knowledge = {
  id: string; title: string; content?: string; snippet?: string; revision?: string; kind?: string;
  sourceId?: string; sourceUrl?: string; sourceRef?: unknown; [key: string]: unknown;
};
export type Change = {
  id: string; version: number; title: string; targetId: string; changeType: string;
  before: unknown; after: unknown; reason: string; productId?: string; status?: string;
  impact?: JsonRecord; [key: string]: unknown;
};
export type Skill = {
  id: string; name: string; title: string; description: string; instructions: string; version: number;
  status: string; contentHash: string; sourceRefs?: unknown[]; toolNames?: string[]; examples?: unknown[];
  validation?: JsonRecord; [key: string]: unknown;
};
export type SkillExecution = {
  artifact: {
    id: string; skillId: string; skillVersion: number; contentHash: string; status: string; jobId?: string;
    sourceRefs?: unknown[]; resultHash?: string;
  };
  result: {
    status: 'answered' | 'refused' | 'insufficient-evidence'; answer: string; evidenceIds: string[];
    receipt?: JsonRecord; sourceRefs?: unknown[]; executionMode?: string; toolsExecuted?: string[];
  } | null;
};
export type Persona = { id: string; name?: string; title?: string; description?: string; age?: number; [key: string]: unknown };
export type PensionSession = {
  id: string; version: number; personaId?: string; persona?: Persona; assumptions?: JsonRecord; facts?: JsonRecord;
  calculation?: JsonRecord; dashboard?: JsonRecord; insights?: unknown[]; answers?: unknown[];
  [key: string]: unknown;
};
export type Report = {
  id: string; version: number; title: string; type: string; status: string; contentHash: string;
  evidence?: unknown[]; unresolved?: unknown[]; [key: string]: unknown;
};
export type { Product, Project, Role };
