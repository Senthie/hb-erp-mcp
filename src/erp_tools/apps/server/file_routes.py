"""附件下载代理路由。

ERP 的附件直链在内网 `http://192.168.1.199:8887/Admin/FuJian/...`，
换网段就不可达。这个路由让**网关自己去取**再回给客户端：

```
GET /v1/files?path=/Admin/FuJian/<模块>/Picture/<guid>.zip&t=<exp>.<hmac>
```

鉴权靠 URL 里的签名（不含 api-key，可贴浏览器），所以本路由在中间件里是**公开路径**，
安全性由 `runtime.file_proxy.verify()` 承担：

- 只接受 `/Admin/FuJian/` 开头的路径（杜绝 SSRF 到任意地址）
- 令牌绑定路径 + 过期时间，默认 30 分钟
- 目标主机只在 `ERP_HOST` / 验证码端口之间回退，不接受调用方指定 host
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...core.errors import UpstreamError
from ...runtime import file_proxy
from ...tools.attachments import AttachmentPageService
from .state import ServerState

_log = logging.getLogger("erp_tools.server.files")


def _deny(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


def _fetch(state: ServerState, path: str) -> tuple[bytes, str, str]:
    """按 `ERP_HOST` → 验证码端口的顺序取文件（与原直链行为一致）。"""
    import requests

    last = ""
    for candidate in AttachmentPageService.candidate_urls(
            f"http://placeholder{path}", state.erp):
        try:
            resp = requests.get(candidate, timeout=180)
        except requests.RequestException as exc:
            last = f"{candidate} → {exc}"
            continue
        if resp.status_code == 200 and resp.content:
            return resp.content, resp.headers.get("Content-Type", ""), candidate
        last = f"{candidate} → HTTP {resp.status_code}"
    raise UpstreamError("附件下载失败（已尝试全部候选地址）", 404, last)


def build_file_routes(state: ServerState) -> list[Route]:
    async def files(request: Request) -> Response:
        path = file_proxy.path_of(request.query_params.get("path", ""))
        token = request.query_params.get("t", "")

        if not path:
            return _deny(400, "missing_path", "缺少 path 参数（附件在 ERP 里的路径）")
        if not file_proxy.is_proxyable(path):
            return _deny(403, "path_not_allowed",
                         "只代理 ERP 附件目录 " + file_proxy.ALLOWED_PREFIX.rstrip("/"),
                         path=path)
        try:
            ok = file_proxy.verify(path, token)
        except RuntimeError:
            ok = False
        if not ok:
            return _deny(403, "bad_signature",
                         "链接无效或已过期；请重新调用 erp_attachment(with_urls=true) 取新链接",
                         ttl_seconds=file_proxy.ttl_default())

        try:
            content, ctype, used = await anyio.to_thread.run_sync(
                lambda: _fetch(state, path)
            )
        except UpstreamError as exc:
            _log.warning("附件代理失败 %s：%s", path, exc)
            return _deny(502, "upstream_failed", "ERP 取不到这个附件（可能已被删除）",
                         path=path)

        name = file_proxy.download_name(path, request.query_params.get("n", ""))
        # RFC 5987：中文文件名用 filename* 传；filename 给老客户端兜底
        # （中文只能变问号，干脆退回物理文件名，别给出一串 "????"）
        ascii_name = name.encode("ascii", "replace").decode().replace('"', "")
        if "?" in ascii_name:
            ascii_name = file_proxy.filename_of(path)
        headers = {
            # RFC 5987：中文文件名用 filename* 传，filename 给老客户端兜底
            "content-disposition":
                f'inline; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(name, safe='')}",
            "cache-control": "private, max-age=60",
            "x-erp-source": used,
        }
        if request.method == "HEAD":
            headers["content-length"] = str(len(content))
            return Response(b"", media_type=ctype or "application/octet-stream",
                            headers=headers)
        return Response(content, media_type=ctype or "application/octet-stream",
                        headers=headers)

    return [Route("/v1/files", files, methods=["GET", "HEAD"])]


__all__ = ["build_file_routes"]
