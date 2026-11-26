import { useState } from 'react'
import type { FormEvent } from 'react'
import { platform } from '../../api'
import type { AccountState, ErpBinding } from '../../api'
import { describeError, formatTime } from '../../lib/format'
import { Alert, Badge, Button, Card, Field } from '../ui'

/** ERP 账号绑定卡片：一个平台账号只能绑一个工号，换绑到别的工号会撤销旧 token。 */
export function ErpCard({
  erp,
  onBound,
}: {
  erp: ErpBinding
  onBound: (state: AccountState & { revoked_tokens: number }) => void
}) {
  const [editing, setEditing] = useState(!erp.bound)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const state = await platform.bindErp(username.trim(), password)
      setPassword('')
      setEditing(false)
      onBound(state)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-white">ERP 账号</h3>
          <p className="mt-0.5 text-xs text-slate-400">
            一个平台账号只能绑定一个 ERP 工号；token 以这个身份访问 ERP。
          </p>
        </div>
        {erp.bound ? (
          <Badge>已绑定 {erp.username}</Badge>
        ) : (
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-xs text-amber-300">
            未绑定
          </span>
        )}
      </div>

      {erp.bound && !editing && (
        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div className="rounded-xl border border-white/10 bg-white/5 px-3.5 py-3">
            <dt className="text-xs text-slate-400">ERP 工号</dt>
            <dd className="mt-0.5 font-mono text-slate-100">{erp.username}</dd>
          </div>
          <div className="rounded-xl border border-white/10 bg-white/5 px-3.5 py-3">
            <dt className="text-xs text-slate-400">ERP 用户 ID</dt>
            <dd className="mt-0.5 font-mono text-slate-100">{erp.user_id ?? '—'}</dd>
          </div>
          <div className="rounded-xl border border-white/10 bg-white/5 px-3.5 py-3">
            <dt className="text-xs text-slate-400">绑定时间</dt>
            <dd className="mt-0.5 text-slate-100">{formatTime(erp.bound_at)}</dd>
          </div>
        </dl>
      )}

      {(editing || !erp.bound) && (
        <form className="mt-2 grid gap-4 sm:grid-cols-2" onSubmit={submit}>
          <Field
            label="ERP 工号"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="如 21146"
            autoComplete="username"
          />
          <Field
            label="ERP 密码"
            type="password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="只用于验证，不会保存"
            autoComplete="current-password"
            hint="服务端只保留 cookie 会话，密码不落盘"
          />
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <Button type="submit" busy={busy}>
              {erp.bound ? '换绑 ERP 账号' : '绑定并验证'}
            </Button>
            {erp.bound && (
              <>
                <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                  取消
                </Button>
                <span className="text-xs text-slate-500">
                  换绑到别的工号会撤销该账号下所有 token
                </span>
              </>
            )}
          </div>
          {error && (
            <div className="sm:col-span-2">
              <Alert kind="error">{error}</Alert>
            </div>
          )}
        </form>
      )}

      {erp.bound && !editing && (
        <div className="mt-4">
          <Button type="button" variant="ghost" onClick={() => setEditing(true)}>
            换绑 ERP 账号
          </Button>
        </div>
      )}
    </Card>
  )
}
