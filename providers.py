# -*- coding: utf-8 -*-
import logging
import random
import re
import string
import time
from datetime import datetime
from typing import Dict, List, Optional

from net_utils import build_session, maybe_apply_proxy

logger = logging.getLogger(__name__)


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
