#!/usr/bin/env bash
# Docker 빌드 컨텍스트 준비 — 컨테이너가 쓰는 에이전트 명세와 스킬을 _ctx/ 로 복사한다 (정본은 agentcore/agent_specs.py, skills/*.md).
#   bash agents/prepare_context.sh && docker build --platform linux/arm64 -t bank-agents:dev agents
# deploy.sh 는 cdk deploy 전에 이 스크립트를 실행해야 한다 (DockerImageAsset 이 agents/ 를 컨텍스트로 쓴다).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf _ctx && mkdir -p _ctx/skills
cp ../agentcore/agent_specs.py _ctx/agent_specs.py
cp ../skills/*.md _ctx/skills/
echo "agents/_ctx prepared: agent_specs.py + $(ls _ctx/skills | wc -l | tr -d ' ') skills"
