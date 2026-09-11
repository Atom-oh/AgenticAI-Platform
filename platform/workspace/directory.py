"""Bounded existing-user lookup for project owners; never sends invitations."""
from __future__ import annotations

import json
import re


class CognitoDirectory:
    def __init__(self, user_pool_id, client=None):
        self.user_pool_id = user_pool_id
        self._client = client

    def __call__(self, query):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200 or any(char in query for char in "\x00\r\n"):
            raise ValueError("사용자 ID 또는 이메일을 두 글자 이상 입력하세요.")
        query = query.strip()
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client("cognito-idp", config=Config(connect_timeout=3, read_timeout=5,
                                                                   retries={"total_max_attempts": 2}))
        attribute, operator = ("sub", "=") if re.fullmatch(r"[a-fA-F0-9-]{36}", query) else ("email", "^=")
        pages = self._client.get_paginator("list_users").paginate(
            UserPoolId=self.user_pool_id, Filter=f"{attribute} {operator} {json.dumps(query, ensure_ascii=False)}",
            PaginationConfig={"MaxItems": 40, "PageSize": 20},
        )
        result, seen = [], set()
        for page in pages:
            for user in page.get("Users", []):
                if user.get("Enabled") is False:
                    continue
                attributes = {item["Name"]: item["Value"] for item in user.get("Attributes", [])
                              if isinstance(item, dict) and isinstance(item.get("Name"), str) and isinstance(item.get("Value"), str)}
                sub = attributes.get("sub")
                if not sub or sub in seen:
                    continue
                seen.add(sub)
                result.append({"sub": sub, "displayName": (attributes.get("name") or attributes.get("email") or sub)[:180]})
                if len(result) == 20:
                    return result
        return result
