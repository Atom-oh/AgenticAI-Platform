import { useState, type ComponentType } from 'react';
import { ButtonV1, ButtonV2, ButtonV3, InputV1, InputV2, InputV3, TableV1, ModalV1, DatePickerV1, SelectV1, CardV1, TabsV1, TableV2, ModalV2, DatePickerV2, SelectV2, CardV2, TabsV2, StepperV1, FileUploadV1, BadgeV1, ToastV1 } from './index';

function DemoButtonV1() {
  const [count, setCount] = useState(0);const [variant, setVariant] = useState<'primary'|'ghost'>('primary');
  return <div className="apc-demo"><div><ButtonV1 label="가상 신청 확인" variant={variant} onClick={()=>setCount(n=>n+1)}/><div className="apc-demo-controls"><label>버튼 표현 <select value={variant} onChange={e=>setVariant(e.target.value as typeof variant)}><option value="primary">기본</option><option value="ghost">고스트</option></select></label></div><output>확인 횟수: {count}</output></div></div>;
}

function DemoButtonV2() {
  const [count, setCount] = useState(0);const [disabled, setDisabled] = useState(false);
  return <div className="apc-demo"><div><ButtonV2 label="가상 신청 확인" kind="primary" disabled={disabled} onClick={()=>setCount(n=>n+1)}/><div className="apc-demo-controls"><label><input type="checkbox" checked={disabled} onChange={e=>setDisabled(e.target.checked)}/>버튼 비활성화</label></div><output>확인 횟수: {count}</output></div></div>;
}

function DemoButtonV3() {
  const [count, setCount] = useState(0);const [disabled, setDisabled] = useState(false);
  return <div className="apc-demo"><div><ButtonV3 label="가상 신청 확인" variant="outline" tone="brand" size="md" disabled={disabled} onClick={()=>setCount(n=>n+1)}/><div className="apc-demo-controls"><label><input type="checkbox" checked={disabled} onChange={e=>setDisabled(e.target.checked)}/>버튼 비활성화</label></div><output>확인 횟수: {count}</output></div></div>;
}

function DemoInputV1() {
  const [value, setValue] = useState('');
  return <div className="apc-demo"><div><InputV1 label="가상 신청명" value={value} onChange={setValue} /><div className="apc-demo-controls"></div><output>현재 입력: {value}</output></div></div>;
}

function DemoInputV2() {
  const [value, setValue] = useState('');
  return <div className="apc-demo"><div><InputV2 label="가상 신청명" value={value} onChange={setValue} placeholder="합성 신청명을 입력하세요" type="text"/><div className="apc-demo-controls"></div><output>현재 입력: {value}</output></div></div>;
}

function DemoInputV3() {
  const [value, setValue] = useState('');const [error, setError] = useState(false);
  return <div className="apc-demo"><div><InputV3 label="가상 신청명" value={value} onChange={setValue} placeholder="합성 신청명을 입력하세요" type="text" prefix="신청" error={error ? "입력 내용을 다시 확인하세요." : undefined}/><div className="apc-demo-controls"><label><input type="checkbox" checked={error} onChange={e=>setError(e.target.checked)}/>오류 표시</label></div><output>현재 입력: {value}</output></div></div>;
}

function DemoTableV1() {
  const [empty, setEmpty] = useState(false);
  return <div className="apc-demo"><div><TableV1 columns={[{key:"name",header:"항목"},{key:"amount",header:"금액"}]} rows={empty ? [] : [{name:"가상 A",amount:20000},{name:"가상 B",amount:10000}]} /><label><input type="checkbox" checked={empty} onChange={e=>setEmpty(e.target.checked)}/>빈 목록 보기</label></div></div>;
}

function DemoTableV2() {
  const [empty, setEmpty] = useState(false);
  return <div className="apc-demo"><div><TableV2 columns={[{key:"name",header:"항목"},{key:"amount",header:"금액"}]} rows={empty ? [] : [{name:"가상 A",amount:20000},{name:"가상 B",amount:10000}]} sortable emptyText="등록된 가상 항목이 없습니다."/><label><input type="checkbox" checked={empty} onChange={e=>setEmpty(e.target.checked)}/>빈 목록 보기</label></div></div>;
}

function DemoModalV1() {
  const [open, setOpen] = useState(false);
  return <div className="apc-demo"><div><button type="button" onClick={()=>setOpen(true)}>대화상자 열기</button><ModalV1 open={open} title="가상 신청 확인" onClose={()=>setOpen(false)} ><p>합성 예제입니다. 실제 신청은 실행하지 않습니다.</p><input aria-label="가상 확인 메모"/><button type="button" onClick={()=>setOpen(false)}>확인 후 닫기</button></ModalV1></div></div>;
}

function DemoModalV2() {
  const [open, setOpen] = useState(false);
  return <div className="apc-demo"><div><button type="button" onClick={()=>setOpen(true)}>대화상자 열기</button><ModalV2 open={open} title="가상 신청 확인" onClose={()=>setOpen(false)} size="lg"><p>합성 예제입니다. 실제 신청은 실행하지 않습니다.</p><input aria-label="가상 확인 메모"/><button type="button" onClick={()=>setOpen(false)}>확인 후 닫기</button></ModalV2></div></div>;
}

