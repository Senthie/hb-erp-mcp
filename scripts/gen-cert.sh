#!/usr/bin/env bash
# 一键生成自签 TLS 证书（服务器上不用装 Python 依赖）
#
#   bash scripts/gen-cert.sh --host 192.168.1.10 --host erp-tools.lan
#   ERP_CERT_DIR=/srv/erp-tools/certs bash scripts/gen-cert.sh --host 192.168.1.10
#   bash scripts/gen-cert.sh                    # 不给 --host 时自动收集本机 IPv4 + localhost
#
# 三个后端按可用性自动挑（都不需要往项目里装东西）：
#   1) docker：复用 nginx:alpine 自带的 openssl（服务器部署 docker 时首选）
#   2) python3 + cryptography：交给 scripts/make-cert.py
#   3) 系统 openssl：最后兜底
#
# 输出：$ERP_CERT_DIR/erp-tools.crt + erp-tools.key（PEM，无口令，自签 CA:TRUE）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CN="${CERT_CN:-erp-tools}"
DAYS="${CERT_DAYS:-825}"
# 与 docker-compose.yml 的 ERP_CERT_DIR 保持同一个变量，避免两处打架
OUT_DIR="${ERP_CERT_DIR:-./certs}"
PYTHON_BIN="${PYTHON:-python3}"
NGINX_IMAGE="${CERT_NGINX_IMAGE:-nginx:1.27-alpine}"
HOSTS=()

log() { printf '[gen-cert] %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
用法: bash scripts/gen-cert.sh [--host IP或域名]... [-o 输出目录]

  --host, -H   写进 SAN 的地址，可重复；用 IP 访问就写 IP，用域名访问就写域名
  -o, --out    输出目录（默认 $ERP_CERT_DIR，再默认 ./certs）
  --cn         证书 CN（默认 erp-tools，也是文件名）
  --days       有效期天数（默认 825，Apple 客户端上限）
  -h, --help   显示本帮助

环境变量: ERP_CERT_DIR / CERT_CN / CERT_DAYS / PYTHON / CERT_NGINX_IMAGE
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host|-H) HOSTS+=("${2:?--host 需要一个参数}"); shift 2 ;;
    -o|--out)  OUT_DIR="${2:?--out 需要一个参数}"; shift 2 ;;
    --cn)      CN="${2:?--cn 需要一个参数}"; shift 2 ;;
    --days)    DAYS="${2:?--days 需要一个参数}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) log "未知参数：$1（--help 看用法）"; exit 2 ;;
  esac
done

if [ ${#HOSTS[@]} -eq 0 ]; then
  # 自动收集本机 IPv4（排除回环/链路本地），再补上 localhost
  while IFS= read -r ip; do
    [ -n "$ip" ] || continue
    case "$ip" in 127.*|169.254.*) continue ;; esac
    HOSTS+=("$ip")
  done < <(
    { ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1; } ||
    { hostname -I 2>/dev/null | tr ' ' '\n'; } || true
  )
  HOSTS+=("localhost" "127.0.0.1")
fi

# 去重并拼 SAN：纯数字点分的当 IP，其余当 DNS
SAN=""
SEEN=" "
for h in "${HOSTS[@]}"; do
  [ -n "$h" ] || continue
  case "$SEEN" in *" $h "*) continue ;; esac
  SEEN="$SEEN$h "
  if printf '%s' "$h" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
    SAN="${SAN:+$SAN,}IP:$h"
  else
    SAN="${SAN:+$SAN,}DNS:$h"
  fi
done
[ -n "$SAN" ] || { log "没有可用的 --host"; exit 2; }

mkdir -p "$OUT_DIR"
OUT_ABS="$(cd "$OUT_DIR" && pwd)"

log "输出目录：$OUT_ABS"
log "SAN：$SAN"

chmod_key() { chmod 600 "$OUT_ABS/$CN.key" 2>/dev/null || true; }

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  log "后端：docker（$NGINX_IMAGE 自带的 openssl）"
  docker run --rm --user "$(id -u):$(id -g)" -v "$OUT_ABS:/certs" "$NGINX_IMAGE" \
    openssl req -x509 -newkey rsa:2048 -nodes \
      -days "$DAYS" \
      -subj "/O=erp-tools/CN=$CN" \
      -addext "subjectAltName=$SAN" \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
      -addext "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign" \
      -addext "extendedKeyUsage=serverAuth" \
      -keyout "/certs/$CN.key" -out "/certs/$CN.crt"
  chmod_key
elif "$PYTHON_BIN" -c 'import cryptography' >/dev/null 2>&1; then
  log "后端：$PYTHON_BIN + cryptography（scripts/make-cert.py）"
  args=(--cn "$CN" --days "$DAYS" --out-dir "$OUT_ABS")
  for h in "${HOSTS[@]}"; do args+=(--host "$h"); done
  "$PYTHON_BIN" scripts/make-cert.py "${args[@]}"
  chmod_key
elif command -v openssl >/dev/null 2>&1; then
  log "后端：系统 openssl"
  openssl req -x509 -newkey rsa:2048 -nodes \
    -days "$DAYS" \
    -subj "/O=erp-tools/CN=$CN" \
    -addext "subjectAltName=$SAN" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign" \
    -addext "extendedKeyUsage=serverAuth" \
    -keyout "$OUT_ABS/$CN.key" -out "$OUT_ABS/$CN.crt"
  chmod_key
else
  log "找不到可用后端：需要 docker，或 python3+cryptography，或 openssl"
  log "  有 docker：docker compose pull nginx && bash scripts/gen-cert.sh --host <地址>"
  log "  有 python：python3 -m pip install --user cryptography && bash scripts/gen-cert.sh --host <地址>"
  exit 2
fi

log "完成：$OUT_ABS/$CN.crt  /  $OUT_ABS/$CN.key"
log "下一步：确认 .env 里 ERP_CERT_DIR=$OUT_ABS，然后 docker compose up -d"
log "客户端需要信任 $CN.crt（见 docs/mcp.md 5.5）"
