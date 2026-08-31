---
title: Tools 심화
description: AgentCore Code Interpreter와 Browser 툴의 샌드박스 격리, 세션 모델, 보안 경계를 다룬다.
outline: [2, 3]
---

# Tools 심화

::: tip 이 장에서 얻는 것
- Code Interpreter와 Browser가 왜 별도의 "Tool" 카테고리로 분리되어 있는지 — Runtime과 같은 microVM 격리를 재사용하면서도 다른 세션 모델을 갖는 이유
- Code Interpreter의 언어 지원, 네트워크 모드(Sandbox/Public/VPC), 세션 타임아웃·쿼터의 정확한 수치와 출처
- Browser 툴의 Automation/Live View 엔드포인트 구조와, "브라우저가 임의의 외부 사이트에 접근해야 한다"는 태생적 특성이 egress 통제를 어떻게 다르게 만드는지
- Anthropic이 2025년 11월에 공개한 "code execution with MCP" 패턴 — 에이전트가 MCP 툴을 파일시스템의 함수처럼 호출해 토큰을 절감하는 기법 — 이 실제로 실행될 샌드박스로 Code Interpreter를 쓸 수 있다는 연결점
- 두 툴 모두에서 공통되는 실패 모드와 프로덕션 체크리스트
:::

## 왜 문제가 되는가

LLM은 자연어로 추론하지만, 데이터 집계·수치 계산·파일 포맷 변환·정확한 문자열 처리 같은 작업에서는 코드를 생성해 실행하는 쪽이 훨씬 정확하고 저렴하다. 문제는 "LLM이 생성한 코드"를 누가 실행하느냐다. 에이전트 프로세스 안에서 `exec()`로 직접 실행하면, 그 코드가 호스트 파일시스템·환경변수·네트워크에 무제한 접근하게 된다 — 프롬프트 인젝션이나 모델의 실수 하나로 전체 실행 환경이 위협받는다. AgentCore Code Interpreter는 이 문제를 "에이전트가 생성한 신뢰할 수 없는 코드를 격리된 환경에서 실행"하는 관리형 서비스로 해결한다.

Browser 툴은 다른 축의 문제를 다룬다. 웹 UI 자동화(폼 제출, 레거시 웹앱 조작, 데이터 추출)를 하려면 에이전트가 실제 브라우저 엔진을 구동해야 하는데, 이 브라우저는 정의상 임의의 외부 사이트에 접근해야 한다. Runtime이나 Code Interpreter의 "아웃바운드를 최대한 좁힌다"는 기본 전략이 Browser에는 그대로 적용되지 않는다 — 툴의 존재 목적 자체가 인터넷/사내 웹앱에 접근하는 것이기 때문이다. 그래서 Browser 툴의 보안 모델은 "얼마나 격리되어 있는가"보다 "어디까지 접근을 허용하고, 그 접근을 어떻게 관찰·기록하는가"에 초점이 맞춰진다.

이 장은 두 툴을 각각 다루면서, [Part 1의 MCP 서버 설계](/01-agent-design/mcp-server-design)에서 언급하는 "code execution with MCP" 패턴이 이 실행 환경들과 어떻게 맞물리는지, 그리고 [Part 9의 egress 제어](/09-authorization/egress-control)에서 다룬 네트워크 통제 원칙이 Tools 레벨에서 어떻게 재현되는지를 짚는다.

## 핵심 개념

### Code Interpreter — 격리 모델과 언어 지원

AgentCore Code Interpreter는 에이전트가 생성한 코드를 실행하고 출력·에러·시각화 결과를 반환하는 관리형 샌드박스다. 지원 언어는 Python, JavaScript, TypeScript다.[^ci-blog] Python 런타임에는 pandas, numpy, matplotlib, scikit-learn, scipy, seaborn, bokeh, sympy, statsmodels 등 데이터 과학 라이브러리가 사전 설치되어 있고, 샌드박스 경계 내에서 추가 패키지 설치도 지원한다.[^ci-blog]

