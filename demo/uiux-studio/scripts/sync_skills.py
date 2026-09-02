"""Upload skills/ tree to the org skill registry bucket (from config/stack.json)."""
import json
import mimetypes
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    bucket = cfg["skills_bucket"]
    s3 = boto3.client("s3", region_name=cfg["region"])
    count = 0
    for path in (ROOT / "skills").rglob("*"):
        if path.is_file():
            key = f"skills/{path.relative_to(ROOT / 'skills')}"
            s3.upload_file(str(path), bucket, key, ExtraArgs={
                "ContentType": mimetypes.guess_type(path.name)[0] or "text/markdown"})
            count += 1
    print(f"synced {count} skill files to s3://{bucket}/skills/")


if __name__ == "__main__":
    main()
