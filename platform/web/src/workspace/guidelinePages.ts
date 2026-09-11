import { newRule } from './rules';
import type { EditableContract, Ontology, Rule } from './types';
export type GuidelinePage = { pageId: string; title: string; content: string; required?: boolean };
const props = (node: Record<string, unknown>) => node.props && typeof node.props === 'object' && !Array.isArray(node.props)
  ? node.props as Record<string, unknown> : {};
const whitespace = (value: string) => value.replace(/\s+/gu, ' ').trim();

export function guidelinePages(ontology?: Pick<Ontology, 'nodes' | 'edges'> | null): GuidelinePage[] {
  if (!ontology || !Array.isArray(ontology.nodes) || !Array.isArray(ontology.edges)) return [];
  return ontology.nodes.filter(node => node.label === 'ScreenMeta').flatMap(node => {
    const value = props(node);
    if (typeof value.pageId !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(value.pageId) ||
        typeof value.title !== 'string' || typeof value.content !== 'string') return [];
    const policies = ontology.edges.filter(edge => edge.rel === 'CONSTRAINS' && edge.dst === node.id)
      .map(edge => ontology.nodes.find(item => item.id === edge.src && item.label === 'PolicyRule')).filter(item => !!item);
    const required = policies.some(node => props(node).required === true) ? true :
      policies.length && policies.every(node => props(node).required === false) ? false : undefined;
    return [{ pageId: value.pageId, title: value.title, content: value.content, required }];
  });
}
export function noticeProblems(contract: EditableContract, pages: GuidelinePage[]) {
  return pages.filter(page => page.required === true).flatMap(page => {
    const values = contract.rules.filter(rule => rule.required).flatMap(rule => rule.steps)
      .filter(step => step.action === 'expectText' && step.target === page.pageId && typeof step.value === 'string')
      .map(step => whitespace(step.value as string)).join(' ');
    return whitespace(page.content) && values.includes(whitespace(page.content)) ? [] :
      [`필수 안내 ‘${page.title}’ 화면의 전체 문구를 확인하는 필수 검사가 필요합니다.`];
  });
}
export function noticeRule(page: GuidelinePage, assetId?: string): Rule {
  const characters = [...whitespace(page.content)];
  const parts: string[] = [];
  while (characters.length) {
    let end = Math.min(2000, characters.length);
    if (end < characters.length && characters[end] !== ' ') {
      end = characters.lastIndexOf(' ', end);
      if (end <= 0) throw new Error('안내문에 2,000자를 넘는 연속 문구가 있습니다. 기획 지침의 문구 구분을 확인하세요.');
    }
    parts.push(characters.splice(0, end).join(''));
    while (characters[0] === ' ') characters.shift();
  }
  if (!parts.length || parts.length > 20) throw new Error('이 안내문을 검사 단계로 나눌 수 없습니다. 기획 지침의 분량을 확인하세요.');
  return { ...newRule(), title: [...`${page.title} 전체 안내문 확인`].slice(0, 180).join(''), required: true,
    source: assetId ? { kind: 'explicit', assetId, quote: parts[0] } : { kind: 'manual' },
    steps: parts.map(value => ({ action: 'expectText', target: page.pageId, targetLabel: page.title,
      value, match: 'contains', normalizeWhitespace: true })) };
}
