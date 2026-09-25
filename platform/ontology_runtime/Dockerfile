FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 AWS_REGION=ap-northeast-2
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock
COPY code/ /app/
COPY Dockerfile requirements.txt requirements.lock source-manifest.json /app/build-inputs/
RUN python -c "import hashlib,json,pathlib; root=pathlib.Path('/app'); manifest=json.loads((root/'build-inputs/source-manifest.json').read_text()); assert all(hashlib.sha256((root/entry['path'].replace('@build/','build-inputs/')).read_bytes()).hexdigest()==entry['sha256'] for entry in manifest)"
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent \
    && chown -R agent:agent /app
USER agent
EXPOSE 8080
CMD ["python", "-m", "ontology_runtime.app"]
