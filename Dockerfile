FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends systemd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "nattvakten.app:app", "--host", "0.0.0.0", "--port", "8000"]