import type { NewToken } from '../../api'
import { CopyButton, Button } from '../ui'

/**
 * 创建成功后的弹窗内容：**上方 API Key、下方使用指南**。
 *
 * token 只返回这一次，所以必须由用户显式点「完成」才关闭弹窗（见 CreateTokenDialog）。
 */
export function TokenResultBody({
  created,
  onDone,
}: {
  created: NewToken
  onDone: () => void
}) {
  return (
    <div className="space-y-5">
      <section>
        <p className="mb-1.5 text-sm font-medium text-white">API Key</p>
        <div className="flex items-start gap-2">
          <code className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-slate-950/70 px-3 py-2 font-mono text-xs leading-5 text-emerald-200">
            {created.token}
          </code>
          <CopyButton value={created.token} label="复制 API Key" />
        </div>
        <p className="mt-2 text-xs text-amber-200/80">
          完整 token 只显示这一次；点「完成」后页面只保留 id 与状态。
        </p>
      </section>

      <div className="border-t border-white/10" />

      <section>
        <p className="mb-3 text-sm font-medium text-white">使用指南</p>
        <ol className="space-y-4 text-xs leading-relaxed text-slate-300">
          <li>
            <p className="mb-1.5">
              <span className="mr-1.5 font-semibold text-emerald-300">1.</span>
              把下面这段写进 MCP 客户端（VS Code / Cursor）的{' '}
              <code className="text-sky-300">mcp.json</code>：
            </p>
            <pre className="overflow-x-auto rounded-lg bg-slate-950/70 p-3 font-mono text-xs leading-5 text-slate-200">
{created.mcp.config_json}
            </pre>
            <div className="mt-2">
              <CopyButton value={created.mcp.config_json} label="复制配置" />
            </div>
          </li>
          <li>
            <p className="mb-1.5">
              <span className="mr-1.5 font-semibold text-emerald-300">2.</span>
              或者直接把这段提示粘给 AI，让它自己接上这个 MCP：
            </p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-slate-950/70 p-3 font-mono text-xs leading-5 text-sky-100">
{created.mcp.prompt}
            </pre>
            <div className="mt-2">
              <CopyButton value={created.mcp.prompt} label="复制提示" />
            </div>
          </li>
          <li>
            <p>
              <span className="mr-1.5 font-semibold text-emerald-300">3.</span>
              之后每次调用都会以 ERP 账号{' '}
              <code className="text-sky-300">{created.erp_username}</code>{' '}
              的身份执行；撤销该 token 或换绑 ERP 后立即失效。
            </p>
          </li>
        </ol>
      </section>

      <div className="flex justify-end">
        <Button type="button" onClick={onDone}>
          完成
        </Button>
      </div>
    </div>
  )
}
