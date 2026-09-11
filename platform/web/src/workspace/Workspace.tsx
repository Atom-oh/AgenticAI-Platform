import { useCallback, useEffect, useRef, useState } from 'react';
import { listAll, messageOf, workspaceClient } from './client';
import IntakePanel from './IntakePanel';
import RulesPanel from './RulesPanel';
import RunsPanel from './RunsPanel';
import { Notice } from './shared';
import type { Asset, Contract, Run, WorkspaceConfig } from './types';
import './workspace.css';

export default function Workspace() {
  const [step, setStep] = useState<'files' | 'rules' | 'runs'>('files');
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [preferredContract, setPreferredContract] = useState('');
  const [editing, setEditing] = useState({ id: '', dirty: false });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setLoading(true);
    const requests = await Promise.allSettled([
      workspaceClient.get<WorkspaceConfig>('/config', abort.signal),
      listAll<Asset>(workspaceClient, 'assets', abort.signal),
      listAll<Contract>(workspaceClient, 'contracts', abort.signal),
      listAll<Run>(workspaceClient, 'runs', abort.signal),
    ]);
    if (abort.signal.aborted) return;
    const [configuration, files, rules, results] = requests;
    if (configuration.status === 'fulfilled') setConfig(configuration.value);
    if (files.status === 'fulfilled') { setAssets(files.value); setSelected(previous => previous.filter(id => files.value.some(asset => asset.id === id && !asset.archived))); }
    if (rules.status === 'fulfilled') setContracts(rules.value);
    if (results.status === 'fulfilled') setRuns(results.value);
    const failures = requests.filter((request): request is PromiseRejectedResult => request.status === 'rejected');
    setError(failures.length ? messageOf(failures[0].reason) : ''); setLoading(false);
  }, []);
  useEffect(() => { void refresh(); return () => controller.current?.abort(); }, [refresh]);
  const onEditing = useCallback((id: string, dirty: boolean) => setEditing(previous =>
    previous.id === id && previous.dirty === dirty ? previous : { id, dirty }), []);
  const onApproved = useCallback((contract: Contract) => {
    setPreferredContract(contract.id); setContracts(previous => [contract, ...previous.filter(item => item.id !== contract.id)]);
  }, []);
  return <div className="designer-workspace">
    <header className="ws-header"><div><h1>파일·스킬 작업실</h1><p>내 파일에서 시작해, 확인한 규칙으로 만들고 근거를 보며 수정하세요.</p></div>
      <button onClick={() => void refresh()} disabled={loading}>{loading ? '조회 중…' : '내 작업 새로 조회'}</button></header>
    <nav className="ws-navigation" aria-label="작업실 단계">
      {([['files', '1', '파일 준비'], ['rules', '2', '규칙 확인·승인'], ['runs', '3', '생성·검수·수정']] as const).map(([id, number, label]) =>
        <button key={id} aria-current={step === id ? 'step' : undefined} className={step === id ? 'is-selected' : ''}
          onClick={() => setStep(id)}><span>{number}</span>{label}</button>)}
    </nav>
    {error && <Notice error>{error} <button onClick={() => void refresh()}>다시 조회</button></Notice>}
    {!config ? <div className="ws-empty">{loading ? '작업실을 준비하고 있습니다…' : '작업실 연결을 확인하지 못했습니다. 다시 조회해 주세요.'}</div> : <>
      <div hidden={step !== 'files'}><IntakePanel config={config} assets={assets} selected={selected} onSelected={setSelected}
        refresh={() => void refresh()} onContinue={() => setStep('rules')} /></div>
      <div hidden={step !== 'rules'}><RulesPanel config={config} assets={assets} selected={selected} contracts={contracts}
        refresh={() => void refresh()} onApproved={onApproved} onEditing={onEditing} /></div>
      <div hidden={step !== 'runs'}><RunsPanel config={config} assets={assets} contracts={contracts} runs={runs}
        refresh={() => void refresh()} preferredContract={preferredContract} editing={editing} /></div>
    </>}
  </div>;
}
