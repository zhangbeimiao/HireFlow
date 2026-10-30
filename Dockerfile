FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir .
RUN addgroup --system hireflow && adduser --system --ingroup hireflow --no-create-home hireflow

USER hireflow
CMD ["uvicorn", "hireflow.api:app", "--host", "0.0.0.0", "--port", "8000"]
