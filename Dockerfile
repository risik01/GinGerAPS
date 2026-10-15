FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DIARIO_DATA=/data

WORKDIR /app

# Librerie native per WeasyPrint (genera i PDF dei report): i font sono quelli
# dell'app stessa (@font-face sui .woff2 in static/fonts), nessun font di
# sistema da installare.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 \
        libgdk-pixbuf-2.0-0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app/ /app/

EXPOSE 8099
CMD ["python3", "-m", "camaps_diary.server"]
