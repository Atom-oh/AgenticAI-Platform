"""Credential-free, bounded child process used by the browser Lambda."""
import base64
import json
import sys
from pathlib import Path

from workspace.browser import evaluate_html


def main():
    event = json.loads(Path(sys.argv[1]).read_text())
    reference = event.get("referenceBase64")
    result = evaluate_html(event.get("html", ""), event.get("contract", {}),
                           base64.b64decode(reference, validate=True) if reference else None,
                           event.get("visualTolerance", 0.15))
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
