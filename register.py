# -*- coding: utf-8 -*-
import asyncio, json, logging, os, random, re, string, sys, time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

import requests
from requests.adapters import HTTPAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
PROXY = os.environ.get("PROXY", "")
AUTH_HOME_URL = "https://auth.business.gemini.google/login"
LOGIN_URL = "https://auth.business.gemini.google/login?continueUrl=https://business.gemini.google/"


def is_business_home_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.netloc == "business.gemini.google" and parsed.path.startswith(
        "/home/cid/"
    )


def is_account_chooser_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return (
        parsed.netloc == "auth.business.gemini.google"
        and parsed.path == "/account-chooser"
    )


def is_verification_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return (
        parsed.netloc == "accountverification.business.gemini.google"
        and "verify-oob-code" in parsed.path
    )


def parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_session(pool_size: int = 10) -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"Connection": "keep-alive"})
    return session


def maybe_apply_proxy(session: requests.Session, email_api: bool = False) -> None:
    if email_api and not parse_bool(os.environ.get("PROXY_EMAIL", ""), False):
        return
    proxy = os.environ.get("PROXY", "") or PROXY
    if proxy and not session.proxies:
        session.proxies = {"http": proxy, "https": proxy}


def extract_code(content: str) -> Optional[str]:
    if not content:
        return None
    cleaned = str(content).replace("=\r\n", "").replace("=\n", "").replace("=3D", "=")

    contextual_patterns = [
        r"(?:一次性验证码(?:为|是)?|验证码(?:为|是)?|verification\s*code(?:\s*is)?|one[-\s]?time\s*code(?:\s*is)?|passcode(?:\s*is)?|security\s*code(?:\s*is)?)\s*[:：]?\s*([A-Z0-9]{6,8})",
        r"verification-code[^>]*>([A-Z0-9]{6,8})<",
        r">\s*([A-Z0-9]{6,8})\s*</span>",
    ]
    for pattern in contextual_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            code = match.group(1).upper().strip()
            if 6 <= len(code) <= 8 and code.isalnum():
                return code

    blacklist = {
        "GEMINI",
        "GOOGLE",
        "VERIFY",
        "CODE",
        "EMAIL",
        "LOGIN",
        "SIGNIN",
        "SECURE",
        "ACCESS",
        "NOTICE",
        "PLEASE",
        "TEAM",
        "ACCOUNT",
    }
    fallback_patterns = [r"\b([A-Z0-9]{6,8})\b", r"\b(\d{6})\b"]
    for pattern in fallback_patterns:
        for match in re.finditer(pattern, cleaned, re.IGNORECASE):
            code = match.group(1).upper().strip()
            if code in blacklist:
                continue
            if not code.isalnum():
                continue
            if not any(ch.isdigit() for ch in code):
                continue
            if 6 <= len(code) <= 8:
                return code
    return None


def parse_expiration(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def random_name(length: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=length))


def load_file_config() -> Dict:
    config_path = (os.environ.get("CONFIG_PATH") or "").strip()
    candidates = [config_path] if config_path else ["config.local.json", "config.json"]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                logger.info(f"已加载配置文件: {path}")
                return data
            logger.warning(f"配置文件格式不是对象: {path}")
        except Exception as exc:
            logger.warning(f"读取配置文件失败 {path}: {exc}")
    return {}


def pick_config(
    config_data: Dict, env_name: str, config_key: str, default=None, value_type=str
):
    env_value = os.environ.get(env_name)
    if env_value is not None and str(env_value).strip() != "":
        raw_value = env_value
    elif config_key in config_data and config_data.get(config_key) not in (None, ""):
        raw_value = config_data.get(config_key)
    else:
        return default
    try:
        if value_type is bool:
            if isinstance(raw_value, bool):
                return raw_value
            return parse_bool(
                str(raw_value), bool(default) if default is not None else False
            )
        if value_type is int:
            return int(raw_value)
        if value_type is float:
            return float(raw_value)
        if value_type is str:
            return str(raw_value).strip()
        return value_type(raw_value)
    except Exception:
        return default


