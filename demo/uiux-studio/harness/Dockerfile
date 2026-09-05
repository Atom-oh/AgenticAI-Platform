FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
COPY harness/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY harness/ harness/
COPY design_loop/ design_loop/
COPY design_seed/ design_seed/
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "harness.app"]
