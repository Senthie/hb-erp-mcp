# syntax=docker/dockerfile:1
#
# erp-tools 网关镜像（CLI + MCP Streamable HTTP）
#
#   docker build -t erp-tools:0.3.0 .
#   docker run --rm -p 8080:8765 -v erpdata:/data erp-tools:0.3.0
#
# 注意：镜像里**不含任何账号密码/证书**。
#   * ERP 地址走环境变量（ERP_HOST 等）；
#   * ERP 账号密码**永不进镜像/环境** —— 每个使用者自己调 POST /v1/auth/login
#     （或 `erp key --server ...`）用**自己的**工号密码换**自己的** api-key，
#     服务端只存 key 的 sha256；
#   * 证书与 api-key 库通过 volume / 反向代理挂进来（见 docker-compose.yml）。

# ───────────────────────── 构建阶段 ─────────────────────────
FROM python:3.11-slim AS builder

# uv 官方镜像里的静态二进制：用 --frozen 复现 uv.lock，比 pip 快很多
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 先只装依赖，让这一层能被缓存（改源码不会重装依赖）
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra server

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra server

# ───────────────────────── 运行阶段 ─────────────────────────
FROM python:3.11-slim AS runtime

# libgomp1    : onnxruntime（ddddocr 识别验证码）依赖的 OpenMP 运行时
# libglib2.0  : opencv-python-headless 在 Linux 上的动态依赖
#               Debian 12 叫 libglib2.0-0，13(trixie) 改成 libglib2.0-0t64，两个都试一次
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && (apt-get install -y --no-install-recommends libglib2.0-0 \
     || apt-get install -y --no-install-recommends libglib2.0-0t64) \
 && rm -rf /var/lib/apt/lists/*

# 非 root 运行
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin erp \
 && mkdir -p /data \
 && chown -R erp:erp /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY pyproject.toml README.md ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    ERP_DATA_DIR=/data \
    COOKIE_DIR=/data/cookies \
    ERPSRV_DB=/data/auth.db \
    ERPSRV_BIND=0.0.0.0:8765 \
    ERPSRV_CONCURRENCY=6

USER erp
VOLUME ["/data"]
EXPOSE 8765

# 健康检查用 python 自带 urllib，省得往镜像里塞 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).status==200 else 1)"]

ENTRYPOINT ["erp"]
CMD ["serve"]
