import { identity } from '../shared';
export interface BadgeV1Props { text: string; tone: 'neutral' | 'success' | 'warning' | 'critical' | 'info' }
export function BadgeV1({ text, tone }: BadgeV1Props) {
  return <span {...identity('Badge', 1)} className="apc-badge" data-tone={tone} role="status">{text}</span>;
}
