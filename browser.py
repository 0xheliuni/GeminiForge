# -*- coding: utf-8 -*-
import asyncio
import importlib
import logging
import os
import random
import re
import string
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

import config as app_config
from config import (
    AUTH_HOME_URL,
    is_account_chooser_url,
    is_business_home_url,
    is_verification_url,
    parse_bool,
)
from models import CredentialData
from providers import BaseMailProvider

logger = logging.getLogger(__name__)

try:
    _playwright_stealth = importlib.import_module("playwright_stealth")
    _StealthClass = getattr(_playwright_stealth, "Stealth", None)
    if _StealthClass is None:
        stealth_async = getattr(_playwright_stealth, "stealth_async", None)
        HAS_STEALTH = callable(stealth_async)
    else:
        stealth_async = None
        HAS_STEALTH = True
except Exception:
    _StealthClass = None
    stealth_async = None
    HAS_STEALTH = False

try:
    _faker_module = importlib.import_module("faker")
    Faker = getattr(_faker_module, "Faker")
    _fake = Faker(["en_US", "en_GB"])
    HAS_FAKER = True
except ImportError:
    _fake = None
    HAS_FAKER = False


CHROME_VERSIONS = [
    "128.0.6613.120",
    "129.0.6668.89",
    "130.0.6723.91",
    "131.0.6778.108",
    "132.0.6834.83",
    "133.0.6943.127",
    "134.0.6998.89",
    "134.0.6998.117",
    "135.0.7049.52",
    "135.0.7049.84",
]

VIEWPORT_PRESETS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 2560, "height": 1440},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
]

DEVICE_SCALE_FACTORS = [1, 1, 1, 1.25, 1.5, 2]

COLOR_SCHEMES = ["light", "dark", "no-preference"]

FINGERPRINT_PROFILES = [
    {
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "geolocation": {"latitude": 40.7128, "longitude": -74.0060},
        "accept_language": "en-US,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
            "Macintosh; Intel Mac OS X 10_15_7",
        ],
    },
    {
        "locale": "en-US",
        "timezone_id": "America/Chicago",
        "geolocation": {"latitude": 41.8781, "longitude": -87.6298},
        "accept_language": "en-US,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
            "X11; Linux x86_64",
        ],
    },
    {
        "locale": "en-US",
        "timezone_id": "America/Denver",
        "geolocation": {"latitude": 39.7392, "longitude": -104.9903},
        "accept_language": "en-US,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
        ],
    },
    {
        "locale": "en-US",
        "timezone_id": "America/Los_Angeles",
        "geolocation": {"latitude": 34.0522, "longitude": -118.2437},
        "accept_language": "en-US,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
            "Macintosh; Intel Mac OS X 10_15_7",
            "X11; Linux x86_64",
        ],
    },
    {
        "locale": "en-GB",
        "timezone_id": "Europe/London",
        "geolocation": {"latitude": 51.5074, "longitude": -0.1278},
        "accept_language": "en-GB,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
            "Macintosh; Intel Mac OS X 10_15_7",
        ],
    },
    {
        "locale": "en-AU",
        "timezone_id": "Australia/Sydney",
        "geolocation": {"latitude": -33.8688, "longitude": 151.2093},
        "accept_language": "en-AU,en;q=0.9",
        "platforms": [
            "Macintosh; Intel Mac OS X 10_15_7",
            "Windows NT 10.0; Win64; x64",
        ],
    },
    {
        "locale": "en-CA",
        "timezone_id": "America/Toronto",
        "geolocation": {"latitude": 43.6532, "longitude": -79.3832},
        "accept_language": "en-CA,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
            "Macintosh; Intel Mac OS X 10_15_7",
        ],
    },
    {
        "locale": "en-US",
        "timezone_id": "America/Phoenix",
        "geolocation": {"latitude": 33.4484, "longitude": -112.0740},
        "accept_language": "en-US,en;q=0.9",
        "platforms": [
            "Windows NT 10.0; Win64; x64",
        ],
    },
    {
        "locale": "en-US",
        "timezone_id": "Pacific/Honolulu",
        "geolocation": {"latitude": 21.3069, "longitude": -157.8583},
        "accept_language": "en-US,en;q=0.9",
        "platforms": [
            "Macintosh; Intel Mac OS X 10_15_7",
        ],
    },
]


def random_name(length: int = 5) -> str:
    if _fake:
        return _fake.first_name()
    return "".join(random.choices(string.ascii_lowercase, k=length))


