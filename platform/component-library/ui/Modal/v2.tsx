import type { ReactNode } from 'react';
import { ModalFrame } from '../shared';
export interface ModalV2Props {
  open: boolean; title: string; onClose: () => void; children?: ReactNode; size?: 'sm' | 'md' | 'lg';
}
export function ModalV2(props: ModalV2Props) { return <ModalFrame major={2} {...props} />; }
