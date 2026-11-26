#!/usr/bin/env bash
# erp-tools 备份 / 校验 / 恢复 —— 一份脚本搞定
#
#   bash scripts/backup.sh                        # 备份（等价于 backup 子命令）
#   bash scripts/backup.sh backup --keep 14       # 备份并只留最近 14 份
#   BACKUP_DIR=/mnt/nas/erp bash scripts/backup.sh
#   bash scripts/backup.sh list                   # 列出已有备份
#   bash scripts/backup.sh verify latest          # 只校验，不动任何数据
#   bash scripts/backup.sh restore latest         # 恢复（数据库 + 数据卷成对）
#
# restore 选项：
#   --yes            跳过交互确认（脚本 / cron 用）
#   --with-config    连 config.yaml / .env / certs 一起覆盖
#   --no-safety      恢复前不做自动安全备份（默认会先备份一次）
#   --skip-db        只恢复数据卷（会警告：密钥/库不配套）
#   --skip-data      只恢复数据库（会警告：密钥/库不配套）
#
# 为什么必须成对：
#   * PostgreSQL 里存的是 api-key 的 sha256、MCP 服务注册信息，以及
#     **ERP 密码的 AES-GCM 密文**（users.erp_secret 等）；
#   * 解密用的 `erp_credentials.key` 存在数据卷 <erpdata>/erp_credentials.key（**不在库里**），
#     同一卷里还有 proxy.secret（附件代理签名）、register.secret（服务注册密钥）
#     和 cookies/<工号>.json（登录会话缓存）。
#   只恢复库 → 密文解不开、附件链接全失效（不影响安全，只是全员要重新绑定 ERP）；
#   只恢复卷 → 库里没有对应的 key 记录，同样对不上。
#
# 备份内容（同一个时间戳目录下）：
#   erp.sql.gz            pg_dump（逻辑备份，可在任意同版本 pg 上恢复）
#   erpdata.tgz           数据卷 tar（解密密钥 + cookie + 注册密钥）
#   config.yaml .env      应用/部署配置（含 pg 密码，目录 700）
#   certs/                nginx TLS 证书（客户端信任过的那份）
#   MANIFEST.txt          镜像版本、卷名、行数、sha256（restore 前会拿来校验）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PG_VOLUME="${ERP_PG_VOLUME:-erp-tools-pgdata}"     # 仅记录进 MANIFEST，实际备份走 pg_dump
DATA_VOLUME="${ERP_DATA_VOLUME:-erp-tools-erpdata}"
BACKUP_ROOT="${BACKUP_DIR:-./backup}"
TAR_IMAGE="${BACKUP_TAR_IMAGE:-alpine:3.20}"
ERP_UID="${ERP_UID:-10001}"                        # 容器里 erp 用户的 uid（见 Dockerfile）

log()  { printf '[erp-bk] %s\n' "$*" >&2; }
warn() { printf '[erp-bk] WARN %s\n' "$*" >&2; }
die()  { printf '[erp-bk] 错误：%s\n' "$*" >&2; exit 2; }

# ══════════════════ 公共 ══════════════════
need_docker() {
  command -v docker >/dev/null 2>&1 || die "没装 docker"
  docker info >/dev/null 2>&1 || die "docker 守护进程没起来（或当前用户没权限）"
}

service_running() { docker compose ps --status running --services 2>/dev/null | grep -qx "$1"; }

# 在 db 容器里执行 psql（用容器自己的 POSTGRES_USER/POSTGRES_DB，避免和 config.yaml 对不上）
psql_db() {  # psql_db "<sql>" [-tA]
  local sql="$1" flags="${2:-}"
  docker compose exec -T db sh -c \
    "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" $flags -c \"\$0\"" "$sql"
}