격리 메커니즘은 Runtime과 동일한 계열이다 — **각 Code Interpreter 세션은 격리된 CPU·메모리·파일시스템을 가진 전용 microVM에서 실행되며, 한 사용자의 세션이 다른 사용자의 세션 데이터에 접근할 수 없다. 세션이 종료되면 microVM은 완전히 폐기되고 메모리가 sanitize되어 세션 간 데이터 유출 가능성을 제거한다.**[^ci-session] 이는 [Runtime 심화 장](/10-agentcore/runtime-deep-dive)에서 다루는 microVM 격리 모델과 사실상 같은 격리 기법을 Tools 레이어에서도 재사용하고 있다는 뜻이다 — Runtime이 에이전트 코드 전체를 담는 컨테이너라면, Code Interpreter는 "에이전트가 즉석에서 생성한 코드"만을 위한 훨씬 좁고 단명한(short-lived) microVM이다.

### Code Interpreter — 세션 모델과 쿼터

Code Interpreter는 세션 기반 모델이다. `CreateCodeInterpreter`로 리소스를 만든 뒤 `StartCodeInterpreterSession`으로 세션을 열면, 세션은 설정한 타임아웃(기본 15분/900초) 후 자동 종료된다.[^ci-session-mgmt] 동일한 Code Interpreter에서 여러 세션을 동시에 열 수 있고, 각 세션은 독립된 상태·환경을 유지한다.[^ci-session-mgmt]

계정 단위 쿼터(2026년 8월 기준, 리전에 따라 다를 수 있음)는 다음과 같다.[^ci-quota]

| 항목 | 기본값 | 조정 가능 여부 |
|---|---|---|
| 계정당 동시 활성 세션 | 1,000 | 가능(지원 티켓) |
| 계정당 Code Interpreter 리소스 총량 | 1,000 | 가능(지원 티켓) |
| 세션당 하드웨어(vCPU/메모리) | 2 vCPU / 8GB | 불가 |
| 동기 요청 타임아웃 | 15분 | 불가 |
| 요청/응답 최대 페이로드 | 100MB | 불가 |
| 비동기 명령 최대 실행 시간 | 8시간 | 불가 |
| 세션당 디스크 크기 | 10GB | 불가 |

세션당 하드웨어가 2vCPU/8GB로 고정되어 있고 조정 불가라는 점은 설계에 직접적인 함의를 갖는다 — 대용량 데이터 분석 워크로드를 Code Interpreter에 밀어넣을 때는 세션 하나가 처리할 수 있는 데이터 크기를 이 메모리 상한 기준으로 미리 가늠해야 하며, 필요하면 Amazon S3를 경유해 파일을 청크 단위로 스트리밍하는 설계가 필요하다. 인라인 파일 업로드는 최대 100MB, S3를 통한 업로드는 최대 5GB까지 지원된다.[^ci-tool]

### Code Interpreter — 네트워크 모드

Code Interpreter는 세 가지 네트워크 모드를 지원한다.[^ci-netmode]

- **Sandbox 모드**: AWS 서비스에 대한 제한적 접근만 허용(예: Amazon S3를 통한 데이터 입출력). 인터넷 전반에는 접근할 수 없다.
- **Public 네트워크 모드**: 퍼블릭 인터넷 접근을 허용 — PyPI에서 패키지를 내려받거나 외부 API를 호출하는 워크로드에 적합하지만, 그만큼 egress 표면이 넓어진다.
- **VPC 모드**: 사용자의 VPC에 연결해 내부 데이터베이스·내부 API 등 프라이빗 리소스에 접근할 수 있게 하면서, 퍼블릭 인터넷으로부터는 격리를 유지한다.

이 세 모드는 [egress 제어 장](/09-authorization/egress-control)에서 다룬 AgentCore Runtime의 `PUBLIC`/`VPC` 네트워크 모드와 개념적으로 같은 축 위에 있다 — 다만 Code Interpreter는 그 사이에 "제한적 AWS 서비스 접근만 허용"하는 Sandbox 모드를 하나 더 두고 있다는 점이 다르다. 프로덕션에서 신뢰할 수 없는(에이전트가 즉석에서 생성한) 코드를 실행한다면, 기본값인 Public 모드를 그대로 두지 말고 Sandbox 또는 VPC 모드로 명시적으로 좁혀야 한다.

