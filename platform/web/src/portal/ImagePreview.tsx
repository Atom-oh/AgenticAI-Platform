import { useEffect, useMemo, useRef, useState } from 'react';
import { imageDocument, imageSource } from './image-source';

function ImageFrame({ image, name }: { image: NonNullable<ReturnType<typeof imageSource>>; name: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [nonce] = useState(() => crypto.randomUUID().replaceAll('-', ''));
  const [status, setStatus] = useState('loading');
  const html = useMemo(() => imageDocument(image, window.location.origin, nonce), [image.src, image.alt, nonce]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== 'null' ||
          event.data?.type !== 'portal-image-status' || event.data.nonce !== nonce) return;
      if (['ready', 'error'].includes(event.data.status)) setStatus(event.data.status);
    };
    window.addEventListener('message', receive);
    const timer = window.setTimeout(() => setStatus(value => value === 'loading' ? 'error' : value), 15000);
    return () => { window.removeEventListener('message', receive); window.clearTimeout(timer); };
  }, [nonce]);
  return <section className="portal-image" aria-label="반입 이미지 미리보기">
    <h3>반입 이미지</h3>
    {status === 'error' && <p className="portal-error" role="alert">이미지를 표시하지 못했습니다. 원본 파일 형식과 연결을 확인해 주세요.</p>}
    {status !== 'error' && <iframe ref={frame} title={`${name} 반입 이미지`} srcDoc={html} sandbox="allow-scripts" referrerPolicy="no-referrer"
      onLoad={() => frame.current?.contentWindow?.postMessage({ type: 'portal-image-poll', nonce }, '*')} />}
    {status === 'loading' && <p role="status" className="portal-muted">이미지를 불러오는 중…</p>}
    <p className="portal-muted">연결된 반입 원본을 표시합니다. 시각 비교·실행 검증 결과를 뜻하지 않습니다.</p>
  </section>;
}

export default function ImagePreview({ props, name }: { props: Record<string, unknown>; name: string }) {
  const image = imageSource(props, name);
  return image ? <ImageFrame key={image.src} image={image} name={name} /> : null;
}
