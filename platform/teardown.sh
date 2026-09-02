#!/usr/bin/env bash
# 시연 후 정리 (SPEC §11 — Neptune·RDS·ECS·엔드포인트는 상시 과금).
#   bash teardown.sh          # 플레인 스택만 삭제 (웹·API는 유지, ALLOW_LOCAL_PLANE 로 폴백)
#   bash teardown.sh --all    # 메인 스택까지 삭제 (CloudFront URL 소멸)
set -euo pipefail
cd "$(dirname "$0")/infra"
REGION=${AWS_REGION:-ap-northeast-2}
echo "플레인 스택 삭제 중 (10~15분)…"
npx cdk destroy BankPlatformPlane --force
for a in "$@"; do
  if [ "$a" = "--all" ]; then echo "메인 스택 삭제 중…"; npx cdk destroy BankPlatform --force -c planeDeployed=false; fi
done
echo "남은 리소스 확인:"
aws cloudformation list-stacks --region "$REGION" --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED \
  --query 'StackSummaries[?starts_with(StackName,`BankPlatform`)].[StackName,StackStatus]' --output table
