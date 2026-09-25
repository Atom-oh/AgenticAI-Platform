"""Build a minimal public-code-only Docker context; no source/customer artifacts."""
from pathlib import Path
import hashlib
import json


def prepare(platform, destination, axe):
    platform, destination, axe = Path(platform).absolute(), Path(destination).resolve(), Path(axe).absolute()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a new empty directory for each Runtime context")
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for module in ("ontology_runtime", "workspace", "workbench", "documents", "engine", "studio", "graph"):
        files.extend(platform.glob(module + "/*.py"))
    files.extend(platform.glob("api/common/*.py"))
    files.extend(path for path in (platform / "react-kit/ui").rglob("*") if path.is_file())
    files.extend(platform / "react-kit" / name for name in ("catalog.json", "package.json", "package-lock.json"))
    planned = [(source, "code/" + source.relative_to(platform).as_posix(),
                source.relative_to(platform).as_posix()) for source in sorted(files)]
    planned.extend([(axe / "axe.min.js", "code/workspace/axe.min.js", "workspace/axe.min.js"),
                    (axe / "LICENSE", "code/workspace/AXE_LICENSE", "workspace/AXE_LICENSE")])
    planned.extend((platform / "ontology_runtime" / name, name, "@build/" + name)
                   for name in ("Dockerfile", "requirements.txt", "requirements.lock"))
    for source, _, _ in planned:
        if not source.is_file() or any(path.is_symlink() for path in (source, *source.parents)):
            raise ValueError("Every Runtime input must be a regular file without symlink parents")
    manifest = []
    for source, relative, name in planned:
        raw = source.read_bytes()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    (destination / "source-manifest.json").write_text(json.dumps(manifest, indent=2))
    return destination


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--axe", required=True)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parents[1], args.output, args.axe)
