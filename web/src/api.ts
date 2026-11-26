export interface HealthInfo {
  status: string
  service?: string
  version?: string
  keys?: number
  store?: string
  signup_enabled?: boolean
  register_secret_required?: boolean
}

export interface ErpBinding {
  bound: boolean
  username: string | null
  user_id: string | null
  bound_at: string | null
}

export interface PlatformUser {
  id: string
  username: string
  role: 'user' | 'admin'
  is_admin: boolean
  disabled: boolean
  created_at: string
  last_login_at: string | null
  erp: ErpBinding
}

export interface McpToken {
  id: string
  label: string
  erp_username: string
  created_at: string
  last_used: string | null
  revoked: boolean
}

export interface McpSnippet {
  url: string
  config: Record<string, unknown>
  config_json: string
  prompt: string
}

export interface NewToken {
  token: string
  id: string
  label: string
  erp_username: string
  mcp: McpSnippet
  note: string
}

export interface AccountState {
  user: PlatformUser
  tokens: McpToken[]
}

export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly hint?: string

  constructor(message: string, status: number, code?: string, hint?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.hint = hint
  }
}

interface ErrorBody {
  error?: string
  message?: string
  hint?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError('连不上网关，请确认服务已启动', 0, 'network_error')
  }

  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  if (!response.ok) {
    const body = (data ?? {}) as ErrorBody
    throw new ApiError(
      body.message || body.error || `请求失败（HTTP ${response.status}）`,
      response.status,
      body.error,
      body.hint,
    )
  }
  return (data ?? {}) as T
}

export function health(): Promise<HealthInfo> {
  return request<HealthInfo>('/healthz')
}

export const platform = {
  me(): Promise<AccountState & { session: { expires_at: string } }> {
    return request('/v1/platform/me')
  },
  login(username: string, password: string): Promise<AccountState> {
    return request('/v1/platform/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  },

  register(username: string, password: string): Promise<AccountState> {
    return request('/v1/platform/register', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  },

  logout(): Promise<void> {
    return request('/v1/platform/logout', { method: 'POST' })
  },

  bindErp(username: string, password: string) {
    return request<AccountState & { revoked_tokens: number }>('/v1/platform/erp/bind', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  },

  tokens(): Promise<{ erp: ErpBinding; tokens: McpToken[] }> {
    return request('/v1/platform/tokens')
  },

  createToken(label: string): Promise<NewToken> {
    return request('/v1/platform/tokens', {
      method: 'POST',
      body: JSON.stringify({ label }),
    })
  },

  revokeToken(id: string): Promise<{ revoked: boolean; id: string }> {
    return request(`/v1/platform/tokens/${encodeURIComponent(id)}/revoke`, {
      method: 'POST',
    })
  },
}

// ══════════════════ 管理员后台 ══════════════════

export interface AdminUser extends PlatformUser {
  token_count: number
  token_total: number
  session_count: number
  tokens: McpToken[]
}

export interface AuditItem {
  id: number | null
  created_at: string
  action: string
  action_label: string
  result: 'success' | 'failure' | string
  actor_id: string | null
  actor_name: string | null
  actor_type: string
  target_type: string | null
  target_id: string | null
  status_code: number | null
  ip: string | null
  user_agent: string | null
  detail: Record<string, unknown>
}

export interface CallItem {
  id: number | null
  created_at: string
  tool: string
  status: 'success' | 'failure' | string
  duration_ms: number
  actor_id: string | null
  actor_name: string | null
  actor_type: string
  token_id: string | null
  erp_username: string | null
  ip: string | null
  user_agent: string | null
  error: string | null
  arguments: Record<string, string>
  owner_name?: string | null
}

export interface AdminOverview {
  admin: PlatformUser
  users: {
    total: number
    sampled: number
    admins: number
    disabled: number
    erp_bound: number
  }
  tokens: { active: number; total: number }
  calls: { total: number }
  actions: Array<{ value: string; label: string }>
  results: string[]
}

export interface AdminUserPage {
  total: number
  limit: number
  offset: number
  items: AdminUser[]
}

export interface AuditPage {
  limit: number
  offset: number
  has_more: boolean
  items: AuditItem[]
}

export interface AuditQuery {
  action?: string
  actor?: string
  result?: string
  since?: string
  until?: string
  limit?: number
  offset?: number
}

export interface CallPage {
  limit: number
  offset: number
  has_more: boolean
  items: CallItem[]
}

export interface CallQuery {
  tool?: string
  actor?: string
  status?: string
  since?: string
  until?: string
  limit?: number
  offset?: number
}

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const admin = {
  overview(): Promise<AdminOverview> {
    return request('/v1/admin/overview')
  },

  users(query: string, limit = 50, offset = 0): Promise<AdminUserPage> {
    return request(`/v1/admin/users${queryString({ query, limit, offset })}`)
  },

  user(id: string): Promise<AdminUser & { recent_audit: AuditItem[] }> {
    return request(`/v1/admin/users/${encodeURIComponent(id)}`)
  },

  updateUser(
    id: string,
    patch: { role?: 'user' | 'admin'; disabled?: boolean },
  ): Promise<{ user: AdminUser; killed_sessions: number; revoked_tokens: number }> {
    return request(`/v1/admin/users/${encodeURIComponent(id)}/update`, {
      method: 'POST',
      body: JSON.stringify(patch),
    })
  },

  unbindErp(id: string): Promise<{ user: AdminUser; revoked_tokens: number }> {
    return request(`/v1/admin/users/${encodeURIComponent(id)}/unbind-erp`, {
      method: 'POST',
    })
  },

  revokeTokens(id: string): Promise<{ user: AdminUser; revoked_tokens: number }> {
    return request(`/v1/admin/users/${encodeURIComponent(id)}/revoke-tokens`, {
      method: 'POST',
    })
  },

  deleteUser(id: string): Promise<{ deleted: boolean; id: string }> {
    return request(`/v1/admin/users/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  audit(query: AuditQuery): Promise<AuditPage> {
    return request(`/v1/admin/audit${queryString(query as Record<string, string | number | undefined>)}`)
  },

  calls(query: CallQuery): Promise<CallPage> {
    return request(`/v1/admin/calls${queryString(query as Record<string, string | number | undefined>)}`)
  },
}
