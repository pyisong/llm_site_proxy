# OpenAI 兼容网关 + cursor agent（Linux 容器）
# 构建需能访问外网以安装 Cursor CLI；也可 INSTALL_CURSOR=0 跳过，运行时挂载宿主 cursor 二进制。
FROM python:3.12-slim-bookworm

ARG INSTALL_CURSOR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$INSTALL_CURSOR" = "1" ]; then \
        curl https://cursor.com/install -fsS | bash; \
    else \
        echo "Skipping Cursor CLI install (INSTALL_CURSOR=0)"; \
    fi

# 官方 install 常见路径（若 which cursor 为空，请在运行时挂载二进制或查阅 Cursor 文档）
ENV PATH="/root/.local/bin:/root/.cursor/bin:${PATH}"
RUN command -v cursor && cursor --version || echo "WARNING: cursor 不在 PATH，请 INSTALL_CURSOR=1 重新构建或挂载 cursor"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY cursor_automation.py openai_bridge.py image_generation.py log_utils.py ./

EXPOSE 8765

ENV CURSOR_BRIDGE_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# 工作区默认挂载到 /workspace；无卷时目录为空，可在 compose 中挂载代码
RUN mkdir -p /workspace

CMD ["python", "cursor_automation.py", "serve", "--host", "0.0.0.0", "--port", "8765", "-w", "/workspace"]
