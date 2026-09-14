import { identity } from '../shared';
export interface ToastV1Props { message: string; tone: 'info' | 'success' | 'warning' | 'error'; onDismiss?: () => void }
export function ToastV1({ message, tone, onDismiss }: ToastV1Props) {
  return <div {...identity('Toast', 1)} className="apc-toast" data-tone={tone}
    role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'}>
    <p>{message}</p>{onDismiss && <button className="apc-close" type="button" aria-label="알림 닫기" onClick={onDismiss}>닫기</button>}
  </div>;
}
