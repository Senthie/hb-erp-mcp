#!/usr/bin/env bash
# 定时拉取 GitHub 并重建堆栈（给服务器用；配合 cron / systemd timer）
#
#   bash scripts/deploy.sh            # 有更新才重建；没更新直接退出 0
#   bash scripts/deploy.sh --force    # 不比对，强制重建一次
#   bash scripts/deploy.sh --check    # 只检查：0=最新，1=有更新（不改动任何东西）
#
# 设计要点：
#   * 只允许 `--ff-only` 快进，服务器上不会产生 merge 提交
#   * 工作区有未提交改动就**拒绝执行**（避免把本地改动丢掉）
#   * 重建用 `up -d --build`：`up -d` 不带 `--build` **不会**重新构建镜像
#   * 命名卷 erp-tools-erpdata 不受重建影响；**绝不要**用 `down -v`
#   * 建议仓库克隆到固定目录、证书与 .env 放在仓库外（见 docs/mcp.md 6.1 / 6.2）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REMOTE="${DEPLOY_REMOTE:-origin}"
BRANCH="${DEPLOY_BRANCH:-main}"
MODE="${1:-auto}"
STAMP="$ROOT/.deployed-sha"

log() { printf '[deploy] %s\n' "$*" >&2; }

case "$MODE" in
  auto|--auto)   MODE=auto ;;
  force|--force) MODE=force ;;
  check|--check) MODE=check ;;
  -h|--help)
    cat >&2 <<'USAGE'
用法: bash scripts/deploy.sh [auto|force|check]
  auto  （默认）远端有新提交才重建
  force 无论是否有更新都重建一次
  check 只检查是否有更新（0=最新，1=有更新）
环境变量: DEPLOY_REMOTE=origin  DEPLOY_BRANCH=main
USAGE
    exit 0 ;;
  *)             log "未知参数：$MODE（可用 auto | force | check）"; exit 2 ;;
esac

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { log "当前目录不是 git 仓库"; exit 2; }

if [ -n "$(git status --porcelain)" ]; then
  log "工作区有未提交的改动，拒绝自动部署（先 git status 看一下）"
  exit 3
fi

log "拉取 $REMOTE/$BRANCH ..."
git fetch --prune "$REMOTE" "$BRANCH"

target="$(git rev-parse "$REMOTE/$BRANCH")"
current="$(git rev-parse HEAD)"

if [ "$MODE" = check ]; then
  if [ "$target" = "$current" ]; then
    log "已是最新：${current:0:8}"
    exit 0
  fi
  log "有更新：${current:0:8} -> ${target:0:8}"
  exit 1
fi

if [ "$MODE" = auto ] && [ "$target" = "$current" ]; then
  log "已是最新：${current:0:8}（未做任何改动）"
  exit 0
fi

# 本地必须能快进到远端：若本地有未推送的提交（current 不是 target 的祖先），
# 说明服务器上有人在手改代码 —— 停下来，别自作主张。
if [ "$target" != "$current" ] && ! git merge-base --is-ancestor "$current" "$target"; then
  log "本地 HEAD ${current:0:8} 不是 ${REMOTE}/${BRANCH} ${target:0:8} 的祖先（有未推送提交或已分叉），跳过"
  exit 4
fi

if [ "$target" != "$current" ]; then
  log "更新代码：${current:0:8} -> ${target:0:8}"
  git merge --ff-only "$target"
else
  log "代码未变（${current:0:8}），仍按要求重建"
fi

log "构建镜像（--pull 顺带更新基础镜像）..."
# 证书缺失会导致 nginx 起不来；--ensure 是幂等的，已有有效证书直接跳过
bash scripts/gen-cert.sh --ensure
docker compose build --pull

log "重建并启动（只替换有变化的容器）..."
docker compose up -d --remove-orphans

log "清理悬空镜像..."
docker image prune -f >/dev/null

git rev-parse HEAD > "$STAMP"

log "完成，当前版本 $(git rev-parse --short HEAD)"
docker compose ps
