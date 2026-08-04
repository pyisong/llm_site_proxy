# syntax=docker/dockerfile:1

ARG USE_CN_MIRROR=1
ARG BASE_IMAGE=python:3.11-slim
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

FROM ${BASE_IMAGE}

ARG USE_CN_MIRROR
ARG PIP_INDEX_URL

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      pip install --no-cache-dir -r requirements.txt -i "${PIP_INDEX_URL}"; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY . /app

EXPOSE 8000

CMD ["python3", "main.py"]
