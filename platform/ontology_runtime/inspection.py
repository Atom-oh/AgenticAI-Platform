"""Private, non-model inspection before any source-analysis transfer."""
import hashlib
from pathlib import Path

from engine import gate
from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.ontology_analysis import KINDS
from workspace.ontology_sources import Sources

POLICY = "source-analysis-private-rules-v1"


def profile():
    return hashlib.sha256(Path(gate.__file__).read_bytes() +
                          Path(gate._common("pii").__file__).read_bytes()).hexdigest()


def inspect_payload(value):
    raw = schema.canonical(value).decode()
    measured = gate.measure("", raw)
    if measured["piiRules"]["count"]:
        fail(422, "agentcore-private-inspection", "식별 가능 정보가 감지되었습니다. 비공개 처리로 정제한 새 원본을 등록하세요.")
    return {"policy": POLICY, "profileHash": profile(), "inputHash": schema.digest(value),
            "chars": measured["chars"], "detectedIdentifiers": 0}


def inspect_source(ctx, reference):
    if reference["sourceKind"] != "asset":
        fail(422, "agentcore-source-kind", "현재 분석 경로는 등록 파일의 비민감 분류만 지원합니다.")
    reader = Sources(ctx)
    value = reader.resolve(reference)
    record = value["record"]
    kind = KINDS.get(Path(record["name"]).suffix.lower())
    if not kind:
        fail(422, "agentcore-source-format", "분석할 수 없는 파일 형식입니다.")
    payload = {"name": record["name"], "sourceRef": reference, "kind": kind}
    if kind != "asset":
        payload["text"] = reader.resolve(reference, text=True)["text"]
    # Binary resources contribute descriptors only in source-analysis/1.
    # This receipt does not admit their original bytes for transfer.
    receipt = inspect_payload(payload)
    receipt.update(sourceHash=reference["sha256"],
                   descriptorHash=schema.digest([reference, record["name"]]),
                   transfer="descriptor-only" if kind == "asset" else "text")
    reader.recheck()
    return receipt
