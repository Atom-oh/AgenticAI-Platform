"""Trusted assembler joining the compiler build and the Browser report (engine plan, Task E16; review rounds 4-5, X6).

`assemble(build, browser, contract=..., contract_hash_fn=...)` mirrors `workspace/react_runtime.py` (the build summary kept on the report)
and `workspace/react_generation.py` (the hashes stamped on the stored report), so an engine round carries exactly the
fields `WorkspaceAPI._run_approve` compares: `sourceHash`, `bundleHash`, `catalogHash`, `artifactSha256` and
`contractHash`. Any mismatch between the parts raises; nothing is repaired.

The production contract hash (`workspace.rules.contract_hash`) is injected by the caller (`contract_hash_fn=`), because
`design_loop` imports only the standard library and the pure schema modules (PR #30 review 1, #9). A missing or
failing hasher raises `EvidenceUnavailable` instead of assembling an unverifiable report.
"""
from __future__ import annotations

import hashlib

_DROPPED = ("files", "previewHtml", "sourceFiles")
_HEX64 = frozenset("0123456789abcdef")


class EvidenceUnavailable(RuntimeError):
    pass


def contract_hash(contract, hasher):
    """`hasher(contract)` -> 64-hex digest. Not callable, raising or malformed -> EvidenceUnavailable."""
    if not callable(hasher):
        raise EvidenceUnavailable("contract hashing is unavailable")
    try:
        value = hasher(contract)
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - an adapter failure is missing evidence, never a pass
        raise EvidenceUnavailable("contract hashing failed") from error
    if not _sha(value):
        raise EvidenceUnavailable("contract hashing returned a malformed digest")
    return value


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def assemble(build, browser, *, contract, contract_hash_fn=None):
    if not isinstance(build, dict) or build.get("ok") is not True:
        raise ValueError("build-not-ok")
    if not isinstance(browser, dict):
        raise ValueError("browser-report-missing")
    for key in ("sourceHash", "bundleHash", "catalogHash"):
        if not _sha(build.get(key)):
            raise ValueError(f"build-{key}-missing")
    if not isinstance(build.get("previewHtml"), str) or not build["previewHtml"]:
        raise ValueError("preview-missing")
    if browser.get("bundleHash") != build["bundleHash"]:
        raise ValueError("bundle-hash-mismatch")
    if not isinstance(contract, dict) or build["catalogHash"] != contract.get("catalogHash"):
        raise ValueError("catalog-hash-mismatch")
    report = dict(browser)
    report["build"] = {key: value for key, value in build.items() if key not in _DROPPED}
    report.update(sourceHash=build["sourceHash"], bundleHash=build["bundleHash"], catalogHash=build["catalogHash"],
                  artifactSha256=hashlib.sha256(build["previewHtml"].encode("utf-8")).hexdigest(),
                  contractHash=contract_hash(contract, contract_hash_fn), outputType="react")
    return report
