---
title: 쿼터와 가격
description: AgentCore Runtime/Gateway/Memory/Browser/Code Interpreter의 정확한 가격 단위와 서비스 쿼터를 공식 문서 기준으로 정리한다.
outline: [2, 3]
---

# 쿼터와 가격

::: tip 이 장에서 얻는 것
- AgentCore Runtime microVM의 과금 단위(vCPU-hour, GB-hour)와 "I/O wait 구간 CPU 과금 중단"이 비용 설계에 미치는 실질적 함의
- Gateway·Memory·Identity·Browser·Code Interpreter·Policy·Evaluations 각 컴포넌트의 정확한 가격표(2026-08-24 기준)
- Runtime을 중심으로 한 계정 단위 서비스 쿼터(동시 세션, TPS, 세션 수명) 전수 목록과 쿼터 위반 시 실패 모드
- 이 장과 Part 8(`/08-scaling-cost/coldstart-session-idle.md`)의 역할 분리: 콜드스타트/유휴 비용의 일반론은 그곳이 정본이며, 이 장은 AgentCore 고유의 숫자에 집중한다
:::

## 왜 문제가 되는가

AgentCore는 컴포넌트별로 과금 모델이 전혀 다르다. Runtime/Browser/Code Interpreter는 "활성 리소스 소비" 기반(초당 CPU·피크 메모리)이고, Gateway는 API 호출 건수 기반, Memory는 이벤트·레코드 건수 기반, Identity는 발급된 토큰/키 건수 기반이다. 하나의 워크로드가 이 컴포넌트를 조합해서 쓰는 것이 일반적이므로, 비용 모델을 한 가지 프레임(예: "요청당 비용")으로 단순화하면 실제 청구서와 어긋난다.

더 중요한 것은 Runtime의 과금 단위가 갖는 설계적 함의다. AWS는 공식 가격 페이지에서 "에이전트 워크로드는 일반적으로 시간의 30~70%를 I/O wait(LLM 응답, 툴/API 호출, DB 쿼리 대기)로 소비한다"고 명시하고, **이 구간에서 다른 백그라운드 프로세스가 CPU를 쓰지 않는 한 CPU 과금이 발생하지 않는다**고 밝힌다. 이는 "사전 할당형(pre-allocated)" 컴퓨트 서비스와 근본적으로 다른 비용 곡선을 만든다 — 즉 에이전트 아키텍처를 "I/O-heavy"하게(모델 호출과 툴 호출에 시간을 쓰고 Runtime 내부에서는 최소한만 처리하도록) 설계할 유인이 가격 구조 자체에 내재되어 있다.

반대로 세션(=microVM) 생성·유지 전략을 잘못 잡으면 이 장점이 무의미해진다. 세션은 8시간까지 유지되고 15분 유휴 후 종료되는데, 호출마다 새 `runtimeSessionId`를 발급하면 매번 새 microVM을 부팅해야 하고, 세션 재사용을 설계하면 동일 microVM의 초기화 비용(파일시스템, 로드된 모델/툴 설정)을 상각할 수 있다. 콜드스타트 자체의 정량적 수치와 완화 전략의 일반론은 Part 8이 정본이지만, 이 장에서는 AgentCore Runtime 세션 수명 파라미터(`idleRuntimeSessionTimeout`, `maxLifetime`)의 정확한 기본값과 쿼터를 다룬다.

## 핵심 개념

**Runtime의 두 가지 컴퓨트 타입.** AgentCore Runtime은 microVMs(서버리스, 소비 기반)와 Instances(사용자 계정의 EC2 인스턴스 + 관리 수수료)를 제공한다. 이 장의 가격 논의는 대부분 microVMs를 대상으로 하며, Instances는 "지속적이거나 리소스 집약적인, 또는 GPU가 필요한" 워크로드를 위한 별도 과금 체계다.

**"활성 리소스 소비" 과금.** microVM 기반 컴포넌트(Runtime, Browser, Code Interpreter)는 세션 수명(microVM 부팅 → 초기화 → 활성 처리 → 유휴 → 세션 종료/microVM 셧다운) 전체에 걸쳐 초 단위로 계측된다.

