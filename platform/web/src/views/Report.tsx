// F7 보고서 생성 — Reader / 인계 / Writer 3열 시각화 + 프롬프트 인젝션 시연 (SPEC §5 F7, §6.1 화면 8)
// Reader(외부 콘텐츠 전용, 내부 도구 권한 없음) | 인계(구조화 JSON만) | Writer(내부 문서 접근, 외부 원문 미접근)
import { useEffect, useState } from 'react';
import { sock, WsEvent } from '../lib';

const SAMPLE_PATH = '/samples/vendor-news.html';
const STEP_ORDER = ['reader_fetch', 'reader_summarize', 'handoff', 'writer_search', 'writer_generate'];

type Stage = WsEvent & { step: string; status?: string };
type Denied = {
  tool: string; args?: Record<string, string>; origin?: string; functionName?: string;
  error?: string; message?: string; requestId?: string; allowed?: boolean; discardedBytes?: number;
  injectionLike?: boolean;
};

const ORIGIN_LABEL: Record<string, string> = {
  probe: '코드 자가 검증 (Reader 가 직접 invoke 시도 — 모델 판단과 무관)',
  model: '모델 도구 요청 (프롬프트는 도구를 언급하지 않음 — 모델이 스스로 요청)',
};

// Writer 보고서 전달 방식 — 오케스트레이터가 실측해 보내는 delivery 값 그대로 표기한다.
const DELIVERY_LABEL: Record<string, string> = {
  stream: '토큰 스트리밍 (Writer → DynamoDB 릴레이 → WsFn → WebSocket)',
  partial_fallback: '일부 스트리밍 후 릴레이 실패 — 잔여분 단일 이벤트 폴백',
  single_event: '단일 이벤트 (스트리밍 미전송 — 폴백)',
};

function Dot({ color }: { color: string }) {
  return <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />;
}

function Running({ label }: { label: string }) {
  return <span className="text-xs text-slate-400">{label} <span className="blink">▌</span></span>;
}

function Tokens({ u }: { u?: Record<string, number> }) {
  if (!u || (!u.inputTokens && !u.outputTokens)) return null;
  return <span className="text-[11px] text-slate-500">실측 토큰 in {u.inputTokens ?? 0} / out {u.outputTokens ?? 0}</span>;
}

