"""core 层：与业务无关的 ERP 通用能力。"""

from .config import ErpConfig
from .errors import (
    AuthError,
    CaptchaRequiredError,
    ConfigError,
    ErpError,
    NetworkError,
    ParseError,
    QxidNotFoundError,
    UpstreamError,
)
from .limiter import RateLimiter
from .session import ErpClient

__all__ = [
    "ErpConfig",
    "ErpClient",
    "RateLimiter",
    "ErpError",
    "ConfigError",
    "NetworkError",
    "AuthError",
    "CaptchaRequiredError",
    "UpstreamError",
    "QxidNotFoundError",
    "ParseError",
]
