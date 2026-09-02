'use strict';
// 시각 회귀 게이트 — **구조 스냅샷** 비교 (픽셀 비교가 아니다).
// 정적 렌더링 HTML 을 정규화한 sha256 + 태그별 개수. 이전 실행의 snapshot 이 주어지면 변경 여부·태그 개수 diff 를 돌려준다.
const crypto = require('crypto');

const sha = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');

function normalize(html) {
  return String(html || '').replace(/\s+/g, ' ').replace(/>\s+</g, '><').trim();
}

function snapshot(html) {
  const norm = normalize(html);
  const tagCounts = {};
  const re = /<([a-zA-Z][a-zA-Z0-9-]*)\b/g;
  let m; let nodeCount = 0;
  while ((m = re.exec(norm))) {
    const t = m[1].toLowerCase();
    tagCounts[t] = (tagCounts[t] || 0) + 1;
    nodeCount++;
  }
  const text = norm.replace(/<[^>]+>/g, '').trim();
  return { version: 1, hash: sha(norm), nodeCount, tagCounts, textLength: text.length, textHash: sha(text) };
}

const NOTE = '구조 스냅샷 비교 (정규화 HTML 해시 + 태그 개수) — 픽셀·레이아웃 비교 아님';

function compare(current, previous) {
  if (!previous || typeof previous !== 'object' || !previous.hash) {
    return { ok: true, changed: null, baseline: false, note: NOTE + ' · 기준선 없음(첫 실행)', snapshot: current };
  }
  const changed = current.hash !== previous.hash;
  const diff = {};
  const tags = new Set([...Object.keys(current.tagCounts || {}), ...Object.keys(previous.tagCounts || {})]);
  for (const t of tags) {
    const a = (previous.tagCounts || {})[t] || 0;
    const b = (current.tagCounts || {})[t] || 0;
    if (a !== b) diff[t] = { before: a, after: b };
  }
  return {
    ok: true, changed, baseline: true, note: NOTE, snapshot: current, diff,
    textChanged: current.textHash !== previous.textHash,
    nodeCountDelta: (current.nodeCount || 0) - (previous.nodeCount || 0),
  };
}

module.exports = { normalize, snapshot, compare, NOTE };
