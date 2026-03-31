FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY ultron ./ultron
COPY config.yaml ./

RUN pip install --no-cache-dir .

CMD ["python", "-m", "ultron"]
