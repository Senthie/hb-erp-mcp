#!/usr/bin/env bash
# 一条命令把栈跑起来：读 .env → 生成 config.yaml → 缺证书就自签
#                       → docker compose up -d --build → 健康检查
#
#   bash scripts/up.sh              # 日常启动/更新（证书已存在且有效时自动跳过）
#   bash scripts/up.sh --no-cert    # 不碰证书（证书由外部提供）
#   bash scripts/up.sh --no-health  # 不做健康检查
#
# 前置：同目录有 .env（cp docker/env.example .env，只有 3 个值）。
# 应用配置 config.yaml 由本脚本按 .env 生成；已存在则沿用，不再覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_CERT=0
SKIP_HEALTH=0

log() { printf '[up] %s\n' "$*" >&2; }

# 从 .env 取一个非敏感键（真实环境变量优先）
dotenv_get() {
  local key="$1" fallback="${2:-}" v
  v="$(grep -E "^[[:space:]]*$key=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  v="${v%$'\r'}"
  v="${v#\"}"; v="${v%\"}"
  printf '%s' "${v:-$fallback}"
}

usage() {
  cat <<'USAGE'
用法: bash scripts/up.sh [选项]

  --no-cert      不生成/检查证书
  --no-health    跳过健康检查
  -h, --help     显示本帮助

流程：读 .env → 生成 config.yaml（应用配置）→ 缺证书就自签（gen-cert.sh --ensure）
      → docker compose up -d --build → 健康检查
环境变量: ERP_CERT_DIR / ERP_HTTPS_PORT / ERP_HTTP_PORT / ERP_ADMIN_USER（也可写在 .env 里）
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-cert)   SKIP_CERT=1; shift ;;
    --no-health) SKIP_HEALTH=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) log "未知参数：$1（--help 看用法）"; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { log "没装 docker"; exit 2; }
docker info >/dev/null 2>&1 || { log "docker 守护进程没起来（或当前用户没权限）"; exit 2; }

if [ ! -f .env ]; then
  log "缺少 .env：先 cp docker/env.example .env（只有 3 个值）"
  exit 2
fi
if ! grep -qE '^[[:space:]]*ERP_HOST=.+' .env; then
  log ".env 里没有 ERP_HOST（应用连哪个 ERP 主站）"
  exit 2
fi

# ── 0. 应用配置：config.yaml 由 .env 生成（不用手写一堆变量）──────
# 已有 config.yaml 但不是 mcp 形态（没有 server 段，比如误从 config.example.yaml 复制）
# 就备份后重新生成，否则容器起来会因为「没有存储」直接退出。
if [ -f config.yaml ] && ! grep -qE '^[[:space:]]*server:' config.yaml; then
  log "现有 config.yaml 没有 server 段（不是 mcp 配置），备份为 config.yaml.bak 后重新生成"
  mv -f config.yaml config.yaml.bak
fi

if [ ! -f config.yaml ]; then
  pg_user="$(dotenv_get POSTGRES_USER erp)"
  pg_pass="$(dotenv_get POSTGRES_PASSWORD '')"
  pg_db="$(dotenv_get POSTGRES_DB erp)"
  erp_host="$(dotenv_get ERP_HOST '')"
  public_host="$(dotenv_get ERP_PUBLIC_HOST '')"
  admin_user="$(dotenv_get ERP_ADMIN_USER '')"
  if [ -z "$pg_pass" ]; then
    log ".env 里没有 POSTGRES_PASSWORD（compose 也会因此拒绝启动）"
    exit 2
  fi
  if [ -n "$admin_user" ]; then
    admin_line="  admin_users: [$admin_user]"
  else
    admin_line="  admin_users: []"
  fi
  cat > config.yaml <<YAML
# 由 scripts/up.sh 按 .env 生成（mode: mcp）。要改并发/管理员等直接编辑本文件。
mode: mcp
erp:
  host: ${erp_host}
server:
  bind: 0.0.0.0:8765
  storage: postgres
  pg_dsn: "postgresql://${pg_user}:${pg_pass}@db:5432/${pg_db}"
${admin_line}
  # TLS 由 nginx 终止：本进程说 HTTP，且信任 X-Forwarded-For
  tls: off
  allow_plaintext: true
  trust_proxy: true
  public_host: "${public_host}"
  allow_hosts: auto
  register_secret: auto
  # ERP 密码 AES-GCM 加密存库 → cookie 过期时后台自动重登（用户无感）
  store_erp_password: true
YAML
  # 容器以 uid 10001（非 root）运行，文件必须对其它用户可读，否则
  # `erp serve` 会报「配置文件解析失败：Permission denied」后反复重启。
  chmod 644 config.yaml 2>/dev/null || true
  log "已生成 config.yaml（PostgreSQL + nginx 终止 TLS；管理员：${admin_user:-未设置}）"
else
  chmod 644 config.yaml 2>/dev/null || true   # 容器 uid 10001 要能读到
  log "沿用已有 config.yaml（要重新生成先删掉它）"
fi

# ── 1. 证书（幂等：有效就跳过）─────────────────────────────
if [ "$SKIP_CERT" = 0 ]; then
  bash scripts/gen-cert.sh --ensure
else
  log "按要求跳过证书生成"
fi

