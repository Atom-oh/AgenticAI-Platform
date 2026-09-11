import { useEffect, useId, useRef, useState } from 'react';
import { listAll, messageOf, readPrivateBlob, resource } from './client';
import { useWorkspaceScope, useWorkspaceClient } from './WorkspaceScope';
import { buildPassed, can, currentGuideline, generationRequest } from './project';
import { BuildGates } from './ReleasePanel';
import { contractProblems, importedHtmlAssets, isOriginalHtmlCheck, roundApprovable, stateLabel } from './rules';
import { JobProgress, ModelPicker, Notice, PrivatePreview, useDownload } from './shared';
import VerificationLoop, { deriveVerificationLoop, hasExactApproval } from './VerificationLoop';
import type { Asset, Batch, Contract, Job, Product, Round, Run, Selection, WorkspaceConfig } from './types';

type Attempt = { id: string; label: string; payload: Record<string, unknown>; job?: Job; run?: Run; error?: string };
const VARIANTS = { balanced: '기본 구성', baseline: '엄격 기준안', layout: '배치 변형', dense: '정보를 한눈에',
  emphasis: '핵심 강조', flow: '진행 흐름 강조', information: '정보 순서 변형' } as const;

export default function RunsPanel({ config, assets, contracts, runs, refresh, preferredContract, editing, onSelection, product }: {
  config: WorkspaceConfig; assets: Asset[]; contracts: Contract[]; runs: Run[]; refresh: () => void;
  preferredContract: string; editing: { id: string; dirty: boolean };
  onSelection?: (selection: Selection) => void; product?: Product;
}) {
  const { client: workspaceClient, role } = useWorkspaceScope();
  const mayGenerate = can(role, 'generate'), mayApprove = can(role, 'approve');
  const [contractId, setContractId] = useState(preferredContract);
  const [contract, setContract] = useState<Contract | null>(null);
  const [model, setModel] = useState(config.defaultModel);
  const [mode, setMode] = useState<'creative' | 'guided'>('creative');
  const [variationCount, setVariationCount] = useState(2);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [batchId, setBatchId] = useState('');
  const [batches, setBatches] = useState<Batch[]>([]);
  const [batchRuns, setBatchRuns] = useState<Run[]>([]);
  const [batchError, setBatchError] = useState('');
  const pendingBatch = useRef<{ fingerprint: string; payload: Record<string, unknown>; batchId?: string } | null>(null);
  const [rounds, setRounds] = useState(3);
  const [reference, setReference] = useState('');
  const [sourceAssetId, setSourceAssetId] = useState('');
  const sourceSelectId = useId();
  const [referencePage, setReferencePage] = useState(1);
  const [tolerance, setTolerance] = useState(0.15);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [error, setError] = useState('');
  const [runId, setRunId] = useState('');
  const [run, setRun] = useState<Run | null>(null);
  const [roundNumber, setRoundNumber] = useState(0);
  const [pageId, setPageId] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [instruction, setInstruction] = useState('');
  const instructionId = useId();
  const [approvedCheck, setApprovedCheck] = useState(false);
  const [variationConsent, setVariationConsent] = useState('');
  const [approving, setApproving] = useState(false);
  const [evidence, setEvidence] = useState<{ key: string; report: Record<string, unknown> } | null>(null);
  const action = useRef<AbortController | null>(null);
  const liveRun = useRef(runId); liveRun.current = runId;
  const currentContractVersion = contracts.find(item => item.id === contractId)?.version;
  const downloading = useDownload();
  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    listAll<Batch>(workspaceClient, 'batches', controller.signal).then(value => {
      if (!controller.signal.aborted) setBatches(value);
    }).catch(reason => { if (!controller.signal.aborted) setBatchError(messageOf(reason)); });
    return () => controller.abort();
  }, [workspaceClient, runs]);
  useEffect(() => {
    const controller = new AbortController(); setBatch(null); setBatchRuns([]); setBatchError('');
    if (batchId) workspaceClient.get<{ batch: Batch; runs: Run[] }>(`/batches/${resource(batchId)}`, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.batch?.id !== batchId || !Array.isArray(value.batch.runIds) || !Array.isArray(value.runs)) throw new Error('저장된 시안 비교를 확인하지 못했습니다.');
      setBatch(value.batch); setBatchRuns(value.runs);
      setRunId(previous => value.batch.runIds.includes(previous) ? previous : value.batch.baselineRunId || value.batch.runIds[0] || '');
    }).catch(reason => { if (!controller.signal.aborted) setBatchError(messageOf(reason)); });
    return () => controller.abort();
  }, [workspaceClient, batchId, refreshKey]);
  useEffect(() => { if (preferredContract) setContractId(preferredContract); }, [preferredContract]);
  useEffect(() => {
    const controller = new AbortController(); setContract(null); setReference(''); setReferencePage(1); setSourceAssetId('');
    if (contractId) workspaceClient.get<{ contract: Contract }>(`/contracts/${resource(contractId)}`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setContract(value.contract); })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [workspaceClient, contractId, currentContractVersion]);
  useEffect(() => {
    const controller = new AbortController(); setRun(previous => previous?.id === runId ? previous : null); setError(''); setApprovedCheck(false);
    if (runId) workspaceClient.get<{ run: Run }>(`/runs/${resource(runId)}`, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        setRun(value.run);
        setRoundNumber(previous => value.run.rounds?.some(round => round.number === previous) ? previous :
          value.run.bestRound || value.run.rounds?.[0]?.number || 0);
      })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [workspaceClient, runId, refreshKey]);
  const selectedRound = run?.rounds?.find(round => round.number === roundNumber);
  const selectedPage = selectedRound?.pageSources?.some(page => page.pageId === pageId) ? pageId : selectedRound?.pageSources?.[0]?.pageId;
  useEffect(() => { onSelection?.(run?.id === runId ? { run, round: selectedRound, pageId: selectedPage } : null); },
    [onSelection, run, runId, selectedRound, selectedPage]);
  const evidenceKey = `${runId}:${roundNumber}:${selectedRound?.artifactSha256 || ''}:${selectedRound?.sourceHash || ''}:${selectedRound?.bundleHash || ''}`;
  const currentEvidence = evidence?.key === evidenceKey ? evidence.report : undefined;
  const verification = deriveVerificationLoop({ run: run?.id === runId ? run : null, round: selectedRound, evidence: currentEvidence });
  const approvalReady = !!selectedRound && roundApprovable(selectedRound) && (run?.outputType !== 'react' ||
    (verification.verificationEligible && selectedRound.hasSource === true && selectedRound.hasDist === true));
  const variationAccepted = variationConsent === evidenceKey;
  useEffect(() => { setApprovedCheck(false); setVariationConsent(''); }, [evidenceKey]);
  const canUseContract = !!contract && contract.status === 'approved' && !contractProblems(contract).length &&
    !(editing.id === contract.id && editing.dirty) &&
    (!product || currentGuideline(product, contract));
  const canGenerate = mayGenerate && canUseContract && config.models.some(option => option.id === model);
  const htmlSources = importedHtmlAssets(assets, contract);
  const selectedHtmlId = htmlSources.some(asset => asset.id === sourceAssetId) ? sourceAssetId : htmlSources.length === 1 ? htmlSources[0].id : '';
  const inspectingOriginal = run ? isOriginalHtmlCheck(run) : false;
  const patchAttempt = (id: string, value: Partial<Attempt>) => setAttempts(previous => previous.map(item => item.id === id ? { ...item, ...value } : item));
  const submit = async (entries: Attempt[]) => {
    if (sendingRef.current) return;
    sendingRef.current = true; setSending(true); setError('');
    const controller = new AbortController(); action.current?.abort(); action.current = controller;
    for (const entry of entries) {
      if (controller.signal.aborted) break;
      patchAttempt(entry.id, { error: '' });
      try {
        const result = await workspaceClient.post<{ job: Job; run: Run }>('/runs', entry.payload, controller.signal);
        if (controller.signal.aborted) break;
        patchAttempt(entry.id, { job: result.job, run: result.run });
        setRunId(previous => previous || result.run.id); refresh();
      } catch (reason) { if (!controller.signal.aborted) patchAttempt(entry.id, { error: messageOf(reason) }); }
    }
    sendingRef.current = false; if (!controller.signal.aborted) setSending(false);
  };
  const generate = async (retryPayload?: Record<string, unknown>) => {
    if (!canGenerate || !contract || sendingRef.current) return;
    const fields = { contractId: contract.id, contractVersion: contract.version, model, maxRounds: rounds, outputType: 'react',
      ...generationRequest(mode, variationCount),
        ...(reference ? { referenceAssetId: reference, referencePage, visualTolerance: tolerance } : {}),
    };
    const fingerprint = JSON.stringify(fields);
    const payload = retryPayload || (pendingBatch.current?.fingerprint === fingerprint ? pendingBatch.current.payload : { ...fields, requestId: crypto.randomUUID() });
    pendingBatch.current = { fingerprint, payload,
      ...(pendingBatch.current?.payload.requestId === payload.requestId ? { batchId: pendingBatch.current?.batchId } : {}) };
    const controller = new AbortController(); action.current?.abort(); action.current = controller;
    sendingRef.current = true; setSending(true); setError('');
    try {
      const value = await workspaceClient.post<{ batch: Batch; runs?: Run[] }>('/batches', payload, controller.signal);
      if (controller.signal.aborted) return;
      if (!value.batch?.id || !Array.isArray(value.batch.runIds)) throw new Error('시안 묶음을 확인하지 못했습니다. 같은 설정으로 다시 조회하세요.');
      pendingBatch.current = { fingerprint, payload, batchId: value.batch.id };
      setBatch(value.batch); setBatchId(value.batch.id); setRunId(value.batch.baselineRunId || value.batch.runIds[0] || ''); setRoundNumber(0); setInstruction('');
      // Each job keeps its own failure state. One failed variant never cancels the others.
      const results = await Promise.allSettled(value.batch.runIds.map(async id => {
        const item = value.runs?.find(item => item.id === id) ||
          (await workspaceClient.get<{ run: Run }>(`/runs/${resource(id)}`, controller.signal)).run;
        if (item.id !== id) throw new Error('시안 기록이 일치하지 않습니다.');
        return item;
      }));
      if (controller.signal.aborted) return;
      const entries: Attempt[] = results.flatMap(result => result.status === 'fulfilled' ? [{
        id: result.value.id, label: result.value.id === value.batch.baselineRunId ? '엄격 기준안' : mode === 'creative' ? '새 UX 시안' : '가이드 변형 시안',
        payload: {}, run: result.value,
        ...(result.value.jobId ? { job: { id: result.value.jobId, task: 'run' as const,
          status: ['completed', 'failed'].includes(result.value.status) ? result.value.status as 'completed' | 'failed' : 'queued' as const } } : {}),
      }] : []);
      setAttempts(previous => [...entries, ...previous.filter(item => !entries.some(entry => entry.id === item.id))]);
      if (results.some(result => result.status === 'rejected')) setError('일부 시안의 진행 기록을 불러오지 못했습니다. 내 작업을 새로 조회하세요.');
      else if (value.batch.status !== 'partial' && !value.batch.slots?.some(slot => slot.error)) pendingBatch.current = null;
      setBatchRuns(results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []));
      refresh();
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { sendingRef.current = false; if (!controller.signal.aborted) setSending(false); }
  };
  const verifyOriginal = () => {
    if (!mayGenerate || !canUseContract || !contract || !selectedHtmlId || sendingRef.current || approving) return;
    const id = crypto.randomUUID();
    const entry: Attempt = { id, label: '원본 HTML 검사', payload: {
      requestId: id, mode: 'verify', sourceAssetId: selectedHtmlId, contractId: contract.id, contractVersion: contract.version, maxRounds: 1,
      ...(reference ? { referenceAssetId: reference, referencePage, visualTolerance: tolerance } : {}),
    } };
    setAttempts(previous => [entry, ...previous]); setRunId(''); setRoundNumber(0); setInstruction(''); void submit([entry]);
  };
  const revise = () => {
    if (!mayGenerate || !run || !selectedRound?.hasHtml || !instruction.trim() || sendingRef.current || !config.models.some(option => option.id === model)) return;
    const id = crypto.randomUUID();
    const entry: Attempt = { id, label: `라운드 ${selectedRound.number} 수정`, payload: {
      requestId: id, contractId: run.contractId, contractVersion: run.contractVersion, mode: 'generate', outputType: 'react', model, maxRounds: rounds,
      variant: run.variant && Object.hasOwn(VARIANTS, run.variant) ? run.variant : 'balanced',
      generationMode: run.generationMode || 'creative',
      baseRunId: run.id, baseRound: selectedRound.number, instruction: instruction.trim(),
      ...(run.referenceAssetId ? { referenceAssetId: run.referenceAssetId, referencePage: run.referencePage ?? 1, visualTolerance: run.visualTolerance ?? 0.15 } : {}),
    } };
    setAttempts(previous => [entry, ...previous]); void submit([entry]);
  };
  const approve = async () => {
    if (!mayApprove || !run || !selectedRound || !approvedCheck || !approvalReady ||
      (verification.needsVariationReview && !variationAccepted) || approving || sending) return;
    const requestedRun = run.id;
    const controller = new AbortController(); action.current?.abort(); action.current = controller;
    setApproving(true); setError('');
    try {
      await workspaceClient.post(`/runs/${resource(run.id)}/approve`, {
        round: selectedRound.number, artifactSha256: selectedRound.artifactSha256, contractVersion: run.contractVersion,
        ...(run.outputType === 'react' ? { sourceHash: selectedRound.sourceHash, bundleHash: selectedRound.bundleHash,
          ...(verification.needsVariationReview ? { acceptVariation: true } : {}) } : {}),
      }, controller.signal);
      if (!controller.signal.aborted && liveRun.current === requestedRun) { setRefreshKey(value => value + 1); refresh(); }
    } catch (reason) { if (!controller.signal.aborted && liveRun.current === requestedRun) setError(messageOf(reason)); }
    finally { if (!controller.signal.aborted) setApproving(false); }
  };
  const jobFinished = () => { refresh(); setRefreshKey(value => value + 1); };
  const selectRun = (id: string) => {
    const item = runs.find(item => item.id === id) || batchRuns.find(item => item.id === id) || attempts.find(item => item.run?.id === id)?.run;
    if (item?.batchId) setBatchId(item.batchId);
    else if (!batch?.runIds.includes(id)) setBatchId('');
    setRunId(id); setRoundNumber(0); setInstruction(''); setApprovedCheck(false);
  };
  const imageReferences = assets.filter(asset => contract?.assetIds.includes(asset.id) && asset.uploadStatus === 'stored' &&
    asset.previews?.some(preview => preview.mime === 'image/png'));
  const referenceAsset = imageReferences.find(asset => asset.id === reference);
  const compared = batch ? [batch.baselineRunId, ...batch.runIds.filter(id => id !== batch.baselineRunId)].filter((id): id is string => !!id) : [];
  const slots = batch?.slots?.length ? [...batch.slots].sort((a, b) => a.index - b.index) : compared.map((id, index) =>
    ({ index, role: id === batch?.baselineRunId ? 'baseline' : 'variation', runId: id, error: undefined }));
  return <div className="ws-runs">
    <section className="ws-section">
      <div className="ws-section-heading"><div><h2>승인한 기준으로 React 시안 만들기</h2><p>고정 컴포넌트와 업무 규칙을 지키며 만듭니다. 반입 HTML 검사는 원본 확인용이며, 동작 검증·시작 화면 비교·사람 승인은 별도입니다. 실제 금융 API는 호출하지 않습니다.</p></div></div>
      {!mayGenerate && <Notice>현재 역할에서는 결과를 조회할 수 있습니다. 생성·수정은 디자인·관리자가 수행합니다.</Notice>}
      <div className="ws-generation-fields">
        <label className="ws-field">사용할 규칙<select aria-label="사용할 규칙" value={contractId} onChange={event => setContractId(event.target.value)} disabled={sending}>
          <option value="">승인한 규칙 선택</option>{contracts.map(item => <option value={item.id} key={item.id}>
            {item.title} · v{item.version} · {stateLabel(item.status)}</option>)}</select></label>
        <ModelPicker models={config.models} value={model} onChange={setModel} disabled={sending} />
        <label className="ws-field">생성 방식<select aria-label="생성 방식" value={mode} onChange={event => setMode(event.target.value as typeof mode)} disabled={sending}>
          <option value="creative">새 UX 만들기 · 필수 지침 준수</option><option value="guided">가이드 준수 비교 · 엄격 기준안 고정</option></select></label>
        {mode === 'guided' && <label className="ws-field">추가 변형 수<select aria-label="추가 변형 수" value={variationCount} onChange={event => setVariationCount(Number(event.target.value))} disabled={sending}>
          {[2, 3, 4, 5].map(count => <option key={count} value={count}>{count}개 · 기준안 포함 총 {count + 1}개</option>)}</select></label>}
      </div>
      <p className="ws-muted">{mode === 'creative' ? '필수 지침 안에서 새로운 UX 시안 1개를 만듭니다.' : `엄격 기준안 1개와 변형 ${variationCount}개를 같은 기준으로 비교합니다. 허용된 배치·강조만 달라지며 컴포넌트와 업무 규칙은 공통입니다.`}</p>
      <details className="ws-generation-options"><summary>반복 횟수와 시작 화면 비교 설정</summary>
        <div className="ws-generation-fields"><label className="ws-field">최대 생성·수정 횟수<select value={rounds} onChange={event => setRounds(Number(event.target.value))}>
          {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}회</option>)}</select></label>
          <label className="ws-field">시작 화면 비교 기준<select value={reference} onChange={event => {
            setReference(event.target.value); setReferencePage(imageReferences.find(asset => asset.id === event.target.value)?.previews?.find(preview => preview.mime === 'image/png')?.page ?? 1);
          }}><option value="">비교 이미지 없음 · 시작 화면 비교 미수행</option>
            {imageReferences.map(asset => <option value={asset.id} key={asset.id}>{asset.name}</option>)}</select></label>
          {reference && <><label className="ws-field">기준 페이지<select value={referencePage} onChange={event => setReferencePage(Number(event.target.value))}>
            {referenceAsset?.previews?.filter(preview => preview.mime === 'image/png').map(preview => <option value={preview.page} key={preview.page}>{preview.page}페이지</option>)}</select></label>
            <label className="ws-field">허용 차이<input type="number" step={0.01} min={0} max={0.5} value={tolerance}
              onChange={event => setTolerance(Math.max(0, Math.min(0.5, Number(event.target.value))))} /></label></>}
        </div><p className="ws-muted">픽셀 비교는 규칙에 지정된 크기의 시작 화면을 기준으로 합니다. 흐름의 모든 화면·상태를 비교했다는 뜻은 아닙니다.</p></details>
      {contract && <p className="ws-muted">고정할 규칙: {contract.title} · 버전 {contract.version} · {contract.rules.length}개 · {contract.viewport.width}×{contract.viewport.height}</p>}
      {product && contract && !currentGuideline(product, contract) &&
        <Notice error>선택한 규칙은 최신 게시 지침과 연결되지 않았습니다. 최신 상품 지침으로 새 규칙을 만들고 승인하세요.</Notice>}
      {!canUseContract && <Notice>미해결 내용이 없는 규칙을 저장·승인한 뒤 선택하세요. 같은 규칙의 미저장 변경이 있으면 검사하거나 생성할 수 없습니다.</Notice>}
      {htmlSources.length > 0 && <div className="ws-original-source">
        <div className="ws-field"><label htmlFor={sourceSelectId}>검사할 반입 HTML</label><select id={sourceSelectId} value={selectedHtmlId}
          disabled={sending || approving} onChange={event => setSourceAssetId(event.target.value)}>
          <option value="">HTML 파일 선택</option>{htmlSources.map(asset => <option value={asset.id} key={asset.id}>{asset.name} · 반입 v{asset.importRevision || 1}</option>)}
        </select></div>
        <p className="ws-muted">승인한 규칙에 포함된 HTML만 선택할 수 있습니다. ‘반입 HTML 검사’는 AI로 코드를 다시 쓰지 않고 한 번 검사합니다. 모델·화면 방향·반복 횟수는 AI 생성에만 적용됩니다.</p>
      </div>}
      <div className="ws-actions"><button className="ws-primary" onClick={() => void generate()} disabled={!canGenerate || sending || approving}>
        {mode === 'creative' ? '시안 1개 만들기' : `기준안 + 변형 ${variationCount}개 만들기`}</button>
        {htmlSources.length > 0 && <button onClick={verifyOriginal} disabled={!mayGenerate || !canUseContract || !selectedHtmlId || sending || approving}>반입 HTML 검사</button>}</div>
      <label className="ws-field">저장된 시안 비교<select aria-label="저장된 시안 비교" value={batchId} onChange={event => {
        setBatchId(event.target.value); setRunId(''); setRoundNumber(0); setInstruction('');
      }}><option value="">비교 묶음 선택</option>
        {[...(batch && !batches.some(item => item.id === batch.id) ? [batch] : []), ...batches]
          .filter(item => !product || contracts.some(contract => contract.id === item.contractId))
          .map((item, index) => <option key={item.id} value={item.id}>{item.mode === 'guided' ? '기준안·변형안' : '새 UX'} · 비교 {index + 1}
            {item.contractVersion ? ` · 규칙 v${item.contractVersion}` : ''}{item.status === 'partial' ? ' · 일부 준비 실패' : ''}</option>)}
      </select></label>
      {batchError && <Notice error>{batchError} <button onClick={() => { refresh(); setRefreshKey(value => value + 1); }}>비교 다시 조회</button></Notice>}
      {batch && <section className="ws-batch" aria-label="같은 기준의 시안 비교"><h3>{batch.mode === 'guided' ? '엄격 기준안 고정 비교' : '새 UX 시안'}</h3>
        {batch.mode === 'guided' && !batch.baselineRunId && <Notice error>엄격 기준안이 아직 준비되지 않았습니다. 다른 변형안을 기준안으로 표시하지 않습니다.</Notice>}
        <div className="ws-comparison">{slots.map((slot, index) => {
          const id = slot.runId, item = runs.find(value => value.id === id) || batchRuns.find(value => value.id === id) || attempts.find(value => value.run?.id === id)?.run;
          return <button key={slot.index} disabled={!id} className={slot.role === 'baseline' ? 'ws-baseline' : ''} aria-pressed={!!id && id === runId} onClick={() => id && selectRun(id)}>
            <strong>{slot.role === 'baseline' ? id ? '엄격 기준안 · 고정' : '엄격 기준안 · 준비 필요' : `시안 ${index + (batch.mode === 'guided' ? 0 : 1)}`}</strong>
            <span>{item ? stateLabel(item.status) : '기록 미확인'}</span>
            {slot.error && <span>{slot.error}</span>}
            <small>{item?.rounds?.length ? `${item.rounds.length}개 라운드 · 개별 근거 확인` : '완료 근거 미확인'}</small></button>;
        })}</div>
        {batch.status === 'partial' && (pendingBatch.current?.batchId === batch.id ? <button disabled={sending || approving || !canGenerate}
          onClick={() => void generate(pendingBatch.current!.payload)}>같은 설정으로 준비 실패한 안 다시 시도</button> :
          <p className="ws-muted">이전 세션의 부분 완료 비교입니다. 준비된 결과는 확인할 수 있으며, 재생성하려면 새 비교를 만들어 주세요.</p>)}
      </section>}
      {!!attempts.length && <div className="ws-attempts">{attempts.map(entry => <div key={entry.id}>
        {entry.job ? <JobProgress job={entry.job} label={entry.label} onComplete={jobFinished} onFailure={jobFinished} /> :
          <p>{entry.label} · {entry.error ? '요청 실패' : '요청 중…'}</p>}
        {entry.run && <button onClick={() => selectRun(entry.run!.id)}>{entry.label} 결과 보기</button>}
        {entry.error && <Notice error>{entry.error} <button disabled={sending || approving} onClick={() => void submit([entry])}>같은 요청 다시 시도</button></Notice>}
      </div>)}</div>}
      {runs.filter(item => item.jobId && ['queued', 'running'].includes(item.status) && !attempts.some(entry => entry.job?.id === item.jobId))
        .map(item => <JobProgress key={item.jobId} job={{ id: item.jobId!, task: 'run', status: item.status as 'queued' | 'running' }}
          label={`${isOriginalHtmlCheck(item) ? '원본 HTML 검사' : contracts.find(value => value.id === item.contractId)?.title || '시안'} 진행 계속 조회`} onComplete={jobFinished} onFailure={jobFinished} />)}
    </section>
    <div className="ws-results-layout">
      <section className="ws-section ws-run-list"><h2>내 시안 이력</h2>
        {!runs.length && <div className="ws-empty">시안 생성 후 이곳에서 결과와 이전 라운드를 확인할 수 있습니다.</div>}
        {runs.map(item => <button className={`ws-run-choice ${item.id === runId ? 'is-selected' : ''}`} key={item.id} onClick={() => selectRun(item.id)}>
          <strong>{contracts.find(value => value.id === item.contractId)?.title || '저장된 규칙의 시안'}</strong>
          <span>규칙 v{item.contractVersion} · {stateLabel(item.status)}</span>
          {isOriginalHtmlCheck(item) ? <small>원본 HTML 검사</small> : <small>{item.baseRunId ? '수정본 · ' : ''}{VARIANTS[item.variant as keyof typeof VARIANTS] || '기본 구성'} · {config.models.find(value => value.id === item.model)?.label || '생성 모델 기록'} · {item.rounds?.length || 0}라운드</small>}
        </button>)}
      </section>
      <section className="ws-section ws-result">
        {error && <Notice error>{error} <button onClick={() => setRefreshKey(value => value + 1)}>결과 다시 조회</button></Notice>}
        {downloading.error && <Notice error>{downloading.error}</Notice>}
        <VerificationLoop run={run?.id === runId ? run : null} round={run?.id === runId ? selectedRound : undefined}
          evidence={run?.id === runId ? currentEvidence : undefined} />
        {!run ? <div className="ws-empty">{runId ? '시안 기록을 불러오고 있습니다…' : '확인할 시안을 선택하세요.'}</div> : <>
          <div className="ws-section-heading"><div><h2>{inspectingOriginal ? '원본 HTML 검사' : '라운드별 결과 확인'}</h2><p>규칙 버전 {run.contractVersion} · {stateLabel(run.status)}</p></div>
            <button onClick={() => setRefreshKey(value => value + 1)}>진행 새로 조회</button></div>
          {inspectingOriginal && <Notice>AI 생성 없이 반입 HTML을 검사한 결과입니다. 검사 대상: {run.assetSnapshots?.find(asset => asset.id === run.sourceAssetId)?.name ||
            assets.find(asset => asset.id === run.sourceAssetId)?.name || '선택한 반입 HTML'}. 원본 파일은 그대로 보관됩니다.</Notice>}
          <div className="ws-rounds" aria-label="시안 라운드">{(run.rounds || []).map(round => <button key={round.number}
            className={roundNumber === round.number ? 'is-selected' : ''} onClick={() => { setRoundNumber(round.number); setInstruction(''); setApprovedCheck(false); }}>
            라운드 {round.number} · {round.passed && (run.outputType !== 'react' || buildPassed(round, run.catalogHash)) ? '필수 검사 통과' : '확인 필요'}{round.number === run.bestRound ? ' · 최종 선택' : ''}</button>)}</div>
          {selectedRound ? <>
            {run.outputType === 'react' && <BuildGates evidence={selectedRound} catalogHash={run.catalogHash} />}
            {!!selectedRound.pageSources?.length && <label className="ws-field">협업할 화면<select aria-label="협업할 화면" value={selectedPage || ''} onChange={event => setPageId(event.target.value)}>
              {selectedRound.pageSources.map((page, index) => <option key={page.pageId} value={page.pageId}>화면 {index + 1}</option>)}</select>
              <span className="ws-muted">의견은 선택한 화면에 연결됩니다. 실행 미리보기와 픽셀 비교의 시작 화면은 바뀌지 않습니다.</span></label>}
            <div className="ws-verification" aria-label="검증과 승인 상태">
              <Verification label="동작 검증" status={roundStatus(selectedRound, run, 'functionalStatus', currentEvidence)} />
              <Verification label="시작 화면 기준 비교" status={roundStatus(selectedRound, run, 'visualStatus', currentEvidence)} />
              <div><strong>사람의 승인</strong><span>{hasExactApproval(run, selectedRound) ? '이 라운드 승인됨' : '미승인'}</span></div>
            </div>
            {selectedRound.visualStatus === 'review-required' && <Notice>
              <strong>시작 화면 변형은 사람의 검토가 필요합니다.</strong>
              <p>필수 동작·컴포넌트 규칙과 별도로 허용된 배치·강조 차이를 검토하세요. 변형 수용은 픽셀 일치 통과를 뜻하지 않습니다.</p>
              {!!currentEvidence?.visual && <p>저장된 픽셀 비교: {stateLabel((currentEvidence.visual as { comparisonStatus?: string }).comparisonStatus)}</p>}
              {hasExactApproval(run, selectedRound) && run.approval?.acceptedVariation && <p>이 소스·번들의 화면 변형을 수용한 승인 기록이 있습니다.</p>}
            </Notice>}
            {run.outputType === 'react' && <div className="ws-actions">
              <button disabled={downloading.busy || selectedRound.hasSource !== true} onClick={() => void downloading.download(
                `/runs/${resource(run.id)}/blob?kind=source&round=${selectedRound.number}`, `react-source-r${selectedRound.number}.zip`)}>React 소스 ZIP 내려받기</button>
              <button disabled={downloading.busy || selectedRound.hasDist !== true} onClick={() => void downloading.download(
                `/runs/${resource(run.id)}/blob?kind=dist&round=${selectedRound.number}`, `react-dist-r${selectedRound.number}.zip`)}>배포 번들 ZIP 내려받기</button>
              {selectedRound.hasCandidate && <details><summary>개발용 생성 후보</summary><p>빌드 실패 라운드에도 후보 코드가 남아 있을 수 있습니다. 검증된 릴리스와는 별개입니다.</p>
                <button disabled={downloading.busy} onClick={() => void downloading.download(
                  `/runs/${resource(run.id)}/blob?kind=candidate&round=${selectedRound.number}`, `react-candidate-r${selectedRound.number}.json`)}>생성 후보 코드 내려받기</button></details>}
            </div>}
            <div className="ws-check-summary" aria-label="선택한 라운드 검수 집계">
              통과 {checkCount(selectedRound.checks?.pass)} · 실패 {checkCount(selectedRound.checks?.fail)} · 미판정 {checkCount(selectedRound.checks?.incomplete)}
            </div>
            {!!run.contextWarnings?.length && <div className="ws-context-warnings"><Notice>
              <strong>자료 적용 범위 · {run.contextWarnings.length}건</strong>
              <p>선택한 파일 중 일부 내용이나 이미지가 AI 입력에서 제한되었습니다. 검수 통과와 자료 전체 반영은 별개입니다.</p>
              <ul>{run.contextWarnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
            </Notice></div>}
            <div className="ws-result-columns">
              <div>{selectedRound.hasHtml === true ? <PrivatePreview path={`/runs/${resource(run.id)}/blob?kind=html&round=${selectedRound.number}`}
                title={`${inspectingOriginal ? '원본 HTML 검사' : '생성 시안'} 라운드 ${selectedRound.number}`} executable height={run.contract?.viewport.height || 760} width={run.contract?.viewport.width} />
                : <Notice>이 라운드에는 조회할 HTML 시안이 없습니다.</Notice>}
                <p className="ws-muted">격리된 시뮬레이션입니다. 화면 안의 모의 상태만 바뀌며, 실제 금융 API를 호출하거나 서버 검증 기록을 바꾸지 않습니다.</p>
                <div className="ws-actions"><button disabled={downloading.busy || selectedRound.hasHtml !== true} onClick={() => void downloading.download(
                  `/runs/${resource(run.id)}/blob?kind=html&round=${selectedRound.number}`, `시안-라운드${selectedRound.number}.html`)}>{inspectingOriginal ? '검사 HTML 내려받기' : '이 라운드 HTML 내려받기'}</button>
                  <button disabled={downloading.busy || selectedRound.hasReport !== true} onClick={() => void downloading.download(
                    `/runs/${resource(run.id)}/blob?kind=report&round=${selectedRound.number}`, `검수근거-라운드${selectedRound.number}.json`)}>검수 근거 내려받기</button></div>
              </div>
              <div className="ws-evidence"><RoundEvidence key={evidenceKey} run={run} round={selectedRound}
                onLoaded={report => setEvidence({ key: evidenceKey, report })} />
                {!!selectedRound.blockingFindings?.length && <Notice error><strong>수정이 필요한 내용</strong>
                  <ul>{selectedRound.blockingFindings.map((finding, index) => <li key={index}>{findingText(finding)}</li>)}</ul></Notice>}
                <div className="ws-field"><label htmlFor={instructionId}>라운드 {selectedRound.number} 수정 지시</label><textarea id={instructionId} rows={4} maxLength={4000} value={instruction}
                  placeholder="예: 다음 버튼을 누르면 입력 금액을 유지하고, 미동의 상태에서는 진행을 막아 주세요."
                  onChange={event => setInstruction(event.target.value)} /></div>
                <button className="ws-primary" onClick={revise} disabled={!mayGenerate || sending || approving || selectedRound.hasHtml !== true || !instruction.trim() ||
                  !config.models.some(option => option.id === model) || (editing.id === run.contractId && editing.dirty)}>
                  {inspectingOriginal ? `AI로 라운드 ${selectedRound.number} 수정본 생성·재검수` : `보고 있는 라운드 ${selectedRound.number} 수정·재검수`}</button>
                <p className="ws-muted">원본 라운드와 규칙 버전 {run.contractVersion}을 고정해 새 시안을 만듭니다. 기존 시안은 유지됩니다.</p>
              </div>
            </div>
            <div className="ws-approval"><label className="ws-check"><input type="checkbox" checked={approvedCheck}
              disabled={!mayApprove || !approvalReady || approving} onChange={event => setApprovedCheck(event.target.checked)} />
              이 라운드의 동작·시작 화면 비교 범위와 근거를 확인했습니다</label>
              {verification.needsVariationReview && <label className="ws-check"><input type="checkbox" checked={variationAccepted}
                disabled={!mayApprove || !approvalReady || approving} onChange={event => setVariationConsent(event.target.checked ? evidenceKey : '')} />
                허용된 배치·강조 변형과 시작 화면 차이를 확인하고 수용합니다</label>}
              <button onClick={() => void approve()} disabled={!mayApprove || !approvalReady || !approvedCheck ||
                (verification.needsVariationReview && !variationAccepted) || approving || sending}>
                라운드 {selectedRound.number} 시안 승인</button>
              {!roundApprovable(selectedRound) && <p>필수 검사 통과와 HTML·검수 기록·실행 화면 근거가 모두 있어야 승인할 수 있습니다.</p>}</div>
          </> : <Notice>아직 검수가 끝난 라운드가 없습니다. 작업 진행을 확인하거나 결과를 다시 조회하세요.</Notice>}
        </>}
      </section>
    </div>
  </div>;
}

