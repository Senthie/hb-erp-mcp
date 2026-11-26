import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { platform } from '../../api'
import type { NewToken } from '../../api'
import { describeError } from '../../lib/format'
import { Alert, Button, Field, Modal } from '../ui'
import { TokenResultBody } from './TokenResultBody'

/**
 * 「新增 MCP Token」弹窗。
 *
 * 两个阶段共用一个弹窗：
 * 1. 表单：备注必填；
 * 2. 结果：上方 API Key、下方使用指南 —— 此阶段 `dismissible=false`，
 *    Esc / 点遮罩都不关，必须点「完成」，避免手滑丢掉只显示一次的 token。
 */
export function CreateTokenDialog({
  open,
  bound,
  onClose,
  onIssued,
}: {
  open: boolean
  bound: boolean
  onClose: () => void
  onIssued: () => void
}) {
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<NewToken | null>(null)

  useEffect(() => {
    if (open) {
      setLabel('')
      setError('')
      setCreated(null)
    }
  }, [open])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = label.trim()
    if (!text) {
      setError('请填写备注，用来区分不同设备或用途')
      return
    }
    setBusy(true)
    setError('')
    try {
      setCreated(await platform.createToken(text))
      onIssued()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title={created ? '创建成功' : '新增 MCP Token'}
      description={
        created
          ? `${created.label} · id ${created.id} · ERP ${created.erp_username}`
          : '备注用来区分不同设备或用途，例如「笔记本」「办公室台式机」。'
      }
      onClose={onClose}
      dismissible={created === null}
      size={created ? 'lg' : 'md'}
    >
      {created ? (
        <TokenResultBody created={created} onDone={onClose} />
      ) : (
        <form className="space-y-4" onSubmit={submit}>
          <Field
            label="备注"
            required
            autoFocus
            maxLength={80}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="如：笔记本"
          />

          {!bound && (
            <Alert kind="info">还没有绑定 ERP 账号，创建前请先在上面完成绑定。</Alert>
          )}
          {error && <Alert kind="error">{error}</Alert>}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" busy={busy} disabled={!bound}>
              创建
            </Button>
          </div>
        </form>
      )}
    </Modal>
  )
}
