"""Build-time fetch of the lockfile-pinned axe browser bundle, with integrity."""
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path


def main():
    package = json.loads(Path(sys.argv[1]).read_text())["packages"]["node_modules/axe-core"]
    url = package["resolved"]
    if not url.startswith("https://registry.npmjs.org/axe-core/-/"):
        raise ValueError("Unexpected axe package source")
    algorithm, expected = package["integrity"].split("-", 1)
    if algorithm != "sha512":
        raise ValueError("Expected SHA-512 package integrity")
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000 or base64.b64encode(hashlib.sha512(data).digest()).decode() != expected:
        raise ValueError("Axe package integrity mismatch")
    destination = Path(sys.argv[2])
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in ("axe.min.js", "LICENSE"):
            member = archive.getmember(f"package/{name}")
            if not member.isfile() or member.size > 5_000_000:
                raise ValueError("Invalid axe package member")
            with archive.extractfile(member) as source:
                (destination / name).write_bytes(source.read())


if __name__ == "__main__":
    main()
