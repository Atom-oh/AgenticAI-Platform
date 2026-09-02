"""Admin-create a designer account (org policy: no self sign-up).

Usage: python scripts/create_designer.py <username> <password>
"""
import json
import pathlib
import sys

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    username, password = sys.argv[1], sys.argv[2]
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    idp = boto3.client("cognito-idp", region_name=cfg["region"])
    try:
        idp.admin_create_user(UserPoolId=cfg["user_pool_id"], Username=username,
                              MessageAction="SUPPRESS")
    except idp.exceptions.UsernameExistsException:
        pass
    idp.admin_set_user_password(UserPoolId=cfg["user_pool_id"], Username=username,
                                Password=password, Permanent=True)
    print(f"designer ready: {username}")


if __name__ == "__main__":
    main()
