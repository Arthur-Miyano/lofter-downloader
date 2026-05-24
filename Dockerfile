FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOFTER_LOG_LEVEL=INFO

COPY pyproject.toml .
RUN pip install --no-cache-dir -e "."

COPY src/lofter_downloader/ src/lofter_downloader/

EXPOSE 8080

CMD ["python", "-m", "lofter_downloader"]
