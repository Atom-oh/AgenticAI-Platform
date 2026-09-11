FROM node:22.23.1-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS node-runtime
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-kor fonts-noto-cjk \
    g++ make cmake libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY workspace/requirements.txt /tmp/workspace-requirements.txt
RUN pip install --no-cache-dir -r /tmp/workspace-requirements.txt
COPY gates/package-lock.json /tmp/gates-package-lock.json
COPY workspace/fetch_axe.py /tmp/fetch_axe.py
RUN python /tmp/fetch_axe.py /tmp/gates-package-lock.json /opt/studio-axe

WORKDIR /var/task
COPY react-kit/package.json react-kit/package-lock.json ./react-kit/
RUN cd react-kit && npm ci --ignore-scripts --no-audit --no-fund
COPY react-kit ./react-kit
COPY workspace ./workspace
COPY engine ./engine
COPY studio ./studio
COPY api/common ./common
COPY graph ./graph
COPY registry ./registry
COPY skills ./skills
COPY seed/out/nodes.jsonl seed/out/edges.jsonl ./seed/out/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright AXE_PATH=/opt/studio-axe/axe.min.js
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["workspace.worker.handler"]
