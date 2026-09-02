import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid(defineConfig({
  base: '/AgenticAI-Platform/',
  lang: 'ko-KR',
  title: 'Agentic AI 플랫폼 엔지니어링',
  description: 'AgentCore 기반 메타플랫폼 설계·운영 레퍼런스',

  themeConfig: {
    nav: [
      { text: '시작', link: '/00-intro/' },
      { text: '캐싱', link: '/04-caching/prompt-caching-basics' },
      { text: '권한 제어', link: '/09-authorization/' },
      { text: 'AgentCore', link: '/10-agentcore/runtime-deep-dive' },
      { text: '보안·규제', link: '/12-security-korea/korea-fsc-regulation' },
      { text: '🚀 라이브 데모', link: '/14-demo/' },
    ],

    sidebar: [
      {
        text: 'Part 0. 서문과 플랫폼 사고',
        collapsed: false,
        items: [
          { text: '개관', link: '/00-intro/' },
          { text: '애플리케이션이 아니라 플랫폼', link: '/00-intro/what-is-agentic-platform' },
          { text: '메타플랫폼 전체 그림', link: '/00-intro/meta-platform-overview' },
          { text: 'AI를 위한 플랫폼 엔지니어링', link: '/00-intro/platform-engineering-for-ai' },
          { text: '6대 통증점', link: '/00-intro/six-pain-points' },
        ],
      },
      {
        text: 'Part 1. 에이전트 설계 기초',
        collapsed: true,
        items: [
          { text: '개관', link: '/01-agent-design/' },
          { text: '에이전트 vs 워크플로우', link: '/01-agent-design/agent-vs-workflow' },
          { text: '단일 vs 멀티 에이전트', link: '/01-agent-design/single-vs-multi-agent' },
          { text: '오케스트레이션 패턴', link: '/01-agent-design/orchestration-patterns' },
          { text: '툴 설계', link: '/01-agent-design/tool-design' },
          { text: 'MCP 서버 설계', link: '/01-agent-design/mcp-server-design' },
          { text: '신뢰성과 durable execution', link: '/01-agent-design/reliability-durable-execution' },
          { text: '프레임워크 지형', link: '/01-agent-design/framework-landscape' },
        ],
      },
      {
        text: 'Part 2. 성능과 지연',
        collapsed: true,
        items: [
          { text: '개관', link: '/02-performance/' },
          { text: '지연의 해부', link: '/02-performance/latency-anatomy' },
          { text: '툴 라운드트립', link: '/02-performance/tool-roundtrips' },
          { text: '스트리밍과 병렬 툴 호출', link: '/02-performance/streaming-parallel-tools' },
          { text: '모델 라우팅', link: '/02-performance/model-routing' },
          { text: '지연 체크리스트', link: '/02-performance/latency-checklist' },
        ],
      },
      {
        text: 'Part 3. 정확도와 평가',
        collapsed: true,
        items: [
          { text: '개관', link: '/03-accuracy-eval/' },
          { text: '툴 과부하', link: '/03-accuracy-eval/tool-overload' },
          { text: '검색과 환각된 인자', link: '/03-accuracy-eval/retrieval-and-hallucinated-args' },
          { text: '평가 하니스', link: '/03-accuracy-eval/eval-harness' },
          { text: 'LLM 판정과 trajectory 평가', link: '/03-accuracy-eval/llm-judge-trajectory' },
          { text: 'AWS 평가 서비스', link: '/03-accuracy-eval/aws-evaluations' },
        ],
      },
      {
        text: 'Part 4. 프롬프트 캐싱과 KV 캐시',
        collapsed: true,
        items: [
          { text: '개관', link: '/04-caching/' },
          { text: '프롬프트 캐싱 기초', link: '/04-caching/prompt-caching-basics' },
          { text: '캐시 미스 근본 원인', link: '/04-caching/cache-miss-root-causes' },
          { text: '캐시 지표와 경제성', link: '/04-caching/cache-metrics-economics' },
          { text: 'vLLM KV 캐시', link: '/04-caching/vllm-kv-cache' },
          { text: '게이트웨이 세션 어피니티', link: '/04-caching/gateway-session-affinity' },
          { text: '시맨틱 캐싱 vs 정확 캐싱', link: '/04-caching/semantic-vs-exact-caching' },
        ],
      },
      {
        text: 'Part 5. 컨텍스트 엔지니어링',
        collapsed: true,
        items: [
          { text: '개관', link: '/05-context/' },
          { text: '컨텍스트 엔지니어링 원칙', link: '/05-context/context-engineering-discipline' },
          { text: 'Context rot', link: '/05-context/context-rot' },
          { text: 'Compaction과 요약', link: '/05-context/compaction-summarization' },
          { text: '컨텍스트 격리와 오프로딩', link: '/05-context/context-isolation-offloading' },
          { text: 'JIT 검색과 토큰 예산', link: '/05-context/jit-retrieval-token-budget' },
        ],
      },
      {
        text: 'Part 6. 임베딩과 벡터 검색',
        collapsed: true,
        items: [
          { text: '개관', link: '/06-vector-search/' },
          { text: '임베딩 기초', link: '/06-vector-search/embeddings-fundamentals' },
          { text: '임베딩 모델 선택', link: '/06-vector-search/embedding-model-choice' },
          { text: '청킹 전략', link: '/06-vector-search/chunking-strategies' },
          { text: 'ANN 인덱스와 양자화', link: '/06-vector-search/ann-indexes-quantization' },
          { text: '하이브리드 검색과 리랭킹', link: '/06-vector-search/hybrid-search-rerank' },
          { text: '검색 평가', link: '/06-vector-search/retrieval-evaluation' },
          { text: '인덱스 신선도와 마이그레이션', link: '/06-vector-search/index-freshness-migration' },
          { text: 'GraphRAG·agentic RAG, 그리고 쓰지 말아야 할 때', link: '/06-vector-search/graphrag-agentic-when-not' },
          { text: 'AWS 벡터 스토어', link: '/06-vector-search/aws-vector-stores' },
        ],
      },
      {
        text: 'Part 7. 메모리 아키텍처',
        collapsed: true,
        items: [
          { text: '개관', link: '/07-memory/' },
          { text: '메모리 유형', link: '/07-memory/memory-types' },
          { text: '메모리 쓰기 정책', link: '/07-memory/memory-write-policies' },
          { text: '메모리 검색과 스코핑', link: '/07-memory/memory-retrieval-scoping' },
          { text: 'AgentCore Memory와 대안', link: '/07-memory/agentcore-memory-alternatives' },
          { text: '메모리 보안과 프라이버시', link: '/07-memory/memory-security-privacy' },
        ],
      },
      {
        text: 'Part 8. 스케일링과 비용',
        collapsed: true,
        items: [
          { text: '개관', link: '/08-scaling-cost/' },
          { text: '동시성 쿼터와 스로틀링', link: '/08-scaling-cost/concurrency-quotas-throttling' },
          { text: 'Bedrock 추론 티어', link: '/08-scaling-cost/bedrock-inference-tiers' },
          { text: '토큰 어카운팅과 차지백', link: '/08-scaling-cost/token-accounting-chargeback' },
          { text: '오토스케일 신호', link: '/08-scaling-cost/autoscaling-signals' },
          { text: '콜드스타트와 세션 유휴', link: '/08-scaling-cost/coldstart-session-idle' },
        ],
      },
      {
        text: 'Part 9. 세밀 권한 제어',
        collapsed: true,
        items: [
          { text: '개관', link: '/09-authorization/' },
          { text: '툴별 On-Behalf-Of', link: '/09-authorization/per-tool-obo' },
          { text: 'OAuth 토큰 교환과 MCP', link: '/09-authorization/oauth-token-exchange-mcp' },
          { text: 'RAG 엔타이틀먼트 스코핑', link: '/09-authorization/rag-entitlement-scoping' },
          { text: 'Confused deputy 문제', link: '/09-authorization/confused-deputy' },
          { text: 'HITL과 감사', link: '/09-authorization/hitl-audit' },
          { text: 'Cedar와 Verified Permissions', link: '/09-authorization/cedar-verified-permissions' },
          { text: 'Egress 제어', link: '/09-authorization/egress-control' },
        ],
      },
      {
        text: 'Part 10. AgentCore 심화',
        collapsed: true,
        items: [
          { text: '개관', link: '/10-agentcore/' },
          { text: 'Runtime 심화', link: '/10-agentcore/runtime-deep-dive' },
          { text: 'Runtime 배포 계약', link: '/10-agentcore/runtime-deploy-contract' },
          { text: 'Gateway 심화', link: '/10-agentcore/gateway-deep-dive' },
          { text: 'Identity 심화', link: '/10-agentcore/identity-deep-dive' },
          { text: 'Tools 심화', link: '/10-agentcore/tools-deep-dive' },
          { text: 'Observability 심화', link: '/10-agentcore/observability-deep-dive' },
          { text: '쿼터와 가격', link: '/10-agentcore/quotas-pricing' },
        ],
      },
      {
        text: 'Part 11. 에이전트를 만드는 에이전트',
        collapsed: true,
        items: [
          { text: '개관', link: '/11-builder-agent/' },
          { text: '요구사항 대화', link: '/11-builder-agent/requirements-dialogue' },
          { text: '생성된 에이전트 가드레일', link: '/11-builder-agent/generated-agent-guardrails' },
          { text: '카탈로그와 레지스트리', link: '/11-builder-agent/catalog-registry' },
          { text: '에이전트 CI/CD', link: '/11-builder-agent/agent-cicd' },
          { text: '에이전트 스킬', link: '/11-builder-agent/agent-skills' },
        ],
      },
      {
        text: 'Part 12. 보안·안전과 한국 금융 규제',
        collapsed: true,
        items: [
          { text: '개관', link: '/12-security-korea/' },
          { text: '프롬프트 인젝션', link: '/12-security-korea/prompt-injection' },
          { text: 'MCP 공급망 공격', link: '/12-security-korea/mcp-supply-chain' },
          { text: '가드레일과 PII', link: '/12-security-korea/guardrails-pii' },
          { text: '한국 금융 규제', link: '/12-security-korea/korea-fsc-regulation' },
          { text: '하이브리드 아키텍처', link: '/12-security-korea/hybrid-architecture' },
        ],
      },
      {
        text: 'Part 13. 부록',
        collapsed: true,
        items: [
          { text: '개관', link: '/13-appendix/' },
          { text: 'VitePress 설정', link: '/13-appendix/vitepress-setup' },
          { text: 'VitePress 컨벤션', link: '/13-appendix/vitepress-conventions' },
          { text: '용어집', link: '/13-appendix/glossary' },
        ],
      },
      {
        text: '라이브 데모',
        collapsed: false,
        items: [
          { text: '데모 안내 · 접속 정보', link: '/14-demo/' },
          { text: '아키텍처와 설계 결정', link: '/14-demo/architecture' },
        ],
      },
    ],

    search: { provider: 'local' },
    outline: { level: [2, 3] },
    docFooter: { prev: '이전', next: '다음' },
  },

  markdown: {
    lineNumbers: true,
  },

  mermaid: {},
}))
