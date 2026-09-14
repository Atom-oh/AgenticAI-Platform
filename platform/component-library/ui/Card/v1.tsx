import type { ReactNode } from 'react';
import { identity } from '../shared';
export interface CardV1Props { title: string; children: ReactNode }
export function CardV1({ title, children }: CardV1Props) {
  return <section {...identity('Card', 1)} className="apc-card"><h3>{title}</h3><div>{children}</div></section>;
}
