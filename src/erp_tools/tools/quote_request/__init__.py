"""报价需求工具。"""

from .parser import parse_quote_page
from .tool import PAGE_PATH, QuoteRequestTool

__all__ = ["QuoteRequestTool", "parse_quote_page", "PAGE_PATH"]
