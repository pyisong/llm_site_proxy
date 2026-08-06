FROM node:22-bookworm AS frontend
WORKDIR /app/frontend
ARG NPM_REGISTRY=https://registry.npmmirror.com
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --registry=${NPM_REGISTRY}
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist
ENV CONSOLE_STATIC_DIR=/app/frontend/dist
ENV CONSOLE_PORT=18020
EXPOSE 18020
WORKDIR /app/backend
CMD ["python", "main.py"]
