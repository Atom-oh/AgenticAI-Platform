FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
# Apply the distribution's signed security updates before freezing the release
# by ECR digest. The base's August build predates the September fixes.
RUN apt-get update && apt-get upgrade -y && apt-get clean
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY __init__.py /app/privacy/__init__.py
COPY gateway /app/privacy/gateway
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "privacy.gateway.server"]