### Browser 툴 — Automation과 Live View, 두 개의 엔드포인트

Browser 툴도 세션 기반이다. Browser 리소스를 만들 때 네트워크 설정, 세션 리플레이용 레코딩 설정, 그리고 IAM 실행 역할(어떤 AWS 리소스에 접근할 수 있는지)을 지정한다.[^browser-fundamentals] 세션을 시작하면 두 개의 서로 다른 엔드포인트가 열린다.[^browser-fundamentals]

- **Automation 엔드포인트**: WebSocket 기반 스트리밍 API로, 에이전트가 페이지 탐색·클릭·폼 채우기·스크린샷 등의 브라우저 동작을 수행한다. Playwright(Chrome DevTools Protocol 경유)나 `browser-use` 같은 라이브러리로 이 인터페이스를 감쌀 수 있다.[^browser-blog]
- **Live View 엔드포인트**: 사람이 실시간으로 브라우저 세션을 관찰하고, 필요하면 직접 개입해 조작할 수 있는 스트림이다.[^browser-fundamentals]

Live View는 단순한 디버깅 편의 기능이 아니라 보안·운영상 의미가 있다 — 브라우저 세션이 로그인·결제·개인정보 입력처럼 되돌리기 어려운 동작을 수행할 가능성이 있는 워크플로에서는, 에이전트가 완전 자율로 실행하게 두는 대신 사람이 Live View로 지켜보다가 개입할 수 있는 human-in-the-loop 경로를 마련하는 것이 실질적인 방어선이 된다.

### Browser 툴 — 격리와 세션 타임아웃

Browser 세션 역시 AgentCore Tools의 공통 격리 모델을 따른다 — 각 세션은 격리된 CPU·메모리·파일시스템을 가진 전용 microVM에서 실행되고, 세션 종료 시 완전히 폐기·메모리 sanitize된다.[^ci-session] 동시에 개념 가이드는 "각 대화(conversation)마다 격리된 브라우저 세션을 가져 사용자 간 교차 오염을 방지한다"고도 설명한다.[^browser-sessions]

세션 타임아웃에는 문서 간 수치 차이가 있어 주의가 필요하다. 개념 가이드(Fundamentals)는 기본 타임아웃을 15분이라고 설명하는 반면,[^browser-fundamentals] Python SDK의 `start_browser_session` 오퍼레이션 레퍼런스는 `session_timeout_seconds`의 기본값을 3600초(1시간), 권장 최소 60초, 최대 28,800초(8시간)로 명시한다.[^browser-sdk]

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 위 두 수치(15분 vs 1시간)는 서로 다른 공식 문서 페이지에서 나온 값으로, 어느 쪽이 최신·정확한 기본값인지는 이 문서 작성 시점(2026년 8월)에 명확히 재확인되지 않았다. 프로덕션에서는 기본값에 의존하지 말고 `StartBrowserSession` 호출 시 `sessionTimeoutSeconds`를 명시적으로 지정하는 것이 안전하다.

### Browser 툴 — egress는 "차단"이 아니라 "관측·제약"의 문제

Browser 툴은 태생적으로 임의의 외부 사이트에 접근해야 하므로, [egress 제어 장](/09-authorization/egress-control)에서 다룬 "SNI 기반 도메인 allowlist로 아웃바운드를 좁힌다"는 원칙을 Runtime/Code Interpreter만큼 단순하게 적용할 수 없다 — 목적지 도메인이 애초에 가변적이고 사전에 완전히 열거할 수 없기 때문이다. 대신 다음 두 가지가 실질적인 통제 지점이 된다.

