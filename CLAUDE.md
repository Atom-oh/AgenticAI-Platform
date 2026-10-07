# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

Use [AGENTS.md](AGENTS.md) as the shared repository guidance and
[docs/REVIEW_CONTEXT.md](docs/REVIEW_CONTEXT.md) to resolve document scope.
Do not maintain a second copy of architecture or review policy here.

## Commands

Docs (repo root):
```bash
npm ci
NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build
npm run docs:dev
git diff --check
```

Platform Python (run from `platform/`):
```bash
pip install pytest pyyaml pg8000 httpx -r workspace/requirements.txt
python -m playwright install chromium
python -m workspace.fetch_axe gates/package-lock.json /tmp/studio-axe   # AXE_PATH=/tmp/studio-axe/axe.min.js
(cd react-kit && npm ci --ignore-scripts)
(cd source-analyzer && npm ci --ignore-scripts)
python3 seed/generate.py && python3 seed/corpus.py
bash agents/prepare_context.sh

python3 -m pytest tests/ -q                              # all tests
python3 -m pytest tests/test_ontology_store.py -q -k <name>  # single test
```

Node suites (install Chromium with `npx playwright install --with-deps chromium`
from `platform/web` and `platform/react-kit` before their browser tests):
```bash
(cd platform/gates && npm ci && npm test)
(cd platform/react-kit && npm ci --ignore-scripts && node --test test/*.test.cjs)
(cd platform/web && npm ci && npx tsc --noEmit && npm run build && node --test test/*.test.cjs)
(cd platform/source-analyzer && npm ci --ignore-scripts && npm test)
```

Offline CDK synth (from `platform/infra`; CI runs additional security checks):
```bash
npm ci
mkdir -p ../api-dist && touch ../api-dist/.keep
node -e "require('fs').writeFileSync('cdk.context.json', JSON.stringify({'availability-zones:account=000000000000:region=ap-northeast-2':['ap-northeast-2a','ap-northeast-2b']}))"
CDK_DEFAULT_ACCOUNT=000000000000 npx cdk synth BankPlatform --quiet -c planeDeployed=false
python3 ../workspace/check_infra.py cdk.out/BankPlatform.template.json
```

CI also greps `.py`/`.ts`/`.tsx`/`.md` files for the shared demo password and `AKIA…`-style keys and
fails the build on a match — never write the demo credential into any file in this repo, CLAUDE.md
included.

`platform/cli.py admin …` and `platform/deploy.sh` act on live resources. Follow the
single-deployment-owner coordination rule in [AGENTS.md](AGENTS.md) before running either.