- **CPU**: 실제 소비된 vCPU-초만 과금. I/O wait 중 다른 프로세스가 CPU를 쓰지 않으면 CPU 과금은 0.
- **메모리**: 해당 초까지의 **피크 메모리 소비량**을 과금 — CPU와 달리 "쉬는 동안 0"이 아니라 계속 청구된다. 즉 유휴 시간을 줄이는 것(짧은 `idleRuntimeSessionTimeout`)이 메모리 비용을 줄이는 유일한 레버다.
- 1초 최소 과금, 메모리는 128MB 최소 과금이 적용된다.
- 시스템 오버헤드(플랫폼 자체의 리소스 사용)도 과금에 포함된다.

**Instances 과금.** EC2 On-Demand 가격 + 관리 수수료(표준 인스턴스 12%, GPU/Graviton 기반 `gr6` 계열은 7.8%)로 구성된다. 관리 수수료는 항상 On-Demand 정가 기준으로 계산되며, Savings Plans/RI/ODCR은 EC2 컴퓨트 부분에만 적용되고 관리 수수료에는 적용되지 않는다.

**세션 = microVM.** `runtimeSessionId`마다 전용 microVM이 프로비저닝된다. 동일 세션에 대한 후속 호출은 같은 microVM을 재사용하고 유휴 타이머를 리셋한다. 세션이 종료(유휴 타임아웃, 최대 수명 도달, 명시적 `StopRuntimeSession`, 헬스체크 실패)되면 다음 호출 시 새 microVM이 프로비저닝된다 — 이 재프로비저닝 구간이 콜드스타트다.

::: warning 미정착 영역
"세션당 콜드스타트가 약 6초"라는 수치는 AWS 공식 문서·가격 페이지·릴리스 노트에서 확인하지 못했다. 공식적으로 확인되는 것은 다음뿐이다.

- AWS Well-Architected Agentic AI Lens(`AGENTCOST06-BP03`)는 "모델 로딩과 툴 등록을 포함한 초기화 시간을 2초 미만 목표로 유지"하라고 권고하지만, 이는 **microVM 부팅 시간을 제외한** 애플리케이션 초기화 목표치이며 측정된 콜드스타트 수치가 아니다.
- 같은 문서는 "콜드스타트 비율이 10%를 넘으면 세션 어피니티나 유휴 타임아웃 설정을 먼저 점검하라"고만 명시한다.
- 마케팅 문구는 microVM이 "즉시 시작되고(start instantly) 온디맨드로 스케일"된다고만 서술한다.

따라서 6초라는 특정 수치는 비공식 출처(블로그 후기, 커뮤니티 벤치마크 등)에서 나온 것으로 보이며, 이 문서에서는 인용하지 않는다. 정확한 콜드스타트 지연 시간은 워크로드(컨테이너 이미지 크기, 초기화 로직)에 따라 달라지므로, **자신의 환경에서 직접 계측**하고(AgentCore Observability의 초기화 관련 span), Part 8(`/08-scaling-cost/coldstart-session-idle.md`)에서 일반적인 콜드스타트 완화 전략을 참조하라.
:::

**컴포넌트별 과금 트리거 정리.**

