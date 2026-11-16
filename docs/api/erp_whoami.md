# `erp_whoami` — 当前身份与会话状态

> 菜单：—（基础设施，不挂在菜单上）
> 页面：—｜CLI：`erp login` / `erp doctor`｜**只读**

## 用途

确认「这把 api-key 是谁的、ERP 会话还活着吗」。AI 客户端最好先调它，
再决定要不要让用户重新登录。

## 参数

无。

## 返回

```json
{
  "username": "21146",
  "user_id": "2474",
  "erp_alive": true,
  "key_created_at": "2026-11-16 09:00:00"
}
```

## 注意

- `erp_alive=false` 表示 ERP 会话过期 → 需要重新 `POST /v1/auth/login` 换新 key。
- `user_id` 是 ERP 内部的 `LoginUserID`（cookie 里那个），与工号不同。
- api-key **没有过期时间**，寿命等于绑定的 ERP 会话；主动作废用 `POST /v1/auth/logout`。
