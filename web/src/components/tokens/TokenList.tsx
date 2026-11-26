import type { McpToken } from '../../api'
import { formatTime } from '../../lib/format'
import { Badge, Button } from '../ui'

/** 当前账号名下的 token 列表（只展示 id 前缀，不含明文 token）。 */
export function TokenList({
  tokens,
  onRevoke,
}: {
  tokens: McpToken[]
  onRevoke: (token: McpToken) => void
}) {
  if (tokens.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-white/10 px-4 py-6 text-center text-sm text-slate-500">
        还没有 token，点右上角「新增」创建一把
      </p>
    )
  }

  return (
    <ul className="space-y-2">
      {tokens.map((token) => (
        <li
          key={token.id}
          className="flex flex-wrap items-center gap-3 rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm"
        >
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-100">{token.label || '未命名 token'}</span>
              <Badge>{token.id}</Badge>
              <span className="text-xs text-slate-500">{token.erp_username}</span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              创建于 {formatTime(token.created_at)} · 最近使用 {formatTime(token.last_used)}
            </p>
          </div>
          <Button
            type="button"
            variant="ghost"
            className="py-1.5 text-xs text-rose-300 hover:bg-rose-500/10"
            onClick={() => onRevoke(token)}
          >
            撤销
          </Button>
        </li>
      ))}
    </ul>
  )
}
