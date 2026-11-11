#!/usr/bin/env bash
# 一条命令把栈跑起来：读 .env → 缺证书就自签 → docker compose up -d --build → 健康检查
#
#   bash scripts/up.sh              # 日常启动/更新（证书已存在且有效时自动跳过）
#   bash scripts/up.sh --no-cert    # 不碰证书（证书由外部提供）
#   bash scripts/up.sh --no-health  # 不做健康检查
#
# 前置：同目录有 .env，且里面 ERP_HOST 已填（ERP_PUBLIC_HOST 用来签证书 SAN）。
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

流程：读 .env → 缺证书就自签（gen-cert.sh --ensure）→ docker compose up -d --build → 健康检查
环境变量: ERP_CERT_DIR / ERP_HTTPS_PORT / ERP_HTTP_PORT（也可写在 .env 里）
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
  log "缺少 .env：先 cp .env.example .env，并填 ERP_HOST / ERP_PUBLIC_HOST"
  exit 2
fi
if ! grep -qE '^[[:space:]]*ERP_HOST=.+' .env; then
  log ".env 里没有 ERP_HOST（compose 会用 \${ERP_HOST:?} 直接拒绝启动）"
  exit 2
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
docker compose up -d --build --remove-orphans

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

url="$scheme://localhost:$port/healthz"
curl_args=(-fsS -o /dev/null)
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
  log "下一步：在客户端机器上换 api-key → erp key --server $scheme://<ERP_PUBLIC_HOST> --user <工号>"
  exit 0
fi

log "FAIL 40 秒内 $url 未返回 200，看日志："
log "  docker compose logs --tail=30 nginx    # 证书/端口问题的报错都在这"
log "  docker compose logs --tail=80 app"
docker compose logs --tail=30 nginx 2>&1 | tail -30 >&2 || true
exit 1
