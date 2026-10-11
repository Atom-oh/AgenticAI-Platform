import { useId, useState, type ReactNode } from 'react';

/** Load private comparison artifacts only on request; closing unmounts readers. */
export default function ComparisonTray({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <section className="ws-comparison-tray" aria-label="시안 나란히 비교">
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
      {open ? '나란히 비교 닫기' : '시안 나란히 비교'}
    </button>
    {open && <>
      <p>같은 기준의 시안을 비교하고, 검토할 시안을 선택하세요. 화면이 좁으면 비교 영역을 좌우로 이동할 수 있습니다.</p>
      <div id={id} className="ws-comparison-previews" tabIndex={0} role="region" aria-label="시안 비교 화면">{children}</div>
    </>}
  </section>;
}
