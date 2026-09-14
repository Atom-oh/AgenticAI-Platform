import { useId, useRef, useState } from 'react';
import { identity } from '../shared';
export interface FileUploadV1Props { label: string; accept?: string; onFiles: (files: File[]) => void }
export function FileUploadV1({ label, accept, onFiles }: FileUploadV1Props) {
  const id = useId();
  const ref = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  function select(next: File[]) { setFiles(next); onFiles(next); }
  return <div {...identity('FileUpload', 1)} className="apc-upload">
    <label htmlFor={id}>{label}</label>
    <input ref={ref} id={id} type="file" multiple accept={accept} aria-describedby={`${id}-hint`}
      onChange={event => select(Array.from(event.currentTarget.files || []))} />
    <p id={`${id}-hint`} className="apc-hint">로컬 파일 선택만 수행합니다. 서버로 전송하지 않습니다.</p>
    <div role="status">{files.length ? <ul>{files.map((file, i) => <li key={`${file.name}-${i}`}>{file.name}</li>)}</ul> : '선택한 파일 없음'}</div>
    {!!files.length && <button type="button" onClick={() => { if (ref.current) ref.current.value = ''; select([]); }}>파일 선택 지우기</button>}
  </div>;
}
