# syntax=docker/dockerfile:1

ARG USE_CN_MIRROR=1
ARG BASE_IMAGE=mcr.azure.cn/playwright/python:v1.60.0-noble
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG APT_MIRROR=mirrors.aliyun.com

FROM ${BASE_IMAGE}

ARG USE_CN_MIRROR
ARG PIP_INDEX_URL
ARG APT_MIRROR

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do \
        [ -f "$f" ] || continue; \
        sed -i "s|archive.ubuntu.com|${APT_MIRROR}|g" "$f"; \
        sed -i "s|security.ubuntu.com|${APT_MIRROR}|g" "$f"; \
      done; \
    fi

COPY pyproject.toml /app/
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      pip install --no-cache-dir --break-system-packages --ignore-installed typing_extensions -e . -i "${PIP_INDEX_URL}"; \
    else \
      pip install --no-cache-dir --break-system-packages --ignore-installed typing_extensions -e .; \
    fi

COPY . /app

ENV METASO_PROXY_API_KEY=local-secret \
    METASO_STORAGE_STATE_FILE=/run/secrets/metaso_storage.json \
    METASO_LOG_LEVEL=INFO \
    METASO_LOG_MAX_CHARS=400 \
    METASO_NEW_CHAT_PER_REQUEST=0 \
    METASO_MIN_REQUEST_INTERVAL_SEC=8

EXPOSE 8000

CMD ["python3", "main.py"]
