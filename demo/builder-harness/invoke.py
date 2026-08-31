import sys
import uuid

import boto3

HARNESS_ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:180294183052:harness/AgenticBookBuilderDemo-6R0pXEwrY1"
REGION = "ap-northeast-2"


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "안녕하세요, 사내 계약서 검토를 도와주는 에이전트를 만들고 싶어요."
    session_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4()) + "-session"

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    response = client.invoke_harness(
        harnessArn=HARNESS_ARN,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": text}]}],
    )

    print(f"[session_id] {session_id}\n")
    for event in response["stream"]:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print(f"\n\n[stopReason] {event['messageStop'].get('stopReason')}")
        elif "metadata" in event:
            print(f"[metadata] {event['metadata']}")
        elif any(k in event for k in ("validationException", "internalServerException", "runtimeClientError")):
            print(f"[error event] {event}")


if __name__ == "__main__":
    main()