def generate_fingerprint() -> Dict:
    chrome_ver = random.choice(CHROME_VERSIONS)
    viewport = random.choice(VIEWPORT_PRESETS)
    profile = random.choice(FINGERPRINT_PROFILES)
    platform_part = random.choice(profile["platforms"])
    ua = (
        f"Mozilla/5.0 ({platform_part}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36"
    )
    geo = profile["geolocation"]
    return {
        "user_agent": ua,
        "viewport": viewport,
        "locale": profile["locale"],
        "timezone_id": profile["timezone_id"],
        "geolocation": {
            "latitude": geo["latitude"] + random.uniform(-0.05, 0.05),
            "longitude": geo["longitude"] + random.uniform(-0.05, 0.05),
            "accuracy": random.randint(10, 100),
        },
        "accept_language": profile["accept_language"],
        "color_scheme": random.choice(COLOR_SCHEMES),
        "device_scale_factor": random.choice(DEVICE_SCALE_FACTORS),
    }


async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


async def human_type(locator, text: str) -> None:
    try:
        await locator.click()
        await human_delay(200, 500)
        await locator.press_sequentially(text, delay=random.uniform(50, 200))
        if random.random() < 0.3:
            await human_delay(100, 400)
    except Exception:
        await locator.fill(text)


async def human_mouse_move(
    page, x: Optional[int] = None, y: Optional[int] = None
) -> None:
    vp = page.viewport_size or {"width": 1920, "height": 1080}
    target_x = x if x is not None else random.randint(100, vp["width"] - 100)
    target_y = y if y is not None else random.randint(100, vp["height"] - 100)
    steps = random.randint(8, 30)
    await page.mouse.move(target_x, target_y, steps=steps)


async def human_scroll(page, direction: str = "down") -> None:
    amount = random.randint(120, 400)
    if direction == "up":
        amount = -amount
    await page.mouse.wheel(0, amount)
    await human_delay(300, 800)


async def human_hesitation(page) -> None:
    """Simulate brief mouse wandering before clicking, as a real user might."""
    vp = page.viewport_size or {"width": 1920, "height": 1080}
    for _ in range(random.randint(1, 3)):
        x = random.randint(200, vp["width"] - 200)
        y = random.randint(200, vp["height"] - 200)
        await page.mouse.move(x, y, steps=random.randint(5, 15))
        await human_delay(150, 500)


async def human_read_pause(page) -> None:
    """Simulate reading page content (longer pause with occasional scroll)."""
    await human_delay(2000, 6000)
    if random.random() < 0.5:
        await human_scroll(page, random.choice(["down", "down", "up"]))
    await human_delay(500, 1500)


async def simulate_human_presence(page) -> None:
    await human_delay(800, 2000)
    await human_mouse_move(page)
    await human_delay(300, 800)
    if random.random() < 0.5:
        await human_scroll(page)
    if random.random() < 0.3:
        await human_hesitation(page)
    if random.random() < 0.25:
        await human_read_pause(page)
    await human_delay(200, 600)


def build_anti_detect_script(fingerprint: Dict) -> str:
    locale = fingerprint.get("locale", "en-US")
    lang_primary = locale.split("-")[0]
    platform_ua = fingerprint.get("user_agent", "")
    if "Windows" in platform_ua:
        nav_platform = "Win32"
    elif "Macintosh" in platform_ua:
        nav_platform = "MacIntel"
    else:
        nav_platform = "Linux x86_64"
    return """
    (function() {
        // WebRTC IP leak protection
        if (window.RTCPeerConnection) {
            const OrigRTC = window.RTCPeerConnection;
            window.RTCPeerConnection = function(config, constraints) {
                if (config && config.iceServers) { config.iceServers = []; }
                return new OrigRTC(config, constraints);
            };
            window.RTCPeerConnection.prototype = OrigRTC.prototype;
            window.RTCPeerConnection.generateCertificate = OrigRTC.generateCertificate;
        }
        if (window.webkitRTCPeerConnection) {
            const OrigWebkitRTC = window.webkitRTCPeerConnection;
            window.webkitRTCPeerConnection = function(config, constraints) {
                if (config && config.iceServers) { config.iceServers = []; }
                return new OrigWebkitRTC(config, constraints);
            };
            window.webkitRTCPeerConnection.prototype = OrigWebkitRTC.prototype;
        }

        // navigator property hardening
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true,
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['""" + locale + """', '""" + lang_primary + """'],
            configurable: true,
        });
        Object.defineProperty(navigator, 'platform', {
            get: () => '""" + nav_platform + """',
            configurable: true,
        });

        // chrome runtime stub
        if (!window.chrome) { window.chrome = {}; }
        if (!window.chrome.runtime) {
            window.chrome.runtime = { connect: function(){}, sendMessage: function(){} };
        }

        // permissions query patch
        const origQuery = navigator.permissions.query;
        navigator.permissions.query = function(params) {
            if (params.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return origQuery.call(this, params);
        };

        // plugins & mimeTypes length spoof
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer',
                      description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                      description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin',
                      description: '' },
                ];
                arr.item = i => arr[i];
                arr.namedItem = n => arr.find(p => p.name === n);
                arr.refresh = () => {};
                return arr;
            },
            configurable: true,
        });
    })();
    """