wait_healthy() {  # wait_healthy <service> [最多等秒]
  local svc="$1" limit="${2:-90}" cid st i=0
  cid="$(docker compose ps -q "$svc" 2>/dev/null | head -1)"
  [ -n "$cid" ] || return 1
  while [ "$i" -lt "$limit" ]; do
    st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
    case "$st" in
      healthy) return 0 ;;
      running) [ "$svc" != db ] && return 0 ;;   # 没有 healthcheck 的服务，running 即算起来
      exited|dead) return 1 ;;
    esac
    i=$((i + 2)); sleep 2
  done
  return 1
}

latest_backup() {
  ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | head -1 || true
}

resolve_dir() {  # "" / latest / 路径
  local arg="${1:-latest}" dir
  case "$arg" in
    ""|latest|last) dir="$(latest_backup)" ;;
    *) dir="$arg" ;;
  esac
  [ -n "$dir" ] || die "找不到备份：$BACKUP_ROOT 下没有任何时间戳目录"
  dir="${dir%/}"
  [ -d "$dir" ] || die "备份目录不存在：$dir"
  ( cd "$dir" && pwd )
}

# ══════════════════ 校验 ══════════════════
verify_archive() {  # verify_archive <dir>  —— 返回非 0 表示不可用
  local dir="$1" bad=0 name manifest_key
  [ -s "$dir/erp.sql.gz" ]  || { warn "缺 erp.sql.gz";  bad=1; }
  [ -s "$dir/erpdata.tgz" ] || { warn "缺 erpdata.tgz"; bad=1; }
  [ "$bad" = 0 ] || return 1

  gzip -t "$dir/erp.sql.gz" 2>/dev/null || { warn "erp.sql.gz 已损坏（gzip -t 失败）"; bad=1; }
  tar tzf "$dir/erpdata.tgz" >/dev/null 2>&1 || { warn "erpdata.tgz 已损坏（tar -t 失败）"; bad=1; }
  [ "$bad" = 0 ] || return 1

  if [ -f "$dir/MANIFEST.txt" ] && grep -q '^sha256' "$dir/MANIFEST.txt"; then
    # 注意：MANIFEST 里的行有缩进，sha256sum -c 不接受行首空格，先规整成「hash  file」
    ( cd "$dir" && awk '/^ +[0-9a-f]{64}[[:space:]]+[^[:space:]]+$/{print $1"  "$2}' MANIFEST.txt \
        | sha256sum -c --quiet - ) || { warn "sha256 与 MANIFEST 不一致（可能被改动或拷贝不完整）"; bad=1; }
  else
    warn "没有 MANIFEST.txt 的 sha256 记录，跳过完整性比对"
  fi

  local tables keys
  tables="$(gunzip -c "$dir/erp.sql.gz" | grep -c '^CREATE TABLE' || true)"
  keys="$(tar tzf "$dir/erpdata.tgz" | grep -c 'erp_credentials.key' || true)"
  log "校验：$dir"
  log "  表数量          : ${tables}"
  log "  erp_credentials : $([ "${keys:-0}" -gt 0 ] && echo 在包内 || echo 缺失)"
  log "  users/api_keys  : $(grep -E '^(users|api_keys) ' "$dir/MANIFEST.txt" 2>/dev/null | tr '\n' ' ' || echo '（MANIFEST 无记录）')"
  [ "${keys:-0}" -gt 0 ] || { warn "数据卷包里没有 erp_credentials.key —— 恢复后无法自动重登 ERP"; bad=1; }
  return "$bad"
}

