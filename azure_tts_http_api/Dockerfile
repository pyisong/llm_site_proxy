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

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    TTS_PROVIDER=site \
    TTS_BROWSER_HEADLESS=1

RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do \
        [ -f "$f" ] || continue; \
        sed -i "s|archive.ubuntu.com|${APT_MIRROR}|g" "$f"; \
        sed -i "s|security.ubuntu.com|${APT_MIRROR}|g" "$f"; \
      done; \
    fi

COPY requirements.txt /app/
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      pip install --no-cache-dir --break-system-packages -r requirements.txt -i "${PIP_INDEX_URL}"; \
    else \
      pip install --no-cache-dir --break-system-packages -r requirements.txt; \
    fi

COPY . /app

EXPOSE 8787

CMD ["python3", "tts_api.py", "--host", "0.0.0.0", "--port", "8787"]