첫째, Browser 리소스를 생성할 때 네트워크 설정(퍼블릭 vs VPC)을 지정할 수 있고, VPC 모드로 구성하면 브라우저 트래픽을 사용자의 VPC로 라우팅해 그 안에 배치된 AWS Network Firewall이나 프록시를 거치게 만들 수 있다.[^browser-cdk] 이렇게 하면 "완전 차단"은 아니어도 "회사가 통제하는 경계를 반드시 통과"하는 구조를 만들 수 있다.

둘째, AWS SDK 레퍼런스에 노출된 `GetBrowserSessionResponse` 구조체에는 `proxy_configuration`, `certificates`, `enterprise_policies`, `filesystem_configurations` 같은 필드가 존재한다.[^browser-ruby-sdk] 이는 Browser 세션이 프록시 구성이나 사내 정책(예: 사내 CA 인증서 신뢰, 접근 가능 도메인 정책)을 세션 단위로 주입할 수 있는 여지가 있음을 시사한다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> `proxy_configuration`·`enterprise_policies` 필드의 정확한 사용법과 설정 가능한 옵션(도메인 allowlist 지정 가능 여부 등)은 이 문서 작성 시점에 개발자 가이드 본문에서 상세 스펙을 확인하지 못했다. SDK 타입 레퍼런스에 필드가 존재한다는 사실만 확인했고, 실제 기능 범위는 공식 개발자 가이드에서 별도로 확인해야 한다.

### Code Interpreter와 "code execution with MCP" 패턴의 연결

[Part 1의 MCP 서버 설계 장](/01-agent-design/mcp-server-design)에서 다루는 "code execution with MCP" 패턴(Anthropic, 2025년 11월 엔지니어링 블로그)[^anthropic-cem]은 에이전트가 MCP 툴을 직접 호출하는 대신, 파일시스템에 노출된 TypeScript/Python 함수처럼 다루는 코드를 작성해 실행하게 하는 기법이다. 이 패턴의 핵심 이점은 모델 컨텍스트에 각 툴의 전체 스키마와 중간 결과를 다 채워 넣지 않고, 코드가 그 결과를 로컬에서 필터링·가공한 뒤 요약만 컨텍스트로 돌려주게 함으로써 토큰 사용량을 줄이는 것이다.

