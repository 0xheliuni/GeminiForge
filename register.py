# -*- coding: utf-8 -*-
import asyncio
import logging
import os
from typing import Any, Dict, List

import config as app_config
from browser import HAS_FAKER, HAS_STEALTH, ProxyRotator
from config import load_file_config, pick_config, set_proxy, validate_config
from flows import run_refresh_flow, run_register_flow
from syncer import CredentialSyncer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
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

    set_proxy(os.environ.get("PROXY", "") or str(file_config.get("proxy") or ""))
    vless_config = os.environ.get("VLESS_CONFIG", "").strip()
    if vless_config:
        try:
            from proxy_helper import setup_proxy

            logger.info("正在启动 VLESS 代理...")
            proxy_process = setup_proxy()
            if proxy_process:
                set_proxy(os.environ.get("PROXY", ""))
                logger.info(f"VLESS 代理已启用: {app_config.PROXY}")
        except Exception as exc:
            logger.warning(f"VLESS 代理启动失败: {exc}")

    run_mode = str(
        pick_config(file_config, "RUN_MODE", "run_mode", "register", str) or "register"
    ).lower()
    email_provider = str(
        pick_config(file_config, "EMAIL_PROVIDER", "email_provider", "worker", str)
        or "worker"
    ).lower()

    config: Dict[str, Any] = {
        "run_mode": run_mode,
        "email_provider": email_provider,
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
        "register_max_retries": pick_config(
            file_config, "REGISTER_MAX_RETRIES", "register_max_retries", 2, int
        ),
        "proxy_validate": pick_config(
            file_config, "PROXY_VALIDATE", "proxy_validate", False, bool
        ),
        "save_failure_screenshots": pick_config(
            file_config,
            "SAVE_FAILURE_SCREENSHOTS",
            "save_failure_screenshots",
            True,
            bool,
        ),
    }
    validate_config(config, str(config["run_mode"]))

    proxy_pool_env = os.environ.get("PROXY_POOL", "").strip()
    if proxy_pool_env:
        proxy_pool_list = [p.strip() for p in proxy_pool_env.split(",") if p.strip()]
    else:
        raw_pool = file_config.get("proxy_pool", [])
        proxy_pool_list = raw_pool if isinstance(raw_pool, list) else []

    if proxy_pool_list:
        try:
            from proxy_helper import setup_proxy_pool

            logger.info("正在处理代理池（支持订阅链接/VLESS/VMESS/HTTP代理）...")
            converted, _sb_procs = setup_proxy_pool(proxy_pool_list)
            if converted:
                proxy_pool_list = converted
                logger.info(f"代理池处理完成，可用代理: {len(converted)} 个")
            else:
                logger.warning("代理池处理后无可用代理")
                proxy_pool_list = []
        except Exception as exc:
            logger.warning(f"代理池初始化失败: {exc}，将尝试使用原始列表")

    proxy_validate = bool(config.get("proxy_validate", False))
    rotator = ProxyRotator(
        pool=proxy_pool_list, single=app_config.PROXY, validate=proxy_validate
    )

    print(f"\n{'=' * 56}")
    print("  GeminiForge")
    print(f"  mode: {config['run_mode']}")
    print(f"  provider: {config['email_provider']}")
    print(f"  register_count: {config['register_count']}")
    print(f"  concurrent: {config['concurrent']}")
    print(f"  proxy_pool: {len(rotator._proxies)} endpoint(s)")
    print(f"  stealth: {'enabled' if HAS_STEALTH else 'disabled'}")
    print(f"  faker: {'enabled' if HAS_FAKER else 'disabled'}")
    print(f"{'=' * 56}\n")

    if not HAS_STEALTH:
        logger.warning(
            "playwright-stealth 未安装，反检测能力受限，建议: pip install playwright-stealth"
        )
    if not HAS_FAKER:
        logger.warning("faker 未安装，使用随机字符串作为姓名，建议: pip install faker")
    if rotator.available:
        logger.info(f"代理轮换已启用: {len(rotator._proxies)} 个代理端点")
    else:
        logger.warning("未配置代理，将使用直连 IP（不推荐，容易被风控）")

    syncer = CredentialSyncer(str(config["sync_url"]), str(config["sync_key"]))
    refreshed_accounts: List[Dict] = []
    new_accounts: List[Dict] = []
    if config["run_mode"] in {"refresh", "both"}:
        refreshed_accounts = await run_refresh_flow(config, syncer, rotator)
        logger.info(f"刷新完成: {len(refreshed_accounts)} 个")
    if config["run_mode"] in {"register", "both"}:
        new_accounts = await run_register_flow(config, syncer, rotator)
        logger.info(f"注册完成: {len(new_accounts)} 个")
    if not refreshed_accounts and not new_accounts:
        logger.info("本次没有可上传或可刷新的账号")


if __name__ == "__main__":
    asyncio.run(main())
