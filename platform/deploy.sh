#!/usr/bin/env bash
# 종합 배포 — api-dist 조립 → (플레인 스택) → 메인 스택 → Registry 시드 → 프론트 빌드/업로드/무효화
#
#   bash deploy.sh                 # 메인 스택만 (플레인 스택이 이미 있으면 자동 연결)
#   bash deploy.sh --plane         # 플레인 스택(VPC·ECS·RDS·Neptune·브리지·Writer)도 배포/갱신 (15~25분)
#   GRAPH_BACKEND=neptune bash deploy.sh   # 시연용: 그래프 백엔드 Neptune (플레인 스택 필수)
#
# 정리: bash teardown.sh
set -euo pipefail
cd "$(dirname "$0")"
LOG=${LOG:-/tmp/bank-platform-deploy.log}
REGION=${AWS_REGION:-ap-northeast-2}
GRAPH_BACKEND=${GRAPH_BACKEND:-local}
MAIN_STACK=${MAIN_STACK:-BankPlatformCore}   # 라이브 메인 스택. (구 `BankPlatform` 스택은 롤백 정리 고착 — 갱신하지 않는다)
WITH_PLANE=0; NO_WEB=0; NO_SEED=0
for a in "$@"; do
  [ "$a" = "--plane" ] && WITH_PLANE=1
  [ "$a" = "--no-web" ] && NO_WEB=1
  [ "$a" = "--no-seed" ] && NO_SEED=1
done
echo "log: $LOG"; : > "$LOG"

echo "== 0) 사전 점검 =="
if [ "${SKIP_TESTS:-0}" != "1" ]; then
  python3 -m pytest tests/ -q 2>&1 | tail -2 | tee -a "$LOG"
else
  echo "(SKIP_TESTS=1 — 테스트 생략: 병렬 편집 중 임시 배포)" | tee -a "$LOG"
fi
[ -f seed/out/nodes.jsonl ] || python3 seed/generate.py >> "$LOG" 2>&1

