"""Exact source classification is independent of project read permission."""
from __future__ import annotations

from intake import admission as intake_admission
from workbench.service import fail, fields
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources, authority_identity
from workspace.collaboration import CollaborationError
from ontology_runtime import inspection

POLICY = "design-nonsensitive-v1"
CLASSES = frozenset({"synthetic", "public", "internal-non-sensitive"})

# ONTOLOGY_CONTRACT transaction-budget preflight (ONT-10). Each protected
# AgentCore transaction (dispatch, every tool call, completion) is one
# DynamoDB `TransactWriteItems` of at most 100 operations. Its non-source
# operations are at most 7 on every path (dispatch: two ledger writes, the
# artifact/job fences, the ontology, project and capability-key fences; a tool
# call: the same; a tool replay: the artifact/job, ontology, project,
# capability-key and execution fences; completion: one ledger write, the
# artifact/job fences, the capability and evidence key fences, the project
# fence; a cached dispatch replay: the artifact/job, ontology, project, both
# key and execution fences), within the contract's reserve of 10. Every
# remaining operation is a source check: the deduplicated source fences plus
# every check `require()` returns for each source.
TRANSACTION_OPERATIONS = 100
NON_SOURCE_OPERATIONS = 10
SOURCE_CHECK_BUDGET = min(90, TRANSACTION_OPERATIONS - max(10, NON_SOURCE_OPERATIONS))
# A source contributes at least three distinct checks: its own source fence,
# its classification (`ac_admission`) and its private-intake decision.
MIN_CHECKS_PER_SOURCE = 3
MAX_SOURCES = SOURCE_CHECK_BUDGET // MIN_CHECKS_PER_SOURCE


# ontology-tools/1: a job has at most 60 tool calls. The source-analysis
# workflow makes one `ontology.source` call per frozen manifest file (paths
# are retrieved individually, even when several reference one source) plus
# fixed control calls: the context stage, `ontology.context`, the analyzed
# stage and `execution.finish`.
MAX_CALLS = 60
CONTROL_CALLS = 4
MAX_FILES = MAX_CALLS - CONTROL_CALLS


def preflight_calls(files):
    """Reject, unchanged and before any read or paid work, a manifest whose
    workflow would exceed the tool-call limit: distinct paths that reference
    one admitted source still need one retrieval each."""
    if len(files) > MAX_FILES:
        fail(422, "execution-completion-scope",
             f"AgentCore 분석 묶음의 도구 호출({len(files) + CONTROL_CALLS}회)이 실행 한도({MAX_CALLS}회)를 넘습니다. "
             f"파일 {MAX_FILES}개 이하의 새 요청으로 나누세요.")


def _scope_error(count, limit):
    fail(422, "execution-completion-scope",
         f"AgentCore 분석 묶음의 원본 검증 항목({count}개)이 실행 트랜잭션 한도({limit}개)를 넘습니다. "
         f"원본 {MAX_SOURCES}개 이하의 새 요청으로 나누세요.")


def preflight_sources(references):
    """Cheap upper bound before any read: more than `MAX_SOURCES` distinct
    sources can never fit the source-check budget."""
    distinct = {identifier(reference) for reference in references}
    if len(distinct) > MAX_SOURCES:
        _scope_error(len(distinct) * MIN_CHECKS_PER_SOURCE, SOURCE_CHECK_BUDGET)


def preflight(source_checks):
    """The complete deduplicated source-check set a protected AgentCore
    transaction includes -- source fences plus every `require()` check -- must
    fit `SOURCE_CHECK_BUDGET`. Submission runs this on the same set dispatch,
    each tool call and completion fence, so an accepted job is always
    executable; those paths rerun it before their own commit. The request is
    rejected unchanged, never trimmed."""
    unique = {(check["owner"], check["kind"], check["id"]) for check in source_checks}
    if len(unique) > SOURCE_CHECK_BUDGET:
        _scope_error(len(unique), SOURCE_CHECK_BUDGET)
    return list(source_checks)


