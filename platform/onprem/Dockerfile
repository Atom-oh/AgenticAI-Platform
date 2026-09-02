FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 ONPREM_DATA_DIR=/app/data
# pg8000: RDS 원장 · boto3(botocore SigV4): OpenSearch Serverless 서명 — VPC 엔드포인트 경로만 (opensearch-py 없음)
RUN pip install --no-cache-dir pg8000==1.31.2 boto3==1.42.97
COPY calc_engine.py masking.py personal_store.py vector_index.py aoss_index.py service.py ./
# 벡터 인덱스 데이터 — deploy.sh 가 seed/out/corpus.jsonl · corpus.embeddings.json 을 onprem/data/ 로 복사한 뒤 빌드한다
COPY data/ ./data/
EXPOSE 8080
CMD ["python", "service.py"]
