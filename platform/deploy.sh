#!/usr/bin/env bash
# 종합 배포 스크립트 — api-dist 조립 → CDK 배포 → 프론트 빌드/업로드/무효화
set -euo pipefail
cd "$(dirname "$0")"
LOG=/tmp/bank-platform-deploy.log

echo "== 1) api-dist 조립 =="
rm -rf api-dist && mkdir -p api-dist/seed/out
cp api/ws_handler.py api-dist/
cp -r engine graph api-dist/
find api-dist -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
# 데이터: 그래프 + 코퍼스 + 임베딩 캐시 (임베딩이 없으면 생성)
[ -f seed/out/corpus.embeddings.json ] || python3 -c "from engine.vectorrag import HybridIndex; HybridIndex.load()"
cp seed/out/nodes.jsonl seed/out/edges.jsonl seed/out/corpus.jsonl seed/out/corpus.embeddings.json api-dist/seed/out/
du -sh api-dist

echo "== 2) CDK 배포 =="
cd infra
[ -d node_modules ] || npm install --no-fund --no-audit > "$LOG" 2>&1
npx cdk deploy --require-approval never --outputs-file outputs.json >> "$LOG" 2>&1 || { tail -30 "$LOG"; exit 1; }
BUCKET=$(python3 -c "import json;o=json.load(open('outputs.json'))['BankPlatform'];print(o['WebBucketName'])")
DIST=$(python3 -c "import json;o=json.load(open('outputs.json'))['BankPlatform'];print(o['DistributionId'])")
WSS=$(python3 -c "import json;o=json.load(open('outputs.json'))['BankPlatform'];print(o['WssUrl'])")
URL=$(python3 -c "import json;o=json.load(open('outputs.json'))['BankPlatform'];print(o['WebUrl'])")
cd ..

echo "== 3) 프론트 빌드 =="
cd web
[ -d node_modules ] || npm install --no-fund --no-audit >> "$LOG" 2>&1
npm run build >> "$LOG" 2>&1 || { tail -30 "$LOG"; exit 1; }
cat > dist/config.json <<EOF
{"wssUrl": "$WSS", "cognitoClientId": "3o8u65rhccnr1ug1f94tctmb0b", "region": "ap-northeast-2"}
EOF
aws s3 sync dist "s3://$BUCKET" --delete >> "$LOG" 2>&1
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" > /dev/null
cd ..

echo "배포 완료: $URL (wss: $WSS)"