def transfer_manifest_hash(paths, resolver):
    """Digest of the transfer metadata a `document-pages` decision does not
    cover: the ordered manifest paths and the resolver sent to the analyzer."""
    return schema.digest({"paths": list(paths), "resolver": resolver})


def inspect_transfer(ctx, paths, resolver):
    """Private identifier inspection of the outgoing paths and resolver
    (source-admission/1: identifier normalization covers code paths and
    resolver metadata; SRC-06/G0-ADMISSION).

    The consumed private-intake `document-pages` decisions admit the source
    TEXT only, while dispatch also transfers the separately supplied manifest
    paths and resolver profile. They are inspected here with the same intake
    facilities the code-collection admission uses for its paths and resolver
    (`intake.admission`'s private deny-list loader, `intake.inspect.inspect`
    with `contiguous=False`): any deny-listed identifier or rule-detected PII
    refuses the request -- AgentCore has no derivative of caller-supplied
    paths to send instead -- and an unavailable deny-list fails closed. The
    returned binding (inspection profile, metadata-only receipt hash and the
    inspected metadata digest) is frozen into the accepted job input; dispatch
    requires the outgoing paths/resolver to match it, so a change aborts."""
    from intake import derivative, inspect as intake_inspect
    try:
        denylist = derivative.canonical_entries(intake_admission._denylist(ctx.host))
    except derivative.DenylistUnavailable:
        fail(503, "agentcore-private-inspection-unavailable",
             "비공개 식별자 검사 기준을 확인하지 못해 AgentCore 전송을 승인할 수 없습니다.")
    pages = [{"page": index + 1, "text": text} for index, text in enumerate(
        [*paths, schema.canonical(resolver).decode()])]
    receipt = intake_inspect.inspect(pages, denylist=denylist, contiguous=False)
    if receipt["blocking"] or receipt["identifiers"]["count"] or receipt["pii"]:
        fail(422, "agentcore-private-inspection",
             "분석 파일 경로 또는 경로 해석 프로필에서 식별 가능 정보가 감지되었습니다. 중립적인 경로로 새 요청을 만드세요.")
    return {"profile": intake_inspect.PROFILE, "receiptHash": receipt["hash"],
            "manifestHash": transfer_manifest_hash(paths, resolver)}


def identifier(reference):
    ref = schema.source_ref(reference)
    return schema.identity("admission", authority_identity(ref))


def _policy_for(ctx, data_class, *, policy_id=None):
    """The current, in-scope `adm_policy` admitting `data_class`, or fail closed."""
    try:
        policy = intake_admission.current_policy(ctx.storage, ctx.project_id, policy_id=policy_id)
    except intake_admission.AdmissionError:
        fail(403, "agentcore-admission-authority-missing",
             "AgentCore 전송을 승인하는 관리형 정책이 없습니다.")
    if data_class not in policy["dataClasses"]:
        fail(403, "agentcore-admission-authority-missing",
             "현재 정책이 이 분류의 AgentCore 전송을 허용하지 않습니다.")
    return policy


def _policy_check(policy):
    return {"owner": intake_admission.INTAKE_OWNER, "kind": "adm_policy", "id": policy["id"], "version": policy["version"]}


