"""design_loop — 상품명세서 → PRD → 프로세스(다단계 플로우) 생성 → 리뷰·테스트 → 리포트 (유계 루프).

두 런타임(플랫폼 agents/, 독립 스튜디오 harness/)이 공유하는 순수 파이썬 모듈. boto3·Strands 의존 없음 —
모델 호출은 호출자가 `deps["generate"]`/`deps["llm_judge"]` 콜백으로 주입한다. 정본은 platform/design_loop/,
구 스튜디오 하니스는 빌드 시 복사한다 (scripts/deploy_runtime.py).

설계: platform/docs/specs/2026-09-04-design-studio-agentic-loop-design.md
"""
from .loop import MAX_REGENERATIONS, run  # noqa: F401
from .prd import derive_prd  # noqa: F401
from .checklist import build_checklist  # noqa: F401
from .review import review  # noqa: F401
from .generate import build_prompts, parse_flow  # noqa: F401
