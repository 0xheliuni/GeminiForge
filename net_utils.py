# -*- coding: utf-8 -*-
import os

import requests
from requests.adapters import HTTPAdapter

import config as app_config
from config import parse_bool


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
    proxy = os.environ.get("PROXY", "") or app_config.PROXY
    if proxy and not session.proxies:
        session.proxies = {"http": proxy, "https": proxy}
