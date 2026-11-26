import { useCallback, useEffect, useState } from 'react'
import { admin } from '../api'
import type { AdminOverview, AdminUser, AuditItem, CallItem } from '../api'
import { AuditTable } from '../components/admin/AuditTable'
import { CallTable } from '../components/admin/CallTable'
import { UserTable } from '../components/admin/UserTable'
import { Alert, Badge, Button, Card, Field } from '../components/ui'
import { describeError } from '../lib/format'

type Tab = 'users' | 'logs' | 'calls'

const PAGE = 30

/** 管理员后台：用户管理 + 审计日志 + MCP 调用日志。仅 `role == admin` 的账号可见。 */
export function AdminPage({ meId, onBack }: { meId: string; onBack: () => void }) {
  const [tab, setTab] = useState<Tab>('users')
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [error, setError] = useState('')

  // 用户
  const [queryInput, setQueryInput] = useState('')
  const [query, setQuery] = useState('')
  const [users, setUsers] = useState<AdminUser[]>([])
  const [userTotal, setUserTotal] = useState(0)
  const [usersLoading, setUsersLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  // 审计日志
  const [logs, setLogs] = useState<AuditItem[]>([])
  const [logsHaveMore, setLogsHaveMore] = useState(false)
  const [logsLoading, setLogsLoading] = useState(false)
  const [filters, setFilters] = useState({ action: '', actor: '', result: '' })

  // MCP 调用日志
  const [calls, setCalls] = useState<CallItem[]>([])
  const [callsHaveMore, setCallsHaveMore] = useState(false)
  const [callsLoading, setCallsLoading] = useState(false)
  const [callFilters, setCallFilters] = useState({ tool: '', actor: '', status: '' })

  const loadUsers = useCallback(
    async (nextQuery: string, offset = 0) => {
      setUsersLoading(true)
      try {
        const data = await admin.users(nextQuery, PAGE, offset)
        setUsers((prev) => (offset === 0 ? data.items : [...prev, ...data.items]))
        setUserTotal(data.total)
      } catch (err) {
        setError(describeError(err))
      } finally {
        setUsersLoading(false)
      }
    },
    [],
  )

  const loadLogs = useCallback(
    async (offset = 0, next = filters) => {
      setLogsLoading(true)
      try {
        const data = await admin.audit({ ...next, limit: PAGE, offset })
        setLogs((prev) => (offset === 0 ? data.items : [...prev, ...data.items]))
        setLogsHaveMore(data.has_more)
      } catch (err) {
        setError(describeError(err))
      } finally {
        setLogsLoading(false)
      }
    },
    [filters],
  )

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await admin.overview())
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  const loadCalls = useCallback(
    async (offset = 0, next = callFilters) => {
      setCallsLoading(true)
      try {
        const data = await admin.calls({ ...next, limit: PAGE, offset })
        setCalls((prev) => (offset === 0 ? data.items : [...prev, ...data.items]))
        setCallsHaveMore(data.has_more)
      } catch (err) {
        setError(describeError(err))
      } finally {
        setCallsLoading(false)
      }
    },
    [callFilters],
  )

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  useEffect(() => {
    void loadUsers(query, 0)
  }, [loadUsers, query])

  useEffect(() => {
    void loadLogs(0, filters)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (tab === 'calls') void loadCalls(0, callFilters)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  async function act(user: AdminUser, run: (id: string) => Promise<unknown>, tip?: string) {
    if (tip && !window.confirm(tip)) return
    setBusyId(user.id)
    setError('')
    try {
      await run(user.id)
      await loadUsers(query, 0)
      await loadOverview()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusyId(null)
    }
  }

  const actionOptions = [
    { value: '', label: '全部动作' },
    ...(overview?.actions ?? []),
  ]

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-widest text-sky-400">管理后台</p>
            <h2 className="mt-1 text-xl font-semibold text-white">平台账号与审计日志</h2>
            <p className="mt-1 text-xs text-slate-400">
              用户管理、ERP 绑定与 Token 撤销都会写入审计日志（PostgreSQL）。
            </p>
          </div>
          <Button type="button" variant="ghost" onClick={onBack}>
            返回 Token 管理
          </Button>
        </div>

        {overview && (
          <div className="mt-4 flex flex-wrap gap-2">
            <Badge>用户 {overview.users.total}</Badge>
            <Badge>管理员 {overview.users.admins}</Badge>
            <Badge>已绑定 ERP {overview.users.erp_bound}</Badge>
            <Badge>禁用 {overview.users.disabled}</Badge>
            <Badge>Token {overview.tokens.active} 可用 / {overview.tokens.total} 总数</Badge>
            <Badge>MCP 调用 {overview.calls.total}</Badge>
          </div>
        )}
      </Card>

      {error && <Alert kind="error">{error}</Alert>}

      <div className="inline-flex w-fit rounded-2xl border border-white/10 bg-white/5 p-1">
        {([
          ['users', '用户管理'],
          ['logs', '审计日志'],
          ['calls', 'MCP 调用'],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={
              tab === value
                ? 'rounded-xl bg-sky-500 px-4 py-2 text-sm font-medium text-slate-950 shadow'
                : 'rounded-xl px-4 py-2 text-sm font-medium text-slate-300 hover:text-white'
            }
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'users' && (
        <Card>
          <form
            className="mb-4 flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery(queryInput.trim())
            }}
          >
            <div className="min-w-56 flex-1">
              <Field
                label="搜索"
                value={queryInput}
                onChange={(event) => setQueryInput(event.target.value)}
                placeholder="用户名或 ERP 工号"
              />
            </div>
            <Button type="submit" busy={usersLoading}>
              查询
            </Button>
            <span className="pb-2 text-xs text-slate-500">
              共 {userTotal} 个账号，已加载 {users.length}
            </span>
          </form>

          <UserTable
            users={users}
            meId={meId}
            busyId={busyId}
            onToggleRole={(user) =>
              void act(
                user,
                (id) =>
                  admin.updateUser(id, { role: user.role === 'admin' ? 'user' : 'admin' }),
                user.role === 'admin'
                  ? `取消 ${user.username} 的管理员权限？`
                  : `把 ${user.username} 设为管理员？`,
              )
            }
            onToggleDisabled={(user) =>
              void act(
                user,
                (id) => admin.updateUser(id, { disabled: !user.disabled }),
                user.disabled
                  ? `启用 ${user.username}？启用后需要重新登录并重新申请 token。`
                  : `禁用 ${user.username}？现有会话与全部 token 会立即失效。`,
              )
            }
            onUnbindErp={(user) =>
              void act(
                user,
                (id) => admin.unbindErp(id),
                `解绑 ${user.username} 的 ERP 账号？该用户所有 token 会被撤销。`,
              )
            }
            onRevokeTokens={(user) =>
              void act(
                user,
                (id) => admin.revokeTokens(id),
                `撤销 ${user.username} 的全部 token？撤销后立即失效。`,
              )
            }
            onDelete={(user) =>
              void act(
                user,
                (id) => admin.deleteUser(id),
                `删除账号 ${user.username}？会话清空、token 撤销，不可恢复。`,
              )
            }
          />

          {users.length < userTotal && (
            <div className="mt-3">
              <Button
                type="button"
                variant="ghost"
                busy={usersLoading}
                className="py-1.5 text-xs"
                onClick={() => void loadUsers(query, users.length)}
              >
                加载更多用户
              </Button>
            </div>
          )}
        </Card>
      )}

      {tab === 'logs' && (
        <Card>
          <form
            className="mb-4 grid gap-3 sm:grid-cols-4"
            onSubmit={(event) => {
              event.preventDefault()
              void loadLogs(0, filters)
            }}
          >
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">动作</span>
              <select
                className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-sky-400/70"
                value={filters.action}
                onChange={(event) =>
                  setFilters((prev) => ({ ...prev, action: event.target.value }))
                }
              >
                {actionOptions.map((item) => (
                  <option key={item.value} value={item.value} className="bg-slate-900">
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">结果</span>
              <select
                className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-sky-400/70"
                value={filters.result}
                onChange={(event) =>
                  setFilters((prev) => ({ ...prev, result: event.target.value }))
                }
              >
                <option value="" className="bg-slate-900">全部</option>
                <option value="success" className="bg-slate-900">成功</option>
                <option value="failure" className="bg-slate-900">失败</option>
              </select>
            </label>
            <Field
              label="操作者"
              value={filters.actor}
              onChange={(event) =>
                setFilters((prev) => ({ ...prev, actor: event.target.value }))
              }
              placeholder="用户名 / id 片段"
            />
            <div className="flex items-end">
              <Button type="submit" busy={logsLoading} className="w-full">
                查询日志
              </Button>
            </div>
          </form>

          <AuditTable
            items={logs}
            hasMore={logsHaveMore}
            loading={logsLoading}
            onLoadMore={() => void loadLogs(logs.length, filters)}
          />
        </Card>
      )}

      {tab === 'calls' && (
        <Card>
          <form
            className="mb-4 grid gap-3 sm:grid-cols-4"
            onSubmit={(event) => {
              event.preventDefault()
              void loadCalls(0, callFilters)
            }}
          >
            <Field
              label="工具名"
              value={callFilters.tool}
              onChange={(event) =>
                setCallFilters((prev) => ({ ...prev, tool: event.target.value }))
              }
              placeholder="erp_quote"
            />
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">结果</span>
              <select
                className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-sky-400/70"
                value={callFilters.status}
                onChange={(event) =>
                  setCallFilters((prev) => ({ ...prev, status: event.target.value }))
                }
              >
                <option value="" className="bg-slate-900">全部</option>
                <option value="success" className="bg-slate-900">成功</option>
                <option value="failure" className="bg-slate-900">失败</option>
              </select>
            </label>
            <Field
              label="用户"
              value={callFilters.actor}
              onChange={(event) =>
                setCallFilters((prev) => ({ ...prev, actor: event.target.value }))
              }
              placeholder="用户名 / ERP 工号"
            />
            <div className="flex items-end">
              <Button type="submit" busy={callsLoading} className="w-full">
                查询调用
              </Button>
            </div>
          </form>

          <CallTable
            items={calls}
            hasMore={callsHaveMore}
            loading={callsLoading}
            onLoadMore={() => void loadCalls(calls.length, callFilters)}
          />
        </Card>
      )}
    </div>
  )
}
