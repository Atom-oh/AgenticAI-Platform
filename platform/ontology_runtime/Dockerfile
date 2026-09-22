FROM public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 AWS_REGION=ap-northeast-2
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY code/ /app/
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent \
    && chown -R agent:agent /app
USER agent
EXPOSE 8080
CMD ["python", "-m", "ontology_runtime.app"]
