import { useCallback, useState } from 'react'
import { platform } from '../api'
import type { AccountState, McpToken } from '../api'
import { CreateTokenDialog } from '../components/tokens/CreateTokenDialog'
import { ErpCard } from '../components/tokens/ErpCard'
import { TokenList } from '../components/tokens/TokenList'
import { Alert, Button, Card } from '../components/ui'
import { describeError, formatTime } from '../lib/format'

/**
 * Token 管理页（容器）：只负责数据流与版面组合。
 *
 * 具体区块拆在 `components/tokens/`：
 * - `ErpCard`            绑定 / 换绑 ERP 账号
 * - `TokenList`          token 列表 + 撤销
 * - `CreateTokenDialog`  新增弹窗（表单 → 结果）
 * - `TokenResultBody`    结果内容（上方 API Key、下方使用指南）
 */
export function TokensPage({
  account,
  onAccountChange,
  onLogout,
}: {
  account: AccountState
  onAccountChange: (state: AccountState) => void
  onLogout: () => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const data = await platform.tokens()
      onAccountChange({
        user: { ...account.user, erp: data.erp },
        tokens: data.tokens,
      })
    } catch (err) {
      setError(describeError(err))
    }
  }, [account.user, onAccountChange])

  async function revoke(token: McpToken) {
    if (!window.confirm(`确认撤销 ${token.label || token.id}？撤销后立即失效，不可恢复。`)) {
      return
    }
    try {
      await platform.revokeToken(token.id)
      await refresh()
    } catch (err) {
      setError(describeError(err))
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-widest text-sky-400">Token 管理</p>
            <h2 className="mt-1 text-xl font-semibold text-white">{account.user.username}</h2>
            <p className="mt-1 text-xs text-slate-400">
              注册于 {formatTime(account.user.created_at)} · 最近登录{' '}
              {formatTime(account.user.last_login_at)}
            </p>
          </div>
          <Button type="button" variant="ghost" onClick={onLogout}>
            退出登录
          </Button>
        </div>
      </Card>

      <ErpCard
        erp={account.user.erp}
        credential={account.erp_credential}
        onBound={(state) => {
          onAccountChange({
            user: state.user,
            tokens: state.tokens,
            erp_credential: state.erp_credential ?? account.erp_credential,
          })
          void refresh()
        }}
      />

      <Card>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-white">我的 Token</h3>
            <p className="text-xs text-slate-500">共 {account.tokens.length} 把可用</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              className="py-1.5 text-xs"
              onClick={() => void refresh()}
            >
              刷新
            </Button>
            <Button
              type="button"
              className="py-1.5 text-xs"
              onClick={() => setDialogOpen(true)}
              title={account.user.erp.bound ? '新增一把 MCP token' : '先绑定 ERP 账号'}
            >
              新增
            </Button>
          </div>
        </div>

        {error && (
          <div className="mb-3">
            <Alert kind="error">{error}</Alert>
          </div>
        )}

        <TokenList tokens={account.tokens} onRevoke={revoke} />
      </Card>

      <CreateTokenDialog
        open={dialogOpen}
        bound={account.user.erp.bound}
        onClose={() => setDialogOpen(false)}
        onIssued={() => void refresh()}
      />
    </div>
  )
}
