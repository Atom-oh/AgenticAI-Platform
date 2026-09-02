FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pg8000==1.31.2
COPY calc_engine.py masking.py personal_store.py service.py ./
EXPOSE 8080
CMD ["python", "service.py"]
