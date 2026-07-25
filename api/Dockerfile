FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY start-prod.sh ./
RUN pip install --no-cache-dir . && chmod +x start-prod.sh
EXPOSE 8000
# Local/compose default: API only. Production (Render) overrides this with
# ./start-prod.sh to run the API + worker + beat in one container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
