# -*- coding: utf-8 -*-
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from browser import GeminiRegistrar, ProxyRotator
from config import parse_expiration
from providers import build_mail_provider
from syncer import CredentialSyncer

logger = logging.getLogger(__name__)


async def process_register(
    worker_id: int, config: Dict, proxy_url: str = ""
) -> Optional[Dict]:
    logger.info(
        f"[Register-{worker_id}] 开始处理"
        + (f" (proxy: {proxy_url[:40]}...)" if proxy_url else "")
    )
    registrar = GeminiRegistrar(build_mail_provider(config), proxy_url=proxy_url)
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


async def process_refresh(
    account: Dict, config: Dict, proxy_url: str = ""
) -> Optional[Dict]:
    provider_name = (
        str(account.get("mail_provider") or config.get("email_provider") or "worker")
        .strip()
        .lower()
    )
    if provider_name not in {"worker", "moemail"}:
        logger.info(f"跳过不支持刷新的账号: {account.get('id')} ({provider_name})")
        return None
    registrar = GeminiRegistrar(
        build_mail_provider(config, account=account), proxy_url=proxy_url
    )
    if await registrar.execute(existing_account=account):
        payload = registrar.credential.to_dict(existing=account)
        logger.info(f"刷新成功: {payload['id']}")
        return payload
    logger.warning(f"刷新失败: {account.get('id')}")
    return None


async def run_register_flow(
    config: Dict, syncer: CredentialSyncer, rotator: Optional[ProxyRotator] = None
) -> List[Dict]:
    count = int(config.get("register_count", 1))
    concurrent = max(1, int(config.get("concurrent", 1)))
    logger.info(
        f"注册模式启动: count={count}, concurrent={concurrent}, provider={config.get('email_provider')}"
    )
    syncer.ensure_accounts_cache()
    if concurrent == 1:
        results: List[Dict] = []
        for index in range(count):
            proxy_url = rotator.next() if rotator and rotator.available else ""
            item = await process_register(index + 1, config, proxy_url=proxy_url)
            if item:
                if not syncer.sync_one(item):
                    raise RuntimeError(f"新注册账号上传失败: {item.get('id')}")
                results.append(item)
            if index < count - 1:
                await asyncio.sleep(random.randint(8, 20))
        return results

    semaphore = asyncio.Semaphore(concurrent)

    async def limited_process_register(worker_id: int) -> Optional[Dict]:
        async with semaphore:
            proxy_url = rotator.next() if rotator and rotator.available else ""
            return await process_register(worker_id, config, proxy_url=proxy_url)

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


async def run_refresh_flow(
    config: Dict, syncer: CredentialSyncer, rotator: Optional[ProxyRotator] = None
) -> List[Dict]:
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
        proxy_url = rotator.next() if rotator and rotator.available else ""
        updated = await process_refresh(account, config, proxy_url=proxy_url)
        if updated:
            if not syncer.sync_one(updated):
                raise RuntimeError(f"刷新后的账号上传失败: {updated.get('id')}")
            refreshed.append(updated)
            if not updated.get("disabled"):
                syncer.enable_account(updated["id"])
        if index < len(candidates):
            await asyncio.sleep(random.randint(5, 12))
    return refreshed
