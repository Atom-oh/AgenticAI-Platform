# Designer workspace integration contract

2026-09-11. This file fixes interfaces between parallel implementation tasks.
The product operates without Figma access, including network 3. Uploaded files
are untrusted data, never agent instructions or executable server programs.

## HTTP

Same-origin `/studio-api`, Cognito access token in `Authorization: Bearer`.
The API Gateway JWT authorizer supplies claims; handler requires `sub` and
`token_use=access`. Owner is the verified `sub`, never a body field.
Every resource lookup is owner-scoped. Responses are JSON unless a blob route.
Errors use HTTP status plus `{error, code}`. No raw exception/prompt/token logs.

- GET `/config`: `{models, defaultModel, maxFileBytes:52428800, chunkBytes:2097152, extensions}`.
- GET `/assets`: `{assets}` (metadata only, at most100; continuation if needed).
- POST `/assets`: `{name,size,sha256,purpose,parentId?}` -> `{asset,chunkBytes}`.
  Purpose: `reference|component|token|skill|guide|prototype|archive`.
  Accept HTML/HTM, CSS, PNG/JPG/JPEG, SVG, PDF, FIG, MD/MARKDOWN, TXT, JSON.
- PUT `/assets/:id/parts/:index`: binary bytes, max2MiB. Exact expected part size;
  retries must match the prior part hash. No writes after upload completion.
- POST `/assets/:id/complete`: `{}` ->202 `{asset,job}`; background finalize.
- GET `/assets/:id`: `{asset,analysis?}`.
- GET `/assets/:id/blob?kind=original|preview&page=1&offset=0`: binary chunk;
  `X-Total-Size`, `X-Chunk-Size`, `X-Content-Type`, `X-SHA256`.
  Original downloads are always attachment/octet-stream, never active same-origin HTML.
- DELETE `/assets/:id`: archive metadata; retain originals and run snapshots.
- POST `/contracts/propose`: `{assetIds,brief,model,requestId}` ->202 `{job}`.
- GET `/contracts`: `{contracts}`; GET `/contracts/:id`: `{contract}`.
- POST `/contracts`: manual `{assetIds,brief,rules,viewport,unresolved?,title}` -> `{contract}`.
- PUT `/contracts/:id`: `{version,...editableContract}` -> `{contract}`; CAS,
  increment version, status draft, clear approval. Snapshot previously approved revisions.
- POST `/contracts/:id/approve`: `{version}` -> `{contract}`; approve exact version/hash.
- POST `/runs`: `{contractId,contractVersion,model,maxRounds,requestId,
  baseRunId?,baseRound?,instruction?,referenceAssetId?,referencePage?,
  visualTolerance?,variant?,mode?,sourceAssetId?}` ->202 `{job,run}`.
  mode generate(default) or verify. Verify freezes a selected imported HTML's
  sourceHtmlKey/sourceArtifactSha256, uses one round and makes no generation call.
  `maxRounds`1..5(default3); variant balanced/dense/emphasis/flow.
  Contract must be approved, resolved and have at least one asserted rule.
- GET `/jobs/:id`: `{job}`; GET `/runs`: `{runs}`; GET `/runs/:id`: `{run}`.
- GET `/runs/:id/blob?kind=html|screenshot|diff|report&round=1&offset=0`: binary chunk.
- POST `/runs/:id/approve`: `{round,artifactSha256,contractVersion}`:
  only exact tested artifact with all required gates passed; keep actor/time/hash.

Upload max50MiB, at most20 selected assets, metadata name max180chars, brief4000,
instruction4000. Frontend uploads sequential parts, can retry without duplicating;
polls queued/running jobs and renders failure explicitly. No external presigned URLs.
Use two-MiB HTTP chunks to stay below Lambda proxy payload limits even with base64.

## Records and storage

`Storage` in `workspace/storage.py`:

```python
get(owner: str, kind: str, id: str) -> dict | None
put(owner: str, kind: str, item: dict, expected_version: int | None = None) -> dict
list(owner: str, kind: str, limit: int = 100) -> list[dict]
claim_job(owner: str, id: str) -> dict | None
```

Kinds `asset`, `job`, `contract`, `run`. Each record has id, version(int),
createdAt/updatedAt(ms), status. Conditional create/update, owner-partitioned
query, no whole-table scans. Use private S3 bucket `WORKSPACE_BUCKET` and DynamoDB
`WORKSPACE_TABLE`. Blob helpers `put_blob(key,data,content_type)`,
`get_blob(key,offset=0,length=None)->bytes`, `blob_info(key)->{size,contentType,sha256}`.
Keys contain owner hash, not raw email/sub. Do not expose arbitrary S3 keys to clients.
`key_for(owner, kind, id, suffix)` returns a safe owner-scoped key.

Asset: name,size,sha256,purpose,parentId, uploadStatus(uploading/processing/stored/failed),
parseStatus(pending/complete/partial/unsupported/failed), originalKey, analysisKey,
previews[{page,key,mime,width,height}], warnings[], error?, archived.
Pages are one-based. `importRevision` and `lineageId` describe file intake lineage;
`version` is the metadata concurrency revision, not the imported file version.
Analysis: text (bounded, truncation explicit), format, pages, dimensions, warnings,
resources (external/missing), parseStatus. FIG is opaque archive/unsupported.