이 패턴을 실제로 프로덕션에 적용하려면 "에이전트가 즉석에서 작성한, 검증되지 않은 코드를 실행할 위치"가 필요하다 — 이것이 정확히 Code Interpreter가 제공하는 것이다. Code Interpreter의 격리된 microVM 세션에서 이 코드를 실행하면, MCP 서버 호출 결과 가공 로직이 에이전트 프로세스나 호스트 인프라를 직접 건드리지 않고 격리된 경계 안에서만 동작한다. 다만 이 구성에서는 Code Interpreter 세션이 MCP 서버(또는 그 결과가 참조하는 리소스)에 네트워크로 접근할 수 있어야 하므로, 앞서 다룬 네트워크 모드 선택(Sandbox/Public/VPC)이 이 패턴의 보안 경계를 실질적으로 결정한다. MCP 서버가 VPC 내부에 있다면 VPC 모드가, 퍼블릭 SaaS API라면 Sandbox 모드로는 접근이 막히므로 Public 또는 VPC+egress 통제 조합이 필요하다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 신뢰할 수 없는 에이전트 생성 코드를 실행해야 함 | Code Interpreter, 기본은 Sandbox 네트워크 모드 | microVM 격리 + 인터넷 egress 자체가 없어 최소 공격면 | 패키지 설치·외부 API 호출이 필요하면 Sandbox로는 부족 |
| Code Interpreter에서 내부 DB·사내 API 접근이 필요 | VPC 네트워크 모드 | 퍼블릭 인터넷 격리를 유지하면서 프라이빗 리소스 접근 | VPC 설계·서브넷/보안그룹 구성 비용 발생 |
| "code execution with MCP" 패턴의 실행 환경이 필요 | Code Interpreter + 대상 MCP 서버 도달 가능한 네트워크 모드 | 격리된 샌드박스에서 에이전트가 만든 오케스트레이션 코드를 안전하게 실행 | MCP 서버가 VPC 내부라면 VPC 모드 필수, 레이턴시 증가 |
| 웹 UI 자동화(폼 제출·데이터 추출)가 필요 | Browser 툴, VPC 모드 + 프록시/Network Firewall 경유 | 사내 웹앱·레거시 시스템 접근을 자사 네트워크 경계 안에서 통제 가능 | 완전한 도메인 allowlist를 사전에 정의하기 어려움 |
| 결제·로그인 등 되돌리기 어려운 동작이 포함된 브라우저 자동화 | Browser 툴 + Live View로 human-in-the-loop 관찰/개입 | 자율 실행의 리스크를 사람이 실시간으로 완화 | 완전 자동화 대비 처리량·속도 저하 |
| 대용량(수 GB) 데이터셋 분석 | Code Interpreter + S3 경유 파일 스트리밍 | 세션당 2vCPU/8GB, 디스크 10GB 고정 상한을 우회 | 청크 처리 로직을 에이전트 코드에 추가 설계해야 함 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| Code Interpreter 세션에서 `pip install`이 조용히 실패하거나 외부 API 호출이 타임아웃 | 네트워크 모드가 Sandbox(제한적 AWS 서비스 접근만 허용)로 설정됨 | 리소스 생성 시 지정한 네트워크 모드 확인 | Public 또는 VPC(+ NAT/엔드포인트) 모드로 변경, 필요한 egress만 열기 |
| Code Interpreter 세션이 대용량 처리 중 OOM 또는 디스크 부족으로 실패 | 세션 하드웨어가 2vCPU/8GB, 디스크 10GB로 고정(조정 불가) | CloudWatch/세션 로그에서 메모리·디스크 사용량 확인 | S3로 데이터를 청크 단위 스트리밍, 집계 후 반환하는 구조로 코드 재설계 |
| 비동기 코드 실행이 8시간을 넘기지 못하고 강제 종료 | 비동기 명령 최대 실행 시간(8시간) 고정 상한 | 실행 시간이 긴 배치 작업인지 확인 | 체크포인트를 두고 여러 세션으로 분할 실행, 중간 결과는 S3에 저장 |
| Browser 세션이 예상보다 일찍(또는 늦게) 끊김 | 세션 타임아웃 기본값을 문서 값(15분 또는 1시간)에 의존, 명시적으로 지정하지 않음 | `StartBrowserSession` 호출 시 `sessionTimeoutSeconds` 파라미터 확인 | 워크로드에 맞게 타임아웃을 명시적으로 지정(최소 60초, 최대 28,800초) |
| 브라우저 자동화가 사내 웹앱에는 도달하지만 공용 리서치 사이트는 막혀 있음 | Browser 리소스가 VPC 모드로만 구성되어 퍼블릭 인터넷 경로가 없음 | 리소스의 네트워크 설정과 라우팅 테이블 확인 | 프록시/Network Firewall을 통한 통제된 퍼블릭 egress 경로 추가, 또는 목적별로 별도 Browser 리소스 분리 |
| "code execution with MCP" 패턴 도입 후 Code Interpreter 세션이 MCP 서버에 연결 못 함 | MCP 서버가 VPC 내부에 있는데 Code Interpreter는 Sandbox/Public 모드 | 세션에서 MCP 서버 엔드포인트로 curl/probe 시도 | Code Interpreter를 MCP 서버와 같은 VPC로 연결 (VPC 모드) |
| Live View로 지켜봐야 할 결제/로그인 흐름을 완전 자율 모드로 돌려 되돌릴 수 없는 동작 발생 | Browser 툴 자동화를 human-in-the-loop 개입 경로 없이 프로덕션에 배포 | 워크플로에 Live View 연동 및 승인 게이트 존재 여부 확인 | 민감 동작 전 단계에 Live View 관찰/승인 스텝 추가 |

## 안티패턴