echo "== 1) api-dist 조립 =="
rm -rf api-dist && mkdir -p api-dist/seed/out
cp api/*.py api-dist/
cp -r api/common api/handlers engine graph onprem semantic api-dist/
for m in registry screengen report agentcore design_loop; do [ -d "$m" ] && cp -r "$m" api-dist/; done
[ -d skills ] && cp -r skills api-dist/
# Harness·Registry API는 최신 boto3가 필요하다 (Lambda 기본 boto3에는 없음) — 배포 패키지에 동봉
pip3 install -q --upgrade --target api-dist boto3 botocore >> "$LOG" 2>&1 || { echo "boto3 vendoring failed"; tail -5 "$LOG"; exit 1; }
# PyPI botocore 모델이 AWS CLI v2 번들보다 뒤처질 수 있다(Harness memory/disabled 등) — CLI 서비스 모델을 덧씌운다
CLI_DATA=$(ls -d /usr/local/aws-cli/v2/*/dist/awscli/botocore/data 2>/dev/null | tail -1)
if [ -n "$CLI_DATA" ]; then
  for svc in bedrock-agentcore-control bedrock-agentcore bedrock-runtime bedrock; do
    for v in "$CLI_DATA/$svc"/*/; do
      ver=$(basename "$v"); mkdir -p "api-dist/botocore/data/$svc/$ver"
      for f in service-2.json paginators-1.json waiters-2.json endpoint-rule-set-1.json; do
        if [ -f "$v/$f" ]; then rm -f "api-dist/botocore/data/$svc/$ver/$f.gz"; gzip -c "$v/$f" > "api-dist/botocore/data/$svc/$ver/$f.gz"; fi
        if [ -f "$v/$f.gz" ]; then cp "$v/$f.gz" "api-dist/botocore/data/$svc/$ver/$f.gz"; fi
      done
    done
  done
  echo "botocore models overlaid from CLI $(basename $(dirname $(dirname $(dirname $CLI_DATA))))" | tee -a "$LOG"
fi
# Skills → S3 SKILL.md 폴더 구조 (Harness skills[].s3.uri = s3://bucket/skills/<name>/)
rm -rf skills-dist && mkdir -p skills-dist
for f in skills/*.md; do n=$(basename "$f" .md); mkdir -p "skills-dist/$n"; cp "$f" "skills-dist/$n/SKILL.md"; done
# Lambda에는 PyYAML이 없다 — metrics.yaml → metrics.json 변환본 포함
python3 -c "import yaml,json,pathlib; p=pathlib.Path('semantic/metrics.yaml'); \
  pathlib.Path('api-dist/semantic/metrics.json').write_text(json.dumps(yaml.safe_load(p.read_text()),ensure_ascii=False))"
[ -f seed/out/corpus.embeddings.json ] || python3 -c "from engine.vectorrag import HybridIndex; HybridIndex.load()"
cp seed/out/nodes.jsonl seed/out/edges.jsonl seed/out/corpus.jsonl seed/out/corpus.embeddings.json api-dist/seed/out/
mkdir -p api-dist/seed/design && cp seed/design/*.json api-dist/seed/design/   # 디자인 스튜디오 시드(상품명세서·SM 모델·체크리스트)
find api-dist -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf api-dist/onprem/data
# 온프렘 컨테이너 데이터 (벡터 인덱스는 플레인 소속 — §3.2)
mkdir -p onprem/data && cp seed/out/corpus.jsonl seed/out/corpus.embeddings.json onprem/data/
# Strands 에이전트 컨테이너 빌드 컨텍스트 (agent_specs + skills)
[ -f agents/prepare_context.sh ] && bash agents/prepare_context.sh | tee -a "$LOG"
# 게이트 실행기(Node) 의존성
if [ -f gates/package.json ]; then (cd gates && ([ -d node_modules ] || npm ci --omit=dev --no-fund --no-audit >> "$LOG" 2>&1)); fi
du -sh api-dist | tee -a "$LOG"

cd infra
[ -d node_modules ] || npm install --no-fund --no-audit >> "$LOG" 2>&1

PLANE_DEPLOYED=false
if [ "$WITH_PLANE" = "1" ]; then
  echo "== 2a) 플레인 스택 배포 (Two-Plane VPC · ECS · RDS · Neptune · 브리지 · Writer) =="
  npx cdk deploy BankPlatformPlane --require-approval never --outputs-file plane-outputs.json >> "$LOG" 2>&1 || { tail -40 "$LOG"; exit 1; }
fi
if aws ssm get-parameter --name /bank-platform/plane/bridgeFnName --region "$REGION" >/dev/null 2>&1; then PLANE_DEPLOYED=true; fi
if [ "$GRAPH_BACKEND" = "neptune" ] && [ "$PLANE_DEPLOYED" != "true" ]; then
  echo "GRAPH_BACKEND=neptune 는 플레인 스택이 필요합니다 (bash deploy.sh --plane)"; exit 1; fi
echo "planeDeployed=$PLANE_DEPLOYED graphBackend=$GRAPH_BACKEND"

echo "== 2b) 메인 스택 배포 ($MAIN_STACK) =="
rm -rf cdk.out   # 오래된 synth 캐시로 배포되는 사고 방지
npx cdk deploy "$MAIN_STACK" --require-approval never --outputs-file outputs.json \
  -c planeDeployed=$PLANE_DEPLOYED -c graphBackend=$GRAPH_BACKEND -c mainStackName=$MAIN_STACK >> "$LOG" 2>&1 || { tail -40 "$LOG"; exit 1; }
out() { python3 -c "import json;print(json.load(open('outputs.json'))['$MAIN_STACK']['$1'])"; }
BUCKET=$(out WebBucketName); DIST=$(out DistributionId); WSS=$(out WssUrl); URL=$(out WebUrl)
ADMIN=$(out AdminFnName); CLIENT=$(out CognitoClientId)
cd ..

if [ "$NO_SEED" = "0" ]; then
echo "== 3) Registry 기준선 시드 (멱등) =="
aws lambda invoke --function-name "$ADMIN" --region "$REGION" --cli-binary-format raw-in-base64-out \
  --payload '{"op":"seed_registry"}' /tmp/seed-registry.json >> "$LOG" 2>&1 && head -c 400 /tmp/seed-registry.json; echo
echo "== 3a) 시나리오 에이전트 Harness 생성 + AgentCore Registry 미러 (멱등) =="
aws lambda invoke --function-name "$ADMIN" --region "$REGION" --cli-binary-format raw-in-base64-out \
  --payload '{"op":"seed_agents"}' /tmp/seed-agents.json >> "$LOG" 2>&1 && head -c 800 /tmp/seed-agents.json; echo
if [ "$GRAPH_BACKEND" = "neptune" ]; then
  echo "== 3b) Neptune 적재 (관리 작업) =="
  aws lambda invoke --function-name "$ADMIN" --region "$REGION" --cli-binary-format raw-in-base64-out \
    --payload '{"op":"load_neptune"}' /tmp/load-neptune.json >> "$LOG" 2>&1 && cat /tmp/load-neptune.json; echo
fi
fi

if [ "$NO_WEB" = "1" ]; then echo "배포 완료(웹 제외): $URL"; exit 0; fi
echo "== 4) 프론트 빌드/업로드 =="
cd web
[ -d node_modules ] || npm install --no-fund --no-audit >> "$LOG" 2>&1
npm run build >> "$LOG" 2>&1 || { tail -30 "$LOG"; exit 1; }
cat > dist/config.json <<CFG
{"wssUrl": "$WSS", "cognitoClientId": "$CLIENT", "region": "$REGION", "graphBackend": "$GRAPH_BACKEND", "planeDeployed": $PLANE_DEPLOYED}
CFG
aws s3 sync dist "s3://$BUCKET" --delete >> "$LOG" 2>&1
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" > /dev/null
cd ..

echo "배포 완료: $URL"
echo "  wss: $WSS · plane: $PLANE_DEPLOYED · graph: $GRAPH_BACKEND"
echo "  점검: aws lambda invoke --function-name $ADMIN --region $REGION --cli-binary-format raw-in-base64-out --payload '{\"op\":\"health\"}' /tmp/h.json && cat /tmp/h.json"
