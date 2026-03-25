# -*- coding: utf-8 -*-
import logging
import time
from typing import Dict, List, Optional

from net_utils import build_session, maybe_apply_proxy

logger = logging.getLogger(__name__)


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
