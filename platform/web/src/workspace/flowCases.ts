export type ConditionCase = Record<string, boolean>;
export type CaseTransition = { id: string; from: string; to: string; condition?: string | null; navigation?: string };
export type CasePath = { screens: string[]; transitions: string[]; end: 'terminal' | 'dead-end' | 'ambiguous' | 'loop' };

/** Same closed conjunction grammar and boolean assignments as ontology_ux.evaluate. */
export function conditionHolds(expression: string | null | undefined, values: ConditionCase): boolean {
  if (expression == null) return true;
  if (typeof expression !== 'string' || !expression.trim() || expression.length > 300) throw new Error('when grammar');
  let result = true;
  for (const raw of expression.split('&')) {
    const term = raw.trim();
    if (!/^!?cond:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(term)) throw new Error('when grammar');
    const negated = term.startsWith('!');
    const id = term.slice(negated ? 6 : 5);
    // Validate every term, including terms following an already false result.
    if (!Object.prototype.hasOwnProperty.call(values, id) || typeof values[id] !== 'boolean') throw new Error('missing condition');
    result = result && values[id] !== negated;
  }
  return result;
}

/** Bounded read-only projection of design_loop.flow.traverse; return failures explicitly. */
export function casePath(entry: string | undefined, terminals: string[], transitions: CaseTransition[], values: ConditionCase): CasePath {
  if (!entry) return { screens: [], transitions: [], end: 'dead-end' };
  const screens = [entry], taken: string[] = [];
  let cursor = entry;
  for (let step = 0; step < 50; step++) {
    if (terminals.includes(cursor)) return { screens, transitions: taken, end: 'terminal' };
    let enabled: CaseTransition[];
    try {
      enabled = transitions.filter(link => (link.navigation || 'forward') === 'forward' && link.from === cursor)
        .filter(link => conditionHolds(link.condition, values));
    } catch {
      return { screens, transitions: taken, end: 'ambiguous' };
    }
    if (!enabled.length) return { screens, transitions: taken, end: 'dead-end' };
    if (enabled.length !== 1) return { screens, transitions: taken, end: 'ambiguous' };
    taken.push(enabled[0].id); cursor = enabled[0].to;
    if (screens.includes(cursor)) return { screens: [...screens, cursor], transitions: taken, end: 'loop' };
    screens.push(cursor);
  }
  return { screens, transitions: taken, end: 'loop' };
}

export function validCases(value: unknown): value is ConditionCase[] {
  if (!Array.isArray(value) || !value.length || value.length > 64 || !value.every(item =>
    item !== null && typeof item === 'object' && !Array.isArray(item) && Object.keys(item).length <= 6 &&
    Object.entries(item).every(([id, active]) => /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(id) && typeof active === 'boolean'))) return false;
  const names = Object.keys(value[0]).sort();
  return value.length === 2 ** names.length && value.every(item => Object.keys(item).sort().join(',') === names.join(',')) &&
    new Set(value.map(item => names.map(id => item[id] ? '1' : '0').join(''))).size === value.length;
}
