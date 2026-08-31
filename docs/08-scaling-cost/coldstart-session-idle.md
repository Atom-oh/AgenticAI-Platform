---
title: 콜드스타트와 세션 유휴
description: 세션=microVM 모델에서 콜드스타트 지연과 유휴 세션 비용이 어떻게 발생하는지, idleRuntimeSessionTimeout·세션 재사용·웜업 전략으로 어떻게 균형점을 잡는지 다룬다.
outline: [2, 3]
---

# 콜드스타트와 세션 유휴

::: tip 이 장에서 얻는 것
- "세션 = microVM" 모델에서 콜드스타트가 언제, 왜 발생하는지 — 그리고 호출당 새 세션을 만드는 코드가 어떻게 그것을 상시화하는지
- AgentCore Runtime 과금 구조(초당 CPU + 피크 메모리, I/O wait 중 CPU 과금 중단)가 콜드스타트·유휴 트레이드오프를 어떻게 규정하는지 — 공식 단가 기반 수치 예시 포함
- `idleRuntimeSessionTimeout`(기본 900초)·`maxLifetime`(기본 28,800초)을 워크로드별로 잡는 결정 표
- 세션 재사용(`runtimeSessionId` 고정)과 웜업 전략, 그리고 Lambda SnapStart와의 구조적 차이
- 콜드스타트율·초기화 시간을 SLI로 계측하는 방법 — 공식 임계값(콜드스타트율 10%, 앱 초기화 2초 목표) 포함

이 장은 콜드스타트/세션 유휴 비용의 **정본**이다. AgentCore 고유의 가격 단가·쿼터 숫자는 [AgentCore 쿼터와 가격](/10-agentcore/quotas-pricing)이 정본이며, 이 장은 그 숫자들이 만들어내는 설계 트레이드오프를 다룬다. 지연 문제의 진단 순서(콜드스타트는 7단계 중 하나)는 [지연 진단 체크리스트](/02-performance/latency-checklist)를 참조하라.
:::

## 왜 문제가 되는가

전통적인 서버리스에서 콜드스타트는 "가끔 겪는 p99 문제"였다. 에이전트 플랫폼에서는 사정이 다르다. AgentCore Runtime은 `runtimeSessionId`마다 전용 microVM을 프로비저닝하고, 같은 세션 ID로 들어온 후속 호출만 기존 microVM을 재사용한다([공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html)). 따라서 클라이언트가 호출마다 새 세션 ID를 발급하면 — 흔한 실수다. `uuid.uuid4()`를 요청 핸들러 안에서 부르는 코드 한 줄이면 충분하다 — **모든 요청이 콜드스타트**가 된다. p99가 아니라 p50이 콜드스타트 비용을 지불한다.

