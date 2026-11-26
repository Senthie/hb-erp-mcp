import { ApiError } from '../api'

/** 把接口错误转成一句可直接展示的话（带 hint 时拼在后面）。 */
export function describeError(
  error: unknown,
  fallback = '操作失败，请稍后重试',
): string {
  if (error instanceof ApiError) {
    return error.hint ? `${error.message}（${error.hint}）` : error.message
  }
  return fallback
}

/** 后端给的是 ISO 时间；空值统一显示成占位符。 */
export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
