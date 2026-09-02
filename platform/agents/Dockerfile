# bank-platform 시나리오 에이전트 컨테이너 (Strands Agents on AgentCore Runtime) — linux/arm64
# 빌드 전 prepare_context.sh 가 ../agentcore/agent_specs.py 와 ../skills/*.md 를 _ctx/ 로 복사한다.
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=ap-northeast-2

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 빌드 컨텍스트(_ctx)에서 명세·스킬을 복사 — prepare_context.sh 가 만든다
COPY _ctx/agent_specs.py ./agent_specs.py
COPY _ctx/skills ./skills
COPY app.py mcp_gateway.py boundary_gate.py ./

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent \
    && chown -R agent:agent /app
USER agent

# OTEL: AgentCore Runtime 이 OTEL_* 환경변수를 주입한다. 이 이미지는 aws-opentelemetry-distro 를 설치하지 않으므로
# 자동 계측(opentelemetry-instrument)은 켜지지 않는다 — 필요하면 requirements 에 추가하고 CMD 앞에 붙인다.
EXPOSE 8080
CMD ["python", "app.py"]
