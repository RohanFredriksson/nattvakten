FROM python:3.12-slim AS base

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8765

CMD ["uvicorn", "nattvakten.app:app", "--host", "0.0.0.0", "--port", "8765"]

FROM base AS development

RUN pip install --no-cache-dir ".[dev]"

FROM base AS runtime