/* ---------------- Reader 열 ---------------- */
function ReaderColumn({ fetch, summ, running }: { fetch?: Stage; summ?: Stage; running: boolean }) {
  const summary = summ?.summary || {};
  const denied: Denied[] = summ?.deniedAttempts || [];
  const realDenied = denied.filter(d => d.error);
  const allowed = denied.filter(d => d.allowed);
  const injected: string[] = summary.injectedInstructions || [];
  return (
    <div className="panel p-4 flex flex-col gap-3" style={{ borderTop: '3px solid var(--cloud)' }}>
      <div>
        <div className="flex items-center gap-2"><Dot color="var(--cloud)" /><b>Reader</b>
          <span className="chip text-[10px]" style={{ borderColor: 'var(--cloud)' }}>클라우드 · 외부 콘텐츠 전용</span></div>
        <div className="text-[11px] text-slate-500 mt-1">역할: <span className="font-mono">ReaderRole</span> — 내부 도구 invoke 권한 <b className="text-rose-300">없음</b> (IAM 역할로 실제 분리). Bedrock invoke 만 허용.</div>
      </div>

      {/* ① fetch */}
      <div className="rounded-lg border border-slate-800 p-3">
        <div className="text-xs font-semibold flex items-center gap-2">① 외부 페이지 읽기
          {fetch?.status === 'running' && <Running label="가져오는 중" />}
          {!fetch && running && <span className="text-[11px] text-slate-600">대기</span>}
        </div>
        {fetch?.status === 'done' && (
          <div className="text-xs text-slate-300 mt-1 space-y-1">
            <div>URL <span className="font-mono text-slate-400 break-all">{fetch.url}</span></div>
            <div>추출 텍스트 <b>{Number(fetch.chars).toLocaleString()}</b>자
              {fetch.fetch?.bytes != null && <> · 수신 {Number(fetch.fetch.bytes).toLocaleString()} bytes{fetch.fetch.truncated && ' (200KB 상한 절단)'}</>}
              {fetch.fetch?.hostListed === false && <span className="text-amber-300"> · 허용 목록 미설정(공개 https 허용, 로그 기록)</span>}
            </div>
            <div className="text-slate-500">script/style 은 제거하고 숨김 텍스트·HTML 주석은 남겨 Reader 가 실제로 읽은 그대로 보여준다.</div>
            {fetch.textExcerpt && (
              <details><summary className="cursor-pointer text-slate-400">Reader 가 읽은 transcript (앞 1,500자)</summary>
                <pre className="mt-1 bg-slate-950 rounded p-2 overflow-x-auto whitespace-pre-wrap text-[11px] max-h-60 overflow-y-auto">{fetch.textExcerpt}</pre>
              </details>
            )}
          </div>
        )}
      </div>

      {/* ② summarize */}
      <div className="rounded-lg border border-slate-800 p-3">
        <div className="text-xs font-semibold flex items-center gap-2 flex-wrap">② 요약 + 인젠션 판정
          {fetch?.status === 'done' && !summ && <Running label="Bedrock 요약 중" />}
          {summ && (summary.injectionDetected
            ? <span className="chip text-[10px] text-rose-300 border-rose-700">⚠ 인젝션 감지</span>
            : <span className="chip text-[10px] text-emerald-300">인젠션 미감지</span>)}
          {summ && <Tokens u={summ.usage} />}
        </div>
        {summ && (
          <div className="text-xs mt-2 space-y-2">
            {summary.signals && (
              <div className="text-slate-500">판정 신호: 모델 {summary.signals.model ? '감지' : '미감지'} · 휴리스틱 {summary.signals.heuristic ? '감지' : '미감지'}
                {summ.rounds != null && <> · 대화 라운드 {summ.rounds}</>}</div>
            )}
            {injected.length > 0 && (
              <div>
                <div className="text-slate-400 mb-1">본문에서 발견된 지시문 (데이터로만 취급, 따르지 않음)</div>
                {injected.map((s, i) => (
                  <div key={i} className="rounded border border-rose-800 bg-rose-950/40 text-rose-200 px-2 py-1 mb-1 break-words">{s}</div>
                ))}
              </div>
            )}
            <div>
              <div className="text-slate-400 mb-1">내부 도구 호출 시도 <b className="text-slate-200">{realDenied.length}</b>건 — 모두 거부됨
                <span className="ml-2 text-rose-300 font-semibold">IAM이 막았다</span></div>
              {realDenied.length === 0 && <div className="text-slate-600">시도 기록 없음</div>}
              {realDenied.map((d, i) => (
                <div key={i} className="rounded border border-slate-700 bg-slate-950 p-2 mb-1 font-mono text-[11px] break-words">
                  <div className="text-slate-500">{ORIGIN_LABEL[d.origin || ''] || d.origin} · {d.tool}({JSON.stringify(d.args || {})})
                    {d.origin === 'model' && (d.injectionLike
                      ? <span className="ml-2 text-rose-300">지시문 유사 질의</span>
                      : <span className="ml-2 text-slate-400">일반 질의</span>)}
                  </div>
                  <div><span className="text-slate-500">FunctionName</span> {d.functionName}</div>
                  <div><span className="text-rose-400 font-bold">{d.error}</span>
                    {d.requestId && <span className="text-slate-600"> · requestId {d.requestId}</span>}</div>
                  <div className="text-rose-200/90">{d.message}</div>
                </div>
              ))}
              {allowed.length > 0 && (
                <div className="rounded border border-rose-600 bg-rose-950/60 text-rose-200 p-2 text-[11px]">
                  ⚠ 권한 분리 실패: Reader 역할이 내부 도구 호출에 <b>성공</b>했습니다 ({allowed.length}건, 결과 폐기). ReaderFn IAM 정책에서 lambda:InvokeFunction 을 제거하세요.
                </div>
              )}
            </div>
            {summ.toolCalls?.length > 0 && (
              <div className="text-slate-500">모델 toolUse 요청 {summ.toolCalls.length}건 (라운드 {summ.toolCalls.map((t: any) => t.round).join(', ')})
                — 지시문 유사 {summ.toolCalls.filter((t: any) => t.injectionLike).length}건 · 일반 {summ.toolCalls.filter((t: any) => !t.injectionLike).length}건. args 는 위 기록에 그대로 표시.</div>
            )}
            {summ.toolCalls?.length === 0 && realDenied.length > 0 && (
              <div className="text-slate-500">모델은 도구를 요청하지 않았다 — 위 거부 기록은 코드 자가 검증(probe) 실측이다.</div>
            )}
            <div className="text-slate-500">사실 {summary.facts?.length ?? 0}건 · 엔터티 {summary.entities?.length ?? 0} · 주제 {summary.topics?.length ?? 0}
              {summary.fallback && <span className="text-amber-300"> · 폴백: {summary.fallback}</span>}</div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- 인계 열 ---------------- */
function HandoffColumn({ h, waiting }: { h?: Stage; waiting: boolean }) {
  return (
    <div className="panel p-4 flex flex-col gap-3" style={{ borderTop: '3px solid #64748b' }}>
      <div>
        <div className="flex items-center gap-2"><Dot color="#94a3b8" /><b>인계</b>
          <span className="chip text-[10px]">구조화 JSON만</span></div>
        <div className="text-[11px] text-slate-500 mt-1">외부 원문·transcript·도구 응답은 여기서 끊긴다. 스키마 키만 통과.</div>
      </div>
      <div className="text-center text-slate-600 text-lg leading-none">→</div>
      {!h && waiting && <Running label="Reader 완료 대기" />}
      {h && (
        <div className="text-xs space-y-2">
          <div className="text-slate-400">{h.bytes?.toLocaleString()} bytes · 키 {h.keys?.length}개</div>
          <div className="flex flex-wrap gap-1">{(h.keys || []).map((k: string) => <span key={k} className="chip text-[10px] font-mono">{k}</span>)}</div>
          {h.piiScan && (
            <div>경계 페이로드 개인식별자(규칙 스캔 실측): <b className={h.piiScan.count === 0 ? 'text-emerald-400' : 'text-rose-400'}>{h.piiScan.count}건</b></div>
          )}
          <details open><summary className="cursor-pointer text-slate-400">인계 JSON</summary>
            <pre className="mt-1 bg-slate-950 rounded p-2 overflow-x-auto whitespace-pre-wrap text-[11px] max-h-96 overflow-y-auto">{JSON.stringify(h.summary, null, 1)}</pre>
          </details>
          <div className="text-slate-500">{h.note}</div>
        </div>
      )}
      <div className="text-center text-slate-600 text-lg leading-none">→</div>
    </div>
  );
}

/* ---------------- Writer 열 ---------------- */
function WriterColumn({ search, gen, report, done, running }: { search?: Stage; gen?: Stage; report: string; done: WsEvent | null; running: boolean }) {
  const docs: any[] = search?.internalDocs || [];
  return (
    <div className="panel p-4 flex flex-col gap-3" style={{ borderTop: '3px solid var(--cloud)' }}>
      <div>
        <div className="flex items-center gap-2"><Dot color="var(--cloud)" /><b>Writer</b>
          <span className="chip text-[10px]" style={{ borderColor: 'var(--cloud)' }}>클라우드 · 격리 서브넷</span></div>
        <div className="text-[11px] text-slate-500 mt-1">역할: <span className="font-mono">WriterRole</span> — 내부 문서 검색 <b className="text-emerald-300">허용</b>, 외부 원문 <b className="text-rose-300">미접근</b> (URL fetch 코드 자체가 없음 — 테스트로 검증).</div>
      </div>

      {/* ③ 내부 검색 — 내부 자산(앰버) */}
      <div className="rounded-lg p-3" style={{ border: '1px solid var(--onprem)', background: 'rgba(251,191,36,.05)' }}>
        <div className="text-xs font-semibold flex items-center gap-2 flex-wrap"><Dot color="var(--onprem)" />③ 내부 관련 문서 검색
          <span className="chip text-[10px]" style={{ borderColor: 'var(--onprem)', color: 'var(--onprem)' }}>내부 자산</span>
          {search?.status === 'running' && <Running label="검색 중" />}
        </div>
        {search?.status === 'done' && (
          <div className="text-xs mt-2 space-y-1">
            {search.searchQueries?.length > 0 && (
              <div className="text-slate-500">질의: {search.searchQueries.map((q: any) => `${q.query}(${q.count})`).join(' · ')}</div>
            )}
            {search.searchError && <div className="text-rose-300">검색 오류: {search.searchError}</div>}
            {docs.length === 0 && !search.searchError && <div className="text-slate-500">일치하는 내부 문서 없음</div>}
            {docs.map((d, i) => (
              <div key={i} className="flex gap-2 items-baseline">
                <span className="font-mono text-[11px]" style={{ color: 'var(--onprem)' }}>{d.docId}</span>
                <span className="text-slate-200">{d.title}</span>
                <span className="text-slate-500 text-[11px]">{d.type} · {d.dept}{d.score != null && ` · ${d.score}`}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ④ 보고서 — Writer Lambda 가 토큰을 WebSocket 으로 직접 스트리밍 (실패 시 폴백을 그대로 표기) */}
      <div className="rounded-lg border border-slate-800 p-3 flex-1">
        <div className="text-xs font-semibold flex items-center gap-2 flex-wrap">④ 보고서 생성
          {gen?.status === 'running' && (
            <>
              <span className="chip text-[10px] text-slate-400">{gen.delivery === 'stream' ? 'Bedrock 스트리밍 — Writer 릴레이를 WsFn 이 폴링해 전달' : '단발 생성 (릴레이 테이블 미설정)'}</span>
              <Running label={report ? '수신 중' : '첫 토큰 대기'} />
            </>
          )}
          {gen?.status === 'done' && (
            <>
              <span className={`chip text-[10px] ${gen.delivery === 'stream' ? 'text-emerald-300' : 'text-amber-300 border-amber-700'}`}>
                {DELIVERY_LABEL[gen.delivery] || gen.delivery || '전달 방식 미기록'}
              </span>
              {gen.delivery === 'stream' && gen.tokenEvents != null && (
                <span className="text-[11px] text-slate-500">{gen.tokenEvents}건 이벤트{gen.firstTokenMs != null && ` · 첫 토큰 ${gen.firstTokenMs}ms (생성 단계 시작 기준, 실측)`}</span>
              )}
              <Tokens u={gen.usage} />
            </>
          )}
          {search?.status === 'done' && !gen && <span className="text-[11px] text-slate-600">대기</span>}
        </div>
        {gen?.status === 'done' && (gen.streamError || gen.relayError) && (
          <div className="text-[11px] text-amber-300 mt-1 break-words">스트리밍 릴레이 실패 → 폴백:
            {gen.streamError && <> Writer 쓰기 <span className="font-mono">{gen.streamError}</span></>}
            {gen.relayError && <> WsFn 읽기 <span className="font-mono">{gen.relayError}</span></>}
            {gen.streamedChars != null && <> (스트리밍 전달 {gen.streamedChars} / 전체 {gen.chars} 자)</>}. STREAM_TABLE·DynamoDB 게이트웨이 엔드포인트·IAM 을 확인하세요.</div>
        )}
        {report
          ? <div className="md text-slate-200 mt-2 text-[13px]">{report}{gen?.status === 'running' && <span className="blink">▌</span>}</div>
          : running && <div className="text-slate-600 text-xs mt-2">…</div>}
        {done && !done.error && (
          <div className="text-[11px] text-slate-500 mt-3">
            traceId {done.traceId} · {done.elapsedMs}ms · 거부된 내부 도구 호출 {done.deniedAttempts}건 · 내부 문서 {done.internalDocs?.length ?? 0}건 · 전달 {done.delivery || '-'} — Two-Plane 뷰에 F7 계측 기록됨
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- 뷰 ---------------- */
export default function ReportView() {
  const [url, setUrl] = useState(() => `${location.origin}${SAMPLE_PATH}`);
  const [sample, setSample] = useState<WsEvent | null>(null);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<Record<string, Stage>>({});
  const [report, setReport] = useState('');
  const [done, setDone] = useState<WsEvent | null>(null);
  const [cached, setCached] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => { sock.request('report_sample').then(setSample).catch(() => {}); }, []);

  const deployed = sample?.deployed;
  const undeployed = deployed && (!deployed.reader || !deployed.writer);

  const run = async (u?: string) => {
    const uu = (u ?? url).trim();
    if (running || !uu) return;
    if (u) setUrl(u);
    setRunning(true); setErr(''); setStages({}); setReport(''); setDone(null); setCached(false);
    try {
      await sock.run('report', { url: uu }, (e) => {
        if (e.cached) setCached(true);
        if (e.type === 'cache.replay') { setStages({}); setReport(''); setDone(null); }
        if (e.type === 'report.stage') setStages(s => ({ ...s, [e.step]: e as Stage }));
        if (e.type === 'report.token') setReport(t => t + (e.t || ''));
        if (e.type === 'report.done') {
          setDone(e); if (e.error) setErr(e.error);
          // 안전망: 토큰 이벤트가 하나도 닿지 않았는데 .done 에 전문이 있으면 그것을 쓴다 (내용은 동일한 실측 결과)
          if (e.report) setReport(t => t || String(e.report));
        }
      });
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  const st = (k: string) => stages[k];
  const progress = STEP_ORDER.filter(k => stages[k]?.status === 'done').length;

  return (
    <div>
      {/* 실행 바 */}
      <div className="panel p-3 mb-3 flex gap-2 items-center flex-wrap">
        <button className="chip whitespace-nowrap hover:border-sky-500 text-sky-300" onClick={() => run(`${location.origin}${SAMPLE_PATH}`)} disabled={running}>
          샘플 페이지로 보고서 생성
        </button>
        <input className="flex-1 min-w-[280px] px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono"
          value={url} onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && run()} placeholder="https://…" />
        <button onClick={() => run()} disabled={running}
          className="px-5 py-2 rounded-lg bg-sky-500/90 hover:bg-sky-400 text-slate-950 font-semibold text-sm disabled:opacity-40">
          {running ? `진행 중… ${progress}/${STEP_ORDER.length}` : '보고서 생성'}
        </button>
        {cached && <span className="chip text-amber-300 border-amber-600">캐시 응답</span>}
        {deployed && (
          <span className="text-[11px] text-slate-500 flex gap-2">
            {[['Reader', deployed.reader], ['InternalTool', deployed.internalTool], ['Writer', deployed.writer], ['스트리밍 릴레이', deployed.stream]].map(([n, ok]) => (
              <span key={String(n)} className={`chip text-[10px] ${ok ? 'text-emerald-300' : 'text-rose-300 border-rose-800'}`}>{n} {ok ? '배포됨' : '미배포'}</span>
            ))}
          </span>
        )}
      </div>
      {err && <div className="text-rose-400 text-sm mb-3">{err}</div>}
      {undeployed && !err && (
        <div className="text-amber-300 text-xs mb-3">Reader/Writer Lambda 가 아직 배포되지 않았습니다 (미배포). 실행하면 결과 대신 미배포 안내가 표시됩니다 — 흉내 내지 않습니다.</div>
      )}

      {/* 시연 안내 */}
      <div className="panel p-3 mb-4 text-xs flex gap-4 items-start flex-wrap">
        <div className="flex-1 min-w-[320px]">
          <div className="text-slate-400 mb-1">샘플 페이지(<a className="underline text-sky-300" href={SAMPLE_PATH} target="_blank" rel="noreferrer">{SAMPLE_PATH}</a>)에 심어둔 인젝션 지시문
            {sample?.placements && <span className="text-slate-500"> — {sample.placements.join(' · ')}</span>}</div>
          <div className="rounded border border-rose-800 bg-rose-950/40 text-rose-200 px-2 py-1 break-words">
            {sample?.injectedInstruction || '…'}
          </div>
        </div>
        <div className="text-slate-500 max-w-md leading-relaxed">
          코드 자가 검증(probe)이 Reader IAM 역할의 <span className="font-mono">lambda:InvokeFunction</span> 부재를
          <b className="text-rose-300"> AccessDeniedException</b> 으로 실측한다. Reader 시스템 프롬프트는 도구를 언급하지 않으며
          <span className="font-mono"> search_internal_documents</span> 는 노출만 되어 있다 — 모델이 도구를 요청하면 그 args 를 그대로 보여준다.
          지시문을 따른 요청('모두·원문·출력')인지 일반 주제 질의인지 화면에서 판단할 수 있다. Writer 는 인계 JSON 만 받으므로 지시문 원문 자체를 명령으로 볼 수 없다.
        </div>
      </div>

      {/* 3열 */}
      <div className="text-xs text-slate-500 mb-2">
        <span style={{ color: 'var(--cloud)' }}>■ 클라우드(모델 호출)</span> · <span style={{ color: 'var(--onprem)' }}>■ 내부 자산(사내 문서)</span> · <span className="text-slate-400">■ 인계 경계</span>
      </div>
      <div className="grid grid-cols-[1fr_300px_1fr] gap-3 items-start">
        <ReaderColumn fetch={st('reader_fetch')} summ={st('reader_summarize')} running={running} />
        <HandoffColumn h={st('handoff')} waiting={running && !st('handoff')} />
        <WriterColumn search={st('writer_search')} gen={st('writer_generate')} report={report} done={done} running={running} />
      </div>

      <div className="text-[11px] text-slate-600 mt-4">
        미구현: 보고서 저장·내보내기, 임의 URL 의 사이트별 렌더링(JS 실행 없음 — 정적 HTML 만 읽음).
        Reader 의 fetch·요약은 Reader Lambda 1회 invoke 안에서 함께 실행되므로 ①의 완료 표시는 ②와 동시에 도착한다.
        Writer 보고서는 Writer Lambda 가 DynamoDB 릴레이에 쓴 토큰을 WsFn 이 폴링(0.25s)해 스트리밍한다 — 격리 서브넷에서는 WebSocket 관리 API 에 직접 닿을 수 없어 릴레이를 쓴다. 릴레이가 없거나 실패하면 ④에 폴백(단일 이벤트)과 오류가 그대로 표기된다.
        Reader 허용 목록(ALLOWED_SAMPLE_HOSTS)이 설정된 배포에서는 목록 외 URL 이 거부된다.
      </div>
    </div>
  );
}
