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
    stealth_async = getattr(_playwright_stealth, "stealth_async", None)
    HAS_STEALTH = callable(stealth_async)
except ImportError:
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
    "120.0.0.0",
    "121.0.0.0",
    "122.0.0.0",
    "123.0.0.0",
    "124.0.0.0",
    "125.0.0.0",
    "126.0.0.0",
    "127.0.0.0",
    "128.0.0.0",
    "129.0.0.0",
    "130.0.0.0",
    "131.0.0.0",
    "132.0.0.0",
    "133.0.0.0",
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

LOCALE_TZ_PAIRS = [
    ("en-US", "America/New_York"),
    ("en-US", "America/Chicago"),
    ("en-US", "America/Denver"),
    ("en-US", "America/Los_Angeles"),
    ("en-GB", "Europe/London"),
    ("en-AU", "Australia/Sydney"),
    ("en-CA", "America/Toronto"),
]

PLATFORM_UA_TEMPLATES = [
    ("Windows NT 10.0; Win64; x64", "Chrome/{ver} Safari/537.36"),
    ("Macintosh; Intel Mac OS X 10_15_7", "Chrome/{ver} Safari/537.36"),
    ("X11; Linux x86_64", "Chrome/{ver} Safari/537.36"),
]


def random_name(length: int = 5) -> str:
    if _fake:
        return _fake.first_name()
    return "".join(random.choices(string.ascii_lowercase, k=length))


def generate_fingerprint() -> Dict:
    chrome_ver = random.choice(CHROME_VERSIONS)
    viewport = random.choice(VIEWPORT_PRESETS)
    locale, tz = random.choice(LOCALE_TZ_PAIRS)
    platform_part, browser_part = random.choice(PLATFORM_UA_TEMPLATES)
    ua = (
        f"Mozilla/5.0 ({platform_part}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) {browser_part.format(ver=chrome_ver)}"
    )
    return {
        "user_agent": ua,
        "viewport": viewport,
        "locale": locale,
        "timezone_id": tz,
    }


async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


async def human_type(locator, text: str) -> None:
    try:
        await locator.press_sequentially(text, delay=random.uniform(60, 180))
    except Exception:
        await locator.fill(text)


async def human_mouse_move(
    page, x: Optional[int] = None, y: Optional[int] = None
) -> None:
    vp = page.viewport_size or {"width": 1920, "height": 1080}
    target_x = x if x is not None else random.randint(100, vp["width"] - 100)
    target_y = y if y is not None else random.randint(100, vp["height"] - 100)
    await page.mouse.move(target_x, target_y, steps=random.randint(8, 25))


async def human_scroll(page, direction: str = "down") -> None:
    amount = random.randint(120, 400)
    if direction == "up":
        amount = -amount
    await page.mouse.wheel(0, amount)
    await human_delay(300, 800)


async def simulate_human_presence(page) -> None:
    await human_delay(800, 2000)
    await human_mouse_move(page)
    await human_delay(300, 800)
    if random.random() < 0.4:
        await human_scroll(page)
    await human_delay(200, 600)


class ProxyRotator:
    def __init__(self, pool: Optional[List[str]] = None, single: str = ""):
        self._proxies: List[str] = []
        self._index = 0
        if pool:
            if isinstance(pool, str):
                pool = [p.strip() for p in pool.split(",") if p.strip()]
            self._proxies = [p for p in pool if p]
        elif single:
            self._proxies = [single]

    def next(self) -> str:
        if not self._proxies:
            return ""
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return proxy

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
        browser_proxy = (
            self.proxy_url or os.environ.get("PROXY", "") or app_config.PROXY
        )
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
                )
                page = await context.new_page()
                if HAS_STEALTH and stealth_async is not None:
                    await stealth_async(page)
                else:
                    logger.warning("playwright-stealth 未安装，反检测能力受限")
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
