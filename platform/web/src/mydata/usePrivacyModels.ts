import { useEffect, useState } from 'react';
import { sock } from '../lib';

export type PrivacyModel = {
  id: string; label: string; family: string; modelId: string; revision: string;
  available: boolean; reason?: string;
};
type ModelState = {
  status: 'loading' | 'ready' | 'error';
  models: PrivacyModel[];
  message: string;
};
const LOADING: ModelState = { status: 'loading', models: [], message: '' };
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
        if (response.type !== 's2_privacy_models.done' || response.processor !== 'eks-sllm' || !Array.isArray(response.models)) {
          throw new Error('프라이버시 모델 상태를 확인하지 못했습니다.');
        }
        const models: PrivacyModel[] = response.models.map((model: PrivacyModel) => {
          if (!model || !nonempty(model.id) || !nonempty(model.label) || !nonempty(model.family)) {
            throw new Error('프라이버시 모델 목록 형식이 올바르지 않습니다.');
          }
          const configured = nonempty(model.modelId) && nonempty(model.revision);
          return {
            id: model.id, label: model.label, family: model.family,
            modelId: configured ? model.modelId : '', revision: configured ? model.revision : '',
            available: response.available !== false && model.available === true && configured,
            reason: typeof model.reason === 'string' ? model.reason
              : !configured ? '엔드포인트 또는 모델 리비전 미등록' : '현재 사용할 수 없음',
          };
        });
        setState({ status: 'ready', models, message: typeof response.message === 'string' ? response.message : '' });
        setSelected(previous => {
          const available = models.filter(model => model.available);
          return available.find(model => model.id === previous)?.id
            || available.find(model => model.id === response.defaultModel)?.id || available[0]?.id || '';
        });
      } catch (error) {
        if (active) {
          setState({ status: 'error', models: [], message: error instanceof Error ? error.message : '모델 목록을 불러오지 못했습니다.' });
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