def _bind_authority(ctx, data_class, reference, actor):
    """The IAM-administered authority backing `data_class` right now, pinned by
    exact revision onto the classification record (source-admission/1, SRC-06/
    AUTH-08). `require()` later rechecks this SAME pinned policy/provenance/grant
    revision rather than rediscovering "any current" authority: a classification
    recorded before authority existed must never be grandfathered in by authority
    that only appears afterward, with no fresh review backing this exact record."""
    policy = _policy_for(ctx, data_class)
    binding = {"policy": {"id": policy["id"], "revision": policy["revision"], "hash": policy["hash"]}}
    checks = [_policy_check(policy)]
    if data_class in ("synthetic", "public"):
        provenance = intake_admission.find_provenance(ctx.storage, policy, reference, ctx.project_id, data_class)
        if provenance is None:
            fail(403, "agentcore-admission-authority-missing",
                 "등록된 출처 증명이 없어 AgentCore 전송을 승인할 수 없습니다.")
        binding["provenance"] = {"id": provenance["id"], "revision": provenance["revision"]}
        checks.append({"owner": intake_admission.INTAKE_OWNER, "kind": "adm_provenance",
                       "id": provenance["id"], "version": provenance["version"]})
    else:
        grant = intake_admission.find_grant(ctx.storage, actor, policy, ctx.project_id)
        if grant is None:
            fail(403, "agentcore-admission-authority-missing",
                 "검토자 권한이 없어 AgentCore 전송을 승인할 수 없습니다.")
        binding["grant"] = {"id": grant["id"], "revision": grant["revision"]}
        checks.append({"owner": intake_admission.INTAKE_OWNER, "kind": "adm_grant",
                       "id": grant["id"], "version": grant["version"]})
    return binding, checks, policy


_DECISION_SOURCE_FIELDS = ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision")
_ADMISSION_KINDS = ("adm_decision", "adm_policy", "adm_provenance", "adm_grant")


def _decision_missing(message="비공개 반입 심사에서 승인된 결정이 필요합니다."):
    fail(403, "agentcore-admission-decision-required", message)


def _verified_decision(ctx, decision_id, reference, data_class, policy):
    """The current, admitting private-intake `adm_decision` for this exact source
    revision (source-admission/1, SRC-06/RUN-04/G0-ADMISSION), verified through
    `intake.admission.verify` -- decision status/expiry, its pinned policy and
    provenance/reviewer-grant revisions, current source access and the stored
    derivative/inspection hashes -- plus the exact intake record versions it
    observed, for the caller's transaction fences.

    The Runtime transfer reads the source through the ordinary source reader,
    so this decision must have admitted those very bytes: its admitted
    derivative must equal the original (`originalHash == derivativeHash`, i.e.
    identifier normalization substituted nothing). A source whose admitted
    derivative differs never reaches AgentCore through this path."""
    if not isinstance(decision_id, str):
        _decision_missing()
    observed = []
    try:
        decision = intake_admission.verify(ctx.host, ctx.scope, schema._identifier(decision_id), claims=ctx.claims,
                                           sources=Sources(ctx), observe=observed)
    except (intake_admission.AdmissionError, ValueError):
        _decision_missing("비공개 반입 결정이 현재 승인 상태가 아니거나 원본과 맞지 않습니다.")
    except CollaborationError as error:
        if error.status == 401:
            raise
        _decision_missing("비공개 반입 결정이 현재 승인 상태가 아니거나 원본과 맞지 않습니다.")
    derivation = decision["derivation"]
    if (decision["status"] != "admitted" or decision["projectId"] != ctx.project_id
            or decision["source"] != {key: reference[key] for key in _DECISION_SOURCE_FIELDS}
            or decision["dataClass"] != data_class or decision["artifact"]["kind"] != "document-pages"
            or decision["policy"] != {"id": policy["id"], "revision": policy["revision"], "hash": policy["hash"]}):
        _decision_missing("비공개 반입 결정이 현재 원본·분류·정책과 맞지 않습니다.")
    if derivation["originalHash"] != derivation["derivativeHash"]:
        fail(403, "agentcore-admission-derivative",
             "반입 승인본이 원본과 달라 원본 전송으로 AgentCore에 사용할 수 없습니다.")
    pinned = {"id": decision["id"], "revision": decision["revision"], "artifactHash": derivation["derivativeHash"],
              "inspectionHash": decision["inspection"]["hash"]}
    checks = [{key: check[key] for key in ("owner", "kind", "id", "version")}
              for check in observed if check["kind"] in _ADMISSION_KINDS]
    return pinned, checks


def _unique(checks):
    unique = {}
    for check in checks:
        unique.setdefault((check["owner"], check["kind"], check["id"]), check)
    return list(unique.values())


