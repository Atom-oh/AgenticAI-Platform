#!/usr/bin/env python3
"""합성데이터 생성기 (SPEC v2 §5·§9) — 시드 고정, 재현 가능.

가상 은행 "아톰은행"의 여신 도메인(§5-1) + UX 자산 도메인(§5-2). 실제 은행 상품명·내규 조항을
사용하지 않고 구조만 모사한다. 개인 식별자는 생성 시점부터 토큰 형태(CUST-xxxx / ACCT-xxxx)로 만든다.

출력: seed/out/nodes.jsonl, seed/out/edges.jsonl (목표: 노드 약 3,800 / 관계 약 11,000)
커버리지 제약(§5-4) — tests/test_coverage.py · tests/test_ontology_v2.py 가 검증:
  - 히어로 규정 3건(HERO_REGULATIONS): products>=4, screens>=6, components>=8, departments>=3, documents>=5
  - 히어로 컴포넌트 3건(HERO_COMPONENTS): screens>=12, patterns>=4, policyRules>=2
여신 도메인은 v1 과 바이트 단위로 동일하게 유지한다 (Regulation 원문 코퍼스의 임베딩 캐시 보존).
UX 자산 도메인은 별도 난수열(SEED+1)로 뒤에 덧붙인다.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20260902
OUT = Path(__file__).resolve().parent / "out"

# §5-4 커버리지 제약의 대상 — 테스트·CLI·Registry 시드가 이 ID를 읽는다 (변경 금지)
HERO_REGULATIONS = ["REG-LN-001", "REG-LN-014", "REG-CS-003"]
HERO_COMPONENTS = ["CMP-Button-v2", "CMP-Button-v3", "CMP-Input-v3"]
# 히어로 컴포넌트를 직접 사용하는 화면 인덱스(SCR-###). 전세(0~7)·주담대(8~15) 흐름 화면이 주 버튼/입력을 쓴다.
HERO_COMPONENT_SCREENS = {
    "CMP-Button-v2": list(range(0, 16)),
    "CMP-Button-v3": list(range(16, 32)),
    "CMP-Input-v3": list(range(0, 4)) + list(range(8, 12)) + list(range(32, 40)),
}

NODES: list[dict] = []
EDGES: list[dict] = []
_ids: set[str] = set()
_edge_keys: set[tuple[str, str, str]] = set()


def node(nid: str, label: str, **props) -> str:
    assert nid not in _ids, f"duplicate node id {nid}"
    _ids.add(nid)
    NODES.append({"id": nid, "label": label, "props": props})
    return nid


def edge(src: str, rel: str, dst: str, **props) -> None:
    """엣지 추가. 같은 (src, rel, dst) 는 1건만 둔다 — Related 카운트(§8-2)가 중복으로 부풀지 않도록.
    난수열은 소비하지 않으므로 중복 제거가 이후 생성 결과를 바꾸지 않는다."""
    key = (src, rel, dst)
    if key in _edge_keys:
        return
    _edge_keys.add(key)
    EDGES.append({"src": src, "rel": rel, "dst": dst, "props": props})


def main() -> None:
    rng = random.Random(SEED)

    # ---------------- Department (20) ----------------
    dept_names = [
        ("D-LNP", "여신기획부", "상품 기획"), ("D-LNR", "여신심사부", "심사"),
        ("D-RTL", "리테일영업부", "영업"), ("D-DIG", "디지털채널부", "채널 운영"),
        ("D-CMP", "준법감시부", "컴플라이언스"), ("D-RSK", "리스크관리부", "리스크"),
        ("D-DEP", "수신기획부", "상품 기획"), ("D-FRX", "외환사업부", "외환"),
        ("D-CRD", "카드사업부", "카드"), ("D-ITP", "IT기획부", "시스템 기획"),
        ("D-ITD", "IT개발부", "개발"), ("D-UXD", "UX디자인부", "디자인"),
        ("D-CSC", "고객센터", "상담"), ("D-MKT", "마케팅부", "마케팅"),
        ("D-LGL", "법무부", "법무"), ("D-AUD", "검사부", "감사"),
        ("D-HRD", "인사부", "인사"), ("D-FIN", "재무기획부", "재무"),
        ("D-SEC", "정보보호부", "보안"), ("D-DAT", "데이터전략부", "데이터"),
    ]
    depts = [node(c, "Department", deptCode=c, name=n, role=r) for c, n, r in dept_names]

    # ---------------- Regulation (60) + Amendment (25) ----------------
    # 히어로 규정 3건 — S1 시연의 대상. 커버리지 제약을 이 3건에 보장한다.
    heroes = [
        node("REG-LN-001", "Regulation", code="REG-LN-001",
             title="전세자금대출 담보 인정 기준", article="여신업무내규 제12조",
             effectiveDate="2025-07-01", version=3, status="EFFECTIVE"),
        node("REG-LN-014", "Regulation", code="REG-LN-014",
             title="주택담보대출 LTV 산정 기준", article="여신업무내규 제27조",
             effectiveDate="2025-01-15", version=5, status="EFFECTIVE"),
        node("REG-CS-003", "Regulation", code="REG-CS-003",
             title="금융소비자 설명의무 이행 기준", article="소비자보호내규 제4조",
             effectiveDate="2024-11-01", version=2, status="EFFECTIVE"),
    ]
    reg_topics = [
        "여신 심사 등급 산정", "연체 관리 및 채권 보전", "금리인하요구권 처리",
        "대출모집인 관리", "수신 상품 판매 절차", "외환 송금 한도 관리",
        "비대면 실명확인 절차", "고객확인의무(CDD)", "전자금융 사고 대응",
        "개인신용정보 처리", "광고물 사전 심의", "임직원 금융거래 신고",
    ]
    regs = list(heroes)
    for i in range(len(heroes), 60):
        topic = reg_topics[i % len(reg_topics)]
        regs.append(node(f"REG-GN-{i:03d}", "Regulation", code=f"REG-GN-{i:03d}",
                         title=f"{topic} 기준 제{i}호", article=f"업무내규 제{i + 30}조",
                         effectiveDate=f"202{rng.randint(3, 5)}-{rng.randint(1, 12):02d}-01",
                         version=rng.randint(1, 6), status="EFFECTIVE"))
    for i in range(25):
        target = regs[i % 12]  # 히어로 포함 앞쪽 규정에 개정 이력 집중
        a = node(f"AMD-{i:03d}", "RegulationAmendment", amendmentId=f"AMD-{i:03d}",
                 date=f"2026-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}",
                 summary=f"{NODES[[n['id'] for n in NODES].index(target)]['props']['title']} 일부 개정",
                 diffType=rng.choice(["요건 강화", "요건 완화", "문구 정비", "적용 대상 확대"]))
        edge(target, "AMENDED_BY", a)
    for i in range(6):  # SUPERSEDES 체인 일부
        edge(regs[12 + i], "SUPERSEDES", regs[24 + i])

    # ---------------- Product (120) ----------------
    ln_products = [
        ("PRD-LN-001", "아톰 안심전세대출 II", "여신"),
        ("PRD-LN-002", "아톰 청년전세대출", "여신"),
        ("PRD-LN-003", "아톰 버팀목연계 전세대출", "여신"),
        ("PRD-LN-004", "아톰 전세보증금 반환보증 대출", "여신"),
        ("PRD-LN-005", "아톰 든든주택담보대출", "여신"),
        ("PRD-LN-006", "아톰 갈아타기 주담대", "여신"),
        ("PRD-LN-007", "아톰 신용대출 프라임", "여신"),
        ("PRD-LN-008", "아톰 소상공인 운영자금대출", "여신"),
    ]
    products = [node(c, "Product", productCode=c, name=n, category=cat,
                     launchDate=f"202{rng.randint(2, 5)}-{rng.randint(1, 12):02d}-01",
                     status="ON_SALE") for c, n, cat in ln_products]
    for i in range(len(ln_products), 120):
        cat = rng.choice(["여신", "수신", "외환", "카드"])
        base = {"여신": "대출", "수신": "예금", "외환": "송금", "카드": "카드"}[cat]
        products.append(node(f"PRD-{i:03d}", "Product", productCode=f"PRD-{i:03d}",
                             name=f"아톰 {base} {i}호", category=cat,
                             launchDate=f"202{rng.randint(0, 5)}-{rng.randint(1, 12):02d}-01",
                             status=rng.choice(["ON_SALE"] * 8 + ["DISCONTINUED"] * 2)))
    # 상품 소유 부서
    for p in products:
        cat = NODES[[n["id"] for n in NODES].index(p)]["props"]["category"]
        owner = {"여신": "D-LNP", "수신": "D-DEP", "외환": "D-FRX", "카드": "D-CRD"}[cat]
        edge(p, "OWNED_BY", owner)

    # ---------------- Condition (800) ----------------
    cond_types = ["자격", "한도", "금리", "우대", "제외"]
    conditions = []
    ci = 0

    def make_condition(reg: str, prod: str, ctype: str) -> str:
        nonlocal ci
        c = node(f"CND-{ci:04d}", "Condition", conditionId=f"CND-{ci:04d}",
                 type=ctype, operator=rng.choice([">=", "<=", "=", "IN"]),
                 value=str(rng.choice([70, 80, 90, 100, 300, 500, 0.3, 0.5, 1.2])),
                 unit=rng.choice(["percent", "krw_million", "count"]),
                 priority=rng.randint(1, 5))
        ci += 1
        edge(prod, "HAS_CONDITION", c)
        edge(c, "DERIVED_FROM", reg)
        conditions.append(c)
        return c

    # 히어로 규정: 각각 전용 상품군 5~6개에 조건을 심는다 (§4.3 products>=4 보장)
    hero_products = {
        "REG-LN-001": products[0:5],   # 전세 계열 + 주담대 1
        "REG-LN-014": products[3:8],   # 주담대·신용 계열
        "REG-CS-003": products[0:8:2] + [products[9], products[15]],  # 카테고리 횡단
    }
    for reg_id, prods in hero_products.items():
        for p in prods:
            for ctype in rng.sample(cond_types, k=3):
                make_condition(reg_id, p, ctype)
    # 나머지 조건은 전 상품·규정에 분산
    while ci < 800:
        make_condition(rng.choice(regs), rng.choice(products), rng.choice(cond_types))
    # REQUIRES / EXCLUDES
    for _ in range(120):
        a, b = rng.sample(conditions, 2)
        edge(a, "REQUIRES", b)

    # ---------------- Merchant (150) ----------------
    mcc = [("5411", "슈퍼마켓"), ("5812", "음식점"), ("5541", "주유소"),
           ("4111", "대중교통"), ("5912", "약국"), ("7832", "영화관"),
           ("5311", "백화점"), ("4899", "통신"), ("8211", "교육"), ("7011", "숙박")]
    merchants = []
    for i in range(150):
        code, catname = mcc[i % len(mcc)]
        merchants.append(node(f"MRC-{i:03d}", "Merchant", merchantId=f"MRC-{i:03d}",
                              name=f"{catname} 가맹점 {i}호", mccCode=code, category=catname))
    for _ in range(60):
        edge(rng.choice(conditions), "EXCLUDES", rng.choice(merchants))

    # ---------------- Screen (150) + Component (80) ----------------
    # 컴포넌트: 버전 체인 포함 (S3 시연 — Button v2 Deprecated → v3 사용)
    components = []
    comp_defs = [("Button", 3), ("Input", 3), ("Table", 2), ("Modal", 2),
                 ("DatePicker", 2), ("Select", 2), ("Card", 2), ("Tabs", 2),
                 ("Stepper", 1), ("FileUpload", 1), ("Badge", 1), ("Toast", 1)]
    for name, versions in comp_defs:
        chain = []
        for v in range(1, versions + 1):
            status = "APPROVED" if v == versions or v == versions - 1 else "DEPRECATED"
            c = node(f"CMP-{name}-v{v}", "Component", componentId=f"CMP-{name}-v{v}",
                     name=name, version=f"{v}.0.0", approvalStatus=status,
                     propsSchema=json.dumps({"variant": ["primary", "ghost"],
                                             "size": ["sm", "md", "lg"]}))
            chain.append(c)
        for older, newer in zip(chain, chain[1:]):
            edge(older, "SUPERSEDED_BY", newer)
        components.extend(chain)
    for i in range(len(components), 80):
        components.append(node(f"CMP-GEN-{i:02d}", "Component", componentId=f"CMP-GEN-{i:02d}",
                               name=f"Widget{i}", version="1.0.0",
                               approvalStatus=rng.choice(["APPROVED"] * 7 + ["DRAFT", "DEPRECATED", "PENDING_APPROVAL"]),
                               propsSchema=json.dumps({"size": ["md"]})))

    screens = []
    channels = ["MBS(모바일)", "IBS(인터넷)", "BRC(창구)", "ADM(운영)"]
    for i in range(150):
        s = node(f"SCR-{i:03d}", "Screen", screenId=f"SCR-{i:03d}",
                 name=f"업무화면 {i}호", channel=channels[i % 4],
                 route=f"/screen/{i:03d}", status="LIVE")
        screens.append(s)
        for c in rng.sample(components, k=rng.randint(3, 6)):
            edge(s, "USES", c)
        edge(s, "OWNED_BY", rng.choice(["D-DIG", "D-ITD", "D-UXD", "D-RTL"]))

    # 히어로 규정에 걸린 상품은 판매 화면을 넉넉히 연결 (§4.3 screens>=6 보장)
    hero_screens = {
        "REG-LN-001": screens[0:8], "REG-LN-014": screens[6:14], "REG-CS-003": screens[12:20],
    }
    linked: set[tuple[str, str]] = set()
    for reg_id, scrs in hero_screens.items():
        for p in hero_products[reg_id]:
            for s in rng.sample(scrs, k=rng.randint(3, 5)):
                if (p, s) not in linked:
                    linked.add((p, s)); edge(p, "SOLD_VIA", s)
    for p in products:
        for s in rng.sample(screens, k=rng.randint(1, 3)):
            if (p, s) not in linked:
                linked.add((p, s)); edge(p, "SOLD_VIA", s)

    # 판매 화면 이름을 상품 흐름답게 정비 (히어로 구간)
    flow = ["상품 안내", "한도 조회", "신청서 작성", "서류 제출", "심사 결과 조회",
            "약정 체결", "실행 조회", "금리 안내"]
    for i, nm in enumerate(flow):
        NODES[[n["id"] for n in NODES].index(f"SCR-{i:03d}")]["props"]["name"] = f"전세대출 {nm}"
    for i, nm in enumerate(flow):
        NODES[[n["id"] for n in NODES].index(f"SCR-{i + 8:03d}")]["props"]["name"] = f"주담대 {nm}"

    # APPLIES_TO (규정→상품 직접 적용 관계)
    for reg_id, prods in hero_products.items():
        for p in prods:
            edge(reg_id, "APPLIES_TO", p)
    for _ in range(170):
        edge(rng.choice(regs), "APPLIES_TO", rng.choice(products))

    # ---------------- Template (12) + Document (200) ----------------
    templates = [node(f"TPL-{i:02d}", "Template", templateId=f"TPL-{i:02d}",
                      name=n, sections=json.dumps(secs))
                 for i, (n, secs) in enumerate([
                     ("규정 개정 기안문", ["개정 사유", "주요 내용", "영향 분석", "시행일"]),
                     ("상품 심의 보고서", ["개요", "수익성", "리스크", "준법 검토"]),
                     ("여신 심사 의견서", ["신청 개요", "심사 의견", "조건"]),
                     ("소비자보호 점검 보고", ["점검 범위", "지적 사항", "조치 계획"]),
                     ("시스템 변경 요청서", ["변경 사유", "영향 화면", "일정"]),
                     ("리스크 점검 보고", ["익스포저", "한도", "조치"]),
                     ("감사 지적사항 회신", ["지적 요지", "조치 내역"]),
                     ("금리 운용 보고", ["기준금리", "가산금리", "우대"]),
                     ("영업점 시행 공문", ["시행 내용", "적용 일자"]),
                     ("교육 자료", ["배경", "절차", "FAQ"]),
                     ("고객 안내문", ["변경 내용", "적용 대상"]),
                     ("회의록", ["안건", "결정 사항", "후속 조치"]),
                 ])]
    doc_types = ["기안문", "보고서", "공문", "회의록", "안내문"]
    documents = []
    for i in range(200):
        d = node(f"DOC-{i:03d}", "Document", docId=f"DOC-{i:03d}",
                 title=f"문서 {i}호", type=doc_types[i % 5],
                 deptCode=rng.choice(depts),
                 updatedAt=f"2026-{rng.randint(1, 8):02d}-{rng.randint(1, 28):02d}")
        documents.append(d)
        edge(d, "FOLLOWS", rng.choice(templates))
        edge(d, "OWNED_BY", rng.choice(depts))
    # 히어로 규정 참조 문서 (§4.3 documents>=5 보장) — 제목도 규정에 맞게
    hero_doc_titles = {
        "REG-LN-001": ["전세자금대출 담보 인정 기준 개정 기안", "전세대출 상품 심의 보고",
                       "전세대출 화면 변경 요청", "담보 인정 비율 리스크 점검",
                       "전세대출 영업점 시행 공문", "전세대출 취급 교육 자료"],
        "REG-LN-014": ["LTV 산정 기준 개정 기안", "주담대 심의 보고", "LTV 개편 시스템 변경 요청",
                       "주담대 리스크 점검", "주담대 영업점 공문", "LTV 관련 감사 회신"],
        "REG-CS-003": ["설명의무 점검 보고", "설명의무 이행 교육 자료", "설명 스크립트 변경 기안",
                       "소비자보호 점검 회의록", "설명의무 관련 고객 안내문"],
    }
    di = 0
    for reg_id, titles in hero_doc_titles.items():
        for t in titles:
            NODES[[n["id"] for n in NODES].index(documents[di])]["props"]["title"] = t
            edge(documents[di], "REFERENCES", reg_id)
            di += 1
    for d in documents[di:di + 90]:
        edge(d, "REFERENCES", rng.choice(regs))

    # ---------------- Customer (500) + Account (1,200) ----------------
    # 개인 식별자는 처음부터 토큰 — 주민번호 형식 자체를 만들지 않는다 (SPEC §8)
    customers = [node(f"CUST-{i:04d}", "Customer", customerId=f"CUST-{i:04d}",
                      segment=rng.choice(["일반", "우대", "프리미엄", "VIP"]),
                      joinDate=f"20{rng.randint(15, 26):02d}-{rng.randint(1, 12):02d}-01")
                 for i in range(500)]
    for i in range(1200):
        a = node(f"ACCT-{i:04d}", "Account", accountId=f"ACCT-{i:04d}",
                 productCode="", balance=rng.randint(0, 500) * 100000,
                 openDate=f"20{rng.randint(18, 26):02d}-{rng.randint(1, 12):02d}-01")
        p = rng.choice(products)
        NODES[-1]["props"]["productCode"] = NODES[[n["id"] for n in NODES].index(p)]["props"]["productCode"]
        edge(rng.choice(customers), "HOLDS", a)
        edge(a, "OF_PRODUCT", p)
        for m in rng.sample(merchants, k=rng.randint(1, 3)):
            edge(a, "TRANSACTED_AT", m)

    # ============================================================
    # UX 자산 도메인 (SPEC v2 §5-2 / §5-3) — Pattern · Procedure · PolicyRule · UXTerm · ScreenMeta
    # 별도 난수열(SEED+1)을 사용해 위 여신 도메인 노드·엣지는 바이트 단위로 동일하게 유지한다
    # (Regulation 원문 코퍼스 → Titan 임베딩 캐시가 그대로 유효).
    # ============================================================
    urng = random.Random(SEED + 1)
    idx = {n["id"]: n for n in NODES}

    # Component.owner (§5-2 속성 추가) — 버전 사슬 컴포넌트는 UI플랫폼팀, 볼륨용 위젯은 3개 팀에 분산
    comp_owners = ["UI플랫폼팀", "디지털채널부", "UX디자인부"]
    for c in components:
        idx[c]["props"]["owner"] = urng.choice(comp_owners) if c.startswith("CMP-GEN-") else "UI플랫폼팀"

    def _dedupe_edge(seen: set, src: str, rel: str, dst: str) -> None:
        if (src, dst) not in seen:
            seen.add((src, dst)); edge(src, rel, dst)

    uses: set[tuple[str, str]] = {(e["src"], e["dst"]) for e in EDGES if e["rel"] == "USES"}
    # 히어로 컴포넌트 3건(§5-4 두 번째 제약 대상) — 흐름 화면이 주 액션 버튼/입력을 직접 사용한다 (screens>=12 보장)
    for cid, scrs in HERO_COMPONENT_SCREENS.items():
        for s in scrs:
            _dedupe_edge(uses, screens[s], "USES", cid)

    # ---------------- Pattern (40) ----------------
    pattern_defs = [
        ("단계형 신청 폼", "폼"), ("조건 검색 목록", "목록"), ("결과 상세 카드", "상세"),
        ("한도·금리 조회 위저드", "폼"), ("서류 제출 업로드", "폼"), ("약정 전자서명", "인증"),
        ("심사 진행 스테퍼", "피드백"), ("금액 입력 키패드", "폼"), ("상품 비교 표", "목록"),
        ("고지·동의 체크리스트", "동의"), ("본인인증 모달", "인증"), ("오류 안내 토스트", "피드백"),
        ("탭 전환 상세", "탐색"), ("필터 칩 목록", "목록"), ("날짜 범위 선택", "폼"),
        ("계좌 선택 드롭다운", "폼"), ("가맹점 제외 안내", "피드백"), ("우대 조건 체크리스트", "동의"),
        ("실행 완료 확인", "피드백"), ("이체 한도 안내 배너", "피드백"),
        ("빈 상태 안내", "피드백"), ("페이지 헤더", "레이아웃"), ("하단 고정 액션바", "레이아웃"),
        ("접이식 섹션", "레이아웃"), ("정렬 가능 데이터 표", "목록"), ("검색 자동완성", "폼"),
        ("파일 미리보기", "상세"), ("타임라인 이력", "상세"), ("알림 센터", "피드백"),
        ("약관 전문 뷰어", "동의"), ("설명의무 확인 다이얼로그", "동의"), ("금리 산정 내역 표", "상세"),
        ("상환 스케줄 표", "상세"), ("담보 물건 입력", "폼"), ("공동 명의자 추가", "폼"),
        ("영업점 방문 예약", "폼"), ("상담사 연결 배너", "피드백"), ("진행 상태 배지", "피드백"),
        ("취소·되돌리기 확인", "피드백"), ("세션 만료 안내", "인증"),
    ]
    assert len(pattern_defs) == 40
    patterns = [node(f"PAT-{i:03d}", "Pattern", patternId=f"PAT-{i:03d}", name=nm, category=cat,
                     status=urng.choice(["APPROVED"] * 8 + ["DRAFT", "DEPRECATED"]))
                for i, (nm, cat) in enumerate(pattern_defs)]

    # Pattern -COMPOSES-> Component. 히어로 컴포넌트는 6개 패턴이 포함한다 (§5-4 patterns>=4 보장)
    composes: set[tuple[str, str]] = set()
    hero_comp_patterns = {
        "CMP-Button-v2": [0, 3, 5, 18, 22, 38],     # 신청 폼·위저드·전자서명·완료·액션바·확인 — 주 버튼
        "CMP-Button-v3": [7, 10, 25, 28, 35, 39],    # 키패드·인증 모달·자동완성·알림·예약·세션 — 차세대 버튼
        "CMP-Input-v3": [0, 3, 7, 25, 33, 34],       # 폼·위저드·키패드·자동완성·담보 입력·명의자
    }
    for cid, pis in hero_comp_patterns.items():
        for pi in pis:
            _dedupe_edge(composes, patterns[pi], "COMPOSES", cid)
    approved_comps = [c for c in components if idx[c]["props"]["approvalStatus"] == "APPROVED"]
    for p in patterns:
        for c in urng.sample(approved_comps, k=urng.randint(4, 8)):
            _dedupe_edge(composes, p, "COMPOSES", c)

    # Screen -FOLLOWS-> Pattern. 히어로 흐름 화면(전세 0~7 · 주담대 8~15)은 흐름 단계에 맞는 패턴을 따른다
    follows: set[tuple[str, str]] = set()
    flow_patterns = [21, 3, 0, 4, 6, 5, 18, 31]  # 상품안내→헤더, 한도조회→위저드, 신청서→폼, 서류→업로드,
    #                                              심사결과→스테퍼, 약정→전자서명, 실행→완료확인, 금리안내→금리내역
    for i, pi in enumerate(flow_patterns):
        _dedupe_edge(follows, screens[i], "FOLLOWS", patterns[pi])
        _dedupe_edge(follows, screens[i + 8], "FOLLOWS", patterns[pi])
    for s in screens:
        for p in urng.sample(patterns, k=urng.randint(2, 3)):
            _dedupe_edge(follows, s, "FOLLOWS", p)

    # ---------------- Procedure (30) ----------------
    proc_names = [
        "전세자금대출 신청 절차", "주택담보대출 신청 절차", "신용대출 한도 조회 절차", "대출 실행 및 입금 절차",
        "금리인하요구권 접수 절차", "대출 만기 연장 절차", "중도상환 처리 절차", "담보 물건 변경 절차",
        "공동 명의자 추가 절차", "우대금리 조건 확인 절차", "비대면 실명확인 절차", "예금 신규 가입 절차",
        "해외 송금 신청 절차", "카드 발급 신청 절차", "자동이체 등록 절차", "연체 안내 및 상환 절차",
        "고객확인의무(CDD) 이행 절차", "설명의무 확인 및 증빙 절차", "영업점 방문 예약 절차",
        "상품 비교 및 추천 절차",
    ]
    proc_names += [f"{cat} 운영 업무 절차 {i}호" for i, cat in
                   enumerate(["여신", "수신", "외환", "카드", "공통"] * 2, start=1)]
    assert len(proc_names) == 30
    proc_screens: list[list[str]] = [screens[0:8], screens[8:16]]
    for _ in range(2, 30):
        proc_screens.append(urng.sample(screens, k=urng.randint(4, 8)))
    procedures = []
    for i, (nm, scrs) in enumerate(zip(proc_names, proc_screens)):
        pr = node(f"PRC-{i:03d}", "Procedure", procedureId=f"PRC-{i:03d}", name=nm,
                  steps=json.dumps([idx[s]["props"]["name"] for s in scrs], ensure_ascii=False),
                  status=urng.choice(["APPROVED"] * 8 + ["DRAFT", "DEPRECATED"]))
        procedures.append(pr)
        for s in scrs:
            edge(pr, "INCLUDES", s)

    # ---------------- ScreenMeta (150, 화면당 1건) ----------------
    prev_map: dict[str, list[str]] = {s: [] for s in screens}
    next_map: dict[str, list[str]] = {s: [] for s in screens}
    for scrs in proc_screens:
        for a, b in zip(scrs, scrs[1:]):
            if b not in next_map[a]:
                next_map[a].append(b)
            if a not in prev_map[b]:
                prev_map[b].append(a)
    purposes = ["고객이 조건을 입력하고 결과를 확인한다", "신청 내용을 검토하고 다음 단계로 진행한다",
                "필수 고지 사항을 확인하고 동의를 받는다", "처리 결과를 조회하고 증빙을 내려받는다",
                "담당자가 접수 건을 검토하고 처리한다"]
    entry_conds = ["로그인 완료", "본인인증 완료", "상품 선택 완료", "약관 동의 완료", "직원 권한(창구·운영)"]
    for i, s in enumerate(screens):
        sp = idx[s]["props"]
        sm = node(f"SM-{i:03d}", "ScreenMeta", screenNo=s,
                  purpose=f"{sp['name']} — {urng.choice(purposes)} ({sp['channel']})",
                  entryCondition=urng.choice(entry_conds),
                  prevScreens=json.dumps(prev_map[s]), nextScreens=json.dumps(next_map[s]))
        edge(sm, "DESCRIBES", s)

    # ---------------- PolicyRule (60) ----------------
    # 두 도메인의 연결점: PolicyRule -DERIVED_FROM-> Regulation, PolicyRule -CONSTRAINS-> Screen (S1 v2 순회)
    constrains: set[tuple[str, str]] = set()
    hero_rules = [
        ("REG-LN-001", "전세대출 담보 인정 범위 고지 문구 필수", "고지의무", "HIGH", list(range(0, 8))),
        ("REG-LN-001", "질권·채권양도 담보 선택 시 인정비율 80% 상한 표기", "표기", "MEDIUM", [1, 2, 3, 7, 40]),
        ("REG-LN-001", "제외 대상 주택 사전 확인 체크리스트", "동의", "HIGH", [2, 3, 4, 41]),
        ("REG-LN-014", "LTV 산정 기준가 출처 표기", "표기", "MEDIUM", list(range(8, 16))),
        ("REG-LN-014", "규제지역 변경 시 한도 재계산 안내", "고지의무", "HIGH", [9, 10, 11, 42]),
        ("REG-LN-014", "LTV 한도 초과 승인 불가 입력 검증", "입력검증", "HIGH", [12, 13, 43]),
        ("REG-CS-003", "우대금리 조건·미충족 시 불이익 설명 확인", "동의", "HIGH", [0, 7, 8, 15, 16, 17, 44, 45]),
        ("REG-CS-003", "비대면 화면 단위 고지·이해 확인 절차", "고지의무", "HIGH", list(range(12, 24))),
        ("REG-CS-003", "설명 확인 증빙(전자서명·녹취) 보관", "보안", "MEDIUM", [5, 13, 16, 18, 20, 46]),
        # 히어로 컴포넌트(Button v3 · Input v3) 화면군을 제약하는 규칙 — §5-4 policyRules>=2 보장
        ("REG-GN-009", "비대면 세션 만료 시 재인증 요구", "보안", "HIGH", list(range(16, 32))),
        ("REG-GN-013", "주 액션 버튼 명칭 표기 통일(신청·확인·취소)", "표기", "LOW", list(range(16, 40))),
        ("REG-GN-012", "금액·계좌 입력 필드 형식 검증", "입력검증", "MEDIUM", list(range(0, 4)) + list(range(8, 12)) + list(range(32, 40))),
    ]
    rule_types = ["고지의무", "접근성", "입력검증", "보안", "표기", "동의"]
    policy_rules = []
    for i in range(60):
        rid = f"POL-{i:03d}"
        if i < len(hero_rules):
            reg_id, title, rtype, sev, scr_idx = hero_rules[i]
            targets = [screens[k] for k in scr_idx]
            derived = [reg_id]
        else:
            reg_id = urng.choice(regs)
            topic = idx[reg_id]["props"]["title"].split(" 기준")[0]
            rtype = urng.choice(rule_types)
            title = f"{topic} 화면 {rtype} 규칙 {i}호"
            sev = urng.choice(["HIGH", "MEDIUM", "MEDIUM", "LOW"])
            targets = urng.sample(screens, k=urng.randint(4, 12))
            derived = [reg_id] + ([urng.choice(regs)] if urng.random() < 0.3 else [])
        pol = node(rid, "PolicyRule", ruleId=rid, title=title, ruleType=rtype, severity=sev,
                   status="ACTIVE" if i < len(hero_rules) else urng.choice(["ACTIVE"] * 8 + ["DRAFT", "RETIRED"]))
        policy_rules.append(pol)
        for s in targets:
            _dedupe_edge(constrains, pol, "CONSTRAINS", s)
        for r in dict.fromkeys(derived):
            edge(pol, "DERIVED_FROM", r)

    # ---------------- UXTerm (200) ----------------
    term_domains = [("전세대출", "여신"), ("주담대", "여신"), ("신용대출", "여신"), ("예금", "수신"),
                    ("송금", "외환"), ("카드", "카드"), ("공통", "공통")]
    concepts = ["한도", "금리", "우대금리", "상환", "만기", "신청", "심사", "약정", "실행", "해지",
                "중도상환수수료", "거치기간", "담보", "보증", "연체", "자동이체", "본인인증", "전자서명",
                "약관", "설명확인", "고객확인", "가맹점", "전월실적", "기준금리", "가산금리", "상품안내",
                "서류제출", "결과조회", "재약정", "증액"]
    term_pool = [(d, cat, c) for d, cat in term_domains for c in concepts][:200]
    term_screens = {"전세대출": screens[0:8], "주담대": screens[8:16]}
    for i, (dom, cat, con) in enumerate(term_pool):
        term = con if dom == "공통" else f"{dom} {con}"
        t = node(f"TRM-{i:04d}", "UXTerm", termId=f"TRM-{i:04d}", term=term,
                 definition=f"{dom} 업무 화면에서 '{con}'을(를) 가리키는 표준 표기. 고객 안내 문구·버튼 명칭은 '{term}'으로 통일한다.",
                 category=cat)
        pool = term_screens.get(dom)
        picks = (urng.sample(pool, k=urng.randint(2, 4)) if pool else []) + urng.sample(screens, k=urng.randint(2, 5))
        for s in dict.fromkeys(picks):
            edge(t, "USED_IN", s)

    # ---------------- 출력 ----------------
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "nodes.jsonl", "w", encoding="utf-8") as f:
        for n in NODES:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    with open(OUT / "edges.jsonl", "w", encoding="utf-8") as f:
        for e in EDGES:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    by_label: dict[str, int] = {}
    for n in NODES:
        by_label[n["label"]] = by_label.get(n["label"], 0) + 1
    print(f"nodes={len(NODES)} edges={len(EDGES)}")
    print(json.dumps(by_label, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
