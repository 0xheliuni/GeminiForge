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
    worker_id: int,
    config: Dict,
    proxy_url: str = "",
    rotator: Optional[ProxyRotator] = None,
    max_retries: int = 2,
) -> Optional[Dict]:
    for attempt in range(max_retries + 1):
        current_proxy = proxy_url if attempt == 0 else (
            rotator.next() if rotator and rotator.available else proxy_url
        )
        logger.info(
            f"[Register-{worker_id}] 开始处理"
            + (f" (attempt {attempt + 1}/{max_retries + 1})" if max_retries > 0 else "")
            + (f" (proxy: {current_proxy[:40]}...)" if current_proxy else "")
        )
        registrar = GeminiRegistrar(build_mail_provider(config), proxy_url=current_proxy)
        if await registrar.execute():
            payload = registrar.credential.to_dict()
            logger.info(f"[Register-{worker_id}] 成功: {payload['id']}")
            return payload
        if rotator and current_proxy:
            rotator.mark_failed(current_proxy)
        if attempt < max_retries:
            wait = random.randint(5, 15)
            logger.warning(
                f"[Register-{worker_id}] 第 {attempt + 1} 次失败，{wait}s 后换代理重试..."
            )
            await asyncio.sleep(wait)
    logger.info(f"[Register-{worker_id}] 所有尝试均失败")
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
    account: Dict,
    config: Dict,
    proxy_url: str = "",
    rotator: Optional[ProxyRotator] = None,
    max_retries: int = 1,
) -> Optional[Dict]:
    provider_name = (
        str(account.get("mail_provider") or config.get("email_provider") or "worker")
        .strip()
        .lower()
    )
    if provider_name not in {"worker", "moemail"}:
        logger.info(f"跳过不支持刷新的账号: {account.get('id')} ({provider_name})")
        return None
    for attempt in range(max_retries + 1):
        current_proxy = proxy_url if attempt == 0 else (
            rotator.next() if rotator and rotator.available else proxy_url
        )
        registrar = GeminiRegistrar(
            build_mail_provider(config, account=account), proxy_url=current_proxy
        )
        if await registrar.execute(existing_account=account):
            payload = registrar.credential.to_dict(existing=account)
            logger.info(f"刷新成功: {payload['id']}")
            return payload
        if rotator and current_proxy:
            rotator.mark_failed(current_proxy)
        if attempt < max_retries:
            wait = random.randint(5, 12)
            logger.warning(
                f"刷新失败 {account.get('id')}，{wait}s 后换代理重试 "
                f"({attempt + 1}/{max_retries + 1})..."
            )
            await asyncio.sleep(wait)
    logger.warning(f"刷新失败 (已用尽重试): {account.get('id')}")
    return None


async def run_register_flow(
    config: Dict, syncer: CredentialSyncer, rotator: Optional[ProxyRotator] = None
) -> List[Dict]:
    count = int(config.get("register_count", 1))
    concurrent = max(1, int(config.get("concurrent", 1)))
    max_retries = int(config.get("register_max_retries", 2))
    logger.info(
        f"注册模式启动: count={count}, concurrent={concurrent}, "
        f"max_retries={max_retries}, provider={config.get('email_provider')}"
    )
    syncer.ensure_accounts_cache()
    if concurrent == 1:
        results: List[Dict] = []
        for index in range(count):
            proxy_url = rotator.next() if rotator and rotator.available else ""
            item = await process_register(
                index + 1, config, proxy_url=proxy_url,
                rotator=rotator, max_retries=max_retries,
            )
            if item:
                if not syncer.sync_one(item):
                    raise RuntimeError(f"新注册账号上传失败: {item.get('id')}")
                results.append(item)
            if index < count - 1:
                interval = random.uniform(10, 30) + random.expovariate(0.15)
                await asyncio.sleep(min(interval, 60))
        return results

    semaphore = asyncio.Semaphore(concurrent)

    async def limited_process_register(worker_id: int) -> Optional[Dict]:
        async with semaphore:
            proxy_url = rotator.next() if rotator and rotator.available else ""
            return await process_register(
                worker_id, config, proxy_url=proxy_url,
                rotator=rotator, max_retries=max_retries,
            )

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
    max_retries = int(config.get("register_max_retries", 1))
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
        updated = await process_refresh(
            account, config, proxy_url=proxy_url,
            rotator=rotator, max_retries=max_retries,
        )
        if updated:
            if not syncer.sync_one(updated):
                raise RuntimeError(f"刷新后的账号上传失败: {updated.get('id')}")
            refreshed.append(updated)
            if not updated.get("disabled"):
                syncer.enable_account(updated["id"])
        if index < len(candidates):
            interval = random.uniform(8, 20) + random.expovariate(0.2)
            await asyncio.sleep(min(interval, 45))
    return refreshed