- ❌ Code Interpreter를 항상 기본(Public) 네트워크 모드로 두고 "샌드박스니까 안전하다"고 가정한다 → ✅ 신뢰할 수 없는 코드 실행이 목적이라면 Sandbox 또는 VPC 모드로 명시적으로 좁히고, Public 모드는 패키지 설치가 반드시 필요한 워크로드에만 의도적으로 선택한다.
- ❌ Browser 툴에도 Runtime/Code Interpreter와 동일한 "egress를 최대한 막는다" 전략을 그대로 적용하려 한다 → ✅ Browser는 목적 자체가 외부 접근이므로, 완전 차단 대신 VPC 경유 + Network Firewall/프록시로 "통제된 경로만 허용"하는 전략을 쓴다.
- ❌ 세션 타임아웃 기본값을 문서에서 확인하지 않고 방치한 채 장시간 작업을 돌린다 → ✅ `sessionTimeoutSeconds`를 워크로드 특성에 맞게 항상 명시적으로 지정한다.
- ❌ "code execution with MCP" 패턴을 도입하면서 그 코드를 에이전트 런타임 프로세스 안에서 직접 `exec`한다 → ✅ Code Interpreter 같은 격리된 샌드박스에서 실행해, 생성된 코드가 호스트 자체를 건드리지 못하게 한다.
- ❌ 결제·계정 변경처럼 되돌리기 어려운 브라우저 동작을 Live View 없이 완전 자율로 실행한다 → ✅ 그런 동작 앞에는 Live View 관찰 또는 사람의 승인 게이트를 둔다.

## 계측 (SLI)

공식 문서에 Code Interpreter/Browser 전용 CloudWatch 메트릭 목록이 상세히 규정되어 있는지는 이 조사에서 확인하지 못했다. [Observability 심화 장](/10-agentcore/observability-deep-dive)에서 다루는 OTel `gen_ai` 스팬 계측 원칙을 그대로 적용한다는 전제로, 최소한 다음을 계측 대상으로 삼는다.

- **세션 생성 실패율**: `StartCodeInterpreterSession`/`StartBrowserSession` 호출의 에러율(쿼터 초과, 네트워크 모드 설정 오류 등 원인별로 태깅)
- **세션 수명 분포**: 세션이 타임아웃으로 종료되는지, 명시적 `Stop`으로 종료되는지 — 타임아웃 종료 비율이 높다면 타임아웃 값이 워크로드와 맞지 않는다는 신호
- **동시 활성 세션 수 대 쿼터**: 계정 쿼터(Code Interpreter 기본 1,000)에 대한 사용률 — 트래픽 증가 전에 지원 티켓으로 상향 조정 필요 시점을 파악
- **네트워크 모드별 요청 분포**: 어떤 세션이 Sandbox/Public/VPC 중 어느 모드로 실행되는지 태깅해, 의도치 않게 Public 모드로 실행되는 워크로드가 없는지 주기적으로 점검
- **Browser Live View 개입 횟수**: 사람이 Live View로 개입한 세션의 비율 — 자율 실행이 실패하거나 위험한 동작 앞에서 얼마나 자주 인간 개입이 필요했는지의 지표

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 위 지표는 이 장의 원칙(격리 경계, 세션 모델, 네트워크 모드)에서 도출한 권고이며, AgentCore가 실제로 이 이름의 메트릭을 CloudWatch에 노출하는지는 별도로 확인이 필요하다.

## 체크리스트

- [ ] Code Interpreter 네트워크 모드를 기본값(Public)에 방치하지 않고 워크로드에 맞게 Sandbox/Public/VPC 중 하나를 명시적으로 선택했다
- [ ] Code Interpreter 세션의 하드웨어 상한(2vCPU/8GB, 디스크 10GB)을 초과할 수 있는 워크로드라면 S3 경유 스트리밍 설계를 적용했다
- [ ] Code Interpreter/Browser 모두에서 `sessionTimeoutSeconds`를 기본값에 의존하지 않고 명시적으로 지정했다
- [ ] Browser 툴의 아웃바운드가 필요한 경우 VPC 모드 + Network Firewall/프록시로 경로를 통제하고 있다 (egress-control.md의 도메인 allowlist 원칙 참고)
- [ ] 되돌리기 어려운 브라우저 동작(결제·계정 변경·민감 정보 제출) 앞에 Live View 관찰 또는 승인 게이트를 두었다
- [ ] "code execution with MCP" 패턴을 쓴다면, 생성된 코드를 에이전트 프로세스가 아니라 Code Interpreter 같은 격리된 샌드박스에서 실행하도록 구성했다
- [ ] Code Interpreter/Browser 세션 생성 실패율, 동시 세션 수 대 쿼터를 계측하고 있다
- [ ] 계정의 동시 활성 세션 쿼터(기본 1,000)에 근접하기 전에 지원 티켓으로 상향 조정 프로세스를 마련했다

