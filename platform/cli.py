#!/usr/bin/env python3
"""플랫폼 CLI — 로컬 점검 + 관리 작업(IAM invoke).

  python3 cli.py impact REG-LN-001         §5-5 규정 영향 순회 (GRAPH_BACKEND 에 따라 local/neptune)
  python3 cli.py impact-component CMP-Button-v2   컴포넌트 변경 영향 (화면·패턴·정책규칙·상품·부서·절차)
  python3 cli.py metric "지난달 사용액"     Semantic Layer 해석
  python3 cli.py admin health              AdminFn: 플레인·브리지·Neptune 점검
  python3 cli.py admin seed_registry       Registry 기준선 시드 (멱등)
  python3 cli.py admin load_neptune        Neptune 적재 (wipe 후 재적재 — 시연 준비)
  python3 cli.py admin reset_demo          시연 상태 리셋
관리 작업은 WebSocket 사용자 경로에 없다 — AdminFn을 IAM 자격으로만 호출한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _admin_fn_name() -> str:
    out = HERE / "infra" / "outputs.json"
    if out.exists():
        return json.loads(out.read_text())["BankPlatform"]["AdminFnName"]
    return os.environ["ADMIN_FN"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__); return 1
    cmd = argv[1]
    if cmd == "impact":
        from graph.store import get_store
        r = get_store().impact_of_regulation(argv[2] if len(argv) > 2 else "REG-LN-001")
        print(json.dumps({"regulation": r.regulation.props if r.regulation else None, "counts": r.counts(),
                          "pathEdges": len(r.path_edges)}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "impact-component":
        from graph.store import get_store
        store = get_store()
        cid = argv[2] if len(argv) > 2 else "CMP-Button-v2"
        r = store.impact_of_component(cid)
        print(json.dumps({"backend": store.name,
                          "component": r.component.props if r.component else None,
                          "counts": r.counts(),
                          "relatedCounts": store.related_counts(r.component.id) if r.component else {},
                          "versionChain": [n.id for n in store.version_chain(r.component.id)] if r.component else [],
                          "screens": [n.props.get("name") for n in r.screens[:12]],
                          "patterns": [n.props.get("name") for n in r.patterns[:8]],
                          "policyRules": [n.props.get("title") for n in r.policy_rules[:8]],
                          "pathEdges": len(r.path_edges)}, ensure_ascii=False, indent=2))
        return 0 if r.component else 1
    if cmd == "metric":
        from semantic.loader import SemanticLayer
        m = SemanticLayer().resolve(" ".join(argv[2:]))
        print(json.dumps(m.__dict__ if m else {"error": "정의 없음"}, ensure_ascii=False, indent=2))
        return 0
    if cmd == "admin":
        op = argv[2] if len(argv) > 2 else "health"
        region = os.environ.get("AWS_REGION", "ap-northeast-2")
        payload = json.dumps({"op": op, **({"reset": True} if "--reset" in argv else {})})
        r = subprocess.run(["aws", "lambda", "invoke", "--function-name", _admin_fn_name(), "--region", region,
                            "--cli-binary-format", "raw-in-base64-out", "--payload", payload, "/tmp/admin-out.json"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr); return r.returncode
        print(Path("/tmp/admin-out.json").read_text())
        return 0
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