function roundStatus(round: Round, run: Run, field: 'functionalStatus' | 'visualStatus', evidence?: Record<string, unknown>) {
  const reported = field === 'functionalStatus' ? evidence?.functionalStatus :
    (evidence?.visual as { status?: unknown } | undefined)?.status;
  return (typeof reported === 'string' ? reported : undefined) || round[field] ||
    (run.bestRound === round.number ? run[field] : undefined);
}
function checkCount(value?: number) {
  return value !== undefined && Number.isSafeInteger(value) && value >= 0 ? value : '미확인';
}
function Verification({ label, status }: { label: string; status?: string }) {
  return <div><strong>{label}</strong><span className={['passed', 'pass', 'verified'].includes(status || '') ? 'ws-good' : ''}>{stateLabel(status)}</span></div>;
}
function findingText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '검수 기록의 상세 근거를 확인하세요.';
  const item = value as Record<string, unknown>;
  return String(item.message || item.title || item.error || item.description || item.reason || '검수 기록의 상세 근거를 확인하세요.');
}
function RoundEvidence({ run, round, onLoaded }: { run: Run; round: Round; onLoaded: (report: Record<string, unknown>) => void }) {
  const workspaceClient = useWorkspaceClient();
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [image, setImage] = useState<'screenshot' | 'diff' | ''>('');
  useEffect(() => {
    const controller = new AbortController(); setReport(null); setError('');
    if (round.hasReport !== true) return () => controller.abort();
    readPrivateBlob(workspaceClient, `/runs/${resource(run.id)}/blob?kind=report&round=${round.number}`, controller.signal)
      .then(async value => {
        const data = JSON.parse(await value.blob.text());
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('검수 근거의 형식을 확인하지 못했습니다.');
        if (!controller.signal.aborted) { setReport(data); onLoaded(data); }
      }).catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [workspaceClient, run.id, round.number, round.hasReport, retry]);
  const findings = report ? ['rules', 'results', 'checks', 'findings', 'blockingFindings'].flatMap(key =>
    Array.isArray(report[key]) ? report[key] as unknown[] : []) : [];
  return <div><h3>이 라운드의 검수 근거</h3>
    {round.hasReport !== true ? <Notice>이 라운드에는 조회할 검수 기록이 없습니다.</Notice>
      : error ? <Notice error>{error} <button onClick={() => setRetry(value => value + 1)}>근거 다시 조회</button></Notice>
      : !report ? <p role="status">근거를 불러오고 있습니다…</p>
        : <><p>저장된 검수 기록을 불러왔습니다. 결과 없는 검사를 통과로 표시하지 않습니다.</p>
          {findings.length > 0 && <ul className="ws-findings">{findings.map((finding, index) => {
            const item = typeof finding === 'object' && finding ? finding as Record<string, unknown> : null;
            return <li key={index}><strong>{findingText(finding)}</strong>
              {item && <span>{typeof item.status === 'string' ? stateLabel(item.status) : item.passed === true ? '통과' : item.passed === false ? '실패' : '미판정'}</span>}
              {item?.evidence != null && <p>{typeof item.evidence === 'string' ? item.evidence : '상세 기록에서 근거를 확인하세요.'}</p>}</li>;
          })}</ul>}
          <details><summary>검수 상세 기록</summary><pre className="ws-text">{JSON.stringify(report, null, 2)}</pre></details></>}
    <div className="ws-actions">
      {round.hasScreenshot === true && <button onClick={() => setImage(image === 'screenshot' ? '' : 'screenshot')}>검수 시작 화면</button>}
      {round.hasDiff === true && <button onClick={() => setImage(image === 'diff' ? '' : 'diff')}>시작 화면 차이 보기</button>}
    </div>
    {image && (image === 'screenshot' ? round.hasScreenshot === true : round.hasDiff === true) &&
      <PrivatePreview path={`/runs/${resource(run.id)}/blob?kind=${image}&round=${round.number}`} title={image === 'diff' ? '시작 화면 기준 이미지와 차이' : '검수 시작 화면'} />}
  </div>;
}