def _recheck_authority(ctx, row):
    """Recheck the EXACT policy/provenance/grant revisions pinned when this
    classification was admitted, never a fresh "any current" lookup: drift
    (retirement, revocation or replacement by a different record) fails closed.
    The exact observed versions are returned so the caller's commit fences them
    too -- a revocation racing the final write aborts it, not only a fresh call."""
    binding = row.get("binding")
    if not isinstance(binding, dict) or "policy" not in binding:
        fail(403, "agentcore-admission-authority-missing", "AgentCore 전송을 승인하는 관리형 정책이 없습니다.")
    data_class = row["classification"]
    pinned_policy = binding["policy"]
    policy = _policy_for(ctx, data_class, policy_id=pinned_policy["id"])
    if policy["revision"] != pinned_policy["revision"] or policy["hash"] != pinned_policy["hash"]:
        fail(403, "agentcore-admission-authority-missing", "원본 분류의 관리형 정책이 변경되었습니다.")
    checks = [_policy_check(policy)]
    if "provenance" in binding:
        pinned = binding["provenance"]
        provenance = intake_admission.find_provenance(ctx.storage, policy, row["sourceRef"], ctx.project_id, data_class)
        if not provenance or provenance["id"] != pinned["id"] or provenance["revision"] != pinned["revision"]:
            fail(403, "agentcore-admission-authority-missing", "등록된 출처 증명이 변경되었습니다.")
        checks.append({"owner": intake_admission.INTAKE_OWNER, "kind": "adm_provenance",
                       "id": provenance["id"], "version": provenance["version"]})
    else:
        pinned = binding.get("grant")
        if not pinned:
            fail(403, "agentcore-admission-authority-missing", "AgentCore 전송을 승인하는 관리형 정책이 없습니다.")
        grant = intake_admission.find_grant(ctx.storage, row["reviewedBy"], policy, ctx.project_id)
        if not grant or grant["id"] != pinned["id"] or grant["revision"] != pinned["revision"]:
            fail(403, "agentcore-admission-authority-missing", "검토자 권한이 변경되었습니다.")
        checks.append({"owner": intake_admission.INTAKE_OWNER, "kind": "adm_grant",
                       "id": grant["id"], "version": grant["version"]})
    # The exact private-intake decision pinned at classification: same id,
    # revision, admitted derivative and inspection hash, still admitted/current.
    pinned = binding.get("decision")
    if not isinstance(pinned, dict):
        _decision_missing()
    decision, decision_checks = _verified_decision(ctx, pinned.get("id"), row["sourceRef"], data_class, policy)
    if decision != pinned:
        _decision_missing("비공개 반입 결정의 리비전 또는 승인본이 변경되었습니다.")
    return _unique([*checks, *decision_checks]), decision


def classify(ctx, body):
    fields(body, {"requestId", "sourceRef", "classification", "reason", "decisionId"})
    ctx.fresh({"owner"})
    request_id = schema._identifier(body.get("requestId"))
    reference = schema.source_ref(body.get("sourceRef"))
    if body.get("classification") not in CLASSES:
        fail(400, "admission-classification", "합성·공개·검토된 비민감 내부 원본만 AgentCore에 사용할 수 있습니다.")
    reason = schema._text(body.get("reason"), 2000)
    reader = Sources(ctx)
    checks = reader.verify([reference])
    inspected = inspection.inspect_source(ctx, reference)
    identity = identifier(reference)
    current = ctx.storage.get(ctx.owner, "ac_admission", identity)
    if current and current.get("requestId") == request_id:
        if current.get("requestHash") != schema.digest(body) or current.get("inspection") != inspected:
            fail(409, "admission-request-changed", "같은 분류 요청 ID의 원본 또는 검사 기준이 변경되었습니다.")
        reader.recheck()
        return current
    # source-admission/1, SRC-06/AUTH-08: authority must exist NOW, backing this
    # exact classification, and is pinned onto the record -- a classification
    # recorded with no authority behind it must never later be grandfathered in
    # by authority that only appears afterward; a fresh call (this one) is the
    # review that binds it.
    binding, authority_checks, policy = _bind_authority(ctx, body["classification"], reference, ctx.actor)
    # ...and the private-intake decision that admitted these exact source bytes
    # under that same policy revision is consumed and pinned (decision id,
    # revision, admitted artifact hash, inspection hash); no decision -- missing,
    # blocked (e.g. `denylist-unavailable`), pending review, expired or for
    # another revision -- means no classification.
    decision, decision_checks = _verified_decision(ctx, body.get("decisionId"), reference, body["classification"],
                                                   policy)
    binding["decision"] = decision
    authority_checks = _unique([*authority_checks, *decision_checks])
    record = {"id": identity, "projectId": ctx.project_id, "sourceRef": reference,
        "classification": body["classification"], "policy": POLICY, "status": "approved",
        "reviewedBy": ctx.actor, "reviewedAt": ctx.storage.clock(), "reason": reason, "inspection": inspected,
        "requestId": request_id, "requestHash": schema.digest(body), "binding": binding}
    reader.recheck()
    return ctx.commit([ctx.write("ac_admission", record, current["version"] if current else None)],
                       checks + authority_checks)[0]