# ══════════════════ 备份 ══════════════════
cmd_backup() {
  local KEEP=0 TS OUT OUT_ABS
  while [ $# -gt 0 ]; do
    case "$1" in
      -o|--out)  BACKUP_ROOT="${2:?--out 需要一个参数}"; shift 2 ;;
      --keep)    KEEP="${2:?--keep 需要一个参数}"; shift 2 ;;
      *) die "未知参数：$1（--help 看用法）" ;;
    esac
  done

  need_docker
  service_running db || die "db 容器没在运行：先 bash scripts/up.sh"

  mkdir -p "$BACKUP_ROOT" 2>/dev/null || true
  [ -w "$BACKUP_ROOT" ] || die "目录不可写：$BACKUP_ROOT
  这个目录很可能是之前用 sudo 建的（比如 sudo docker ... > backup/x）。
  修一次就行：  sudo chown -R \"\$USER\":\"\$USER\" \"$BACKUP_ROOT\"
  或者换目录：  BACKUP_DIR=\$HOME/erp-backup bash scripts/backup.sh"

  TS="$(date +%Y%m%d-%H%M%S)"
  OUT="$BACKUP_ROOT/$TS"
  mkdir -p "$OUT"
  OUT_ABS="$(cd "$OUT" && pwd)"
  chmod 700 "$OUT_ABS"     # 里面有 pg 密码、ERP 会话 cookie、解密密钥

  # 1) 数据库（-T 必须要有：TTY 会往管道塞控制字符，gzip 包会坏）
  log "pg_dump → $OUT_ABS/erp.sql.gz"
  docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    | gzip -9 > "$OUT_ABS/erp.sql.gz"
  gzip -t "$OUT_ABS/erp.sql.gz" || die "erp.sql.gz 校验失败"

  # 2) 数据卷（容器内用 root 才能读 600 的密钥；产物走 stdout 归当前用户所有）
  log "打包数据卷 $DATA_VOLUME → $OUT_ABS/erpdata.tgz"
  docker run --rm -v "$DATA_VOLUME":/data:ro "$TAR_IMAGE" \
    tar czf - -C /data . > "$OUT_ABS/erpdata.tgz"
  [ -s "$OUT_ABS/erpdata.tgz" ] || die "erpdata.tgz 是空的（数据卷 $DATA_VOLUME 不存在？）"
  tar tzf "$OUT_ABS/erpdata.tgz" >/dev/null || die "erpdata.tgz 校验失败"

  # 3) 配置与证书
  log "复制 config.yaml / .env / certs/"
  cp -p config.yaml "$OUT_ABS/" 2>/dev/null || warn "没有 config.yaml（跳过）"
  cp -p .env        "$OUT_ABS/" 2>/dev/null || warn "没有 .env（跳过）"
  if [ -d certs ]; then
    mkdir -p "$OUT_ABS/certs"
    cp -p certs/*.crt certs/*.key "$OUT_ABS/certs/" 2>/dev/null || true
  fi
  chmod 600 "$OUT_ABS/.env" "$OUT_ABS/certs/"*.key 2>/dev/null || true

  # 4) MANIFEST
  {
    echo "time        : $(date -Is)"
    echo "host        : $(hostname) ($(hostname -I 2>/dev/null | tr ' ' ',') ) "
    echo "app image   : $(docker compose ps app --format '{{.Image}}' 2>/dev/null | head -1)"
    echo "pg image    : $(docker compose ps db  --format '{{.Image}}' 2>/dev/null | head -1)"
    echo "pg version  : $(psql_db 'show server_version' -tA 2>/dev/null)"
    echo "pg volume   : $PG_VOLUME (由 erp.sql.gz 逻辑备份，未做文件级拷贝)"
    echo "data volume : $DATA_VOLUME"
    echo "tables      : $(psql_db "select count(*) from information_schema.tables where table_schema='public'" -tA 2>/dev/null)"
    echo "users       : $(psql_db 'select count(*) from users' -tA 2>/dev/null)"
    echo "api_keys    : $(psql_db 'select count(*) from api_keys' -tA 2>/dev/null)"
    echo "has_key     : $(tar tzf "$OUT_ABS/erpdata.tgz" | grep -c 'erp_credentials.key')  (1 = 密钥已在包里)"
    echo "sha256      :"
    ( cd "$OUT_ABS" && sha256sum erp.sql.gz erpdata.tgz 2>/dev/null | sed 's/^/  /' )
  } > "$OUT_ABS/MANIFEST.txt"

  # 5) 清理旧备份
  if [ "$KEEP" -gt 0 ] 2>/dev/null; then
    ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
      [ "$(cd "$old" && pwd)" = "$OUT_ABS" ] && continue
      log "删除旧备份：$old"
      rm -rf "$old"
    done
  fi

  log "完成：$OUT_ABS"
  du -sh "$OUT_ABS"/* 2>/dev/null | sed 's/^/[erp-bk]   /' >&2
}

# ══════════════════ 恢复 ══════════════════
restore_db() {  # restore_db <dir>
  local dir="$1"
  log "恢复数据库（先 drop schema public，再整包导入）"
  docker compose up -d db >/dev/null
  wait_healthy db 90 || die "db 起不来（docker compose logs db 看看）"
  psql_db 'drop schema if exists public cascade; create schema public;' >/dev/null
  gunzip -c "$dir/erp.sql.gz" \
    | docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
  log "  表数量：$(psql_db "select count(*) from information_schema.tables where table_schema='public'" -tA)"
}

restore_data() {  # restore_data <dir>
  local dir="$1"
  log "恢复数据卷 $DATA_VOLUME"
  docker run --rm -v "$DATA_VOLUME":/data "$TAR_IMAGE" \
    sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null; true'
  docker run --rm -i -v "$DATA_VOLUME":/data "$TAR_IMAGE" \
    tar xzf - -C /data < "$dir/erpdata.tgz"
  # 容器以 uid 10001 运行，属主不对会读不到 600 的密钥
  docker run --rm -v "$DATA_VOLUME":/data "$TAR_IMAGE" \
    chown -R "$ERP_UID:$ERP_UID" /data
  log "  $(docker run --rm -v "$DATA_VOLUME":/data "$TAR_IMAGE" ls -1 /data | tr '\n' ' ')"
}

health_check() {
  local mapped scheme port
  mapped="$(docker compose port nginx 443 2>/dev/null | head -1 || true)"
  scheme=https; [ -n "$mapped" ] || { mapped="$(docker compose port nginx 80 2>/dev/null | head -1 || true)"; scheme=http; }
  [ -n "$mapped" ] || { warn "拿不到 nginx 端口，跳过健康检查"; return 0; }
  port="${mapped##*:}"
  command -v curl >/dev/null 2>&1 || { warn "没有 curl，跳过健康检查"; return 0; }
  if curl -fsSk -o /dev/null -m 10 "$scheme://localhost:$port/healthz"; then
    log "健康检查通过：$scheme://localhost:$port/healthz"
  else
    warn "健康检查失败：docker compose logs --tail=50 app"
    return 1
  fi
}

cmd_restore() {
  local DIR="" ASSUME_YES=0 WITH_CONFIG=0 SAFETY=1 DO_DB=1 DO_DATA=1 ANS
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)       ASSUME_YES=1; shift ;;
      --with-config)  WITH_CONFIG=1; shift ;;
      --no-safety)    SAFETY=0; shift ;;
      --skip-db)      DO_DB=0; shift ;;
      --skip-data)    DO_DATA=0; shift ;;
      -h|--help)      usage; exit 0 ;;
      -*)             die "未知参数：$1（--help 看用法）" ;;
      *)              DIR="$1"; shift ;;
    esac
  done

  need_docker
  DIR="$(resolve_dir "$DIR")"
  verify_archive "$DIR" || die "备份校验不通过，拒绝恢复：$DIR"

  # 在「裸机恢复」时这两样必须先就位，否则 compose 直接报错/应用起不来
  if [ ! -f .env ] && [ ! -f "$DIR/.env" ]; then
    die "缺 .env：先 cp docker/env.example .env 填好 POSTGRES_PASSWORD，或加 --with-config 使用备份里的"
  fi
  if [ ! -f config.yaml ] && [ ! -f "$DIR/config.yaml" ]; then
    die "缺 config.yaml：先 bash scripts/up.sh 生成，或加 --with-config 使用备份里的"
  fi

  if [ "$DO_DB" = 0 ] || [ "$DO_DATA" = 0 ]; then
    warn "只恢复一半：库里的密文与数据卷里的 erp_credentials.key 不配套时，全员需重新绑定 ERP"
  fi

  log "来源：$DIR"
  cat "$DIR/MANIFEST.txt" 2>/dev/null | sed 's/^/[erp-bk]   /' >&2 || true
  if [ "$ASSUME_YES" = 0 ]; then
    printf '[erp-bk] 将覆盖当前数据库/数据卷，输入 RESTORE 继续：' >&2
    read -r ANS </dev/tty || die "非交互环境请加 --yes"
    [ "$ANS" = RESTORE ] || die "已取消"
  fi

  # 恢复前先留一份安全备份（避免「恢复错版本」后无路可退）
  if [ "$SAFETY" = 1 ] && service_running db; then
    log "先做一份安全备份（不想做加 --no-safety）"
    ( cmd_backup --out "$BACKUP_ROOT" ) || warn "安全备份失败，继续（数据卷/库马上会被覆盖）"
  fi

  log "停止 app / nginx（保留 db）"
  docker compose stop app nginx >/dev/null 2>&1 || true

  [ "$DO_DB" = 1 ] && restore_db "$DIR"
  [ "$DO_DATA" = 1 ] && restore_data "$DIR"

  if [ "$WITH_CONFIG" = 1 ]; then
    log "覆盖 config.yaml / .env / certs"
    [ -f "$DIR/config.yaml" ] && cp -p "$DIR/config.yaml" ./config.yaml
    [ -f "$DIR/.env" ]        && cp -p "$DIR/.env" ./        # cp -p 会保留 600
    if [ -d "$DIR/certs" ]; then mkdir -p certs && cp -p "$DIR/certs/"* certs/ 2>/dev/null || true; fi
    chmod 644 config.yaml 2>/dev/null || true   # 容器 uid 10001 要能读
  fi

  log "启动全部服务"
  docker compose up -d >/dev/null
  wait_healthy app 120 || warn "app 还没 healthy，稍后自己看 docker compose ps"
  health_check || true

  log "完成。核对："
  log "  users    = $(psql_db 'select count(*) from users' -tA 2>/dev/null)"
  log "  api_keys = $(psql_db 'select count(*) from api_keys' -tA 2>/dev/null)"
  log "  卷内文件 = $(docker run --rm -v "$DATA_VOLUME":/data "$TAR_IMAGE" ls -1 /data 2>/dev/null | tr '\n' ' ')"
  log "客户端已发的 api-key 无需重发；只有恢复了一半时才需要重新 erp key"
}

# ══════════════════ 列表 ══════════════════
cmd_list() {
  local dirs
  dirs="$(ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null || true)"
  [ -n "$dirs" ] || { log "$BACKUP_ROOT 下还没有备份"; return 0; }
  printf '%-22s %-10s %-8s %-7s %-7s %s\n' 时间 大小 表 用户 key 路径 >&2
  while read -r d; do
    [ -n "$d" ] || continue
    d="${d%/}"
    local mf="$d/MANIFEST.txt" size tables users keys
    size="$(du -sh "$d" 2>/dev/null | cut -f1)"
    tables="$(awk -F': *' '/^tables/{print $2}' "$mf" 2>/dev/null)"
    users="$(awk -F': *' '/^users/{print $2}' "$mf" 2>/dev/null)"
    keys="$(awk -F': *' '/^api_keys/{print $2}' "$mf" 2>/dev/null)"
    printf '%-22s %-10s %-8s %-7s %-7s %s\n' \
      "$(basename "$d")" "${size:-?}" "${tables:-?}" "${users:-?}" "${keys:-?}" "$d" >&2
  done <<< "$dirs"
}

usage() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
}

# ══════════════════ 入口 ══════════════════
CMD="backup"
case "${1:-}" in
  backup|restore|verify|list) CMD="$1"; shift ;;
  -h|--help) usage; exit 0 ;;
esac

case "$CMD" in
  backup)  cmd_backup "$@" ;;
  restore) cmd_restore "$@" ;;
  list)    cmd_list ;;
  verify)  DIR="$(resolve_dir "${1:-latest}")"; verify_archive "$DIR" && log "校验通过：$DIR" ;;
esac