| 컴포넌트 | 과금 단위 |
|---|---|
| Runtime / Browser / Code Interpreter (microVM) | vCPU-hour + GB-hour (초 단위 계측) |
| Runtime (Instances) | EC2 On-Demand 인스턴스-시간 + 관리 수수료(%) |
| Gateway | API 호출 건수(ListTools/InvokeTool/Ping 등), Search API 호출, 인덱싱된 툴 수 |
| Memory | 단기 메모리 이벤트 생성 건수, 장기 메모리 저장 레코드 수(시간당), 검색 호출 건수 |
| Identity | 발급된 OAuth 토큰/API 키 요청 건수 (Runtime/Gateway를 통한 사용은 무료) |
| Policy | 인가(authorization) 요청 건수 + 자연어→Cedar 정책 변환 시 입력 토큰 |
| Evaluations | 입력/출력 토큰(빌트인), 평가 건수(커스텀) |
| Web Search on AgentCore | 검색 쿼리 건수 |

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 호출 간격이 짧고 빈번한 대화형 에이전트 | 동일 `runtimeSessionId` 재사용, 짧은 `idleRuntimeSessionTimeout`은 지양 | microVM 재사용으로 콜드스타트 회피, 초기화 비용 상각 | 유휴 구간에도 메모리 과금은 계속 발생(CPU는 0이어도) |
| 호출이 드물고 세션 간 격리가 중요한 워크로드(멀티테넌트 SaaS) | 세션별 신규 `runtimeSessionId`, 짧은 `idleRuntimeSessionTimeout` | 유휴 메모리 과금 최소화, 강한 격리 유지 | 매 호출마다 콜드스타트 리스크, 초기화 비용 반복 |
| 시간당 8시간을 넘는 장시간 실행, 여러 에이전트가 파일시스템 공유 | Runtime Instances (EC2 기반) | 최대 14일 세션, 인스턴스당 다중 에이전트 co-location, EC2 Savings Plans/RI 적용 가능(컴퓨트 부분) | 관리 수수료(12%/7.8%)는 On-Demand 정가 기준, 자체 EC2/EBS/VPC 쿼터 관리 필요 |
| 툴 호출이 많고 API를 MCP 호환 툴로 통합해야 함 | Gateway | 호출당 $0.005/1,000(기본) + 필요 시 semantic search만 추가 과금 | 요금 제한(rate limiting) 설정 자체는 무료지만 tool-call TPS(게이트웨이/계정당 200 TPS) 쿼터에 걸릴 수 있음 |
| 대화 컨텍스트만 필요, 장기 지식 축적 불필요 | 단기 메모리만 사용 ($0.25/1,000 이벤트) | 장기 메모리 저장·추출·검색 비용 회피 | 세션 종료 시 컨텍스트 소실, 재구성 비용이 다른 곳으로 이전될 뿐 |
| 장기 메모리가 필요하지만 빌트인 추출 전략의 정확도/커스터마이징이 부족 | Self-managed/override 전략 | 저장 비용이 레코드당 $0.25/1,000(빌트인 $0.75/1,000의 1/3) | 자체 모델/프롬프트로 추출 로직을 운영해야 하는 엔지니어링 부담 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| Runtime 비용이 세션 wall-clock 시간에 비례해 늘어나는 것처럼 보임 | 메모리 과금은 CPU와 달리 I/O wait 중에도 계속되며, `idleRuntimeSessionTimeout`이 과도하게 길게 설정됨 | CloudWatch에서 세션별 피크 메모리·유휴 구간 길이를 관찰 | `idleRuntimeSessionTimeout`을 호출 패턴에 맞게 단축, 불필요하게 큰 메모리를 점유하는 초기화 로직 점검 |
| `InvokeAgentRuntime` 호출이 대량 트래픽에서 스로틀링됨 | 계정 단위 데이터 플레인 API 요청률(공유 쿼터) 1,000 TPS 초과, 또는 신규 세션 생성률 25 TPS 초과 | CloudWatch에서 `ThrottlingException` 발생률 확인, 호출을 API별로 분리해도 공유 쿼터라는 점 재확인 | 세션 재사용으로 신규 세션 생성률 자체를 줄이고, Service Quotas로 증량 요청 |
| 동시 활성 세션 수가 예상보다 빨리 한도에 도달 | 계정당 활성 세션 워크로드 한도(us-east-1/us-west-2 5,000, 그 외 리전 2,500)를 인지하지 못하고 세션을 장시간 유지 | 계정별 활성 세션 수를 Observability로 모니터링 | 유휴 타임아웃을 단축해 세션 회전율을 높이거나 Service Quotas로 증량, 리전 분산 검토 |
| Memory `CreateEvent`가 특정 세션에서 실패 | 액터·세션당 처리율 한도(대화 페이로드 포함 5 TPS, 미포함 10 TPS)를 계정 전체 한도(200 TPS)와 혼동 | per-actor/per-session 단위로 요청률 분해 후 확인 | 이벤트 배치화, 세션 단위 쓰기 폭주를 클라이언트에서 큐잉 |
| 장기 메모리 추출이 갑자기 멈추거나 지연됨 | 빌트인 전략의 장기 메모리 추출 토큰/분 한도(계정 150,000) 또는 세션당 에피소드 추출 한도(50,000 토큰/분, 조정 불가)를 초과 | CloudWatch `Bedrock-AgentCore` 네임스페이스의 `TokenCount` 메트릭 확인 | 추출 배치 주기 조정, 계정 한도는 Service Quotas로 증량, 세션당 한도는 조정 불가하므로 세션 분할 |
| Gateway를 경유한 툴 호출이 대량 세션에서 실패 | gateway-level/account-level tool-call·tool-list 동시 연결 수(각 5,000)나 TPS(각 200) 초과, 또는 semantic search 기반 호출이 분당 25건 초과 | Gateway 관련 CloudWatch 지표, `ThrottlingException` 발생 위치(게이트웨이별 vs 계정 전체) 구분 | 게이트웨이를 워크로드별로 분리, semantic search 호출을 캐싱하거나 명시적 툴 이름 호출로 전환 |
| Runtime Instances 사용 시 EC2 관련 쿼터에서 막힘 | AgentCore 자체 쿼터는 통과했지만 계정의 EC2 `RunInstances`/`CreateFleet` 요청률, EBS 볼륨 쿼터, VPC ENI 쿼터가 부족 | EC2/EBS/VPC 서비스별 쿼터 콘솔 확인 | 해당 AWS 서비스(EC2, EBS, VPC, Auto Scaling)에 개별 증량 요청 — AgentCore 쿼터 증량만으로는 해결되지 않음 |
| Direct code deployment 배포 실패 | ZIP 압축 250MB, 압축 해제 750MB 한도 초과(조정 불가) | 배포 파이프라인에서 아티팩트 크기 로깅 | 컨테이너 기반 배포로 전환하거나(이미지 2GB 한도는 더 큼) 의존성 최소화 |

