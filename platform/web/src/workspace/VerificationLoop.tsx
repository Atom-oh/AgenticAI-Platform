import { useId } from 'react';
import type { Round, Run } from './types';

type State = 'pending' | 'recorded' | 'passed' | 'failed' | 'incomplete' | 'approved';
type Node = { id: 'rules' | 'artifact' | 'browser' | 'evidence' | 'approval'; title: string; state: State; detail: string };
type Props = { run?: Run | null; round?: Round; evidence?: Record<string, unknown> };
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
const object = (value: unknown): Record<string, unknown> | undefined =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;

export function hasExactApproval(run?: Run | null, round?: Round): boolean {
  const approval = object(run?.approval);
  return !!run && !!round && hash(run.contractHash) && hash(round.artifactSha256) &&
    run.rounds.some(item => item.number === round.number && item.artifactSha256 === round.artifactSha256) &&
    approval?.round === round.number && approval.artifactSha256 === round.artifactSha256 &&
    approval.contractVersion === run.contractVersion && approval.contractHash === run.contractHash &&
    typeof approval.actor === 'string' && !!approval.actor && typeof approval.at === 'number' && approval.at > 0;
}

export function deriveVerificationLoop({ run, round, evidence }: Props) {
  const fixed = !!run && !!run.contractId && Number.isInteger(run.contractVersion) && run.contractVersion > 0 && hash(run.contractHash);
  const selected = !!run && !!round && run.rounds.some(item => item.number === round.number && item.artifactSha256 === round.artifactSha256);
  const artifact = selected && round?.hasHtml === true && hash(round.artifactSha256);
  const evidenceMatches = fixed && artifact && round?.hasReport === true && evidence !== undefined &&
    evidence.artifactSha256 === round.artifactSha256 && evidence.contractVersion === run?.contractVersion &&
    evidence.contractHash === run?.contractHash;
  let verification: State = 'pending';
  let verificationLabel = '완료 여부 미확인';
  if (selected && (round?.functionalStatus === 'incomplete' || (round?.checks?.incomplete || 0) > 0)) {
    verification = 'incomplete'; verificationLabel = '미판정 기록';
  } else if (selected && round?.passed === false) {
    verification = 'failed'; verificationLabel = '검수 미통과';
  } else if (!selected && run?.status === 'failed') {
    verification = 'incomplete'; verificationLabel = '실행 완료 미확인';
  }
  if (evidence !== undefined && !evidenceMatches) {
    verification = 'incomplete'; verificationLabel = '근거 일치 확인 필요';
  } else if (evidenceMatches && run && round) {
    const checks = Array.isArray(evidence!.checks) ? evidence!.checks.map(object).filter((item): item is Record<string, unknown> => !!item) : [];
    const rules = run.contract?.rules || [];
    const byId = new Map(checks.map(check => [check.caseId, check]));
    const covered = rules.length > 0 && checks.length === rules.length && byId.size === rules.length &&
      rules.every(rule => byId.has(rule.id));
    const hasIncomplete = !covered || checks.some(check => !['pass', 'fail'].includes(String(check.status)));
    const accessibility = object(evidence!.accessibility)?.status;
    const visual = object(evidence!.visual)?.status;
    const requiredPassed = rules.some(rule => rule.required) && rules.filter(rule => rule.required).every(rule => byId.get(rule.id)?.status === 'pass');
    const visualPassed = visual === 'pass' || (!run.referenceAssetId && visual === 'not-run');
    const blocking = Array.isArray(evidence!.blockingFindings) ? evidence!.blockingFindings.length : null;
    const network = Array.isArray(evidence!.networkRequests) ? evidence!.networkRequests : null;
    const consoleErrors = Array.isArray(evidence!.consoleErrors) ? evidence!.consoleErrors : null;
    if (!network || !consoleErrors || hasIncomplete || evidence!.functionalStatus === 'incomplete' || accessibility === 'incomplete' ||
        visual === 'incomplete' || (run.referenceAssetId && visual === 'not-run')) {
      verification = 'incomplete'; verificationLabel = '미판정 · 근거 보완 필요';
    } else if (network.length > 0 || consoleErrors.length > 0 || evidence!.passed === false || round.passed === false || !requiredPassed ||
        evidence!.functionalStatus === 'fail' || accessibility === 'fail' || visual === 'fail') {
      verification = 'failed'; verificationLabel = '검수 미통과';
    } else if (evidence!.passed === true && round.passed === true && round.hasScreenshot === true &&
        requiredPassed && evidence!.functionalStatus === 'pass' && accessibility === 'pass' && visualPassed && blocking === 0 &&
        network.length === 0 && consoleErrors.length === 0) {
      verification = 'passed'; verificationLabel = '필수 검수 통과';
    } else {
      verification = 'incomplete'; verificationLabel = '검수 근거 확인 필요';
    }
  }
  const approved = hasExactApproval(run, round);
  const nodes: Node[] = [
    { id: 'rules', title: '기준 고정', state: fixed ? 'recorded' : 'pending', detail: fixed ? `규칙 v${run!.contractVersion} 고정` : '고정된 규칙 미확인' },
    { id: 'artifact', title: '시안 준비', state: artifact ? 'recorded' : 'pending',
      detail: `${run ? run.mode === 'verify' || run.model === 'no-inference' ? '반입 HTML' : 'AI 생성' : '반입 HTML / AI 생성'} · ${artifact ? '산출물 저장됨' : '산출물 미확인'}` },
    { id: 'browser', title: '브라우저 검증', state: verification, detail: verificationLabel },
    { id: 'evidence', title: '검수 근거', state: evidenceMatches ? 'recorded' : evidence ? 'incomplete' : 'pending',
      detail: evidenceMatches ? '이 라운드의 저장 근거' : evidence ? '시안·규칙과 근거 불일치' : '근거 내용 미확인' },
    { id: 'approval', title: '사람 승인', state: approved ? 'approved' : 'pending', detail: approved ? '정확한 시안의 승인 기록 있음' : '이 시안의 승인 기록 없음' },
  ];
  return {
    nodes, retry: verification === 'failed' || verification === 'incomplete',
    complete: fixed && artifact && evidenceMatches && verification === 'passed' && approved,
    contractVersion: run?.contractVersion, contractHash: run?.contractHash,
  };
}

