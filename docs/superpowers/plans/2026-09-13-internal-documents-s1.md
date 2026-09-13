# Internal Documents and Source-bound S1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Check off completed steps; preserve the shared contract.

**Goal:** Build a private, versioned document library and make S1 results open the exact authorized source paragraph.

**Architecture:** Reuse the committed workspace authentication, membership, private storage and asynchronous worker. Add isolated document namespaces, document ACLs and immutable extraction revisions. S1 uses private asynchronous analyses with source-bound citations and human decisions; it does not reuse the shared demonstration event cache.

**Tech Stack:** Python 3.12, existing S3/DynamoDB workspace storage, existing extraction dependencies and Bedrock gate, React/TypeScript, Playwright.

**Spec:** `platform/documents/CONTRACT.md`

## Global Constraints

- Original user workspace and `/tmp/agenticai-workbench-implementation` are out of the write scope.
- Read access applies to metadata, chunks, search, worker input and result replay.
- Originals 20 MiB; chunks 2 MiB; 200 documents; 100 revisions/document; PDF 200 pages; text 400,000 characters; paragraph 2,000 characters.
- Model context at most 20 documents and 36,000 characters. Missing regulation source means no model call.
- No private documents in public source/static hosting/reviewer payloads. Use synthetic fixtures.
- Do not claim source authenticity, semantic correctness or human approval from a valid ID/hash.
- Latest-HEAD AI review, no unresolved Critical/Major, all required CI and content score >=85 precede merge/deployment.

## Task 1: Private library, intake and permissions

**Owner/write set:** Implementer: `platform/documents/{__init__,errors,library,intake,api}.py`, `platform/tests/test_documents_library.py`, `platform/tests/test_documents_intake.py`.

**Consumes:** Existing `Storage`, `Collaboration`, host invocation helpers and the contract.
**Produces:** The `Library`, `handle`, `finalize` and `authorize_job` interfaces in the contract.

- [ ] Add failing tests using `FakeTable`, `FakeS3` and access-token events. Cover foreign owner/project denial, restricted-role listing/download, duplicate request/part behavior, hash mismatch, immutable revisions, prefix-safe history, review/binding uniqueness and concurrent revocation.
- [ ] Implement chunk registration/upload, immutable finalization/extraction, literal paragraph source reads, version review, permission changes, archive and activity.
- [ ] Use CAS writes/checks for project/document authority and approval binding. Keep document bytes outside ordinary asset namespaces.
- [ ] Run `python -m pytest platform/tests/test_documents_library.py platform/tests/test_documents_intake.py -q` and record failures-before/fixes-after.
- [ ] Parent reviews task source/tests and commits the exact owned files.

Example authorization assertion:

```python
status, payload, _ = call(api, "GET", f"/documents/{doc_id}", owner="foreign")
assert status in (403, 404)
assert "originalKey" not in repr(payload)
```

## Task 2: Integration and source-bound analysis

**Owner/write set:** Parent: workspace `http.py`, `storage.py`, `worker.py`; `documents/analysis.py`, `documents/samples.py`; new analysis/integration tests; relevant IaC/package files.

**Consumes:** Task 1 Library interfaces. **Produces:** `/impact-analyses` and `document-analysis`, document routes/job dispatch wired into the existing API.

- [ ] Verify the unchanged HTTP/storage baseline, then add kinds and safe prefix pagination with a cross-prefix cursor regression.
- [ ] Route document HTTP actions after current membership resolution. Protect document-related `/jobs` reads before generic serialization.
- [ ] Add worker dispatch and terminal failure handling for both document task types. Add required scoped DynamoDB condition-check permission and package the new module in both API and worker.
- [ ] Implement explicit-regulation traversal, authorized approved bindings, immutable evidence snapshots, bounded context selection and strict model JSON validation.
- [ ] Add before-model / before-publication / read-time access fences; private result storage; human decisions. No shared event cache.
- [ ] Add a server-owned, clearly synthetic sample installer returning drafts only.
- [ ] Test denied content absent from prompts, revocation during a model call blocking output, changed blobs, unknown citations, source absence/no model, historical source links and decisions.

Model-validation assertion:

```python
result = validate_answer({"summary": "x", "findings": [
    {"nodeId": "DOC-missing", "reason": "x", "citationIds": ["E99"]}
]}, allowed_nodes={"DOC-000"}, evidence_ids={"E1"})
assert result["accepted"] is False
```

## Task 3: Library and commercial S1 interface

**Owner/write set:** Parent or a later fresh implementer: `platform/web/src/documents/`, `platform/web/src/S1.tsx`, App navigation integration, document/S1 browser tests.

**Consumes:** Exact HTTP/DTO contract. **Produces:** authenticated file/review/source-reader and analysis/evidence flows.

- [ ] Create typed client helpers using the existing workspace client and blob verifier, not raw bearer URLs.
- [ ] Build personal/project document lists, bounded uploads, history, original downloads, paragraph links, review and permissions.
- [ ] Replace S1's primary benchmark framing with regulation/source readiness, private job progress, candidate/evidence lists and review decisions.
- [ ] Keep protected source content out of browser persistent storage. Abort/clear on source/scope/auth changes.
- [ ] Exercise file upload through linked result to exact paragraph, plus denied/stale/missing/partial states and desktop/narrow layouts.
- [ ] Run web tests, TypeScript and production build; inspect screenshots.

## Task 4: Review, integration and deployment

- [ ] Record task reviews and final immutable branch diff; fix valid Critical/Major and obtain a fresh review after each changed HEAD.
- [ ] Review IaC/security changes and content >=85; verify all required CI without disabling checks.
- [ ] Recheck base/main and concurrent workbench work before merging. Only one deployment owner; preserve existing MyData/EKS configuration and user drafts.
- [ ] Deploy scoped API/worker code and required scoped IAM change through inspected CloudFormation changes; static index last, with a concurrency guard.
- [ ] Verify live auth, a private synthetic document/revision and exact S1 evidence link. Verify forbidden/source-missing paths. Record actual model invocation vs no-model status.
- [ ] Report PR/merge/deployment evidence and concrete remaining integration limits.