## 안티패턴

- ❌ 매 사용자 요청마다 새 `runtimeSessionId`를 발급 → ✅ 사용자/대화 단위로 세션 ID를 고정하고, 세션 만료는 `idleRuntimeSessionTimeout`/`maxLifetime`으로 제어
- ❌ Runtime CPU 비용만 보고 "I/O wait는 공짜니까 세션을 오래 열어둬도 무해하다"고 가정 → ✅ 메모리는 I/O wait 중에도 피크 기준으로 계속 과금된다는 점을 비용 모델에 반영
- ❌ Gateway의 semantic search(Search API, $0.025/1,000)를 매 요청마다 호출해 사용 가능한 툴을 찾음 → ✅ 툴 카탈로그가 안정적이면 명시적 툴 이름으로 `InvokeTool`을 직접 호출($0.005/1,000)해 비용을 5배 절감
- ❌ 모든 대화 기록을 장기 메모리에 빌트인 전략으로 무조건 저장 → ✅ 실제로 재사용되는 지식만 장기 메모리로 승격하고, 나머지는 단기 메모리(세션 종료 시 소멸)에 둔다
- ❌ Runtime Instances의 관리 수수료를 EC2 Savings Plans/RI로 절감하려 시도 → ✅ 관리 수수료는 항상 On-Demand 정가 기준이므로, 절감은 컴퓨트 부분에만 적용됨을 예산 계획에 반영
- ❌ 계정 전체 데이터 플레인 TPS(1,000)를 API별로 별도 한도가 있다고 오해하고 용량 계획 → ✅ `InvokeAgentRuntime`, `StopRuntimeSession`, `GetAgentCard` 등이 하나의 공유 쿼터를 나눠 쓴다는 점을 반영해 계획

## 계측 (SLI)

AgentCore 비용/쿼터를 운영하려면 최소한 다음을 CloudWatch(AgentCore Observability 경로) 및 Cost Explorer에서 추적해야 한다.

- **세션당 CPU/메모리 소비 시계열**: 공식 가격 예시(고객 지원 에이전트 사례)와 동일한 방식으로 `소비 vCPU-초 / 3600 × vCPU-hour rate`, `초당 피크 GB 합 / 3600 × GB-hour rate`를 재현할 수 있어야 비용 예측이 가능하다.
- **세션 재사용률(session affinity rate)**: 콜드스타트 유발 비율의 대리 지표. Well-Architected Lens는 10%를 임계값으로 제시한다.
- **스로틀링 발생률**: `ThrottlingException`을 API/쿼터 카테고리(데이터 플레인 1,000 TPS 공유, 세션 생성 25 TPS, 컨트롤 플레인 mutation 50 TPS 등)별로 태깅해 어떤 쿼터가 실제 병목인지 구분.
- **`TokenCount`(Memory 장기 추출)**: 계정 한도 150,000 토큰/분 대비 사용률.
- **Gateway tool-call 동시 연결 수 및 TPS**: 게이트웨이별/계정별 각각 5,000 연결, 200 TPS 한도 대비.
- **활성 세션 수 vs. 리전별 한도**: us-east-1/us-west-2 5,000, 그 외 리전 2,500.