export default function VerificationLoop(props: Props) {
  const headingId = useId();
  const state = deriveVerificationLoop(props);
  return <section className="ws-loop" aria-labelledby={headingId} data-complete={state.complete ? 'true' : 'false'}>
    <div className="ws-loop-heading"><h3 id={headingId}>UX 검증 루프</h3>
      <span>{props.round ? `선택한 라운드 ${props.round.number}` : '시안·라운드를 선택하세요'}</span></div>
    <p className="ws-muted">저장된 근거를 기준으로 표시합니다. 진행 위치를 추정하지 않으며, 근거 조회는 사람 승인이 아닙니다.</p>
    <ol className="ws-loop-nodes">
      {state.nodes.map((node, index) => <li key={node.id} data-stage={node.id} data-state={node.state}>
        <div><span className="ws-loop-number">{index + 1}</span><strong>{node.title}</strong>
          {index < state.nodes.length - 1 && <span aria-hidden="true" className="ws-loop-arrow">→</span>}</div>
        <p>{node.detail}</p>
      </li>)}
    </ol>
    {state.retry && <div className="ws-loop-return" role="group" aria-label="수정·재검증 경로"
      data-contract-version={state.contractVersion} data-contract-hash={state.contractHash}>
      <span aria-hidden="true">↶</span><span>실패·미판정</span><span aria-hidden="true">→</span>
      <strong>수정본 생성</strong><span aria-hidden="true">→</span>
      <span>{state.contractVersion ? `규칙 v${state.contractVersion} 유지 · 같은 기준으로 브라우저 재검증` : '고정된 기준 확인 후 재검증'}</span>
    </div>}
  </section>;
}
