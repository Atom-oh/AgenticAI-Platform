"""One fixed baseline and bounded variations, with independent idempotent runs."""
from __future__ import annotations

import json

from workspace.storage import Conflict

VARIANTS = ("layout", "dense", "emphasis", "flow", "information")


def create_batch(api, owner, body, scope):
    from engine import model_catalog
    from workspace.http import HTTPError, _integer, _json
    mode = body.get("mode", "guided")
    if mode not in ("creative", "guided"):
        raise HTTPError(400, "invalid-mode", "새 UX 또는 기준안·변형안 비교를 선택하세요.")
    variations = _integer(body.get("variationCount", 2), "Variation count", 2, 5) if mode == "guided" else 0
    identifier = api._request_id(body, "batch")
    data = {"mode": mode, "variationCount": variations, "contractId": body.get("contractId"),
            "contractVersion": _integer(body.get("contractVersion"), "Contract version", 1, 2**53 - 1),
            "model": model_catalog.resolve(body.get("model")), "actor": scope["actor"],
            "maxRounds": _integer(body.get("maxRounds", 3), "Rounds", 1, 5)}
    for key in ("referenceAssetId", "referencePage", "visualTolerance"):
        if key in body:
            data[key] = body[key]
    fingerprint = api._fingerprint(data)
    batch = api.storage.get(owner, "batch", identifier)
    if batch and batch.get("requestHash") != fingerprint:
        raise HTTPError(409, "request-changed", "이 비교 요청의 입력이 변경되었습니다.")
    contract = api._get(owner, "contract", data["contractId"])
    if (contract.get("status") != "approved" or contract["version"] != data["contractVersion"]
            or not contract.get("catalogHash")):
        raise HTTPError(409, "code-criteria-required", "현재 React 코드·상품 기준이 승인된 규칙을 선택하세요.")
    api._criteria(owner, {}, scope, contract)
    if batch is None:
        record = {**data, "id": identifier, "requestHash": fingerprint, "status": "preparing",
                  "contractHash": api.rules().contract_hash(contract), "catalogHash": contract["catalogHash"],
                  "slots": [], "runIds": [], "expectedCount": variations + 1,
                  **({"projectId": scope["project"]["id"]} if scope.get("project") else {})}
        try:
            batch = api.storage.put(owner, "batch", record)
        except Conflict:
            batch = api._get(owner, "batch", identifier)
            if batch.get("requestHash") != fingerprint:
                raise HTTPError(409, "request-changed", "비교 요청이 변경되었습니다.")
    directions = ["baseline", *VARIANTS[:variations]] if mode == "guided" else ["balanced"]
    for index, variant in enumerate(directions):
        current = api._get(owner, "batch", identifier)
        existing = next((slot for slot in current["slots"] if slot["index"] == index and slot.get("runId")), None)
        if existing:
            continue
        request = {key: data[key] for key in ("contractId", "contractVersion", "model", "maxRounds",
                   "referenceAssetId", "referencePage", "visualTolerance") if key in data}
        request.update(requestId=f"{identifier}.{index}", mode="generate", outputType="react", variant=variant,
                       generationMode=mode, batchId=identifier, variationIndex=index,
                       visualPolicy="exact" if variant == "baseline" else "variation-review")
        slot = {"index": index, "variant": variant, "role": "baseline" if variant == "baseline" else mode if mode == "creative" else "variation"}
        try:
            response = api._run_create(owner, request, scope=scope, batch_context=current)
            value = json.loads(response["body"])
            slot.update(runId=value["run"]["id"], jobId=value["job"]["id"])
        except HTTPError as error:
            slot["error"] = error.message
            slot["errorCode"] = error.code
        except Exception:
            slot["error"] = "이 안의 생성 요청을 준비하지 못했습니다. 같은 요청으로 다시 시도할 수 있습니다."
            slot["errorCode"] = "prepare-failed"
        for attempt in range(3):
            current = api._get(owner, "batch", identifier)
            previous = next((item for item in current["slots"] if item["index"] == index), None)
            if previous and previous.get("runId") and not slot.get("runId"):
                break
            if previous and previous.get("runId") and slot.get("runId") and previous["runId"] != slot["runId"]:
                raise Conflict("The comparison slot changed")
            slots = [item for item in current["slots"] if item["index"] != index] + [slot]
            slots.sort(key=lambda item: item["index"])
            try:
                api.storage.put(owner, "batch", {**current, "slots": slots,
                    "runIds": [item["runId"] for item in slots if item.get("runId")],
                    "baselineRunId": next((item.get("runId") for item in slots if item["role"] == "baseline"), None),
                    "status": "queued" if len(slots) == current["expectedCount"] and all(item.get("runId") for item in slots) else "partial"},
                    current["version"])
                break
            except Conflict:
                if attempt == 2:
                    raise
    return _json(202, batch_view(api, owner, api._get(owner, "batch", identifier)))


def batch_view(api, owner, batch):
    return {"batch": batch, "runs": [api._get(owner, "run", identifier) for identifier in batch.get("runIds", [])]}
