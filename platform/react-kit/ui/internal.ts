import type { ChoiceOption } from './types';

export const VERSION = '1.0.0';
export function choice<T extends string | number>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}
export const text = (value: unknown): string => typeof value === 'string' ? value : '';
export const optionalText = (value: unknown): string | undefined => typeof value === 'string' && value ? value : undefined;
export const finite = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
export const dimension = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : undefined;
export function label(value: unknown, name: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${name}: 비어 있지 않은 문자열이 필요합니다.`);
  return value;
}
export function options(value: unknown): ChoiceOption[] {
  if (!Array.isArray(value)) throw new Error('선택 항목은 배열이어야 합니다.');
  const seen = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== 'object' || typeof item.value !== 'string' || typeof item.label !== 'string' ||
        !item.label.trim() || seen.has(item.value)) throw new Error('선택 항목의 값은 중복되지 않아야 하며 이름이 필요합니다.');
    seen.add(item.value);
  }
  return value;
}
export function requireCallback(value: unknown, name: string): asserts value is (...args: never[]) => unknown {
  if (typeof value !== 'function') throw new Error(`${name}: 변경 처리 함수가 필요합니다.`);
}
