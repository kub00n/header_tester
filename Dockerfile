FROM openresty/openresty:1.31.1.1-5-bookworm

RUN apt-get update \
    && apt-get install --no-install-recommends --yes python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app.py entrypoint.sh ./
COPY openresty/ ./openresty/

ENV HEADER_SERVER=python

EXPOSE 8080

STOPSIGNAL SIGTERM

ENTRYPOINT ["/app/entrypoint.sh"]
