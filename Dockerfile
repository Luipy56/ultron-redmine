FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY ultron ./ultron
# Default template so the image builds without a tracked config in Git.
# In production, mount your real config from the host so it survives container restarts, e.g.
#   docker run ... -v /path/on/host/config.yaml:/app/config.yaml:ro ...
COPY config.example.yaml ./config.yaml

RUN pip install --no-cache-dir .

CMD ["python", "-m", "ultron"]
