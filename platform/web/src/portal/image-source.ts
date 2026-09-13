const IMAGE_KEYS = ['imageDataUrl', 'previewImage', 'screenshot', 'thumbnail', 'image', 'src'] as const;
const DATA_IMAGE = /^data:image\/(?:png|jpeg|webp|gif|svg\+xml)(?:;charset=utf-8)?(?:;base64)?,/i;
const STATIC_IMAGE = /^\/(?:samples|studio-samples|studio\/assets)\/[A-Za-z0-9_/-]+\.(?:png|jpe?g|webp|gif|svg)$/i;

export type ImageSource = { src: string; field: string; alt: string } | null;

/** Only imported bytes or explicit static app assets; never follow a Figma/external URL. */
export function imageSource(props: Record<string, unknown>, name: string): ImageSource {
  for (const field of IMAGE_KEYS) {
    const value = props[field];
    if (typeof value !== 'string' || value.length > 2_000_000) continue;
    if (DATA_IMAGE.test(value) || STATIC_IMAGE.test(value)) {
      return { src: value, field, alt: typeof props.alt === 'string' ? props.alt.slice(0, 300) : name.slice(0, 180) };
    }
  }
  return null;
}

const escape = (text: string) => text.replace(/[&<>"']/g, char =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);

/** The opaque frame confines resources inside SVG; its one script reports decode status only. */
export function imageDocument(image: NonNullable<ImageSource>, origin: string, nonce: string): string {
  if (!/^[a-f0-9]{32}$/.test(nonce)) throw new Error('Invalid preview binding');
  const base = new URL(origin);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) throw new Error('Invalid preview origin');
  const src = image.src.startsWith('/') ? new URL(image.src, base.origin).href : image.src;
  const imagePolicy = image.src.startsWith('/') ? `${base.origin}/samples/ ${base.origin}/studio-samples/ ${base.origin}/studio/assets/` : '';
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: ${escape(imagePolicy)}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><style>html,body{margin:0;min-height:100%;background:#f3f6f5}body{display:grid;place-items:center;box-sizing:border-box;padding:16px}img{max-width:100%;height:auto;object-fit:contain}</style></head>
<body><img src="${escape(src)}" alt="${escape(image.alt)}"><script nonce="${nonce}">
const image=document.querySelector('img');
const report=()=>parent.postMessage({type:'portal-image-status',nonce:'${nonce}',status:!image.complete?'loading':image.naturalWidth>0?'ready':'error'},'*');
image.addEventListener('load',report);image.addEventListener('error',report);
addEventListener('message',event=>{if(event.source===parent&&event.data?.type==='portal-image-poll'&&event.data.nonce==='${nonce}')report()});
report();
</script></body></html>`;
}
