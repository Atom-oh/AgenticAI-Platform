FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d

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
