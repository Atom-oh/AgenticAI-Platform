FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
COPY harness/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY harness/ harness/
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "harness.app"]
