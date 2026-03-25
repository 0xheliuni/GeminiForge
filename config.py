# -*- coding: utf-8 -*-
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional, Type
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PROXY = os.environ.get("PROXY", "")
AUTH_HOME_URL = "https://auth.business.gemini.google/login"
LOGIN_URL = "https://auth.business.gemini.google/login?continueUrl=https://business.gemini.google/"


def set_proxy(value: str) -> None:
    global PROXY
    PROXY = value or ""


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


def parse_expiration(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


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
    config_data: Dict[str, Any],
    env_name: str,
    config_key: str,
    default: Any = None,
    value_type: Type[Any] = str,
) -> Any:
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
            return int(str(raw_value))
        if value_type is float:
            return float(str(raw_value))
        if value_type is str:
            return str(raw_value).strip()
        return value_type(raw_value)
    except Exception:
        return default


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
