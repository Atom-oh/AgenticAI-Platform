"""Designer identity: validate a Cognito access token (x-hana-auth header).

GetUser is authenticated by the access token itself, so no extra IAM is
required; Cognito rejects expired/forged tokens server-side.
"""
import boto3


def actor_from_event(event):
    """Return the Cognito username for the request, or None when unauthenticated."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    token = headers.get("x-hana-auth", "")
    if not token:
        return None
    try:
        user = boto3.client("cognito-idp").get_user(AccessToken=token)
        return user["Username"]
    except Exception:
        return None
