import { sock, WsEvent } from '../lib';
import { ReviewItem } from './types';

type Request = (action: string, payload: Record<string, unknown>) => Promise<WsEvent>;
export type RoundReport = {
  round: number; itemsComplete: boolean; items: ReviewItem[]; url?: string;
};

/** Publish a report to the caller only after every chunk of one version arrives. */
export async function loadRoundReport(
  jobId: string, round: number, request: Request = (action, payload) => sock.request(action, payload),
): Promise<RoundReport> {
  let offset = 0;
  let version = '';
  const chunks: string[] = [];
  let report: any;
  for (let page = 0; page < 128; page++) {
    const response = await request('studio_round', { jobId, round, offset, reportVersion: version || undefined });
    if (response.error) throw new Error(response.error);
    if (response.round && !chunks.length) {
      report = response.round;
      break;
    }
    if (typeof response.chunk !== 'string' || !response.chunk
        || response.offset !== offset || typeof response.reportVersion !== 'string' || !response.reportVersion
        || (version && response.reportVersion !== version)) {
      throw new Error('검수표의 전송 기록이 일치하지 않습니다. 해당 라운드를 다시 선택하세요.');
    }
    version = response.reportVersion;
    chunks.push(response.chunk);
    if (response.nextOffset === null) {
      report = JSON.parse(chunks.join(''));
      break;
    }
    if (!Number.isInteger(response.nextOffset) || response.nextOffset <= offset) {
      throw new Error('검수표의 이어 읽기 위치가 올바르지 않습니다.');
    }
    offset = response.nextOffset;
  }
  if (!report || report.round !== round || typeof report.itemsComplete !== 'boolean') {
    throw new Error('선택한 라운드의 전체 검수표를 확인하지 못했습니다.');
  }
  const items = report.itemsComplete ? report.items : report.failures;
  if (!Array.isArray(items)) throw new Error('검수 항목을 확인하지 못했습니다.');
  return { ...report, items };
}