class ProxyRotator:
    def __init__(
        self,
        pool: Optional[List[str]] = None,
        single: str = "",
        validate: bool = False,
    ):
        self._proxies: List[str] = []
        self._failed: List[str] = []
        self._index = 0
        self._ip_cache: Dict[str, str] = {}
        if pool:
            if isinstance(pool, str):
                pool = [p.strip() for p in pool.split(",") if p.strip()]
            self._proxies = [p for p in pool if p]
        elif single:
            self._proxies = [single]
        if validate and self._proxies:
            self._validate_all()

    def _health_check(self, proxy_url: str, timeout: int = 10) -> bool:
        import requests as _req

        try:
            resp = _req.get(
                "https://httpbin.org/ip",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=timeout,
            )
            if resp.status_code == 200:
                ip = resp.json().get("origin", "unknown")
                self._ip_cache[proxy_url] = ip
                logger.info(f"代理可用 [{proxy_url[:40]}] 出口 IP: {ip}")
                return True
        except Exception as exc:
            logger.warning(f"代理不可用 [{proxy_url[:40]}]: {exc}")
        return False

    def _validate_all(self) -> None:
        logger.info(f"正在验证 {len(self._proxies)} 个代理...")
        valid = []
        for proxy in self._proxies:
            if self._health_check(proxy):
                valid.append(proxy)
            else:
                self._failed.append(proxy)
        if valid:
            self._proxies = valid
            logger.info(f"代理验证完成: {len(valid)} 可用, {len(self._failed)} 不可用")
        else:
            logger.warning("所有代理验证均失败，保留原始列表以便后续重试")

    def next(self) -> str:
        if not self._proxies:
            return ""
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return proxy

    def mark_failed(self, proxy_url: str) -> None:
        if proxy_url and proxy_url in self._proxies and len(self._proxies) > 1:
            self._proxies.remove(proxy_url)
            self._failed.append(proxy_url)
            logger.warning(
                f"代理已标记为失败: {proxy_url[:40]}... (剩余 {len(self._proxies)})"
            )

    def get_exit_ip(self, proxy_url: str) -> str:
        return self._ip_cache.get(proxy_url, "")

    @property
    def available(self) -> bool:
        return len(self._proxies) > 0


