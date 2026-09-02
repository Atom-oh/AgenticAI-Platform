"""F4 Agent Registry — 사내 AI 자산(MCP/AGENT/SKILL/CUSTOM)의 버전·승인 관리 (SPEC §5 F4, §2 S3).

구성:
  model.py      레코드 스키마 · 상태 기계 (허용 전이 외에는 TransitionError)
  store.py      DynamoDB(REGISTRY_TABLE) 저장소 — 테이블 객체 주입 가능
  fake_table.py 테스트/로컬용 인메모리 DynamoDB 테이블 페이크
  api.py        다른 모듈이 import 하는 공개 함수 (Consumer API는 APPROVED만 반환)
  seed.py       시연 기준선 시드(멱등) · 시연 리셋
"""
from __future__ import annotations
