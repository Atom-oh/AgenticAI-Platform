// 경량 마크다운 렌더러 — LLM 출력(S1/S2/보고서/에이전트 채팅) 전용.
// 외부 의존성 없이 escape-후-변환. 지원: 제목(#~####), **굵게**, *기울임*, `코드`,
// ``` 코드블록, 표, 순서/비순서 목록, 인용(>), 구분선(---).
import React, { useMemo } from 'react';

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
}

function render(src: string): string {
  const lines = esc(src).split('\n');
  const out: string[] = [];
  let i = 0;
  let inCode = false;
  const codeBuf: string[] = [];
  while (i < lines.length) {
    const l = lines[i];
    if (l.trim().startsWith('```')) {
      if (inCode) { out.push(`<pre class="mdc"><code>${codeBuf.join('\n')}</code></pre>`); codeBuf.length = 0; }
      inCode = !inCode; i++; continue;
    }
    if (inCode) { codeBuf.push(l); i++; continue; }

    // 표: |a|b| 형태 연속 행
    if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        const cells = lines[i].trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
        if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      if (rows.length) {
        const [h, ...body] = rows;
        out.push('<table class="mdt"><thead><tr>' + h.map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>'
          + body.map(r => '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
      }
      continue;
    }
    // 목록 (연속 블록)
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]\s+/.test(l);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ''))}</li>`); i++;
      }
      out.push(`<${ordered ? 'ol' : 'ul'} class="mdl">${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length + 2} class="mdh">${inline(h[2])}</h${h[1].length + 2}>`); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { out.push('<hr class="mdr">'); i++; continue; }
    if (/^\s*&gt;\s?/.test(l)) { out.push(`<blockquote class="mdq">${inline(l.replace(/^\s*&gt;\s?/, ''))}</blockquote>`); i++; continue; }
    if (l.trim() === '') { out.push('<div class="mds"></div>'); i++; continue; }
    out.push(`<p class="mdp">${inline(l)}</p>`); i++;
  }
  if (inCode && codeBuf.length) out.push(`<pre class="mdc"><code>${codeBuf.join('\n')}</code></pre>`);
  return out.join('');
}

export default function Md({ text, className = '' }: { text: string; className?: string }) {
  const html = useMemo(() => render(text || ''), [text]);
  return <div className={`mdbox ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
