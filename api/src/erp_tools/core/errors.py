"""异常体系：让上层能区分「重试有意义」和「重试没用」。"""

from __future__ import annotations


class ErpError(Exception):
    """所有 ERP 相关异常的基类。"""

    retryable = False


class ConfigError(ErpError):
    """配置缺失/非法（缺账号、主机不可达配置错误等）。"""


class NetworkError(ErpError):
    """连接/超时等网络层问题，可以重试。"""

    retryable = True


class AuthError(ErpError):
    """登录失败（账号密码错误、验证码连续失败）。"""


class CaptchaRequiredError(AuthError):
    """需要人工输入验证码：取不到 OCR，也不允许交互。"""

    def __init__(self, message: str, image_path: str | None = None) -> None:
        super().__init__(message)
        self.image_path = image_path


class UpstreamError(ErpError):
    """ERP 返回了错误（HTTP 5xx、ASP.NET 请求校验拒绝、业务报错）。"""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def retryable(self) -> bool:  # type: ignore[override]
        return bool(self.status and self.status >= 500)


class QxidNotFoundError(ErpError):
    """菜单里找不到目标页面的权限号（通常是账号权限不足）。"""


class ParseError(ErpError):
    """页面结构与预期不符（多半是 ERP 改版）。"""


def brief(text: str, limit: int = 200) -> str:
    """把 HTML/响应体压成一行短文本，便于放进异常信息。"""
    flat = " ".join((text or "").split())
    return flat[:limit]
