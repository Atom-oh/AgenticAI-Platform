import type { ReactNode } from 'react';
import { ModalFrame } from '../shared';
export interface ModalV1Props { open: boolean; title: string; onClose: () => void; children?: ReactNode }
export function ModalV1(props: ModalV1Props) { return <ModalFrame major={1} {...props} />; }
