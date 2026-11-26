import { useState } from 'react'
import type { FormEvent } from 'react'
import { platform } from '../api'
import type { AccountState } from '../api'
import { describeError } from '../lib/format'
import { Alert, Button, Card, Field, cn } from '../components/ui'

export type AuthMode = 'login' | 'register'

export function AuthPage({
  mode,
  signupEnabled,
  onModeChange,
  onAuthenticated,
}: {
  mode: AuthMode
  signupEnabled: boolean
  onModeChange: (mode: AuthMode) => void
  onAuthenticated: (state: AccountState) => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const registering = mode === 'register'

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (registering && password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setBusy(true)
    try {
      const state = registering
        ? await platform.register(username.trim(), password)
        : await platform.login(username.trim(), password)
      setPassword('')
      setConfirm('')
      onAuthenticated(state)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="mb-5 flex rounded-xl border border-white/10 bg-slate-950/50 p-1">
        {(['login', 'register'] as const).map((item) => (
          <button
            key={item}
            type="button"
            disabled={item === 'register' && !signupEnabled}
            onClick={() => {
              setError('')
              onModeChange(item)
            }}
            className={cn(
              'flex-1 rounded-lg px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40',
              mode === item
                ? 'bg-sky-500 text-slate-950 shadow'
                : 'text-slate-300 hover:text-white',
            )}
          >
            {item === 'login' ? '登录' : '注册账号'}
          </button>
        ))}
      </div>

      <div className="mb-6">
        <h2 className="text-xl font-semibold text-white">
          {registering ? '创建 HB-MCP 平台账号' : '登录 HB-MCP 平台'}
        </h2>
        <p className="mt-1 text-sm text-slate-400">
          {registering
            ? '这是本平台的账号，不是 ERP 工号；注册后到「Token 管理」里再绑定 ERP。'
            : '登录后进入 Token 管理：绑定 ERP 账号、申请和撤销 MCP token。'}
        </p>
      </div>

      <form className="space-y-4" onSubmit={submit}>
        <Field
          label="用户名"
          name="username"
          autoComplete="username"
          placeholder="字母 / 数字 / . _ -，3~32 位"
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
        <Field
          label="密码"
          name="password"
          type="password"
          autoComplete={registering ? 'new-password' : 'current-password'}
          placeholder="至少 8 位"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {registering && (
          <Field
            label="确认密码"
            type="password"
            autoComplete="new-password"
            placeholder="再输一次"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        )}

        {error && <Alert kind="error">{error}</Alert>}
        {registering && !signupEnabled && (
          <Alert kind="info">本实例未开放自助注册，请联系管理员开通账号。</Alert>
        )}

        <Button type="submit" busy={busy} className="w-full" disabled={registering && !signupEnabled}>
          {registering ? '注册并登录' : '登录'}
        </Button>
      </form>

      <p className="mt-5 text-xs leading-relaxed text-slate-500">
        平台密码用 scrypt 存储；登录后只靠 HttpOnly cookie 会话，不会在页面里回显。
      </p>
    </Card>
  )
}
