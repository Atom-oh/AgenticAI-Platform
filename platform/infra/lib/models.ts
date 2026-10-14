/**
 * Bedrock models the platform may invoke. Must equal engine/model_catalog.py
 * (tests/test_model_catalog_parity.py). Profiles verified with Bedrock
 * GetInferenceProfile 2026-09-11; 5.5, GPT-6.1 Sol and GPT-6 Luna verified by
 * Converse 2026-10-14. The first entry is the default generation model.
 */
export const CATALOG_MODEL_IDS = [
  'global.anthropic.claude-sonnet-5', 'global.anthropic.claude-sonnet-5-5',
  'global.anthropic.claude-opus-5-5', 'global.anthropic.claude-opus-5', 'global.anthropic.claude-haiku-5-5',
  'global.openai.gpt-6.1-sol', 'global.openai.gpt-6-luna', 'global.openai.gpt-6-astra',
  'global.anthropic.claude-fable-5-1', 'global.anthropic.claude-fable-5',
] as const;
export const DEFAULT_MODEL_ID: string = CATALOG_MODEL_IDS[0];

/**
 * Fixed non-catalog models (engine/gate.py EMBED_MODEL, RERANK_MODEL, GEMMA_MODEL); not user-selectable.
 * Gemma is called through bedrock-mantle with an IAM-derived short-term key, which inherits these grants.
 */
export const FIXED_FOUNDATION_MODELS = ['amazon.titan-embed-text-v2:0', 'cohere.rerank-v3-5:0', 'google.gemma-4-31b'] as const;

/** Foundation-model family behind a `global.` inference profile. */
export const foundationModelOf = (profile: string): string => profile.replace(/^global\./, '');

/**
 * Exact InvokeModel resources for the catalog (global profile + its routed
 * foundation models in any region) plus, when requested, the fixed models.
 */
export function bedrockModelArns(partition: string, account: string, includeFixed = false): string[] {
  return [
    ...CATALOG_MODEL_IDS.flatMap(profile => [
      `arn:${partition}:bedrock:*:${account}:inference-profile/${profile}`,
      `arn:${partition}:bedrock:*::foundation-model/${foundationModelOf(profile)}`,
    ]),
    ...(includeFixed ? FIXED_FOUNDATION_MODELS.map(model => `arn:${partition}:bedrock:*::foundation-model/${model}`) : []),
  ];
}
