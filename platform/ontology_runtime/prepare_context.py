"""Build a minimal public-code-only Docker context; no source/customer artifacts."""
from pathlib import Path
import hashlib
import json
import shutil


def prepare(platform, destination, axe):
    platform, destination = Path(platform).resolve(), Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a new empty directory for each Runtime context")
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for module in ("ontology_runtime", "workspace", "workbench", "documents", "engine", "studio", "graph"):
        files.extend(platform.glob(module + "/*.py"))
    files.extend(platform.glob("api/common/*.py"))
    files.extend(path for path in (platform / "react-kit/ui").rglob("*") if path.is_file())
    files.extend(platform / "react-kit" / name for name in ("catalog.json", "package.json", "package-lock.json"))
    manifest = []
    for source in sorted(files):
        if source.is_symlink():
            raise ValueError("The Runtime context cannot include source symlinks")
        target = destination / "code" / source.relative_to(platform)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest.append({"path": source.relative_to(platform).as_posix(),
                         "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    axe = Path(axe).resolve()
    shutil.copyfile(axe / "axe.min.js", destination / "code/workspace/axe.min.js")
    shutil.copyfile(axe / "LICENSE", destination / "code/workspace/AXE_LICENSE")
    for name in ("Dockerfile", "requirements.txt"):
        shutil.copyfile(platform / "ontology_runtime" / name, destination / name)
    (destination / "source-manifest.json").write_text(json.dumps(manifest, indent=2))
    return destination


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--axe", required=True)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parents[1], args.output, args.axe)
