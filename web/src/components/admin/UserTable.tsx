import type { AdminUser } from '../../api'
import { formatTime } from '../../lib/format'
import { Badge, Button } from '../ui'

/** 管理员后台的用户表格 + 行内操作。 */
export function UserTable({
  users,
  meId,
  busyId,
  onToggleRole,
  onToggleDisabled,
  onUnbindErp,
  onRevokeTokens,
  onDelete,
}: {
  users: AdminUser[]
  meId: string
  busyId: string | null
  onToggleRole: (user: AdminUser) => void
  onToggleDisabled: (user: AdminUser) => void
  onUnbindErp: (user: AdminUser) => void
  onRevokeTokens: (user: AdminUser) => void
  onDelete: (user: AdminUser) => void
}) {
  if (users.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-slate-500">
        没有匹配的用户
      </p>
    )
  }

  return (
    <ul className="space-y-2">
      {users.map((user) => {
        const self = user.id === meId
        const busy = busyId === user.id
        return (
          <li
            key={user.id}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-100">{user.username}</span>
              {user.role === 'admin' && (
                <span className="rounded-full border border-sky-500/40 bg-sky-500/10 px-2.5 py-0.5 text-xs text-sky-300">
                  管理员
                </span>
              )}
              {user.disabled && (
                <span className="rounded-full border border-rose-500/40 bg-rose-500/10 px-2.5 py-0.5 text-xs text-rose-300">
                  已禁用
                </span>
              )}
              {self && <Badge>我</Badge>}
              <span className="ml-auto font-mono text-xs text-slate-500">
                {user.id.slice(0, 12)}
              </span>
            </div>

            <div className="mt-2 grid gap-x-4 gap-y-1 text-xs text-slate-400 sm:grid-cols-2">
              <p>
                ERP：
                {user.erp.bound ? (
                  <span className="font-mono text-slate-200">{user.erp.username}</span>
                ) : (
                  <span className="text-amber-300">未绑定</span>
                )}
              </p>
              <p>
                Token：<span className="text-slate-200">{user.token_count}</span> 可用 /{' '}
                {user.token_total} 总数 · 会话 {user.session_count}
              </p>
              <p>注册于 {formatTime(user.created_at)}</p>
              <p>最近登录 {formatTime(user.last_login_at)}</p>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                type="button"
                variant="ghost"
                className="py-1.5 text-xs"
                disabled={self || busy}
                onClick={() => onToggleRole(user)}
              >
                {user.role === 'admin' ? '取消管理员' : '设为管理员'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="py-1.5 text-xs"
                disabled={self || busy}
                onClick={() => onToggleDisabled(user)}
              >
                {user.disabled ? '启用账号' : '禁用账号'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="py-1.5 text-xs"
                disabled={!user.erp.bound || busy}
                onClick={() => onUnbindErp(user)}
              >
                解绑 ERP
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="py-1.5 text-xs"
                disabled={user.token_count === 0 || busy}
                onClick={() => onRevokeTokens(user)}
              >
                撤销全部 Token
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="py-1.5 text-xs text-rose-300 hover:bg-rose-500/10"
                disabled={self || busy}
                onClick={() => onDelete(user)}
              >
                删除账号
              </Button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
