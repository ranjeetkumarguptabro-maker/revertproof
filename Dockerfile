FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY sql/ ./sql/

RUN pip install --no-cache-dir -e ".[postgres,aws]"

EXPOSE 8000

CMD ["uvicorn", "revertproof.dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
