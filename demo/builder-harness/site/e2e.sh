#!/usr/bin/env bash
# NEXUS platform e2e — 10축 핵심 경로 검증. 사용: ./e2e.sh
set -u
CF="https://d1twhttjtzqewp.cloudfront.net"
GURL="https://nexus-platform-tools-dgt6g0wppb.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp"
CID="3o8u65rhccnr1ug1f94tctmb0b"; R=ap-northeast-2
pass=0; fail=0
ck(){ if [ "$1" = "$2" ]; then echo "  ✅ $3"; pass=$((pass+1)); else echo "  ❌ $3 (got $1, want $2)"; fail=$((fail+1)); fi; }

echo "[1] 진입점·인증 경계"
ck "$(curl -s -o /dev/null -w %{http_code} $CF/)" 200 "SPA 서빙"
ck "$(curl -s -o /dev/null -w %{http_code} $CF/api/agents)" 401 "무토큰 API 차단"
ck "$(curl -s -o /dev/null -w %{http_code} https://00l4tzkyqi.execute-api.$R.amazonaws.com/)" 403 "API GW 직접 접근 차단(시크릿 헤더)"

TOK=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id $CID \
 --auth-parameters USERNAME=admin@demo.nexus,PASSWORD=Nexus-Admin-2026 --region $R \
 --query AuthenticationResult.IdToken --output text)
MTOK=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id $CID \
 --auth-parameters USERNAME=alpha@demo.nexus,PASSWORD=Nexus-Member-2026 --region $R \
 --query AuthenticationResult.IdToken --output text)
GTOK=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id $CID \
 --auth-parameters USERNAME=admin@demo.nexus,PASSWORD=Nexus-Admin-2026 --region $R \
 --query AuthenticationResult.AccessToken --output text)

echo "[2] RBAC"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOK" $CF/api/admin/overview)" 200 "관리자 운영 API"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $MTOK" $CF/api/admin/overview)" 403 "팀원 운영 API 차단"

echo "[3] 카탈로그·그래프·지식"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOK" $CF/api/graph)" 200 "커버리지 그래프"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOK" $CF/api/wiki/security-governance)" 200 "AI Wiki 거버넌스 문서"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOK" $CF/api/ontology/entities)" 200 "온톨로지"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOK" $CF/api/skills)" 200 "스킬 카탈로그"

echo "[4] 중앙 MCP (Gateway + Identity)"
ck "$(curl -s -o /dev/null -w %{http_code} -X POST $GURL -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')" 401 "무토큰 MCP 차단"
NT=$(curl -s -X POST $GURL -H "Authorization: Bearer $GTOK" -H 'Content-Type: application/json' \
 -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('result',{}).get('tools',[])))")
ck "$NT" 3 "MCP tools/list = 3개 플랫폼 툴"

echo "[5] 실행 (approved agent chat)"
AG=$(curl -s -H "Authorization: Bearer $TOK" $CF/api/agents | python3 -c "import json,sys; print([a['id'] for a in json.load(sys.stdin)['agents'] if a['status']=='APPROVED'][0])")
RL=$(curl -s --max-time 110 -X POST $CF/api/chat -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
 -d '{"agentId":"'"$AG"'","message":"한 문장으로 자기소개해줘","sessionId":"'"$(uuidgen|tr -d -)"'e2"}' \
 | python3 -c "import json,sys; d=json.load(sys.stdin); print('ok' if len(d.get('reply',''))>5 else 'empty')")
ck "$RL" ok "에이전트 응답"

echo "[6] 도메인·크롤러"
DSN=$(curl -s -H "Authorization: Bearer $TOK" $CF/api/datasources | python3 -c "import json,sys; d=json.load(sys.stdin)['datasources']; print(len(d), len([x for x in d if x.get('source')=='crawler']))")
ck "$(echo $DSN | cut -d' ' -f1 | awk '{print ($1>=6)?"y":"n"}')" y "데이터소스 6종 이상"
ck "$(echo $DSN | cut -d' ' -f2)" 1 "자동 수집(크롤러) 데이터소스 1건"
ck "$(curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOK" $CF/api/wiki/news-crawler-design)" 200 "크롤러 설계 위키"
ck "$(curl -s -o /dev/null -w %{http_code} -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' $CF/api/admin/crawl)" 200 "수동 크롤 트리거"

echo; echo "결과: $pass passed / $fail failed"
[ $fail -eq 0 ]
