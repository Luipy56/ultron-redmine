FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY ultron ./ultron
# Plantilla por defecto para que la imagen construya sin config versionada en Git.
# En producción: monta tu config real en el host para que no se pierda al reiniciar el contenedor, p. ej.
#   docker run ... -v /ruta/config.yaml:/app/config.yaml:ro ...
COPY config.example.yaml ./config.yaml

RUN pip install --no-cache-dir .

CMD ["python", "-m", "ultron"]