Job: task(finalize/propose/run), input, status(queued/running/completed/failed),
progress, result?, error?. API invokes `WORKSPACE_WORKER_FN` asynchronously with
`{owner,jobId}`. Worker is sole writer of claimed jobs; duplicate invocation is a no-op.
Async invocation retries disabled; worker catches errors and persists failed.

Run: contractId,contractVersion,contractHash,assetSnapshots[{id,sha256,name}],
model, status(queued/running/completed/needs_changes/failed), bestRound, rounds[],
functionalStatus,visualStatus,approval?. Round: number,passed,artifactSha256,
htmlKey,screenshotKey?,diffKey?,reportKey, checks summary, blockingFindings[].
Store large reports/HTML/analysis in S3, not DynamoDB records.

## Rule contract

```json
{
  "schemaVersion":1,
  "title":"가입 입력값 전달",
  "brief":"모바일 가입 흐름",
  "assetIds":["asset-id"],
  "viewport":{"width":390,"height":844},
  "rules":[{
    "id":"R1","title":"입력한 금액이 확인 화면에 나타난다","required":true,
    "source":{"assetId":"guide-id","quote":"입력한 금액을 확인 화면에 표시","kind":"explicit","page":1},
    "steps":[
      {"action":"fill","target":"amount","targetLabel":"납입금액","value":"10000"},
      {"action":"click","target":"next","targetLabel":"다음"},
      {"action":"expectText","target":"summary","targetLabel":"확인 금액","value":"10,000","match":"contains"}
    ]
  }],
  "unresolved":[]
}
```

Targets are `data-testid` identifiers matching `[A-Za-z][A-Za-z0-9_-]{0,79}`.
Optional contract `bindings` maps target IDs to actual CSS selectors in imported
HTML. Renderer uses a unique data-testid first, then the approved CSS binding.
No XPath, selector-engine chaining or JavaScript expressions are accepted.
The UI primarily shows targetLabel/title and Korean action names.
Allowed actions: fill(string),click,check(bool),select(string),press(Enter/Tab/Escape/
ArrowUp/ArrowDown/ArrowLeft/ArrowRight/Space),
expectText(string,match contains|equals),expectValue(string),expectVisible(bool),
expectEnabled(bool),expectChecked(bool). No evaluate, arbitrary JS, goto or network.
`expectStyle` checks a string value and `property` from
color/backgroundColor/fontSize/fontWeight/fontFamily/borderRadius/padding/margin/
gap/minHeight/height/width/borderColor/borderWidth/display against computed CSS.
Max20 rules, each max20 steps; every rule has an expectation. Unresolved requirements
block approval; unsupported rules are never silently dropped or marked passed.
Explicit source quotes must be present in the selected asset's extracted text.
Inferred/manual sources are labelled honestly and require designer approval.
Contract edits reset approval. Every run freezes the exact contract and assets.

`workspace/rules.py`: `validate_contract(data, asset_texts=None)->dict` returns
normalized contract or raises ValueError; `contract_hash(contract)->str`.
Host owns this file and AI orchestration; other workers import it.

## Extraction and browser worker

`workspace/intake.py`: `extract_file(name: str, data: bytes)->dict`.
Return analysis plus `previews` containing `{page,mime,data:bytes,width,height}`.
PDF rasterizes locally (PDFium) and extracts text; limit pages and mark partial.
Pillow validates images; bound decompression/pixels. Text UTF8, safe JSON/Markdown.
HTML parse+external-reference report; preview disallows scripts/connections.
SVG XML has no DTD/entity expansion or active/external nodes; preview safely rasterized
or returned as sanitized image only. Never execute uploaded skill programs.
Original bytes remain unchanged regardless of parser outcome.

`workspace/browser.py`: `evaluate_html(html,contract,reference_png=None,
visual_tolerance=0.15)->dict`. Real Playwright Chromium, fresh context per rule.
All network/service workers/popups blocked; network attempts fail gate.
No arbitrary test-code evaluation. Rendered axe accessibility results, actual
DOM assertions, console errors, screenshots; optional pixel diff at exact viewport.
Fail/incomplete cannot pass. Independent of AWS.
Return JSON plus screenshotBase64/diffBase64, bounded below Lambda response limit.
`workspace/browser_handler.py`: Lambda entry with `{html,contract,referenceBase64?,
visualTolerance?}`, no AWS SDK calls. Browser process gets a minimal environment.
Browser Lambda is placed in isolated subnets, no NAT and no outbound SG rules,
with only runtime logging permissions. No Bedrock, S3, registry or user credentials.

`workspace/worker.py` (host): completes imports, proposes rules through the existing
Bedrock gate, generates executable HTML with data-testid bindings, invokes browser
Lambda, repairs failures against the SAME approved rules up to maxRounds.
No real financial API/auth/transaction calls. Simulated states must be labelled.
Images/resources are embedded from selected imports; external references fail.
Artifacts remain private. A real internal React/MCP source is only claimed when
the configured registry actually supplies it.
