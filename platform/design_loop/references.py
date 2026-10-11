"""Opaque, reversible prompt aliases for verified structural references, never prose or amounts."""
from __future__ import annotations

import json
import re

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESOURCE = re.compile(r"[a-z][a-z0-9-]{0,30}-[0-9a-f]{32,64}(?:--r[0-9]{6})?\Z")
_CANONICAL = re.compile(r"(?:node|edge)-[0-9a-f]{40,64}\Z")


def _letters(number):
    value = ""
    while True:
        number, digit = divmod(number, 26)
        value = chr(97 + digit) + value
        if number == 0:
            return value
        number -= 1


def structural(snapshot, pages=()):
    """Only references obtained from an authorized, hash-validated snapshot/admission."""
    values = set()
    for node in [*snapshot.get("nodes", []), *snapshot.get("edges", [])]:
        if _CANONICAL.fullmatch(node.get("id", "")):
            values.add(node["id"])
        if _HASH.fullmatch(node.get("contentHash", "")):
            values.add(node["contentHash"])
        for source in node.get("sourceRefs", []):
            for field in ("sourceId", "revision"):
                if _RESOURCE.fullmatch(source.get(field, "")):
                    values.add(source[field])
            if _HASH.fullmatch(source.get("sha256", "")):
                values.add(source["sha256"])
    if _HASH.fullmatch(snapshot.get("generation", "")):
        values.add(snapshot["generation"])
    for page in pages:
        for value in [page.get("admissionId", ""), page.get("sourceRef", {}).get("sourceId", "")]:
            if _RESOURCE.fullmatch(value):
                values.add(value)
        if _HASH.fullmatch(page.get("derivativeHash", "")):
            values.add(page["derivativeHash"])
    return values


class References:
    def __init__(self, snapshot, pages=()):
        context = json.dumps([snapshot, pages], ensure_ascii=False)
        prefix = "opaque_ref_"
        while prefix in context:
            prefix += "x"
        self.forward = {value: prefix + _letters(i) for i, value in enumerate(sorted(structural(snapshot, pages)))}
        self.reverse = {alias: value for value, alias in self.forward.items()}

    @staticmethod
    def _replace(text, mapping):
        if not mapping:
            return text
        pattern = "|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True))
        return re.sub(pattern, lambda match: mapping[match.group()], text)

    def encode(self, text):
        return self._replace(text, self.forward)

    def decode(self, text):
        return self._replace(text, self.reverse)
