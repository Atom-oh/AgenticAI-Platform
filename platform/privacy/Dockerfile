FROM public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY __init__.py /app/privacy/__init__.py
COPY gateway /app/privacy/gateway
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "privacy.gateway.server"]
