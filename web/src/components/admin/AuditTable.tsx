import type { AuditItem } from '../../api'
import { formatTime } from '../../lib/format'
import { Button } from '../ui'

function detailText(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  )
  if (entries.length === 0) return ''
  return entries.map(([key, value]) => `${key}=${String(value)}`).join(' · ')
}

/** 审计日志列表（按时间倒序，分页由父组件控制）。 */
export function AuditTable({
  items,
  hasMore,
  loading,
  onLoadMore,
}: {
  items: AuditItem[]
  hasMore: boolean
  loading: boolean
  onLoadMore: () => void
}) {
  if (items.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-slate-500">
        没有匹配的日志
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {items.map((item) => (
          <li
            key={`${item.id}-${item.created_at}`}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-100">{item.action_label}</span>
              <span
                className={
                  item.result === 'success'
                    ? 'rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300'
                    : 'rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-rose-300'
                }
              >
                {item.result === 'success' ? '成功' : '失败'}
              </span>
              {item.status_code !== null && (
                <span className="font-mono text-slate-500">{item.status_code}</span>
              )}
              <span className="ml-auto text-slate-500">{formatTime(item.created_at)}</span>
            </div>
            <p className="mt-1 text-slate-400">
              操作者：<span className="text-slate-200">{item.actor_name ?? '匿名'}</span>
              <span className="text-slate-600">（{item.actor_type}）</span>
              {item.target_type && (
                <>
                  {' · '}目标：{item.target_type}
                  {item.target_id ? ` ${item.target_id}` : ''}
                </>
              )}
              {item.ip && <> · IP {item.ip}</>}
            </p>
            {detailText(item.detail) && (
              <p className="mt-1 font-mono text-slate-500">{detailText(item.detail)}</p>
            )}
          </li>
        ))}
      </ul>

      {hasMore && (
        <div className="pt-1">
          <Button
            type="button"
            variant="ghost"
            busy={loading}
            className="py-1.5 text-xs"
            onClick={onLoadMore}
          >
            加载更多
          </Button>
        </div>
      )}
    </div>
  )
}
