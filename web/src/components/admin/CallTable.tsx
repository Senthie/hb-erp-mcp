import type { CallItem } from '../../api'
import { formatTime } from '../../lib/format'
import { Button } from '../ui'

function argsText(args: Record<string, string>): string {
  const entries = Object.entries(args).filter(([, value]) => value !== '')
  if (entries.length === 0) return ''
  return entries.map(([key, value]) => `${key}=${value}`).join(' · ')
}

function durationText(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`
  return `${ms}ms`
}

/** MCP 工具调用日志列表（每个工具调用一行，按时间倒序）。 */
export function CallTable({
  items,
  hasMore,
  loading,
  onLoadMore,
}: {
  items: CallItem[]
  hasMore: boolean
  loading: boolean
  onLoadMore: () => void
}) {
  if (items.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-slate-500">
        没有匹配的调用记录
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {items.map((item) => (
          <li
            key={`${item.id}-${item.created_at}-${item.tool}`}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-medium text-slate-100">{item.tool}</span>
              <span
                className={
                  item.status === 'success'
                    ? 'rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300'
                    : 'rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-rose-300'
                }
              >
                {item.status === 'success' ? '成功' : '失败'}
              </span>
              <span className="text-slate-500">{durationText(item.duration_ms)}</span>
              <span className="ml-auto text-slate-500">{formatTime(item.created_at)}</span>
            </div>

            <p className="mt-1 text-slate-400">
              用户：
              <span className="text-slate-200">
                {item.owner_name ?? item.actor_name ?? '未知'}
                {item.owner_name && item.actor_name && item.owner_name !== item.actor_name
                  ? `（${item.actor_name}）`
                  : ''}
              </span>
              {item.erp_username && <> · ERP {item.erp_username}</>}
              {item.token_id && (
                <>
                  {' · '}Token <span className="font-mono">{item.token_id}</span>
                </>
              )}
              {item.ip && <> · IP {item.ip}</>}
            </p>

            {argsText(item.arguments) && (
              <p className="mt-1 break-all font-mono text-slate-500">{argsText(item.arguments)}</p>
            )}
            {item.error && (
              <p className="mt-1 break-all font-mono text-rose-400/80">{item.error}</p>
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
