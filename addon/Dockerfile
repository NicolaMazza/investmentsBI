FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ .

RUN pip install --no-cache-dir -e .

# Playwright + headless Chromium — used by the Vanguard holdings fetcher.
# Chromium binaries add ~290 MB; --with-deps installs required system libs.
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

RUN mkdir -p /data/cache

COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8000

CMD ["/run.sh"]