@dataclass
class CredentialData:
    email: str = ""
    csesidx: str = ""
    config_id: str = ""
    c_ses: str = ""
    c_oses: str = ""
    mail_provider: str = ""
    mail_address: str = ""
    mail_password: str = ""
    mail_base_url: str = ""
    mail_api_key: str = ""
    mail_domain: str = ""

    def is_complete(self) -> bool:
        return all([self.email, self.csesidx, self.config_id, self.c_ses, self.c_oses])

    def to_dict(self, existing: Optional[Dict] = None) -> Dict:
        payload = dict(existing or {})
        expire_hours = int(os.environ.get("ACCOUNT_EXPIRE_HOURS", "20"))
        payload.update(
            {
                "id": self.email,
                "csesidx": self.csesidx,
                "config_id": self.config_id,
                "secure_c_ses": self.c_ses,
                "host_c_oses": self.c_oses,
                "expires_at": (datetime.now() + timedelta(hours=expire_hours)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
        for key in [
            "mail_provider",
            "mail_address",
            "mail_password",
            "mail_base_url",
            "mail_api_key",
            "mail_domain",
        ]:
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload


class BaseMailProvider:
    provider_name = ""

    def __init__(self):
        self.session = build_session(5)
        self.email = ""

    def create_email(self) -> str:
        raise NotImplementedError

    def prepare_existing_account(self, account: Dict) -> str:
        raise NotImplementedError

    def check_verification_code(
        self, email: str, max_retries: int = 20, since_time: Optional[datetime] = None
    ) -> Optional[str]:
        raise NotImplementedError

    def export_account_fields(self) -> Dict:
        raise NotImplementedError


class WorkerMailProvider(BaseMailProvider):
    provider_name = "worker"

    def __init__(self, worker_domain: str, email_domain: str, admin_password: str):
        super().__init__()
        self.worker_domain = worker_domain.strip()
        self.email_domain = email_domain.strip()
        self.admin_password = admin_password.strip()

    def create_email(self) -> str:
        maybe_apply_proxy(self.session, email_api=True)
        username = f"{''.join(random.choices(string.ascii_lowercase, k=4))}{''.join(random.choices(string.digits, k=2))}{''.join(random.choices(string.ascii_lowercase, k=3))}"
        url = f"https://{self.worker_domain}/admin/new_address"
        headers = {
            "Content-Type": "application/json",
            "x-admin-auth": self.admin_password,
        }
        payload = {"enablePrefix": True, "name": username, "domain": self.email_domain}
        for attempt in range(3):
            try:
                response = self.session.post(
                    url, json=payload, headers=headers, timeout=30
                )
                if response.status_code == 200:
                    data = response.json()
                    self.email = data.get("address", f"{username}@{self.email_domain}")
                    logger.info(f"邮箱创建成功: {self.email}")
                    return self.email
            except Exception as exc:
                logger.warning(f"创建 worker 邮箱失败 ({attempt + 1}/3): {exc}")
            time.sleep((2**attempt) + 1)
        raise RuntimeError("worker 邮箱创建失败")

    def prepare_existing_account(self, account: Dict) -> str:
        self.email = (account.get("mail_address") or account.get("id") or "").strip()
        if not self.email:
            raise RuntimeError("缺少可刷新的邮箱地址")
        return self.email

    def check_verification_code(
        self, email: str, max_retries: int = 20, since_time: Optional[datetime] = None
    ) -> Optional[str]:
        maybe_apply_proxy(self.session, email_api=True)
        url = f"https://{self.worker_domain}/admin/mails"
        headers = {"x-admin-auth": self.admin_password}
        params = {"limit": 5, "offset": 0, "address": email}
        for index in range(max_retries):
            try:
                response = self.session.get(
                    url, params=params, headers=headers, timeout=30
                )
                if response.status_code == 200:
                    for item in response.json().get("results", []) or []:
                        code = extract_code(item.get("raw", ""))
                        if code:
                            logger.info(f"获取到验证码: {code}")
                            return code
            except Exception as exc:
                logger.warning(f"读取 worker 验证码失败: {exc}")
            logger.info(f"等待 worker 验证码... ({index + 1}/{max_retries})")
            time.sleep(3)
        return None

    def export_account_fields(self) -> Dict:
        return {"mail_provider": self.provider_name, "mail_address": self.email}


class MoemailProvider(BaseMailProvider):
    provider_name = "moemail"

    def __init__(self, base_url: str, api_key: str = "", domain: str = ""):
        super().__init__()
        self.base_url = (base_url or "https://moemail.app").rstrip("/")
        self.api_key = api_key.strip()
        self.domain = domain.strip()
        self.email_id = ""
        self.available_domains: List[str] = []

    def _request(self, method: str, url: str, **kwargs):
        maybe_apply_proxy(self.session, email_api=True)
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.api_key:
            headers.setdefault("X-API-Key", self.api_key)
        headers.setdefault("Content-Type", "application/json")
        return self.session.request(
            method, url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs
        )

    def _domains(self) -> List[str]:
        if self.available_domains:
            return self.available_domains
        try:
            response = self._request("GET", f"{self.base_url}/api/config")
            if response.status_code == 200:
                text = str(response.json().get("emailDomains", ""))
                self.available_domains = [
                    item.strip() for item in text.split(",") if item.strip()
                ]
        except Exception as exc:
            logger.warning(f"获取 moemail 域名失败: {exc}")
        if not self.available_domains:
            self.available_domains = ["moemail.app"]
        return self.available_domains

    def create_email(self) -> str:
        selected_domain = self.domain or random.choice(self._domains())
        local_part = f"t{str(int(time.time()))[-4:]}{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"
        response = self._request(
            "POST",
            f"{self.base_url}/api/emails/generate",
            json={"name": local_part, "expiryTime": 0, "domain": selected_domain},
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"moemail 创建失败: HTTP {response.status_code}")
        data = response.json() if response.content else {}
        self.email = str(data.get("email") or "").strip()
        self.email_id = str(data.get("id") or "").strip()
        if not self.email or not self.email_id:
            raise RuntimeError("moemail 返回缺少 email 或 id")
        logger.info(f"Moemail 创建成功: {self.email}")
        return self.email

    def prepare_existing_account(self, account: Dict) -> str:
        self.base_url = (account.get("mail_base_url") or self.base_url).rstrip("/")
        self.api_key = str(account.get("mail_api_key") or self.api_key).strip()
        self.domain = str(account.get("mail_domain") or self.domain).strip()
        self.email = (account.get("mail_address") or account.get("id") or "").strip()
        self.email_id = str(account.get("mail_password") or "").strip()
        if not self.email:
            raise RuntimeError("moemail 账号缺少邮箱地址")
        if not self.email_id:
            raise RuntimeError("moemail 账号缺少 email_id，无法刷新")
        return self.email

    def _message_time(self, message: Dict) -> Optional[datetime]:
        for key in [
            "createdAt",
            "receivedAt",
            "sentAt",
            "created_at",
            "received_at",
            "sent_at",
        ]:
            raw_time = message.get(key)
            if raw_time is None:
                continue
            if isinstance(raw_time, (int, float)):
                timestamp = float(raw_time)
                if timestamp > 1e12:
                    timestamp /= 1000.0
                return datetime.fromtimestamp(timestamp)
            raw_text = str(raw_time).strip()
            if raw_text.isdigit():
                timestamp = float(raw_text)
                if timestamp > 1e12:
                    timestamp /= 1000.0
                return datetime.fromtimestamp(timestamp)
            try:
                normalized = re.sub(r"(\.\d{6})\d+", r"\1", raw_text)
                return (
                    datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                    .astimezone()
                    .replace(tzinfo=None)
                )
            except Exception:
                return None
        return None

    def _message_code(
        self, message: Dict, since_time: Optional[datetime]
    ) -> Optional[str]:
        message_time = self._message_time(message)
        if since_time and message_time and message_time < since_time:
            return None
        code = extract_code(str(message.get("content") or ""))
        if code:
            return code
        message_id = message.get("id")
        if not message_id:
            return None
        detail = self._request(
            "GET", f"{self.base_url}/api/emails/{self.email_id}/{message_id}"
        )
        if detail.status_code != 200:
            return None
        payload = detail.json() if detail.content else {}
        if isinstance(payload.get("message"), dict):
            payload = payload["message"]
        text_content = (
            payload.get("text")
            or payload.get("textContent")
            or payload.get("content")
            or ""
        )
        html_content = payload.get("html") or payload.get("htmlContent") or ""
        if isinstance(text_content, list):
            text_content = "".join(map(str, text_content))
        if isinstance(html_content, list):
            html_content = "".join(map(str, html_content))
        return extract_code(f"{text_content}{html_content}")

    def check_verification_code(
        self, email: str, max_retries: int = 30, since_time: Optional[datetime] = None
    ) -> Optional[str]:
        if not self.email_id:
            raise RuntimeError("moemail 缺少 email_id")
        logger.info(f"[{email}] 开始轮询 Moemail 验证码 (最多 {max_retries} 次)")
        for index in range(max_retries):
            try:
                logger.info(f"[{email}] 正在拉取 Moemail 邮件列表...")
                logger.info(
                    f"[{email}] 发送 GET 请求: {self.base_url}/api/emails/{self.email_id}"
                )
                response = self._request(
                    "GET", f"{self.base_url}/api/emails/{self.email_id}"
                )
                logger.info(f"[{email}] 收到响应: HTTP {response.status_code}")
                if response.status_code == 200:
                    messages = response.json().get("messages", []) or []
                    logger.info(
                        f"[{email}] 收到 {len(messages)} 封邮件，开始检查验证码..."
                    )
                    messages = sorted(
                        messages,
                        key=lambda item: self._message_time(item) or datetime.min,
                        reverse=True,
                    )
                    for message in messages:
                        code = self._message_code(message, since_time)
                        if code:
                            logger.info(f"[{email}] 找到验证码: {code}")
                            return code
                    logger.warning(f"[{email}] 所有邮件中均未找到验证码")
            except Exception as exc:
                logger.warning(f"[{email}] 读取 Moemail 验证码失败: {exc}")
            logger.info(f"[{email}] 等待 Moemail 验证码... ({index + 1}/{max_retries})")
            time.sleep(4)
        return None

    def export_account_fields(self) -> Dict:
        payload = {
            "mail_provider": self.provider_name,
            "mail_address": self.email,
            "mail_password": self.email_id,
            "mail_base_url": self.base_url,
        }
        if self.api_key:
            payload["mail_api_key"] = self.api_key
        if self.domain:
            payload["mail_domain"] = self.domain
        return payload


def build_mail_provider(config: Dict, account: Optional[Dict] = None):
    provider_name = (
        str(
            (account or {}).get("mail_provider")
            or config.get("email_provider")
            or "worker"
        )
        .strip()
        .lower()
    )
    if provider_name == "moemail":
        provider = MoemailProvider(
            config.get("moemail_base_url", ""),
            config.get("moemail_api_key", ""),
            config.get("moemail_domain", ""),
        )
    else:
        provider = WorkerMailProvider(
            config.get("worker_domain", ""),
            config.get("email_domain", ""),
            config.get("admin_password", ""),
        )
    if account is not None:
        provider.prepare_existing_account(account)
    return provider


class GeminiRegistrar:
    def __init__(self, mail_provider: BaseMailProvider):
        self.mail_provider = mail_provider
        self.credential = CredentialData()

    @staticmethod
    def _describe_page(url: str) -> str:
        parsed = urlparse(url or "")
        if is_business_home_url(url):
            return "business_home"
        if is_verification_url(url):
            return "verification_code"
        if is_account_chooser_url(url):
            return "account_chooser"
        host = parsed.netloc or "unknown-host"
        path = parsed.path or "/"
        return f"{host}{path}"

    def _build_launch_args(self) -> Dict:
        headless = parse_bool(os.environ.get("BROWSER_HEADLESS", "true"), True)
        slow_mo_ms = int(os.environ.get("BROWSER_SLOW_MO_MS", "0") or "0")
        launch_args = {"headless": headless}
        if slow_mo_ms > 0:
            launch_args["slow_mo"] = slow_mo_ms
        browser_proxy = os.environ.get("PROXY", "") or PROXY
        if browser_proxy:
            parsed_proxy = urlparse(browser_proxy)
            proxy_config = {
                "server": f"http://{parsed_proxy.hostname}:{parsed_proxy.port}"
            }
            if parsed_proxy.username:
                proxy_config["username"] = parsed_proxy.username
                proxy_config["password"] = parsed_proxy.password or ""
            launch_args["proxy"] = proxy_config
        return launch_args

    @staticmethod
    def _playwright_install_hint() -> str:
        return "请先执行: python -m playwright install chromium"

    async def _find_code_input(self, page):
        selectors = [
            'input[name="pinInput"]',
            "input[jsname='ovqh0b']",
            "input[type='tel']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        return None

    async def _wait_for_code_input(self, page, timeout_ms: int = 30000) -> bool:
        selectors = [
            'input[name="pinInput"]',
            "input[jsname='ovqh0b']",
            "input[type='tel']",
        ]
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=1500)
                    return True
                except Exception:
                    pass
            await asyncio.sleep(1)
        return False

    async def _wait_for_refresh_landing(
        self, page, email: str, timeout_ms: int = 45000
    ) -> str:
        deadline = time.time() + (timeout_ms / 1000)
        last_state = None
        while time.time() < deadline:
            current_url = page.url or ""
            if is_business_home_url(current_url):
                logger.info(f"[{email}] 已进入 Gemini Business 业务页")
                return "home"
            if is_verification_url(current_url):
                logger.info(f"[{email}] 已到达验证码页面")
                return "verify"
            if await self._find_code_input(page):
                logger.info(f"[{email}] 已检测到验证码输入框")
                return "verify"

            current_state = self._describe_page(current_url)
            if current_state != last_state:
                if current_state == "account_chooser":
                    logger.info(f"[{email}] 当前在账号选择页，继续等待验证码页面...")
                else:
                    logger.info(f"[{email}] 当前页面状态: {current_state}")
                last_state = current_state
            await asyncio.sleep(1.5)
        return "timeout"

    async def _click_resend_code_button(self, page) -> bool:
        candidates = [
            'button:has-text("重新发送")',
            'button:has-text("重发")',
            'button:has-text("Resend")',
            'button:has-text("resend")',
            'button:has-text("Send again")',
        ]
        for selector in candidates:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    await locator.first.click()
                    return True
            except Exception:
                continue
        try:
            buttons = page.locator("button")
            count = await buttons.count()
            for index in range(min(count, 20)):
                button = buttons.nth(index)
                text = ((await button.text_content()) or "").strip().lower()
                if "resend" in text or "重新" in text or "重发" in text:
                    await button.click()
                    return True
        except Exception:
            pass
        return False

    async def _poll_verification_code(
        self, page, email: str, since_time: datetime
    ) -> str:
        # Keep the refresh cadence close to gemini-business2api:
        # one initial wait, then two resend rounds if needed.
        attempts = [
            {"max_retries": 6, "resend": False},
            {"max_retries": 4, "resend": True},
            {"max_retries": 4, "resend": True},
        ]
        logger.info(f"[{email}] 开始轮询验证码")
        for index, attempt in enumerate(attempts, start=1):
            if attempt["resend"]:
                logger.warning(
                    f"[{email}] 验证码未到达，准备重发 ({index - 1}/{len(attempts) - 1})"
                )
                logger.info(
                    f"æ££æ ¨î‚¼æžî†¿î‡—é‡æ–°å‘é€éªŒè¯ç  ({index - 1}/{len(attempts) - 1})..."
                )
                if not await self._click_resend_code_button(page):
                    logger.warning("éˆî…å£˜é’ä¼´å™¸éªŒè¯ç æŒ‰é’®å¤±è´¥")
                    continue
                logger.info(f"[{email}] 已点击重发按钮")
                await asyncio.sleep(2)
            logger.info(
                f"[{email}] 第 {index}/{len(attempts)} 轮轮询，邮箱轮询次数={attempt['max_retries']}"
            )
            code = self.mail_provider.check_verification_code(
                email,
                max_retries=attempt["max_retries"],
                since_time=since_time,
            )
            if code:
                logger.info(f"[{email}] 验证码获取成功: {code}")
                return code
        raise RuntimeError("éˆî…æ•¹é’ä¼´ç™ç’‡ä½ºçˆœ")

    async def _submit_verification_code(self, page, code_input, code: str) -> None:
        await code_input.fill(code)
        await asyncio.sleep(1)

        submit_selectors = [
            'button[jsname="XooR8e"]',
            'button:has-text("Verify")',
            'button:has-text("Continue")',
            'button:has-text("Next")',
        ]
        for selector in submit_selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    await locator.first.click()
                    return
            except Exception:
                continue

        await code_input.press("Enter")

    async def _extract_xsrf_token(self, page) -> str:
        try:
            html = await page.content()
            patterns = [
                r'name=["\']xsrf-token["\']\s+content=["\']([^"\']+)["\']',
                r'name=["\']xsrfToken["\'][^>]*value=["\']([A-Za-z0-9_-]{20,})["\']',
                r'xsrfToken["\']?\s*[=:]\s*["\']([A-Za-z0-9_-]{20,})["\']',
                r"xsrfToken=([A-Za-z0-9_-]{20,})",
            ]
            for pattern in patterns:
                match = re.search(pattern, html or "", re.IGNORECASE)
                if match:
                    return match.group(1)
        except Exception as exc:
            logger.warning(f"提取 XSRF token 失败: {exc}")
        return "GXO_B0wnNhs6UQJZMcrSbTsbEEs"

    def _build_refresh_auth_url(self, email: str, xsrf_token: str) -> str:
        login_hint = quote(email, safe="")
        return (
            "https://auth.business.gemini.google/login/email"
            "?continueUrl=https%3A%2F%2Fbusiness.gemini.google%2F"
            f"&loginHint={login_hint}&xsrfToken={xsrf_token}"
        )

    def _build_refresh_entry_url(self, account: Dict) -> str:
        config_id = str(account.get("config_id") or "").strip()
        csesidx = str(account.get("csesidx") or "").strip()
        if config_id and csesidx:
            return (
                f"https://business.gemini.google/home/cid/{config_id}?csesidx={csesidx}"
            )
        return "https://business.gemini.google/"

    async def _populate_credentials_from_session(self, context, page) -> None:
        for cookie in await context.cookies():
            if cookie.get("name") == "__Host-C_OSES":
                self.credential.c_oses = cookie.get("value", "")
            elif cookie.get("name") == "__Secure-C_SES":
                self.credential.c_ses = cookie.get("value", "")
        parsed_url = urlparse(page.url)
        self.credential.csesidx = parse_qs(parsed_url.query).get("csesidx", [""])[0]
        match = re.search(r"/cid/([a-f0-9-]+)", parsed_url.path)
        if match:
            self.credential.config_id = match.group(1)

    async def execute(self, existing_account: Optional[Dict] = None) -> bool:
        from playwright.async_api import (
            TimeoutError as PlaywrightTimeoutError,
            async_playwright,
        )

        try:
            async with async_playwright() as playwright:
                launch_args = self._build_launch_args()
                try:
                    browser = await playwright.chromium.launch(**launch_args)
                except Exception as exc:
                    message = str(exc)
                    if (
                        "Executable doesn't exist" in message
                        or "Executable doesn\u2019t exist" in message
                    ):
                        raise RuntimeError(
                            f"Playwright 浏览器未安装。{self._playwright_install_hint()}"
                        ) from exc
                    raise

                email = (
                    self.mail_provider.prepare_existing_account(existing_account)
                    if existing_account
                    else self.mail_provider.create_email()
                )
                account_fields = self.mail_provider.export_account_fields()
                self.credential.email = email
                self.credential.mail_provider = account_fields.get("mail_provider", "")
                self.credential.mail_address = account_fields.get("mail_address", email)
                self.credential.mail_password = account_fields.get("mail_password", "")
                self.credential.mail_base_url = account_fields.get("mail_base_url", "")
                self.credential.mail_api_key = account_fields.get("mail_api_key", "")
                self.credential.mail_domain = account_fields.get("mail_domain", "")
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()
                try:
                    poll_since_time = datetime.now() - timedelta(seconds=30)
                    if existing_account:
                        logger.info(f"refresh mode with existing cookies: {email}")
                        logger.info(f"[{email}] 打开 Gemini 登录页，准备走认证刷新流程")
                        await page.goto(
                            AUTH_HOME_URL, wait_until="domcontentloaded", timeout=90000
                        )
                        xsrf_token = await self._extract_xsrf_token(page)
                        logger.info(f"[{email}] 设置 XSRF Cookie...")
                        await context.add_cookies(
                            [
                                {
                                    "name": "__Host-AP_SignInXsrf",
                                    "value": xsrf_token,
                                    "url": AUTH_HOME_URL,
                                    "secure": True,
                                }
                            ]
                        )
                        refresh_url = self._build_refresh_auth_url(email, xsrf_token)
                        logger.info(
                            f"[{email}] 使用认证入口进入刷新流程: {refresh_url}"
                        )
                        await page.goto(
                            refresh_url, wait_until="domcontentloaded", timeout=90000
                        )
                        await asyncio.sleep(3)
                    else:
                        logger.info(f"login mode from auth bootstrap: {email}")
                        logger.info(f"[{email}] 打开 Gemini 登录页，准备走认证注册流程")
                        await page.goto(
                            AUTH_HOME_URL, wait_until="domcontentloaded", timeout=90000
                        )
                        xsrf_token = await self._extract_xsrf_token(page)
                        logger.info(f"[{email}] 设置 XSRF Cookie...")
                        await context.add_cookies(
                            [
                                {
                                    "name": "__Host-AP_SignInXsrf",
                                    "value": xsrf_token,
                                    "url": AUTH_HOME_URL,
                                    "secure": True,
                                }
                            ]
                        )
                        auth_url = self._build_refresh_auth_url(email, xsrf_token)
                        logger.info(f"[{email}] 使用认证入口进入注册流程: {auth_url}")
                        await page.goto(
                            auth_url, wait_until="domcontentloaded", timeout=90000
                        )
                        await asyncio.sleep(3)
                    logger.info(f"[{email}] 当前 URL: {page.url}")
                    landing_state = await self._wait_for_refresh_landing(
                        page,
                        email,
                        timeout_ms=45000 if existing_account else 30000,
                    )
                    if landing_state == "home":
                        logger.info(f"[{email}] 已直接进入业务页，跳过验证码页面")
                        await self.finish_login(page)
                        await self._populate_credentials_from_session(context, page)
                        if not self.credential.is_complete():
                            raise RuntimeError("éˆî‡å…˜éŽ»æ„¬å½‡ç€¹å±¾æš£é‘î…¡ç˜‰")
                        logger.info(f"ç’ï¹€å½¿æ¾¶å‹­æ‚ŠéŽ´æ„¬å§›: {email}")
                        return True
                    if landing_state != "verify":
                        raise RuntimeError(
                            f"未能进入验证码页或业务页，当前页面: {page.url}"
                        )
                    if not await self._wait_for_code_input(page, timeout_ms=30000):
                        raise RuntimeError(f"验证码输入框未出现，当前页面: {page.url}")
                    logger.info(f"[{email}] 已进入验证码输入页面")

                    logger.info(f"[{email}] 发送验证码...")
                    if await self._click_resend_code_button(page):
                        logger.info(f"[{email}] 已点击重新发送按钮")
                        await asyncio.sleep(2)
                    else:
                        logger.warning(
                            f"[{email}] 未找到重新发送按钮，继续等待邮箱验证码"
                        )
                    logger.info(f"[{email}] 等待邮箱验证码...")
                    code = await self._poll_verification_code(
                        page, email, poll_since_time
                    )
                    if not code:
                        logger.warning("首次轮询未收到验证码，尝试重发一次")
                        if await self._click_resend_code_button(page):
                            await asyncio.sleep(2)
                            code = self.mail_provider.check_verification_code(
                                email, max_retries=8, since_time=poll_since_time
                            )
                    if not code:
                        raise RuntimeError("未收到验证码")
                    code_input = await self._find_code_input(page)
                    if not code_input:
                        raise RuntimeError("验证码输入框已失效")
                    logger.info(f"[{email}] 输入验证码...")
                    await self._submit_verification_code(page, code_input, code)
                    logger.info(f"[{email}] 提交验证码")
                    logger.info(f"[{email}] 等待验证后跳转...")
                    await asyncio.sleep(3)
                    await self.finish_login(page)
                    logger.info(f"[{email}] 验证后 URL: {page.url}")
                    for cookie in await context.cookies():
                        if cookie.get("name") == "__Host-C_OSES":
                            self.credential.c_oses = cookie.get("value", "")
                        elif cookie.get("name") == "__Secure-C_SES":
                            self.credential.c_ses = cookie.get("value", "")
                    parsed_url = urlparse(page.url)
                    self.credential.csesidx = parse_qs(parsed_url.query).get(
                        "csesidx", [""]
                    )[0]
                    match = re.search(r"/cid/([a-f0-9-]+)", parsed_url.path)
                    if match:
                        self.credential.config_id = match.group(1)
                    if not self.credential.is_complete():
                        raise RuntimeError("未能提取完整凭证")
                    logger.info(f"[{email}] 登录成功，开始提取并保存配置")
                    logger.info(f"账号处理成功: {email}")
                    return True
                finally:
                    await context.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            logger.error(f"页面等待超时: {exc}")
            return False
        except Exception as exc:
            logger.error(f"账号处理失败: {exc}")
            return False

    async def finish_login(self, page) -> None:
        for _ in range(20):
            if is_business_home_url(page.url):
                return
            locator = page.locator('input[formcontrolname="fullName"]')
            if await locator.count() > 0 and await locator.first.is_visible():
                await locator.first.fill(random_name())
                await asyncio.sleep(1)
                agree = page.locator("button.agree-button")
                if await agree.count() > 0 and await agree.first.is_visible():
                    await agree.first.click()
            await asyncio.sleep(2)
        deadline = time.time() + 90
        while time.time() < deadline:
            if is_business_home_url(page.url):
                return
            await asyncio.sleep(2)
        raise RuntimeError(f"登录后未进入业务页，当前页面: {page.url}")


class CredentialSyncer:
    def __init__(self, base_url: str, admin_key: str):
        self.base_url = base_url.rstrip("/")
        self.admin_key = admin_key
        self.session = build_session(10)
        self._logged_in = False
        self._accounts_cache: Optional[List[Dict]] = None

    def request(self, method: str, path: str, **kwargs):
        maybe_apply_proxy(self.session, email_api=False)
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                return self.session.request(method, url, timeout=30, **kwargs)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep((2**attempt) + 1)
        raise RuntimeError("request failed")

    def login(self) -> bool:
        if self._logged_in:
            return True
        response = self.request(
            "POST",
            "/login",
            data={"admin_key": self.admin_key},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            if response.status_code == 401:
                logger.error(
                    "登录 gemini-business2api 失败: HTTP 401，SYNC_KEY 必须等于远端 gemini-business2api 的 ADMIN_KEY"
                )
            else:
                logger.error(
                    f"登录 gemini-business2api 失败: HTTP {response.status_code}"
                )
            return False
        self._logged_in = True
        return True

    def fetch_accounts(self) -> List[Dict]:
        response = self.request("GET", "/admin/accounts-config")
        if response.status_code != 200:
            raise RuntimeError(f"拉取账号配置失败: HTTP {response.status_code}")
        return response.json().get("accounts", []) or []

    def ensure_accounts_cache(self) -> List[Dict]:
        if not self.login():
            raise RuntimeError(
                "登录 gemini-business2api 失败，无法同步账号；请检查 sync_key 是否与远端 ADMIN_KEY 一致"
            )
        if self._accounts_cache is None:
            self._accounts_cache = self.fetch_accounts()
        return self._accounts_cache

    def upload_accounts(self, accounts: List[Dict]) -> bool:
        response = self.request(
            "PUT",
            "/admin/accounts-config",
            json=accounts,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            logger.error(f"上传账号配置失败: HTTP {response.status_code}")
            return False
        self._accounts_cache = [dict(item) for item in accounts]
        return True

    def enable_account(self, account_id: str) -> bool:
        response = self.request("PUT", f"/admin/accounts/{account_id}/enable")
        if response.status_code != 200:
            logger.warning(
                f"enable account failed: {account_id} (HTTP {response.status_code})"
            )
            return False
        return True

    @staticmethod
    def merge_accounts(existing: List[Dict], updates: List[Dict]) -> List[Dict]:
        merged = [dict(item) for item in existing]
        index_map = {
            item.get("id"): index for index, item in enumerate(merged) if item.get("id")
        }
        for update in updates:
            account_id = update.get("id")
            if not account_id:
                continue
            if account_id in index_map:
                merged[index_map[account_id]].update(update)
            else:
                index_map[account_id] = len(merged)
                merged.append(dict(update))
        return merged

    def sync(self, new_accounts: List[Dict]) -> bool:
        existing = self.ensure_accounts_cache()
        merged = self.merge_accounts(existing, new_accounts)
        logger.info(f"同步账号数量: 现有 {len(existing)} / 更新后 {len(merged)}")
        return self.upload_accounts(merged)

    def sync_one(self, account: Dict) -> bool:
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            logger.error("同步单个账号失败: 缺少账号 id")
            return False
        existing = self.ensure_accounts_cache()
        merged = self.merge_accounts(existing, [account])
        logger.info(
            f"同步单个账号: {account_id} (远端总数 {len(existing)} -> {len(merged)})"
        )
        return self.upload_accounts(merged)


def validate_config(config: Dict, run_mode: str) -> None:
    if config.get("email_provider") == "moemail":
        if not config.get("moemail_base_url"):
            config["moemail_base_url"] = "https://moemail.app"
    elif not all(
        [
            config.get("worker_domain"),
            config.get("email_domain"),
            config.get("admin_password"),
        ]
    ):
        logger.error("缺少 worker 邮箱配置")
        sys.exit(1)
    if run_mode in {"register", "refresh", "both"} and (
        not config.get("sync_url") or not config.get("sync_key")
    ):
        logger.error("缺少 gemini-business2api 同步配置")
        sys.exit(1)


async def process_register(worker_id: int, config: Dict) -> Optional[Dict]:
    logger.info(f"[Register-{worker_id}] 开始处理")
    registrar = GeminiRegistrar(build_mail_provider(config))
    if await registrar.execute():
        payload = registrar.credential.to_dict()
        logger.info(f"[Register-{worker_id}] 成功: {payload['id']}")
        return payload
    logger.info(f"[Register-{worker_id}] 失败")
    return None


def should_refresh(
    account: Dict, refresh_before_hours: float, include_disabled: bool
) -> bool:
    if not account.get("id"):
        return False
    if account.get("disabled") and not include_disabled:
        return False
    expires_at = parse_expiration(account.get("expires_at"))
    if expires_at is None:
        return False
    return expires_at <= datetime.now() + timedelta(hours=refresh_before_hours)


async def process_refresh(account: Dict, config: Dict) -> Optional[Dict]:
    provider_name = (
        str(account.get("mail_provider") or config.get("email_provider") or "worker")
        .strip()
        .lower()
    )
    if provider_name not in {"worker", "moemail"}:
        logger.info(f"跳过不支持刷新的账号: {account.get('id')} ({provider_name})")
        return None
    registrar = GeminiRegistrar(build_mail_provider(config, account=account))
    if await registrar.execute(existing_account=account):
        payload = registrar.credential.to_dict(existing=account)
        logger.info(f"刷新成功: {payload['id']}")
        return payload
    logger.warning(f"刷新失败: {account.get('id')}")
    return None


async def run_register_flow(config: Dict, syncer: CredentialSyncer) -> List[Dict]:
    count = int(config.get("register_count", 1))
    concurrent = max(1, int(config.get("concurrent", 1)))
    logger.info(
        f"注册模式启动: count={count}, concurrent={concurrent}, provider={config.get('email_provider')}"
    )
    syncer.ensure_accounts_cache()
    if concurrent == 1:
        results: List[Dict] = []
        for index in range(count):
            item = await process_register(index + 1, config)
            if item:
                if not syncer.sync_one(item):
                    raise RuntimeError(f"新注册账号上传失败: {item.get('id')}")
                results.append(item)
            if index < count - 1:
                await asyncio.sleep(random.randint(3, 6))
        return results

    semaphore = asyncio.Semaphore(concurrent)

    async def limited_process_register(worker_id: int) -> Optional[Dict]:
        async with semaphore:
            return await process_register(worker_id, config)

    tasks = [
        asyncio.create_task(limited_process_register(index + 1))
        for index in range(count)
    ]
    results: List[Dict] = []
    for completed in asyncio.as_completed(tasks):
        item = await completed
        if not isinstance(item, dict):
            continue
        if not syncer.sync_one(item):
            raise RuntimeError(f"新注册账号上传失败: {item.get('id')}")
        results.append(item)
    return results


async def run_refresh_flow(config: Dict, syncer: CredentialSyncer) -> List[Dict]:
    existing_accounts = syncer.ensure_accounts_cache()
    refresh_before_hours = float(config.get("refresh_before_hours", 0))
    refresh_limit = int(config.get("refresh_limit", 0))
    include_disabled = bool(config.get("refresh_include_disabled", False))
    candidates = [
        account
        for account in existing_accounts
        if should_refresh(account, refresh_before_hours, include_disabled)
    ]
    if refresh_limit > 0:
        candidates = candidates[:refresh_limit]
    logger.info(f"待刷新账号数量: {len(candidates)}")
    refreshed: List[Dict] = []
    for index, account in enumerate(candidates, start=1):
        logger.info(f"[{index}/{len(candidates)}] 正在刷新: {account.get('id')}")
        updated = await process_refresh(account, config)
        if updated:
            if not syncer.sync_one(updated):
                raise RuntimeError(f"刷新后的账号上传失败: {updated.get('id')}")
            refreshed.append(updated)
            if not updated.get("disabled"):
                syncer.enable_account(updated["id"])
        if index < len(candidates):
            await asyncio.sleep(5)
    return refreshed


async def main() -> None:
    global PROXY
    file_config = load_file_config()
    for env_name, config_key in [
        ("PROXY", "proxy"),
        ("PROXY_EMAIL", "proxy_email"),
        ("VLESS_CONFIG", "vless_config"),
        ("ACCOUNT_EXPIRE_HOURS", "account_expire_hours"),
        ("BROWSER_HEADLESS", "browser_headless"),
        ("BROWSER_SLOW_MO_MS", "browser_slow_mo_ms"),
    ]:
        if os.environ.get(env_name, "").strip() == "" and file_config.get(
            config_key
        ) not in (None, ""):
            os.environ[env_name] = str(file_config.get(config_key))
    PROXY = os.environ.get("PROXY", "") or str(file_config.get("proxy") or "")
    vless_config = os.environ.get("VLESS_CONFIG", "").strip()
    if vless_config:
        try:
            from proxy_helper import setup_proxy

            logger.info("正在启动 VLESS 代理...")
            proxy_process = setup_proxy()
            if proxy_process:
                PROXY = os.environ.get("PROXY", "")
                logger.info(f"VLESS 代理已启用: {PROXY}")
        except Exception as exc:
            logger.warning(f"VLESS 代理启动失败: {exc}")
    config = {
        "run_mode": (
            pick_config(file_config, "RUN_MODE", "run_mode", "register", str)
            or "register"
        ).lower(),
        "email_provider": (
            pick_config(file_config, "EMAIL_PROVIDER", "email_provider", "worker", str)
            or "worker"
        ).lower(),
        "worker_domain": pick_config(
            file_config, "WORKER_DOMAIN", "worker_domain", "", str
        ),
        "email_domain": pick_config(
            file_config, "EMAIL_DOMAIN", "email_domain", "", str
        ),
        "admin_password": pick_config(
            file_config, "ADMIN_PASSWORD", "admin_password", "", str
        ),
        "moemail_base_url": pick_config(
            file_config,
            "MOEMAIL_BASE_URL",
            "moemail_base_url",
            "https://moemail.app",
            str,
        ),
        "moemail_api_key": pick_config(
            file_config, "MOEMAIL_API_KEY", "moemail_api_key", "", str
        ),
        "moemail_domain": pick_config(
            file_config, "MOEMAIL_DOMAIN", "moemail_domain", "", str
        ),
        "sync_url": pick_config(file_config, "SYNC_URL", "sync_url", "", str),
        "sync_key": pick_config(file_config, "SYNC_KEY", "sync_key", "", str),
        "register_count": pick_config(
            file_config, "REGISTER_COUNT", "register_count", 1, int
        ),
        "concurrent": pick_config(file_config, "CONCURRENT", "concurrent", 1, int),
        "refresh_before_hours": pick_config(
            file_config, "REFRESH_BEFORE_HOURS", "refresh_before_hours", 0.0, float
        ),
        "refresh_limit": pick_config(
            file_config, "REFRESH_LIMIT", "refresh_limit", 0, int
        ),
        "refresh_include_disabled": pick_config(
            file_config,
            "REFRESH_INCLUDE_DISABLED",
            "refresh_include_disabled",
            False,
            bool,
        ),
    }
    validate_config(config, config["run_mode"])
    print(f"\n{'=' * 56}")
    print("  GeminiForge")
    print(f"  mode: {config['run_mode']}")
    print(f"  provider: {config['email_provider']}")
    print(f"  register_count: {config['register_count']}")
    print(f"  concurrent: {config['concurrent']}")
    print(f"{'=' * 56}\n")
    syncer = CredentialSyncer(config["sync_url"], config["sync_key"])
    refreshed_accounts: List[Dict] = []
    new_accounts: List[Dict] = []
    if config["run_mode"] in {"refresh", "both"}:
        refreshed_accounts = await run_refresh_flow(config, syncer)
        logger.info(f"刷新完成: {len(refreshed_accounts)} 个")
    if config["run_mode"] in {"register", "both"}:
        new_accounts = await run_register_flow(config, syncer)
        logger.info(f"注册完成: {len(new_accounts)} 个")
    if not refreshed_accounts and not new_accounts:
        logger.info("本次没有可上传或可刷新的账号")


if __name__ == "__main__":
    asyncio.run(main())