# ── 1.5 证书预检：文件必须真的在 compose 会挂进去的目录里 ──
# 否则 nginx 会无限重启：cannot load certificate "/etc/nginx/certs/erp-tools.crt"
cert_dir="${ERP_CERT_DIR:-$(dotenv_get ERP_CERT_DIR ./certs)}"
case "$cert_dir" in
  /*) ;;
  *) cert_dir="$ROOT/${cert_dir#./}" ;;
esac
cacert=""
nginx_conf="$(dotenv_get NGINX_CONF docker/nginx.conf)"
case "$nginx_conf" in
  *nginx-http.conf)
    log "纯 HTTP 版（$nginx_conf），不需要证书" ;;
  *)
    missing=""
    [ -s "$cert_dir/erp-tools.crt" ] || missing="$missing $cert_dir/erp-tools.crt"
    [ -s "$cert_dir/erp-tools.key" ] || missing="$missing $cert_dir/erp-tools.key"
    if [ -n "$missing" ]; then
      log "证书不在 compose 挂载的目录里，nginx 会无限重启。缺："
      for f in $missing; do log "  - $f"; done
      log "排查："
      log "  1) .env 里的 ERP_CERT_DIR=$cert_dir —— 这就是容器要挂的宿主机目录"
      log "  2) 若证书其实在 ${ROOT}/certs，要么 bash scripts/gen-cert.sh -o \"$cert_dir\"，"
      log "     要么把 .env 的 ERP_CERT_DIR 改成 ./certs（然后 docker compose up -d）"
      log "  3) 目录权限：Docker 自动建的目录属主是 root，非 root 用户写不进去 → sudo chown -R \"\$USER\" \"$cert_dir\""
      exit 3
    fi
    if ! [ -r "$cert_dir/erp-tools.key" ]; then
      log "私钥不可读：$cert_dir/erp-tools.key（检查权限，通常 chmod 600 + 属主是自己）"
      exit 3
    fi
    cacert="$cert_dir/erp-tools.crt"
    log "证书就绪：$cert_dir" ;;
esac

# ── 2. 构建并启动 ─────────────────────────────────────────
log "docker compose up -d --build --remove-orphans"
if ! docker compose up -d --build --remove-orphans; then
  log "启动失败，最近日志："
  docker compose ps >&2 || true
  docker compose logs --tail=50 app 2>&1 | tail -50 >&2 || true
  docker compose logs --tail=20 nginx 2>&1 | tail -20 >&2 || true
  log "排查提示："
  log "  · 应用起不来最常见的原因是 config.yaml 读不到/内容不对："
  log "      docker compose exec app cat /app/api/config.yaml     # 看不到就是权限问题"
  log "      chmod 644 config.yaml                                # 容器以 uid 10001 运行"
  log "  · PostgreSQL 连不上：POSTGRES_PASSWORD 含 @ : / # 等特殊字符会让 DSN 失效"
  exit 1
fi

# ── 3. 健康检查 ───────────────────────────────────────────
if [ "$SKIP_HEALTH" = 1 ]; then
  docker compose ps
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  log "没有 curl，跳过健康检查（用 docker compose ps 自己看一眼）"
  docker compose ps
  exit 0
fi

# 发布的端口从 compose 读，别写死
mapped="$(docker compose port nginx 443 2>/dev/null | head -1 || true)"
scheme="https"
if [ -z "$mapped" ]; then
  mapped="$(docker compose port nginx 80 2>/dev/null | head -1 || true)"
  scheme="http"
fi
port="${mapped##*:}"
[ -n "$port" ] || { log "拿不到 nginx 的发布端口，跳过健康检查"; docker compose ps; exit 0; }

# 用「客户端实际访问的域名」验，而不是 localhost：
# 自签证书的 SAN 里是 ERP_PUBLIC_HOST，用 localhost 会证书校验失败（假警报）。
public_host_first="$(dotenv_get ERP_PUBLIC_HOST '' | cut -d, -f1 \
  | sed -e 's#^[[:space:]]*##' -e 's#^https\?://##' -e 's/:.*$//')"
url="$scheme://localhost:$port/healthz"
curl_args=(-fsS -o /dev/null -m 8)
if [ "$scheme" = https ] && [ -n "$public_host_first" ]; then
  url="$scheme://$public_host_first:$port/healthz"
  curl_args+=(--resolve "$public_host_first:$port:127.0.0.1")
fi
[ "$scheme" = https ] && [ -f "$cacert" ] && curl_args+=(--cacert "$cacert")

log "健康检查：$url"
ok=0
for _ in $(seq 1 20); do
  if curl "${curl_args[@]}" "$url"; then ok=1; break; fi
  sleep 2
done

docker compose ps

if [ "$ok" = 1 ]; then
  log "OK 栈已就绪（$url 返回 200）"
  log "下一步：在客户端机器上换 api-key → erp key --server $scheme://<ERP_PUBLIC_HOST> --write-mcp-json ./mcp.json"
  exit 0
fi

log "FAIL 40 秒内 $url 未返回 200，看日志："
log "  docker compose logs --tail=30 nginx    # 证书/端口问题的报错都在这"
log "  docker compose logs --tail=80 app"
docker compose logs --tail=30 nginx 2>&1 | tail -30 >&2 || true
exit 1
