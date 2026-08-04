# syntax=docker/dockerfile:1

# 国内加速（build-arg 可关闭：--build-arg USE_CN_MIRROR=0）
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

ENV KIMI_BACKEND=browser \
    KIMI_PROXY_API_KEY=local-secret \
    KIMI_BROWSER_PROFILE=/data/profile \
    KIMI_BROWSER_HEADLESS=1 \
    KIMI_LOG_LEVEL=INFO \
    KIMI_LOG_MAX_CHARS=500

EXPOSE 8000

CMD ["python3", "main.py"]
