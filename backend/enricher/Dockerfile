FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
LABEL org.opencontainers.image.source=https://github.com/HelmuthJ22/aviones-cercanos
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY enricher.py .
RUN useradd -r app && chown app /app
USER app
CMD ["python", "enricher.py"]
