#!/usr/bin/env python3
"""합성 규정 원문 코퍼스 생성기 (Vector RAG 비교군용, SPEC F2).

seed/out/nodes.jsonl의 Regulation 노드마다 조항 원문을 결정론적으로 생성한다.
실제 감독규정을 인용하지 않고 구조만 모사한다 (SPEC §8).
히어로 규정 3건은 다층 조항으로 풍부하게 만들어 벡터 검색이 '문서 청크는 잘 찾지만
영향 범위는 답하지 못하는' 대비가 공정하게 드러나게 한다 (비교군 약화 금지 — §12.5).

출력: seed/out/corpus.jsonl — {chunkId, regCode, title, article, seq, text}
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
SEED = 20260902

HERO_TEXTS = {
    "REG-LN-001": [
        "제1항(목적) 이 기준은 전세자금대출 취급 시 담보로 인정할 수 있는 보증 및 권리의 범위와 "
        "인정 비율을 정함을 목적으로 한다.",
        "제2항(담보 인정 범위) 담보로 인정하는 것은 다음 각 호와 같다. 1. 주택금융공사 전세자금보증서 "
        "2. 주택도시보증공사 전세보증금반환보증 3. 서울보증보험 전세금보장신용보험 4. 임차보증금 "
        "반환채권에 대한 질권 설정 또는 채권양도. 임차주택이 다가구·다중주택인 경우 선순위 임차보증금 "
        "합계를 확인하여야 한다.",
        "제3항(인정 비율) 보증서 담보의 인정 비율은 보증금액의 100분의 100으로 하되, 질권·채권양도 "
        "방식은 임차보증금의 100분의 80을 초과할 수 없다. 신혼가구·청년가구에 대한 우대 비율은 "
        "상품별 기준에 따른다.",
        "제4항(제외 대상) 다음 각 호의 주택은 담보 인정 대상에서 제외한다. 1. 경매·공매 진행 중인 주택 "
        "2. 압류·가압류·가처분이 설정된 주택 3. 미등기 주택 및 무허가 건축물 4. 임대인이 법인인 경우로서 "
        "회생·파산 절차가 진행 중인 주택.",
        "제5항(개정 절차) 이 기준의 개정은 여신기획부가 입안하고 리스크관리부·준법감시부 협의를 거쳐 "
        "여신협의회 의결로 시행한다. 개정 시 관련 상품설명서, 업무화면 및 영업점 매뉴얼을 함께 정비하여야 한다.",
    ],
    "REG-LN-014": [
        "제1항(목적) 이 기준은 주택담보대출의 담보인정비율(LTV) 산정 방법과 적용 한도를 정한다.",
        "제2항(평가액 산정) 담보평가액은 감정평가액, KB시세, 한국부동산원 시세 중 낮은 값을 적용한다. "
        "시세가 없는 물건은 감정평가를 의무화한다.",
        "제3항(비율 한도) 지역·보유주택수·상품 유형별 LTV 한도는 별표 1에 따르며, 규제지역 변경 시 "
        "고시일부터 즉시 적용한다. 한도 초과 승인은 여신심사부장 전결로 할 수 없다.",
        "제4항(재산정) 금리 재약정, 증액, 만기 연장 시 LTV를 재산정한다. 재산정 결과 한도를 초과하는 "
        "경우 초과분 상환 계획을 징구한다.",
    ],
    "REG-CS-003": [
        "제1항(목적) 이 기준은 금융소비자보호법상 설명의무의 이행 방법과 증빙 보관을 정한다.",
        "제2항(설명 사항) 판매 직원은 상품의 주요 내용, 원금 손실 가능성, 우대금리 적용 조건과 "
        "미충족 시 불이익, 중도상환수수료를 설명하여야 한다.",
        "제3항(증빙) 설명 확인은 전자서명 또는 녹취로 보관하며 보관 기간은 10년으로 한다. "
        "비대면 채널은 화면 단위 고지와 이해 확인 절차를 갖추어야 한다.",
    ],
}

GENERIC_CLAUSES = [
    "제1항(목적) 이 기준은 {t}에 관한 업무 처리 원칙을 정함을 목적으로 한다.",
    "제2항(적용 범위) 이 기준은 본점 및 영업점의 {t} 관련 업무 전반에 적용한다.",
    "제3항(업무 절차) {t} 업무는 신청 접수, 요건 확인, 승인, 사후관리의 순으로 처리하며 "
    "각 단계의 처리 결과를 전산 등록하여야 한다.",
    "제4항(보고) 담당 부서는 {t} 관련 이상 징후 발견 시 지체 없이 소관 임원에게 보고한다.",
]


def main() -> None:
    rng = random.Random(SEED)
    regs = []
    with open(OUT / "nodes.jsonl", encoding="utf-8") as f:
        for line in f:
            n = json.loads(line)
            if n["label"] == "Regulation":
                regs.append(n["props"])
    chunks = []
    for r in regs:
        texts = HERO_TEXTS.get(r["code"]) or [
            c.format(t=r["title"].split(" 기준")[0]) for c in
            rng.sample(GENERIC_CLAUSES, k=rng.randint(2, 4))
        ]
        for seq, t in enumerate(texts):
            chunks.append({
                "chunkId": f"{r['code']}#c{seq}",
                "regCode": r["code"], "title": r["title"],
                "article": r["article"], "seq": seq,
                "text": f"[{r['article']}] {r['title']} — {t}",
            })
    with open(OUT / "corpus.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"corpus chunks={len(chunks)}")


if __name__ == "__main__":
    main()