function DemoDatePickerV1() {
  const [value, setValue] = useState('2026-09-14');
  return <div className="apc-demo"><div><DatePickerV1 label="가상 검토일" value={value} onChange={setValue} /><output>검토일: {value}</output></div></div>;
}

function DemoDatePickerV2() {
  const [value, setValue] = useState('2026-09-14');
  return <div className="apc-demo"><div><DatePickerV2 label="가상 검토일" value={value} onChange={setValue} min="2026-01-01" max="2026-12-31"/><output>검토일: {value}</output></div></div>;
}

function DemoSelectV1() {
  const [value, setValue] = useState('');
  return <div className="apc-demo"><div><SelectV1 label="가상 업무" value={value} onChange={setValue} options={[{value:"savings",label:"저축"},{value:"pension",label:"연금"},{value:"loan",label:"대출"}]} /><output>선택한 업무: {value || "미선택"}</output></div></div>;
}

function DemoSelectV2() {
  const [value, setValue] = useState('');
  return <div className="apc-demo"><div><SelectV2 label="가상 업무" value={value} onChange={setValue} options={[{value:"savings",label:"저축"},{value:"pension",label:"연금"},{value:"loan",label:"대출"}]} searchable placeholder="업무를 선택하세요"/><output>선택한 업무: {value || "미선택"}</output></div></div>;
}

function DemoCardV1() {

  return <div className="apc-demo"><CardV1 title="가상 상품 요약" ><p>합성 상품의 설명을 표시하는 영역입니다.</p></CardV1></div>;
}

function DemoCardV2() {

  return <div className="apc-demo"><CardV2 title="가상 상품 요약" elevated footer={<p>가상 상품 조건을 표시하는 하단 영역입니다.</p>}><p>합성 상품의 설명을 표시하는 영역입니다.</p></CardV2></div>;
}

function DemoTabsV1() {
  const [activeId, setActiveId] = useState('apply');
  return <div className="apc-demo"><div><TabsV1 items={[{id:"apply",label:"신청"},{id:"review",label:"검토"},{id:"done",label:"완료"}]} activeId={activeId} onChange={setActiveId} /><p role="tabpanel" aria-label="선택한 탭 내용">현재 단계: {activeId}</p></div></div>;
}

function DemoTabsV2() {
  const [activeId, setActiveId] = useState('apply');
  return <div className="apc-demo"><div><TabsV2 items={[{id:"apply",label:"신청"},{id:"review",label:"검토"},{id:"done",label:"완료"}]} activeId={activeId} onChange={setActiveId} variant="pill"/><p role="tabpanel" aria-label="선택한 탭 내용">현재 단계: {activeId}</p></div></div>;
}

function DemoStepperV1() {
  const [step, setStep] = useState(0);const steps = [{id:'apply',label:'신청'},{id:'review',label:'검토'},{id:'done',label:'완료'}];
  return <div className="apc-demo"><div><StepperV1 steps={steps} current={steps[step].id}/><button type="button" onClick={()=>setStep(n=>(n+1)%steps.length)}>다음 단계</button></div></div>;
}

function DemoFileUploadV1() {
  const [count, setCount] = useState(0);
  return <div className="apc-demo"><div><FileUploadV1 label="가상 첨부 파일" accept=".txt,.pdf" onFiles={files=>setCount(files.length)}/><output>선택 수: {count}</output></div></div>;
}

function DemoBadgeV1() {
  const [tone, setTone] = useState<'neutral'|'success'|'warning'|'critical'|'info'>('success');
  return <div className="apc-demo"><div><BadgeV1 text="가상 상태" tone={tone}/><label>상태 표현 <select value={tone} onChange={e=>setTone(e.target.value as typeof tone)}>{["neutral","success","warning","critical","info"].map(t=><option key={t} value={t}>{t}</option>)}</select></label></div></div>;
}

function DemoToastV1() {
  const [visible, setVisible] = useState(true);
  return <div className="apc-demo"><div>{visible ? <ToastV1 message="가상 변경 내용을 확인했습니다." tone="success" onDismiss={()=>setVisible(false)}/> : <p>알림을 닫았습니다.</p>}</div></div>;
}

export const DEMOS: Record<string, ComponentType> = {
  'CMP-Button-v1': DemoButtonV1,
  'CMP-Button-v2': DemoButtonV2,
  'CMP-Button-v3': DemoButtonV3,
  'CMP-Input-v1': DemoInputV1,
  'CMP-Input-v2': DemoInputV2,
  'CMP-Input-v3': DemoInputV3,
  'CMP-Table-v1': DemoTableV1,
  'CMP-Table-v2': DemoTableV2,
  'CMP-Modal-v1': DemoModalV1,
  'CMP-Modal-v2': DemoModalV2,
  'CMP-DatePicker-v1': DemoDatePickerV1,
  'CMP-DatePicker-v2': DemoDatePickerV2,
  'CMP-Select-v1': DemoSelectV1,
  'CMP-Select-v2': DemoSelectV2,
  'CMP-Card-v1': DemoCardV1,
  'CMP-Card-v2': DemoCardV2,
  'CMP-Tabs-v1': DemoTabsV1,
  'CMP-Tabs-v2': DemoTabsV2,
  'CMP-Stepper-v1': DemoStepperV1,
  'CMP-FileUpload-v1': DemoFileUploadV1,
  'CMP-Badge-v1': DemoBadgeV1,
  'CMP-Toast-v1': DemoToastV1,
};
