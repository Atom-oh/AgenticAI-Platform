"""F7 보고서 생성 — Reader / Writer 분리 (SPEC §5 F7, §6.1 화면 8).

세 개의 Lambda 핸들러가 같은 배포 자산(api-dist)을 공유하되 **별도 IAM 역할**로 실행된다.

  reader_handler        : 외부 웹 콘텐츠 전용. Bedrock invoke 권한만 — 내부 도구 invoke 권한 없음.
  internal_tool_handler : 사내 문서 검색(seed/out Document·Regulation 노드). 아무 권한도 없다.
  writer_handler        : 내부 문서 검색 + Bedrock. URL fetch 코드가 없다(테스트가 소스를 검사한다).

Reader → Writer 사이는 구조화 JSON 요약만 통과한다. 순수 도우미는 report.common 에 있다.
"""
from __future__ import annotations
