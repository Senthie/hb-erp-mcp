"""认证 + 权限号（QXID）探测。

登录协议（实测）：
  1. GET /Admin/Login.aspx?JudgeUrl=1  → 取 __VIEWSTATE / __VIEWSTATEGENERATOR
  2. GET {captcha_host}/ValidateCode.aspx → 验证码图片（**注意是另一个端口**）
  3. POST /Admin/Login.aspx            → 302 即成功
  4. 服务端下发 `LoginUserID=LoginUserID=2474` 这类「值里带前缀」的 Cookie，
     真实 ID 需要 split('=') 之后取右半段。

QXID 探测（实测）：
  GET /Admin/indexAjax.ashx?FunName=GetMenuTree&Value1=<userID>&Value2=
  返回的是 JS 字面量（键未加引号），里面每个节点形如
      url:'BaoJia/BJ_KHBJXQ/Add.aspx?QXID=873&MenuTree=1&LoginUserID=2474'
  用正则取 url 即可得到 路径 → QXID 的映射。
  注意：POST 到该地址返回空，必须用 GET + 查询参数。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from .config import ErpConfig
from .errors import (
    AuthError,
    CaptchaRequiredError,
    ConfigError,
    ErpError,
    NetworkError,
    QxidNotFoundError,
    UpstreamError,
    brief,
)
from .session import ErpClient

LOGIN_PATH = "/Admin/Login.aspx"
CAPTCHA_PATH = "/ValidateCode.aspx"
INDEX_PATH = "/Admin/index.html"
MENU_AJAX_PATH = "/Admin/indexAjax.ashx"

#: 菜单探测不可用时（例如拿不到用户 ID）的兜底 QXID。
#: 实测：只要会话有效，业务页不带 LoginUserID 也能正常查询。
DEFAULT_QXIDS = {
    "baojia/bj_khbjxq/add.aspx": "873",
    "baojia/bj_khbjxq/index.aspx": "876",
    "renshi/rs_kqda_m/add.aspx": "2553",
}

_MENU_URL_RE = re.compile(r"""url\s*:\s*['"]([^'"]+)['"]""", re.I)
_QXID_RE = re.compile(r"[?&]QXID=(\d+)", re.I)

#: captcha 解码器签名：(图片字节) -> 识别文本
CaptchaSolver = Callable[[bytes], str]


def normalize_page_path(path: str) -> str:
    """把各种写法的页面路径归一到同一把 key。

    '/Admin/BaoJia/BJ_KHBJXQ/Add.aspx?QXID=1&MenuTree=1' -> 'baojia/bj_khbjxq/add.aspx'
    """
    p = (path or "").split("?", 1)[0].strip().lower().replace("\\", "/")
    p = p.lstrip("/")
    if p.startswith("admin/"):
        p = p[len("admin/"):]
    return p


def parse_menu_urls(text: str) -> dict[str, str]:
    """从菜单 JS 字面量里抽取 {页面路径: QXID}。"""
    mapping: dict[str, str] = {}
    for url in _MENU_URL_RE.findall(text or ""):
        m = _QXID_RE.search(url)
        if not m:
            continue
        key = normalize_page_path(url)
        if key:
            mapping.setdefault(key, m.group(1))
    return mapping


class AuthManager:
    """负责登录态与权限号，被所有工具复用。"""

    def __init__(self, client: ErpClient, *, interactive: bool = True) -> None:
        self.client = client
        self.cfg: ErpConfig = client.cfg
        self.interactive = interactive
        self._qxid_map: dict[str, str] | None = None
        self._solver: CaptchaSolver | None = None
        self._ocr_failed = False

    # ══════════════════ 持久化 ══════════════════
    @property
    def cookie_file(self) -> Path:
        name = self.cfg.username or "default"
        return self.cfg.cookie_dir / f"{name}.json"

    @property
    def qxid_cache_file(self) -> Path:
        name = self.cfg.username or "default"
        return self.cfg.cookie_dir / f"qxid-{name}.json"

    def save_cookies(self) -> None:
        self.cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.client.capture_cookies()
        payload = {
            "cookies": self.client.cookies,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "username": self.cfg.username,
            "host": self.cfg.host,
        }
        self.cookie_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_cookies(self) -> bool:
        p = self.cookie_file
        if not p.is_file():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return False
        cookies = data.get("cookies") or {}
        if not cookies:
            return False
        self.client.cookies = dict(cookies)
        self.client.reset_session()
        self._log(f"    📦 已加载 {self.cfg.username} 的 cookies（保存于 {data.get('saved_at', '未知')}）")
        return True

    # ══════════════════ 会话有效性 ══════════════════
    def is_logged_in(self) -> bool:
        try:
            resp = self.client.get(INDEX_PATH)
        except Exception:
            return False
        if "Login.aspx" in resp.url:
            return False
        text = resp.text or ""
        if "txtPass" in text and "txtUsername" in text:
            return False
        return "ERP" in text or len(text) > 2000

    # ══════════════════ 登录 ══════════════════
    def ensure_login(self, *, force: bool = False, captcha: str | None = None,
                     timeout: float | None = None) -> None:
        if not force and self.load_cookies() and self.is_logged_in():
            self._log("    ✅ 会话有效，跳过登录")
            return
        if not self.cfg.username or not self.cfg.password:
            raise ConfigError("缺少 ERP 账号或密码（设置 ERP_USERNAME / ERP_PASSWORD 或 .env）")
        self.login(captcha=captcha, timeout=timeout)

    def login(self, *, captcha: str | None = None, timeout: float | None = None,
              attempts: int = 2) -> None:
        self._log(f"🔐 正在登录 ERP（用户 {self.cfg.username}）…")
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            code = captcha if captcha else self._solve_captcha()
            try:
                self._post_login(code, timeout)
                self.save_cookies()
                self._log("    ✅ 登录成功")
                return
            except AuthError as exc:
                last_error = exc
                self._log(f"    ⚠️  第 {attempt} 次登录失败：{exc}")
                if captcha:  # 显式指定的验证码只试一次
                    break
                captcha = None
        raise AuthError(f"登录失败：{last_error}")

    def _post_login(self, check_code: str | None, timeout: float | None) -> None:
        stamp = int(time.time() * 1000)
        try:
            resp = self.client.get(f"{LOGIN_PATH}?JudgeUrl=1&time={stamp}",
                                   timeout=self._t(timeout))
        except NetworkError as exc:
            raise NetworkError(self._net_hint("打开登录页", exc)) from exc

        if resp.status_code != 200:
            raise NetworkError(
                f"登录页不可访问（HTTP {resp.status_code}）：{self.client.url(LOGIN_PATH)}\n"
                + self._hint_block()
            )

        from .aspnet import extract_viewstate

        vs = extract_viewstate(resp.text)
        if not vs:
            raise AuthError("登录页结构异常，未取到 __VIEWSTATE")

        form = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": vs.get("__VIEWSTATEGENERATOR", ""),
            # 必须带上：ERP 开了 <pages enableEventValidation="true">，
            # 缺了它无论验证码对不对都会 500「回发或回调参数无效」
            "__EVENTVALIDATION": vs.get("__EVENTVALIDATION", ""),
            "__SCROLLPOSITIONX": "0",
            "__SCROLLPOSITIONY": "0",
            "txtUsername": self.cfg.username,
            "txtPass": self.cfg.password,
            "CheckCode": check_code or "",
            "btnLogin.x": "44",
            "btnLogin.y": "19",
            "lblLanguage": "0",
            "txtCbtn": "",
            "txtUseID": "",
        }
        try:
            resp = self.client.post(
                f"{LOGIN_PATH}?JudgeUrl=1&time={stamp}",
                data=form,
                timeout=self._t(timeout),
                allow_redirects=False,
            )
        except NetworkError as exc:
            raise NetworkError(self._net_hint("提交登录", exc)) from exc

        if resp.status_code == 302:
            # 302 只是「回发被接受」，不代表真的取到了会话：再验证一次
            self.client.capture_cookies()
            if not self.user_id:
                # 有的部署把 Set-Cookie 放在跳转目标上，跟一次补齐
                location = resp.headers.get("Location") or ""
                if location and "Login" not in location:
                    self.client.get(location)
                    self.client.capture_cookies()
            if not self.user_id and not self.is_logged_in():
                raise AuthError("登录未生效（未取得会话 cookie，验证码或账号可能不正确）")
            if not self.user_id:
                self._log("    ⚠️  已登录但未取得用户 ID，QXID 将使用配置/默认值")
            return
        text = resp.text or ""
        if "验证码" in text or "CheckCode" in text:
            raise AuthError("验证码错误")
        if resp.status_code >= 500:
            # 不重试：多半是地址/端口填错，或 ERP 内部报错，重试无意义
            raise NetworkError(
                f"登录被拒绝（HTTP {resp.status_code}）：{brief(text)}\n{self._hint_block()}"
            )
        raise AuthError(f"登录被拒绝（HTTP {resp.status_code}）：{brief(text)}")

    # ══════════════════ 验证码 ══════════════════
    def set_captcha_solver(self, solver: CaptchaSolver | None) -> None:
        self._solver = solver

    def _solve_captcha(self) -> str:
        image = self._fetch_captcha()
        if self._solver is not None:
            return self._solver(image)

        ocr_text = self._try_ocr(image)
        if ocr_text:
            self._log(f"    📸 OCR 验证码：{ocr_text}")
            return ocr_text
        return self._ask_human(image)

    def _fetch_captcha(self) -> bytes:
        # 验证码在独立端口/独立会话上，与 ERP 主站 cookie 无关
        import requests

        try:
            resp = requests.get(
                urljoin(self.cfg.captcha_host.rstrip("/") + "/", CAPTCHA_PATH.lstrip("/")),
                headers={"User-Agent": self.client.session.headers["User-Agent"],
                         "Connection": "close"},
                timeout=(self.cfg.timeout_connect, self.cfg.timeout_read / 2),
            )
        except requests.RequestException as exc:
            # 取不到验证码是**网络/端口**问题，不是账号问题：
            # 必须抛 NetworkError（服务端映射成 502），否则会被当成 invalid_credentials
            # 并白白消耗「连续失败 10 次锁定」的额度。
            raise NetworkError(f"获取验证码失败：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"获取验证码失败：{exc}") from exc
        if resp.status_code != 200 or not resp.content:
            raise AuthError(f"获取验证码失败（HTTP {resp.status_code}）")
        return resp.content

    def _try_ocr(self, image: bytes) -> str:
        if self._ocr_failed:
            return ""
        if self._solver is None:
            try:
                import ddddocr  # 已是默认依赖；源码环境缺失时退回人工输入
            except ImportError:
                self._ocr_failed = True
                self._log("    ⚠️  验证码识别组件缺失（ddddocr 未安装），需要人工输入")
                return ""
            self._log("    📸 初始化验证码识别器…")
            try:
                ocr = ddddocr.DdddOcr(show_ad=False)
            except Exception as exc:  # noqa: BLE001
                self._ocr_failed = True
                self._log(f"    ⚠️  初始化 OCR 失败：{exc}")
                return ""
            self._solver = lambda data: ocr.classification(data)
        try:
            text = (self._solver(image) or "").strip()
        except Exception as exc:  # noqa: BLE001
            self._log(f"    ⚠️  验证码识别异常：{exc}")
            return ""
        return text

    def _ask_human(self, image: bytes) -> str:
        path = self.cfg.cookie_dir / "captcha.png"
        self.cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        if not self.interactive:
            raise CaptchaRequiredError(
                f"需要验证码：已保存到 {path}（可安装 erp-tools[ocr] 自动识别）",
                image_path=str(path),
            )
        print(f"\n📸 验证码已保存：{path}", file=sys.stderr)
        code = input("   请输入验证码：").strip()
        if not code:
            raise AuthError("未输入验证码")
        return code

    # ══════════════════ 用户 ID ══════════════════
    @property
    def user_id(self) -> str | None:
        """`LoginUserID=LoginUserID=2474` → '2474'。"""
        self.client.capture_cookies()
        for name in ("LoginUserID", "UserID"):
            raw = self.client.cookies.get(name, "")
            if raw:
                return raw.split("=", 1)[1] if "=" in raw else raw
        return None

    # ══════════════════ QXID 探测 ══════════════════
    def qxid_map(self, *, refresh: bool = False) -> dict[str, str]:
        if self._qxid_map is not None and not refresh:
            return self._qxid_map
        if not refresh:
            cached = self._load_qxid_cache()
            if cached:
                self._qxid_map = cached
                return cached

        uid = self.user_id
        if not uid:
            raise AuthError("无法获取用户 ID（cookie 不完整），无法探测 QXID")
        resp = self.client.get(
            f"{MENU_AJAX_PATH}?FunName=GetMenuTree&Value1={uid}&Value2=",
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": self.client.url(INDEX_PATH)},
        )
        mapping = parse_menu_urls(resp.text)
        if not mapping:
            raise UpstreamError("菜单接口没有返回任何菜单项，可能权限不足或接口变更",
                                status=resp.status_code, body=brief(resp.text))
        self._qxid_map = mapping
        self._save_qxid_cache(mapping)
        return mapping

    def qxid_for(self, page_path: str, *, refresh: bool = False) -> str:
        """取页面 QXID：优先菜单探测，探测不到时回退到内置默认值。"""
        key = normalize_page_path(page_path)
        fallback = DEFAULT_QXIDS.get(key)

        try:
            mapping = self.qxid_map(refresh=refresh)
        except ErpError as exc:
            if fallback:
                self._log(f"    ⚠️  QXID 探测失败（{exc}）；沿用默认值 {fallback}")
                return fallback
            raise

        if key in mapping:
            return mapping[key]
        if fallback:
            self._log(f"    ⚠️  菜单里没有 {page_path}；沿用默认 QXID {fallback}")
            return fallback
        raise QxidNotFoundError(f"当前账号菜单里没有页面 {page_path}（可能是权限不足）")

    def _load_qxid_cache(self) -> dict[str, str]:
        p = self.qxid_cache_file
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {str(k): str(v) for k, v in (data.get("qxid") or {}).items()}

    def _save_qxid_cache(self, mapping: dict[str, str]) -> None:
        try:
            self.cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
            self.qxid_cache_file.write_text(
                json.dumps({"probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "qxid": mapping}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ══════════════════ 杂项 ══════════════════
    def _hint_block(self) -> str:
        """把配置疑点整理成多行提示（账号密码缺失不算错误，仅提示）。"""
        hints = [h for h in self.cfg.validate() if "账号或密码" not in h]
        if not hints:
            return f"  请确认该地址可访问：{self.cfg.host}"
        return "  配置疑点：\n" + "\n".join(f"   · {h}" for h in hints)

    def _net_hint(self, action: str, exc: Exception) -> str:
        return f"{action}失败：{exc}\n{self._hint_block()}"

    def _t(self, timeout: float | None) -> tuple[float, float]:
        read = timeout or self.cfg.timeout_read
        return (self.cfg.timeout_connect, read)

    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)


def ensure_authenticated(cfg: ErpConfig, *, force_login: bool = False,
                        interactive: bool = True) -> AuthManager:
    """一步到位：建 client → 登录 → 返回 AuthManager（供工具直接使用）。"""
    client = ErpClient(cfg)
    auth = AuthManager(client, interactive=interactive)
    if cfg.require_auth or force_login:
        auth.ensure_login(force=force_login)
    return auth