반대 방향의 실수도 있다. 콜드스타트를 피하려고 세션을 오래 열어두면 유휴 비용이 발생한다. AgentCore 과금은 "세션이 열려 있는 동안"이 아니라 "실제 리소스 소비"를 기준으로 하지만, 계측 구간은 microVM 부팅부터 초기화·활성 처리·**유휴 구간**·세션 종료까지 세션 수명 전체다([공식 pricing 페이지](https://aws.amazon.com/bedrock/agentcore/pricing/): "You only pay for actual resource consumption during your session, which spans from microVM boot, initialization, active processing, idle periods, until session termination"). CPU는 유휴 중 아무 프로세스도 돌지 않으면 과금이 0이지만, 메모리는 초당 피크 소비량 기준으로 계속 청구된다. 즉 유휴 세션의 비용은 순수하게 **메모리 비용**이고, 그것을 제어하는 레버가 `idleRuntimeSessionTimeout`이다.

이 두 힘 — 콜드스타트 지연(세션을 짧게 가져갈수록 커짐)과 유휴 메모리 비용(세션을 길게 가져갈수록 커짐) — 의 균형점을 워크로드별로 잡는 것이 이 장의 주제다. 균형점은 하나가 아니다. 대화형 챗봇과 야간 배치 에이전트는 정반대의 설정이 정답이다.

## 핵심 개념

### 과금 구조가 트레이드오프를 규정한다

AgentCore Runtime(microVM)의 과금 단위는 두 축이다([공식 pricing](https://aws.amazon.com/bedrock/agentcore/pricing/), 2026-08 확인 — 상세 단가와 다른 컴포넌트는 [쿼터와 가격](/10-agentcore/quotas-pricing) 참조):

- **CPU**: $0.0895/vCPU-hour — 단, **실제 CPU를 소비한 초에만** 과금. 공식 문구: "if your agent consumes no CPU during I/O wait, there are no CPU charges."
- **메모리**: $0.00945/GB-hour — 해당 초까지의 **피크 메모리** 기준으로, 세션 계측 구간 전체(유휴 포함)에 걸쳐 과금.
- 1초 최소 과금, 메모리 128MB 최소 과금.

AWS는 에이전트 워크로드가 "일반적으로 시간의 30~70%를 I/O wait(LLM 응답, 툴/API 호출, DB 쿼리 대기)로 소비한다"고 명시한다(같은 pricing 페이지). 에이전트 런타임의 실제 일은 대부분 "기다림"이므로, CPU 과금이 I/O wait 중 멈춘다는 사실은 I/O-heavy 에이전트에 구조적으로 유리하다.

**개념적 계산으로 확인해 보자** (공식 단가에 산수만 얹은 것이다):

총 60초짜리 세션에서 CPU active가 5초(나머지 55초는 LLM 응답·툴 호출 대기), 피크 메모리 2GB라고 하자.

| 항목 | 계산 | 비용 |
|---|---|---|
| CPU | 5초 × 1 vCPU × ($0.0895 / 3600) | ≈ $0.000124 |
| 메모리 | 60초 × 2GB × ($0.00945 / 3600) | ≈ $0.000315 |
| **세션 합계** | | **≈ $0.000439** |

두 가지 함의가 있다. 첫째, wall-clock 60초 중 CPU 과금은 5초분뿐이다 — 사전 할당형(pre-allocated) 컴퓨트라면 60초 × vCPU 전체를 냈을 것이다. 둘째, **I/O-heavy 세션에서는 메모리가 CPU보다 비싸다.** CPU 단가가 GB당 메모리 단가의 약 9.5배지만, 과금 시간이 12배 차이 나면 역전된다. 공식 pricing 페이지의 예시(고객 지원 에이전트, 세션 60초·I/O wait 70%·CPU active 18초)도 같은 구조다: CPU $0.0004475, 세션 합계 $0.0007235.

이제 유휴를 더해 보자. 위 세션이 끝난 뒤 기본값인 900초(15분) 동안 유휴 상태로 microVM이 살아 있다가 타임아웃으로 종료된다면:

| 항목 | 계산 | 비용 |
|---|---|---|
| 유휴 메모리 (피크 2GB 유지 가정) | 900초 × 2GB × ($0.00945 / 3600) | ≈ $0.004725 |

**유휴 15분의 메모리 비용이 활성 60초 세션 전체 비용의 10배를 넘는다.** 후속 호출 없이 세션이 그대로 만료되는 패턴(예: 사용자가 한 번 묻고 떠나는 챗봇)이라면, 청구서의 지배 항목은 에이전트가 일한 시간이 아니라 아무것도 안 한 시간이다. 이것이 `idleRuntimeSessionTimeout`을 기본값에 방치하면 안 되는 이유다.

::: warning 미정착 영역
"유휴 중 메모리 과금이 계속된다"는 서술은 pricing 페이지의 계측 구간 정의("... idle periods, until session termination")와 피크 메모리 과금 방식에서 따라 나오는 해석이며, 이 책의 [쿼터와 가격](/10-agentcore/quotas-pricing) 장과 일관된다. 다만 AWS가 "유휴 구간의 메모리 과금"을 한 문장으로 명시한 별도 서술은 찾지 못했다. 비용 모델이 크리티컬한 워크로드라면 짧은 세션을 유휴 상태로 방치하는 실험을 돌려 Cost Explorer에서 직접 검증하라 — 검증 자체가 몇 센트짜리다.
:::

### 세션 수명 파라미터

세션 수명은 `LifecycleConfiguration`의 두 파라미터로 제어한다([공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html)):

| 파라미터 | 기본값 | 범위 (microVM) | 동작 |
|---|---|---|---|
| `idleRuntimeSessionTimeout` | 900초 (15분) | 60~28,800초 | 유휴 지속 시 microVM 종료. **호출마다 타이머 리셋** |
| `maxLifetime` | 28,800초 (8시간) | 60~28,800초 | microVM 생성 시점부터 카운트, **리셋 불가**. 도달 시 종료(세션 자체는 새 microVM으로 재개 가능) |

제약: `idleRuntimeSessionTimeout` ≤ `maxLifetime`. Instances(EC2 capacity provider) 기반 Runtime은 두 값 모두 최대 1,209,600초(14일)까지 허용된다. 종료 자체도 로깅 등으로 최대 15초가 걸린다는 점이 문서에 명시되어 있다 — 짧은 타임아웃으로 세션을 빠르게 회전시키는 설계라면 이 종료 오버헤드도 회전율 계산에 넣어라.

중요한 세부 사항 하나: **코드 배포와 세션 수명이 얽힌다.** 각 microVM은 생성 시점의 `agentRuntimeArtifact`를 사용하므로, 새 버전을 배포해도 살아 있는 세션은 종료될 때까지 구버전으로 돈다(공식 문서 Tip). `maxLifetime`이 곧 "구버전이 살아 있을 수 있는 최대 시간"이다.

### 콜드스타트: 무엇이 확인되었고 무엇이 아닌가

콜드스타트는 microVM 재프로비저닝 구간이다. 새 세션 ID의 첫 호출, 유휴 타임아웃 후 재개, `maxLifetime` 도달 후 재개, 명시적 세션 종료 후 재개 — 모두 같은 비용을 낸다: microVM 부팅 + 컨테이너/코드 로드 + 애플리케이션 초기화(모델 클라이언트 생성, 툴 등록, 설정 로드).

::: warning 미정착 영역
커뮤니티에서 "AgentCore 콜드스타트 약 6초"라는 수치가 돌지만, AWS 공식 문서·pricing·릴리스 노트 어디에서도 확인하지 못했다([쿼터와 가격](/10-agentcore/quotas-pricing)에서 확인 시도한 결과와 동일). 공식적으로 확인되는 수치는 다음뿐이다.

- [Well-Architected Agentic AI Lens AGENTCOST06-BP03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost06-bp03.html): 모델 로딩·툴 등록을 포함한 **애플리케이션 초기화를 2초 미만**으로 유지하라는 목표치(microVM 부팅 시간은 제외), 그리고 **콜드스타트율 10% 초과 시** 세션 어피니티·유휴 타임아웃부터 점검하라는 임계값.

콜드스타트 절대치는 컨테이너 이미지 크기와 초기화 로직에 지배되므로, 남의 수치를 믿지 말고 자체 계측하라 — 방법은 아래 [계측 (SLI)](#계측-sli) 절.
:::

콜드스타트 지연을 줄이는 레버는 세 층이다.

1. **발생 빈도를 줄인다** — 세션 재사용(아래). 가장 효과가 크다.
2. **발생했을 때의 초기화를 줄인다** — 컨테이너 이미지 다이어트, lazy import, 초기화 시 원격 호출(설정 fetch, 웜업 쿼리) 제거. Lens의 2초 목표가 이 층의 SLO다.
3. **발생 시점을 사용자 경로 밖으로 옮긴다** — 웜업(아래).

### 세션 재사용이 기본 해법이다

`runtimeSessionId`를 사용자/대화 단위로 고정하면 후속 호출이 같은 microVM에 붙고 유휴 타이머가 리셋된다. 콜드스타트는 대화당 1회로 상각되고, 초기화 비용(로드된 클라이언트, 파일시스템 상태)도 재사용된다. 이 책의 데모 하네스 호출 스크립트(`demo/builder-harness/invoke.py`)가 이 패턴의 최소 예시다 — 세션 ID를 CLI 인자로 받아 재사용하고, 인자가 없을 때만 새로 발급한다:

```python
# demo/builder-harness/invoke.py (발췌)
session_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4()) + "-session"

response = client.invoke_harness(
    harnessArn=HARNESS_ARN,
    runtimeSessionId=session_id,   # 같은 ID 재호출 = 같은 microVM 재사용
    messages=[{"role": "user", "content": [{"text": text}]}],
)
```

프로덕션에서는 "세션 ID를 어디에 붙일 것인가"가 설계 결정이 된다. 사용자당 하나면 격리는 사용자 단위가 되고(멀티테넌트에서 흔한 선택), 대화(thread)당 하나면 대화 간에도 파일시스템이 격리된다. 어느 쪽이든 세션 ID의 발급·저장·만료 처리를 담당하는 레이어(오케스트레이터 또는 클라이언트)가 명시적으로 있어야 한다 — 세션 만료 후 재사용된 ID는 새 microVM에서 재개되므로, in-memory 상태에 의존하던 코드는 이 시점에 조용히 깨진다. 세션에 담긴 상태와 [Memory](/07-memory/)에 담긴 상태를 처음부터 구분해서 설계하라: 세션(microVM)의 상태는 언제든 증발할 수 있는 캐시고, 대화 연속성은 Memory가 정본이어야 한다.

### 웜업 전략

트래픽이 예측 가능하면 콜드스타트를 사용자 경로 밖으로 옮길 수 있다. 프레임워크 무관한 일반 패턴이다:

- **사전 세션 생성(pre-provisioning)**: 피크 직전(업무 시작 시각, 캠페인 오픈)에 예상 동시성만큼 세션을 미리 만들어 두고, 요청이 오면 풀에서 할당한다. 세션 풀 관리 로직(할당·반환·만료 교체)이 필요해지는 대가가 있다.
- **keep-alive 핑**: 유휴 타임아웃보다 짧은 주기로 가벼운 no-op 호출을 보내 타이머를 리셋한다. 단, 위 계산이 보여주듯 살아 있는 세션은 메모리 과금이 계속되므로, 이것은 "콜드스타트 비용을 유휴 메모리 비용으로 환전"하는 것이다. 세션당 재사용 빈도가 높을 때만 이득이다.
- **초기화 지점 이동**: 첫 사용자 요청이 오기 전에(예: 사용자가 채팅 UI를 여는 시점, 로그인 시점) 세션 생성 호출을 미리 던진다. UI 이벤트가 자연스러운 웜업 트리거가 된다.

어느 패턴이든 **계측 없이 도입하지 마라.** 콜드스타트율이 이미 10% 미만이고 p99에 세션 첫 요청이 잡히지 않는다면, 웜업은 비용만 얹는다.

### Lambda 콜드스타트와의 비교

Lambda에 익숙한 팀을 위한 준거점이다. 둘 다 Firecracker microVM 기반이지만 콜드스타트 완화의 축이 다르다.

Lambda의 대표 해법인 [SnapStart](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html)는 **스냅샷 접근**이다: 함수 버전 게시 시점에 초기화 완료된 실행 환경의 메모리·디스크 스냅샷을 떠 두고, 호출 시 스냅샷에서 resume한다. AWS는 "as low as sub-second startup performance"를 공식적으로 제시한다. 핵심 특성은 **하나의 스냅샷이 여러 실행 환경의 초기 상태로 복제**된다는 것 — 그래서 초기화 시점에 만든 고유값(난수 시드, 커넥션)의 uniqueness 문제를 코드가 직접 다뤄야 한다.

AgentCore microVM의 축은 **세션 재사용**이다: 스냅샷을 복제하는 게 아니라, 살아 있는 microVM 하나를 같은 세션의 후속 호출이 계속 쓴다. 상태가 복제되지 않으므로 uniqueness 문제가 없고, 오히려 세션 내 상태 누적(파일시스템, 프로세스 메모리)이 기능이다. 대신 재사용은 세션 단위로만 일어나므로 **새 세션의 첫 호출은 항상 풀 콜드스타트**다 — Lambda라면 다른 사용자의 요청이 데워 놓은 실행 환경을 물려받을 수 있지만, AgentCore의 세션 격리 모델에서는 그런 교차 재사용이 없다. 요약하면: Lambda는 "초기화 결과를 복제해서 나눠 쓰는" 모델, AgentCore는 "세션마다 한 번 초기화하고 대화 내내 우려먹는" 모델이다. 콜드스타트 최적화 노력의 방향이 다를 수밖에 없다 — Lambda에서는 스냅샷 친화적 초기화, AgentCore에서는 세션 ID 수명 설계.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 대화형 에이전트, 호출 간격 수 분 이내 | 대화 단위 세션 ID 고정 + `idleRuntimeSessionTimeout`을 대화 갭 p95 수준(예: 10~15분)으로 | 대화당 콜드스타트 1회, 유휴 타이머가 호출마다 리셋되므로 활발한 대화는 계속 웜 상태 | 대화가 끝난 뒤 타임아웃만큼의 유휴 메모리 과금 꼬리 |
| 단발성 요청 위주 (한 번 묻고 떠남) | 짧은 `idleRuntimeSessionTimeout`(60~120초) | 후속 호출이 없는데 15분 유휴 과금을 낼 이유가 없음 — 위 계산에서 유휴가 세션 비용의 10배였다 | 드물게 돌아오는 사용자는 콜드스타트를 다시 겪음 |
| 배치/파이프라인 에이전트 (야간 대량 처리) | 워커당 세션 재사용 + 긴 idle 허용, 작업 종료 시 명시적 세션 종료 | 처리량이 목적이므로 콜드스타트를 작업 시작 시 1회로 상각, 끝나면 유휴 없이 정리 | 세션 정리 로직 누락 시 유휴 과금 — 종료를 파이프라인 단계로 강제하라 |
| 예측 가능한 피크 (업무 시작, 이벤트) | 피크 직전 사전 세션 생성 | 콜드스타트를 사용자 경로 밖으로 이동 | 세션 풀 관리 복잡도 + 예측 빗나가면 유휴 과금만 발생 |
| 멀티테넌트, 격리 최우선 | 테넌트(또는 사용자)당 세션 + 짧은 idle | 세션=microVM 격리를 보안 경계로 활용 | 테넌트별 트래픽이 산발적이면 콜드스타트율 상승 — 격리 요구가 진짜인지 먼저 확인 |
| 8시간 초과 장기 실행, GPU 필요 | Runtime Instances (idle/maxLifetime 최대 14일) | microVM의 8시간 `maxLifetime` 한계 회피 | 과금 모델이 EC2 기반으로 바뀜 — [쿼터와 가격](/10-agentcore/quotas-pricing)의 Instances 절 참조 |
| 배포 롤아웃 속도가 중요 | `maxLifetime` 단축 (예: 2~4시간) | 살아 있는 세션은 생성 시점 코드로 돌므로, `maxLifetime`이 구버전 잔존 상한 | 장수 세션이 중간에 끊기고 재개 시 콜드스타트 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 모든 요청의 지연이 균일하게 높음 (p50부터) | 호출 핸들러가 매번 새 `runtimeSessionId` 발급 → 전 요청 콜드스타트 | 콜드스타트율 SLI 확인 — 100%에 가깝다면 확정. 클라이언트 코드에서 세션 ID 발급 위치 확인 | 세션 ID를 대화/사용자 단위로 고정하고 클라이언트/오케스트레이터에 저장 |
| p99만 주기적으로 튐 | 유휴 타임아웃 만료 후 첫 재개 호출이 콜드스타트 | p99 스파이크 간격과 `idleRuntimeSessionTimeout` 값의 상관 확인 ([지연 진단 체크리스트](/02-performance/latency-checklist) 7단계) | 대화 갭 분포를 보고 타임아웃 조정, 또는 keep-alive — 단 유휴 비용과 교환임을 계산 후 |
| Runtime 비용이 트래픽 대비 과도 | 유휴 메모리 과금 — 긴 타임아웃 + 낮은 세션 재사용률 조합 | 세션당 (유휴 시간 × 피크 메모리)를 CloudWatch에서 산출, Cost Explorer의 GB-hour 항목과 대조 | 타임아웃 단축, 피크 메모리를 키우는 초기화 로직(대형 인메모리 캐시 등) 점검 |
| 정확히 8시간(또는 `maxLifetime`)마다 세션 단절 + 콜드스타트 | `maxLifetime` 도달 — 유휴 타이머와 달리 리셋 불가 | 세션 생성 시각과 단절 시각의 차가 `maxLifetime`과 일치하는지 확인 | 장기 실행이면 체크포인트 후 재개 설계, 또는 Instances로 전환 (최대 14일) |
| 배포했는데 일부 트래픽이 구버전 응답 | 살아 있는 microVM은 생성 시점 코드 사용 | 응답에 버전 태그를 심어 세션 나이와 대조 | 롤아웃 완료 = "모든 기존 세션 만료"임을 배포 절차에 반영, 급하면 세션 명시적 종료 |
| 세션 재개 후 에이전트가 이전 맥락/파일을 잃음 | 세션 만료 후 같은 ID로 재개 → 새 microVM, 파일시스템·프로세스 상태 소실 | 콜드스타트 직후 요청에서만 재현되는지 확인 | 대화 연속성은 [Memory](/07-memory/)를 정본으로, microVM 내 상태는 캐시로만 취급 |
| 세션 회전율 높은 워크로드에서 종료 지연 누적 | 종료 자체가 최대 15초 소요 (로깅 등, 공식 문서 명시) | 짧은 타임아웃(60초대) + 높은 세션 생성률 조합인지 확인 | 회전율 계산에 종료 오버헤드 반영, 신규 세션 생성률 쿼터(계정당 TPS)도 함께 점검 — [쿼터와 가격](/10-agentcore/quotas-pricing) |
| 웜업 도입 후 비용 증가, 지연은 그대로 | 콜드스타트율이 애초에 낮았거나, keep-alive 주기가 유휴 과금만 연장 | 웜업 도입 전후의 콜드스타트율·p99·GB-hour 비교 | 계측이 임계값(10%) 초과를 보일 때만 웜업 유지, 아니면 제거 |

## 안티패턴

- ❌ 요청 핸들러 안에서 `uuid.uuid4()`로 세션 ID 생성 → ✅ 세션 ID는 대화/사용자 수명에 묶어 발급·저장하고, 핸들러는 조회만 한다 (`demo/builder-harness/invoke.py`처럼 재사용 경로를 기본으로)
- ❌ "I/O wait는 공짜니까 세션을 무한정 열어둬도 된다" → ✅ 공짜인 것은 CPU뿐이다. 메모리는 유휴 포함 세션 수명 전체에 피크 기준 과금 — 유휴 15분이 활성 60초의 10배 비용이 될 수 있다
- ❌ 콜드스타트가 느리다고 무조건 keep-alive 핑 도입 → ✅ 먼저 콜드스타트율을 계측하고(임계 10%), 초기화 2초 목표부터 달성한 뒤, 핑의 유휴 메모리 비용과 비교해 결정
- ❌ 커뮤니티의 "콜드스타트 6초" 수치를 SLO 근거로 사용 → ✅ 공식 확인된 수치가 아니다. 자기 워크로드(이미지 크기·초기화 로직)에서 직접 계측한 분포를 근거로
- ❌ 초기화 코드에서 원격 설정 fetch·웜업 쿼리·대형 모델 아티팩트 다운로드 → ✅ 초기화는 2초 목표에 맞게 다이어트하고, 무거운 준비는 lazy 초기화 또는 이미지 빌드 시점으로
- ❌ 대화 연속성을 microVM 파일시스템/프로세스 메모리에 의존 → ✅ 세션 상태는 증발 가능한 캐시. 정본은 Memory/외부 스토어에 두고 콜드스타트 후 재수화(rehydrate)
- ❌ `idleRuntimeSessionTimeout`·`maxLifetime`을 기본값(15분/8시간)에 방치 → ✅ 결정 표에 따라 워크로드별 명시 설정 — 기본값은 대화형에는 대체로 무난하지만 단발성·배치에는 돈 낭비다

## 계측 (SLI)

콜드스타트·유휴를 관리하려면 최소 다음 네 SLI를 대시보드에 올려라.

1. **콜드스타트율** = 신규 microVM 프로비저닝을 동반한 호출 / 전체 호출. 세션 첫 호출 여부는 클라이언트가 알고 있으므로(새 ID 발급 여부), 호출 시점에 태깅하면 가장 정확하다. [Well-Architected Lens](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost06-bp03.html)의 임계값은 **10%** — 초과 시 세션 어피니티와 유휴 타임아웃부터 점검.
2. **초기화 시간 분포**: 애플리케이션 초기화(모델 클라이언트·툴 등록·설정 로드) 구간을 AgentCore Observability의 span으로 분리 계측. 목표 **2초 미만**(같은 Lens, microVM 부팅 제외). 콜드 호출과 웜 호출의 E2E 지연 차이도 함께 — 그 차이가 곧 "사용자가 체감하는 콜드스타트 세금"이다.
3. **세션 재사용률과 세션당 호출 수**: 세션당 호출 수가 1에 가깝다면 세션 재사용 설계가 작동하지 않는 것이다 — 콜드스타트율과 함께 보면 원인(클라이언트 버그 vs. 트래픽 특성)이 갈린다.
4. **유휴 비용 비율** = 유휴 구간 GB-초 / 세션 전체 GB-초. 세션별 (마지막 호출 종료 ~ 세션 종료) 구간 길이 × 피크 메모리로 근사할 수 있다. 이 비율이 높으면 타임아웃 단축이, 콜드스타트율이 높으면 연장이 답이다 — 두 SLI가 반대 방향을 가리키면 워크로드를 세그먼트로 쪼개서 Runtime을 분리하는 것을 검토하라(설정은 Runtime 단위이므로).

지연 문제로 진입했다면 계측 순서 자체는 [지연 진단 체크리스트](/02-performance/latency-checklist)를 따르라 — 콜드스타트는 7단계이고, 그 앞 단계(루프·prefill·툴)가 원인인 경우가 더 많다.

## 체크리스트

- [ ] 세션 ID 발급이 요청 단위가 아니라 대화/사용자 단위인가 — 핸들러 안의 `uuid4()` 한 줄을 의심하라
- [ ] `idleRuntimeSessionTimeout`·`maxLifetime`을 워크로드별로 명시 설정했는가 (기본 900초/28,800초 방치 금지)
- [ ] 유휴 타임아웃 값이 실제 대화 갭 분포(p95)에 근거하는가, 아니면 감인가
- [ ] 콜드스타트율을 계측하고 있고 10% 임계 대비 현재 위치를 아는가
- [ ] 애플리케이션 초기화 시간이 2초 미만인가 — 초기화 중 원격 호출·대형 다운로드를 제거했는가
- [ ] 유휴 구간 GB-초를 세션 전체 대비 비율로 추적하는가 (유휴가 지배 항목이면 타임아웃 단축)
- [ ] 세션 만료 후 재개 시 상태 소실을 전제로 설계했는가 — 대화 연속성의 정본이 Memory/외부 스토어에 있는가
- [ ] 배포 롤아웃 절차가 "기존 세션은 `maxLifetime`까지 구버전"임을 반영하는가
- [ ] 웜업(사전 생성·keep-alive)을 도입했다면, 도입 전후의 콜드스타트율·p99·GB-hour 비교 데이터가 있는가
- [ ] 콜드스타트 절대치를 자체 환경에서 계측했는가 — 외부 수치(공식 미확인)를 SLO 근거로 쓰지 않았는가
- [ ] 세션 생성률·동시 세션 수 쿼터를 함께 점검했는가 — 숫자는 [쿼터와 가격](/10-agentcore/quotas-pricing) 참조

## 참고

- [Amazon Bedrock AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) — vCPU-hour/GB-hour 단가, I/O wait 중 CPU 무과금, 계측 구간 정의("... idle periods, until session termination"), 30~70% I/O wait 서술, 고객 지원 에이전트 예시 계산. 확인 시점 2026-08. 가격은 변경될 수 있으므로 재확인할 것.
- [Configure Amazon Bedrock AgentCore lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html) — `idleRuntimeSessionTimeout`(기본 900초)·`maxLifetime`(기본 28,800초) 범위·제약, 세션=microVM·타이머 리셋 동작, 종료 최대 15초, 세션의 코드 버전 고정, 유스케이스별 권장 타임아웃 표.
- [Use isolated sessions for agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html) — 세션 상태 전이(Active/Idle/Stopped)와 격리 모델.
- [AWS Well-Architected Agentic AI Lens — AGENTCOST06-BP03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost06-bp03.html) — 초기화 2초 목표, 콜드스타트율 10% 임계값.
- [Improving startup performance with Lambda SnapStart](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html) — 스냅샷 방식("as low as sub-second startup performance"), uniqueness 고려사항 — microVM 세션 재사용과의 비교 준거.
- [Amazon Bedrock AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/) — 소비 기반 과금 모델의 공식 서술.
- 이 책 내부: [AgentCore 쿼터와 가격](/10-agentcore/quotas-pricing) (단가·쿼터 숫자의 정본), [지연 진단 체크리스트](/02-performance/latency-checklist) (콜드스타트가 7단계인 진단 런북), `demo/builder-harness/invoke.py` (세션 재사용 호출 예시).
