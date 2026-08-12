ARG BASE_IMAGE=mcr.azure.cn/playwright/python:v1.60.0-noble

FROM node:22-bookworm AS frontend
WORKDIR /app/frontend
ARG NPM_REGISTRY=https://registry.npmmirror.com
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then \
      npm ci --registry=${NPM_REGISTRY}; \
    else \
      npm install --registry=${NPM_REGISTRY}; \
    fi
COPY frontend/ ./
RUN npm run build

# 与其它 proxy 一致：国内镜像；含 Chromium，供 Console 内嵌登录刷新
FROM ${BASE_IMAGE}
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist
ENV CONSOLE_STATIC_DIR=/app/frontend/dist
ENV CONSOLE_PORT=18020
ENV CONSOLE_SECRETS_ROOT=/secrets
EXPOSE 18020
WORKDIR /app/backend
CMD ["python", "main.py"]