## 참고

- [Introducing the Amazon Bedrock AgentCore Code Interpreter](https://aws.amazon.com/blogs/machine-learning/introducing-the-amazon-bedrock-agentcore-code-interpreter/)
- [Resource and session management — Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-resource-session-management.html)
- [Resource management (network modes) — Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-resource-management.html)
- [Execute code and analyze data using Amazon Bedrock AgentCore Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html)
- [Session management (microVM isolation) — Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-session-characteristics.html)
- [Quotas for Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html)
- [Amazon Bedrock AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/)
- [Browser Tool — Resource and session management (Fundamentals)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-resource-session-management.html)
- [Browser Tool Sessions (console help panel)](https://docs.aws.amazon.com/help-panel/bedrock-agentcore/latest/console/hp-builtin-browser-sessions.html)
- [start_browser_session — Python SDK reference](https://docs.aws.amazon.com/sdk-for-python/v1/reference/clients/bedrock-agentcore/operations/start_browser_session/)
- [Automate legacy web applications with Amazon Bedrock AgentCore Browser Tool](https://aws.amazon.com/blogs/machine-learning/automate-legacy-web-applications-with-amazon-bedrock-agentcore-browser-tool/)
- [Embed a live AI browser agent in your React app with Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/machine-learning/embed-a-live-ai-browser-agent-in-your-react-app-with-amazon-bedrock-agentcore/)
- [Class: Aws::BedrockAgentCore::Types::GetBrowserSessionResponse — AWS SDK for Ruby V3](https://docs.aws.amazon.com/sdk-for-ruby/v3/api/Aws/BedrockAgentCore/Types/GetBrowserSessionResponse.html)
- [Amazon Bedrock AgentCore Construct Library — AWS CDK (BrowserNetworkConfiguration / CodeInterpreterNetworkMode)](https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrockagentcore/README.html)
- [Anthropic Engineering — Code execution with MCP (2025년 11월)](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Part 1 — MCP 서버 설계](/01-agent-design/mcp-server-design)
- [Part 9 — Egress 제어](/09-authorization/egress-control)
- [Part 10 — Runtime 심화](/10-agentcore/runtime-deep-dive)

[^ci-blog]: https://aws.amazon.com/blogs/machine-learning/introducing-the-amazon-bedrock-agentcore-code-interpreter/
[^ci-session]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-session-characteristics.html
[^ci-session-mgmt]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-resource-session-management.html
[^ci-quota]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html
[^ci-tool]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html
[^ci-netmode]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-resource-management.html
[^browser-fundamentals]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-resource-session-management.html
[^browser-blog]: https://aws.amazon.com/blogs/machine-learning/embed-a-live-ai-browser-agent-in-your-react-app-with-amazon-bedrock-agentcore/
[^browser-sessions]: https://docs.aws.amazon.com/help-panel/bedrock-agentcore/latest/console/hp-builtin-browser-sessions.html
[^browser-sdk]: https://docs.aws.amazon.com/sdk-for-python/v1/reference/clients/bedrock-agentcore/operations/start_browser_session/
[^browser-cdk]: https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrockagentcore/README.html
[^browser-ruby-sdk]: https://docs.aws.amazon.com/sdk-for-ruby/v3/api/Aws/BedrockAgentCore/Types/GetBrowserSessionResponse.html
[^anthropic-cem]: https://www.anthropic.com/engineering/code-execution-with-mcp