에이전트 ID·워크플로 ID를 모든 호출에 태깅해 두면(AWS Well-Architected Agentic AI Lens 권고) 위 지표를 에이전트 단위로 분해해 어떤 에이전트/워크로드가 비용과 쿼터 소비를 주도하는지 Cost Explorer와 Observability에서 함께 볼 수 있다.

## 체크리스트

- [ ] Runtime 비용을 "vCPU-hour + GB-hour" 두 축으로 분리해 추적하고 있는가(하나의 "세션당 비용"으로 뭉치지 않았는가)
- [ ] `idleRuntimeSessionTimeout`과 `maxLifetime`을 워크로드의 호출 빈도·격리 요구사항에 맞춰 명시적으로 설정했는가(기본값 15분/8시간에 의존하지 않는가)
- [ ] 세션 재사용(동일 `runtimeSessionId`)이 콜드스타트를 줄이는 방향으로 클라이언트/오케스트레이션 레이어에 구현되어 있는가
- [ ] Gateway 호출을 semantic search(Search API)와 직접 툴 호출(InvokeTool)로 구분해 비용이 5배 차이 나는 지점을 인지했는가
- [ ] 장기 메모리에 빌트인 전략(레코드당 $0.75/1,000)과 self-managed 전략(레코드당 $0.25/1,000) 중 워크로드에 맞는 것을 선택했는가
- [ ] 계정 단위 공유 쿼터(데이터 플레인 1,000 TPS, 세션 생성 25 TPS 등)가 API별 개별 한도가 아니라는 점을 용량 계획에 반영했는가
- [ ] Runtime Instances를 쓰는 경우 AgentCore 쿼터뿐 아니라 EC2/EBS/VPC/Auto Scaling 쿼터도 별도로 점검했는가
- [ ] 에이전트 ID/워크플로 ID 태깅으로 컴포넌트별 비용을 Cost Explorer에서 분해 가능한가
- [ ] 콜드스타트 지연의 정량 수치를 (공식 미확인 상태이므로) 자체 환경에서 직접 계측하고 있는가

## 참고

- [Amazon Bedrock AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) — 확인 시점: 2026-08-24. 컴포넌트별 정확한 단가와 예시 계산이 실린 1차 출처. 가격은 예고 없이 변경될 수 있으므로 최신 페이지를 항상 재확인할 것.
- [Quotas for Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html) — Runtime/Memory/Identity/Gateway/Browser/Code Interpreter/Evaluations 등 전 컴포넌트 쿼터 표.
- [Amazon Bedrock AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/) — 소비 기반 과금 모델의 핵심 논리("I/O wait 구간은 무료") 공식 서술.
- [Securely launch and scale your agents and tools on Amazon Bedrock AgentCore Runtime](https://aws.amazon.com/blogs/machine-learning/securely-launch-and-scale-your-agents-and-tools-on-amazon-bedrock-agentcore-runtime/) — Runtime 이벤트 루프에서 무엇이 과금 대상(Runtime 내부 처리)이고 무엇이 아닌지(LLM/툴 호출) 도식화.
- [AWS Well-Architected Agentic AI Lens — AGENTCOST04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost04-bp02.html), [AGENTCOST06-BP03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost06-bp03.html) — 세션 어피니티, 콜드스타트 비율 임계값(10%), 초기화 시간 목표(2초) 등 운영 권고.
- [Configure Amazon Bedrock AgentCore lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html) — `idleRuntimeSessionTimeout`/`maxLifetime` API 파라미터.
- [Use isolated sessions for agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html) — 세션 상태 전이(Active/Idle/Stopped)와 세션=microVM 모델.
- [Amazon Bedrock AgentCore Runtime observability and cost controls](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html) — Runtime 비용 추정 공식(vCPU-초/3600 × 비율 등).
- [Release notes for Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html) — 쿼터 기본값 변경 이력(예: 활성 세션 워크로드 1,000→5,000/2,500 증량, TPS 25→200 증량) — 쿼터 숫자는 시점에 따라 바뀌므로 이 페이지에서 최신 변경 여부를 확인할 것.
- 콜드스타트·세션 유휴 비용의 일반론(프레임워크 무관 원칙, 워밍 전략 등)은 `/08-scaling-cost/coldstart-session-idle.md`를 정본으로 참조.