def require(ctx, reference):
    """The transaction fences for one admitted source (see `_require`)."""
    return _require(ctx, reference)[0]


def consumed(ctx, references):
    """`(fences, decisions)` for an AgentCore transfer of `references`.

    `fences` are the flat record-version checks every `require()` returns, for
    the caller's transaction (and its identity comparisons). `decisions` are
    the consumed private-intake decisions, kept separate from those fences, in
    the platform-execution/1 shape `[{decisionId, revision, artifactHash}]`
    (string revision, distinct decision ids, in first-reference order) that the
    canonical ledger's admission validator accepts; callers bind this list
    into the immutable job input, execution request, attempt and signed
    Runtime receipt (RUN-04/05)."""
    fences, decisions = [], {}
    for reference in references:
        checks, decision = _require(ctx, reference)
        fences.extend(checks)
        entry = {"decisionId": decision["id"], "revision": str(decision["revision"]),
                 "artifactHash": decision["artifactHash"]}
        if decisions.setdefault(entry["decisionId"], entry) != entry:
            _decision_missing("비공개 반입 결정의 리비전 또는 승인본이 변경되었습니다.")
    return fences, list(decisions.values())


def _require(ctx, reference):
    try:
        row = ctx.get("ac_admission", identifier(reference))
    except CollaborationError as error:
        if error.status != 404:
            raise
        fail(403, "agentcore-input-unclassified", "AgentCore에 사용할 원본의 비민감 분류 검토가 필요합니다.")
    receipt = row.get("inspection", {})
    source = ctx.get("asset", reference["sourceId"]) if reference["sourceKind"] == "asset" else {}
    if (receipt.get("policy") != inspection.POLICY or receipt.get("profileHash") != inspection.profile()
            or receipt.get("sourceHash") != reference["sha256"]
            or receipt.get("descriptorHash") != schema.digest([reference, source.get("name")])
            or receipt.get("detectedIdentifiers") != 0):
        fail(403, "agentcore-inspection-stale", "현재 원본에 대한 비공개 경계 검사를 다시 실행해야 합니다.")
    if (row.get("policy") != POLICY or row.get("status") != "approved"
            or row.get("classification") not in CLASSES
            or identifier(row["sourceRef"]) != identifier(reference)):
        fail(403, "agentcore-input-unclassified", "AgentCore에 사용할 원본의 비민감 분류 검토가 필요합니다.")
    # source-admission/1, SRC-06/AUTH-08: recheck the EXACT policy/provenance/
    # grant revisions pinned when this classification was admitted (not merely
    # "any current" authority), and fence their observed versions into the
    # caller's commit so a revocation racing the final write aborts it too.
    checks, decision = _recheck_authority(ctx, row)
    return [ctx.check("ac_admission", row), *checks], decision
