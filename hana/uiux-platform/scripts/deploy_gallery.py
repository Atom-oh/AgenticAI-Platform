"""Upload gallery/ to the drafts bucket root (CloudFront default origin)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
TYPES = {".html": "text/html", ".json": "application/json"}


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    s3 = boto3.client("s3", region_name=cfg["region"])
    for path in (ROOT / "gallery").iterdir():
        # never clobber a live manifest that already has drafts
        if path.name == "drafts.json":
            try:
                s3.head_object(Bucket=cfg["drafts_bucket"], Key="drafts.json")
                continue
            except s3.exceptions.ClientError:
                pass
        s3.upload_file(str(path), cfg["drafts_bucket"], path.name,
                       ExtraArgs={"ContentType": TYPES.get(path.suffix, "text/plain")})
        print(f"uploaded {path.name}")


if __name__ == "__main__":
    main()
