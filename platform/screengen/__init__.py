"""F5 화면 생성 에이전트 (SPEC §5 F5, §2 S3).

구성:
  agent.py               파이프라인: Registry 정확 조회 → 스킬 로드 → Bedrock 스트리밍 생성 → 헤더/import 파싱
                         → Registry 게이트 → 검증 게이트 Lambda(gates/) → 실패 시 1회 재생성 (§12.9)
  components_fixture.py  Registry 모듈을 import 할 수 없을 때(로컬 테스트) 쓰는 기준선 컴포넌트 목록 — UI에 '픽스처'로 표기
"""
from __future__ import annotations
