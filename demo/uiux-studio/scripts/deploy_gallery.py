"""Upload gallery/ to the drafts bucket root (CloudFront default origin)."""
import json
import pathlib
import time

import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from scripts.deployment_config import client as deployment_client, config_path, load_config, save_config
TYPES = {".html": "text/html", ".json": "application/json"}


def main():
    cfg = load_config(for_deploy=True)
    s3 = deployment_client(cfg, "s3", region_name=cfg["region"])
    for path in (ROOT / "gallery").iterdir():
        # never clobber a live manifest that already has drafts
        if path.name == "drafts.json":
            try:
                s3.head_object(Bucket=cfg["drafts_bucket"], Key="drafts.json")
                continue
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code not in ("404", "NoSuchKey"):
                    raise
        s3.upload_file(str(path), cfg["drafts_bucket"], path.name,
                       ExtraArgs={"ContentType": TYPES.get(path.suffix, "text/plain")})
        print(f"uploaded {path.name}")

    distribution_id = cfg.get("distribution_id")
    if not distribution_id:
        print("WARNING: distribution_id missing from config/stack.json — "
              "run scripts/write_config.py first, then re-run this script to "
              "invalidate the CloudFront cache (otherwise the new UI may stay hidden "
              "behind the stale edge cache).")
        return
    cf = deployment_client(cfg, "cloudfront", region_name=cfg["region"])
    resp = cf.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": 2, "Items": ["/", "/index.html"]},
            "CallerReference": str(time.time()),
        })
    print(f"invalidation id: {resp['Invalidation']['Id']}")


if __name__ == "__main__":
    main()
