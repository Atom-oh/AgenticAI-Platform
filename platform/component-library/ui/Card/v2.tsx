import type { ReactNode } from 'react';
import { identity } from '../shared';
export interface CardV2Props { title: string; children: ReactNode; elevated?: boolean; footer?: ReactNode }
export function CardV2({ title, children, elevated = false, footer }: CardV2Props) {
  return <section {...identity('Card', 2)} className="apc-card" data-elevated={elevated}>
    <h3>{title}</h3><div>{children}</div>{footer != null && <footer>{footer}</footer>}
  </section>;
}