class GeminiRegistrar:
    def __init__(self, mail_provider: BaseMailProvider, proxy_url: str = ""):
        self.mail_provider = mail_provider
        self.credential = CredentialData()
        self.proxy_url = proxy_url
        self.fingerprint = generate_fingerprint()

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

    def _build_launch_args(self) -> Dict[str, Any]:
        headless = parse_bool(os.environ.get("BROWSER_HEADLESS", "true"), True)
        slow_mo_ms = int(os.environ.get("BROWSER_SLOW_MO_MS", "0") or "0")
        launch_args: Dict[str, Any] = {"headless": headless}
        if slow_mo_ms > 0:
            launch_args["slow_mo"] = slow_mo_ms
        launch_args["args"] = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
        ]
        browser_proxy = (
            self.proxy_url or os.environ.get("PROXY", "") or app_config.PROXY
        )
        if browser_proxy:
            parsed_proxy = urlparse(browser_proxy)
            if not parsed_proxy.hostname or not parsed_proxy.port:
                logger.warning(
                    f"无效的代理 URL（缺少 host 或 port），跳过: {browser_proxy[:60]}"
                )
            elif parsed_proxy.path and len(parsed_proxy.path) > 1:
                logger.warning(
                    f"代理 URL 包含路径，可能是订阅链接而非代理地址，跳过: {browser_proxy[:60]}"
                )
            else:
                scheme = parsed_proxy.scheme or "http"
                proxy_config = {
                    "server": f"{scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"
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
                    f"[{email}] 正在尝试重新发送验证码 ({index - 1}/{len(attempts) - 1})..."
                )
                if not await self._click_resend_code_button(page):
                    logger.warning(f"[{email}] 点击重新发送验证码按钮失败")
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
        raise RuntimeError(f"[{email}] 所有轮次均未收到验证码")

    async def _submit_verification_code(self, page, code_input, code: str) -> None:
        await human_type(code_input, code)
        await human_delay(800, 1800)

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
        raise RuntimeError("无法从页面提取 XSRF token，页面可能未正常加载")

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

    async def _save_failure_screenshot(self, page, email: str) -> None:
        try:
            screenshot_dir = "screenshots"
            os.makedirs(screenshot_dir, exist_ok=True)
            filename = f"{screenshot_dir}/fail_{email}_{int(time.time())}.png"
            await page.screenshot(path=filename, full_page=True)
            logger.info(f"[{email}] 失败截图已保存: {filename}")
        except Exception:
            pass

    async def execute(self, existing_account: Optional[Dict] = None) -> bool:
        from playwright.async_api import (
            TimeoutError as PlaywrightTimeoutError,
            async_playwright,
        )

        _page = None
        _email = ""
        try:
            async with async_playwright() as playwright:
                launch_args = self._build_launch_args()
                try:
                    browser = await playwright.chromium.launch(**launch_args)
                except Exception as exc:
                    message = str(exc)
                    if (
                        "Executable doesn't exist" in message
                        or "Executable doesn’t exist" in message
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
                _email = email
                account_fields = self.mail_provider.export_account_fields()
                self.credential.email = email
                self.credential.mail_provider = account_fields.get("mail_provider", "")
                self.credential.mail_address = account_fields.get("mail_address", email)
                self.credential.mail_password = account_fields.get("mail_password", "")
                self.credential.mail_base_url = account_fields.get("mail_base_url", "")
                self.credential.mail_api_key = account_fields.get("mail_api_key", "")
                self.credential.mail_domain = account_fields.get("mail_domain", "")
                fp = self.fingerprint
                context = await browser.new_context(
                    user_agent=fp["user_agent"],
                    viewport=fp["viewport"],
                    locale=fp["locale"],
                    timezone_id=fp["timezone_id"],
                    color_scheme=fp.get("color_scheme", "light"),
                    device_scale_factor=fp.get("device_scale_factor", 1),
                    geolocation=fp.get("geolocation"),
                    permissions=["geolocation"],
                    extra_http_headers={
                        "Accept-Language": fp.get("accept_language", "en-US,en;q=0.9"),
                    },
                )
                if HAS_STEALTH:
                    if _StealthClass is not None:
                        await _StealthClass().apply_stealth_async(context)
                    elif stealth_async is not None:
                        pass  # will apply to page after creation
                else:
                    logger.warning("playwright-stealth 未安装，反检测能力受限")
                page = await context.new_page()
                _page = page
                if HAS_STEALTH and stealth_async is not None and _StealthClass is None:
                    await stealth_async(page)
                await page.add_init_script(build_anti_detect_script(fp))
                try:
                    poll_since_time = datetime.now() - timedelta(seconds=30)
                    if existing_account:
                        logger.info(f"refresh mode with existing cookies: {email}")
                        logger.info(f"[{email}] 打开 Gemini 登录页，准备走认证刷新流程")
                        await page.goto(
                            AUTH_HOME_URL, wait_until="domcontentloaded", timeout=90000
                        )
                        await human_delay(1000, 3000)
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
                        await simulate_human_presence(page)
                    else:
                        logger.info(f"login mode from auth bootstrap: {email}")
                        logger.info(f"[{email}] 打开 Gemini 登录页，准备走认证注册流程")
                        await page.goto(
                            AUTH_HOME_URL, wait_until="domcontentloaded", timeout=90000
                        )
                        await human_delay(1000, 3000)
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
                        await simulate_human_presence(page)
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
                            raise RuntimeError(f"[{email}] 凭证不完整，无法提取完整会话信息")
                        logger.info(f"[{email}] 直接登录成功，已提取凭证")
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
                    await human_delay(2000, 5000)
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
            if _page:
                await self._save_failure_screenshot(_page, _email)
            return False
        except Exception as exc:
            logger.error(f"账号处理失败: {exc}")
            if _page:
                await self._save_failure_screenshot(_page, _email)
            return False

    async def finish_login(self, page) -> None:
        for _ in range(20):
            if is_business_home_url(page.url):
                return
            locator = page.locator('input[formcontrolname="fullName"]')
            if await locator.count() > 0 and await locator.first.is_visible():
                await human_delay(800, 2000)
                await human_mouse_move(page)
                await human_type(locator.first, random_name())
                await human_delay(500, 1500)
                agree = page.locator("button.agree-button")
                if await agree.count() > 0 and await agree.first.is_visible():
                    await human_mouse_move(page)
                    await human_delay(300, 800)
                    await agree.first.click()
            await human_delay(1500, 3500)
        deadline = time.time() + 90
        while time.time() < deadline:
            if is_business_home_url(page.url):
                return
            await asyncio.sleep(2)
        raise RuntimeError(f"登录后未进入业务页，当前页面: {page.url}")
