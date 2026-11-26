import { useEffect, useState } from 'react'
import { health, platform } from './api'
import type { AccountState, HealthInfo } from './api'
import { Badge, Alert } from './components/ui'
import { AuthPage } from './pages/AuthPage'
import type { AuthMode } from './pages/AuthPage'
import { AdminPage } from './pages/AdminPage'
import { TokensPage } from './pages/TokensPage'

type View = AuthMode | 'tokens' | 'admin'

function readHash(): View {
  const value = window.location.hash.replace(/^#\/?/, '')
  if (value === 'register') return 'register'
  if (value === 'tokens') return 'tokens'
  if (value === 'admin') return 'admin'
  return 'login'
}

export function App() {
  const [info, setInfo] = useState<HealthInfo | null>(null)
  const [account, setAccount] = useState<AccountState | null>(null)
  const [view, setView] = useState<View>(readHash)
  const [booted, setBooted] = useState(false)

  useEffect(() => {
    const sync = () => setView(readHash())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  useEffect(() => {
    let alive = true
    health().then((data) => alive && setInfo(data)).catch(() => undefined)
    platform
      .me()
      .then((state) => {
        if (!alive) return
        setAccount({
          user: state.user,
          tokens: state.tokens,
          erp_credential: state.erp_credential,
        })
        const current = readHash()
        if (current === 'register') return
        if (current === 'admin' && state.user.is_admin) {
          setView('admin')
          return
        }
        window.location.hash = '#/tokens'
        setView('tokens')
      })
      .catch(() => undefined)
      .finally(() => alive && setBooted(true))
    return () => {
      alive = false
    }
  }, [])

  function go(next: View) {
    window.location.hash = `#/${next}`
    setView(next)
  }

  function authenticated(state: AccountState) {
    setAccount(state)
    go('tokens')
  }

  async function logout() {
    try {
      await platform.logout()
    } finally {
      setAccount(null)
      go('login')
    }
  }

  const signupEnabled = info?.signup_enabled !== false
  const showTokens = view === 'tokens' && account !== null
  const showAdmin = view === 'admin' && account !== null && account.user.is_admin
  const wide = showAdmin
  const authMode: AuthMode = view === 'register' ? 'register' : 'login'

  useEffect(() => {
    if (account && view === 'admin' && !account.user.is_admin) {
      window.location.hash = '#/tokens'
      setView('tokens')
    }
  }, [account, view])

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-8 sm:px-6">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="grid size-11 place-items-center rounded-2xl bg-gradient-to-br from-sky-400 to-indigo-500 font-black text-slate-950 shadow-lg shadow-sky-500/20">
            HB
          </span>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-white">HB-MCP</h1>
            <p className="text-xs text-slate-400">华博 ERP 工具网关 · 账号与 MCP Token 管理</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {account && (
            <div className="mr-1 flex items-center gap-1 rounded-2xl border border-white/10 bg-white/5 p-1">
              <button
                type="button"
                onClick={() => go('tokens')}
                className={
                  view === 'tokens'
                    ? 'rounded-xl bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950'
                    : 'rounded-xl px-3 py-1.5 text-xs font-medium text-slate-300 hover:text-white'
                }
              >
                Token 管理
              </button>
              {account.user.is_admin && (
                <button
                  type="button"
                  onClick={() => go('admin')}
                  className={
                    view === 'admin'
                      ? 'rounded-xl bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950'
                      : 'rounded-xl px-3 py-1.5 text-xs font-medium text-slate-300 hover:text-white'
                  }
                >
                  管理后台
                </button>
              )}
            </div>
          )}
          {info?.version && <Badge>v{info.version}</Badge>}
          {info?.store && <Badge>{info.store}</Badge>}
          {account && <Badge>{account.user.username}</Badge>}
          <Badge>{info ? '在线' : '未连接'}</Badge>
        </div>
      </header>

      <main
        className={
          wide
            ? 'flex-1'
            : 'grid flex-1 gap-6 lg:grid-cols-[1.05fr_1fr] lg:items-start'
        }
      >
        <section className={wide ? 'hidden' : 'hidden lg:block'}>
          <h2 className="text-3xl font-semibold leading-tight text-white">
            一个平台账号，
            <br />
            管住所有 MCP Token。
          </h2>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-slate-400">
            HB-MCP 把华博 ERP 的报价需求、客户报价、考勤与附件能力封装成 MCP 工具。
            先用平台账号登录，绑定你的 ERP 工号，再申请 token——申请成功后会直接给出
            AI 客户端配置和一段可复制给 AI 的提示。
          </p>

          <ol className="mt-6 space-y-3 text-sm text-slate-400">
            {[
              '登录 / 注册平台账号（你自己的用户名密码，不是 ERP 工号）',
              '绑定 ERP 账号（一个平台账号只绑一个工号，密码不落盘）',
              '申请 MCP Token，复制配置或 AI 提示即可用',
            ].map((text, index) => (
              <li key={text} className="flex gap-3">
                <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-sky-500/15 text-xs font-semibold text-sky-300">
                  {index + 1}
                </span>
                {text}
              </li>
            ))}
          </ol>

          <div className="mt-8 rounded-2xl border border-white/10 bg-slate-900/50 p-4 font-mono text-xs leading-6 text-slate-400">
            <p className="text-slate-500"># 申请到的 token 长这样，放进 mcp.json</p>
            <p className="text-slate-200">{'{ "servers": { "hb-mcp": {'}</p>
            <p className="pl-4 text-slate-200">
              "type": "http", "url": "{window.location.origin}/mcp"
            </p>
            <p className="pl-4 text-slate-200">{'}, } }'}</p>
          </div>
        </section>

        <div className="w-full">
          {showAdmin ? (
            <AdminPage
              meId={account.user.id}
              onBack={() => go('tokens')}
            />
          ) : showTokens ? (
            <TokensPage
              account={account}
              onAccountChange={setAccount}
              onLogout={() => void logout()}
            />
          ) : (
            <div className="space-y-4">
              {booted && (view === 'tokens' || view === 'admin') && !account && (
                <Alert kind="info">登录后才能管理 Token，请先登录。</Alert>
              )}
              <AuthPage
                mode={authMode}
                signupEnabled={signupEnabled}
                onModeChange={(mode) => go(mode)}
                onAuthenticated={authenticated}
              />
            </div>
          )}
        </div>
      </main>

      <footer className="mt-10 flex flex-wrap items-center gap-3 border-t border-white/5 pt-5 text-xs text-slate-500">
        <span>HB-MCP</span>
        <a className="hover:text-slate-300" href="/healthz">
          /healthz
        </a>
        <span className="ml-auto">平台密码 scrypt · token 只存哈希 · ERP 密码不落盘</span>
      </footer>
    </div>
  )
}
