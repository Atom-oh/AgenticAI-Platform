import { useEffect, useState } from 'react';
import { sock } from '../lib';

export type PrivacyProcessor = 'regex-anonymizer' | 'eks-sllm';
export type PrivacyModel = {
  id: string; label: string; family: string; processor: PrivacyProcessor; modelId: string; revision: string;
  available: boolean; reason?: string;
};
export type ExplanationModel = { id: string; label: string; provider?: string };
type ModelState = {
  status: 'loading' | 'ready' | 'error';
  models: PrivacyModel[];
  explanationModels: ExplanationModel[];
  defaultExplanationModel: string;
  message: string;
};
const LOADING: ModelState = { status: 'loading', models: [], explanationModels: [], defaultExplanationModel: '', message: '' };
const PROCESSORS: readonly string[] = ['regex-anonymizer', 'eks-sllm'];
const nonempty = (value: unknown): value is string => typeof value === 'string' && !!value.trim();

export function usePrivacyModels() {
  const [state, setState] = useState<ModelState>(LOADING);
  const [selected, setSelected] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const response = await sock.request('s2_privacy_models', {});
        if (!active) return;
        if (response.type !== 's2_privacy_models.done' || !Array.isArray(response.models)) {
          throw new Error('프라이버시 처리기 상태를 확인하지 못했습니다.');
        }
        const models: PrivacyModel[] = response.models.map((model: PrivacyModel) => {
          if (!model || !nonempty(model.id) || !nonempty(model.label) || !nonempty(model.family)
            || !PROCESSORS.includes(model.processor)) {
            throw new Error('프라이버시 처리기 목록 형식이 올바르지 않습니다.');
          }
          const configured = nonempty(model.modelId) && nonempty(model.revision);
          return {
            id: model.id, label: model.label, family: model.family, processor: model.processor,
            modelId: configured ? model.modelId : '', revision: configured ? model.revision : '',
            available: response.available !== false && model.available === true && configured,
            reason: typeof model.reason === 'string' ? model.reason
              : !configured ? '엔드포인트 또는 모델 리비전 미등록' : '현재 사용할 수 없음',
          };
        });
        const explanationModels: ExplanationModel[] = Array.isArray(response.explanationModels)
          ? response.explanationModels.filter((m: ExplanationModel) => m && nonempty(m.id) && nonempty(m.label))
          : [];
        const defaultExplanationModel = explanationModels.find(m => m.id === response.defaultExplanationModel)?.id
          || explanationModels[0]?.id || '';
        setState({ status: 'ready', models, explanationModels, defaultExplanationModel,
          message: typeof response.message === 'string' ? response.message : '' });
        setSelected(previous => {
          const available = models.filter(model => model.available);
          return available.find(model => model.id === previous)?.id
            || available.find(model => model.id === response.defaultModel)?.id || available[0]?.id || '';
        });
      } catch (error) {
        if (active) {
          setState({ ...LOADING, status: 'error', message: error instanceof Error ? error.message : '처리기 목록을 불러오지 못했습니다.' });
          setSelected('');
        }
      }
    }
    void load();
    return () => { active = false; };
  }, [attempt]);

  const ready = state.status === 'ready' && state.models.some(model => model.id === selected && model.available);
  return {
    ...state, selected, ready,
    select: (id: string) => { if (state.models.some(model => model.id === id && model.available)) setSelected(id); },
    refresh: () => { setState(LOADING); setAttempt(value => value + 1); },
  };
}
