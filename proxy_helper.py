# -*- coding: utf-8 -*-
"""
代理启动器 - 支持 VLESS/VMESS/Trojan/SS 节点 + 订阅链接
通过 sing-box 将代理节点转为本地 HTTP 代理供 Playwright 使用
"""

import base64
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

LOCAL_HTTP_PORT = 7890
LOCAL_SOCKS_PORT = 7891
MAX_POOL_NODES = int(os.environ.get("MAX_POOL_NODES", "5"))

# ---------------------------------------------------------------------------
# Node URL parsers
# ---------------------------------------------------------------------------

def parse_vless_url(vless_url: str) -> Dict[str, Any]:
    parsed = urlparse(vless_url)
    uuid = unquote(parsed.username) if parsed.username else ""
    params = parse_qs(parsed.query)
    return {
        "protocol": "vless",
        "uuid": uuid,
        "server": parsed.hostname,
        "port": parsed.port or 443,
        "type": params.get("type", ["tcp"])[0],
        "security": params.get("security", ["none"])[0],
        "flow": params.get("flow", [""])[0],
        "sni": params.get("sni", [""])[0],
        "fp": params.get("fp", ["chrome"])[0],
        "pbk": params.get("pbk", [""])[0],
        "sid": params.get("sid", [""])[0],
    }


def parse_vmess_url(vmess_url: str) -> Dict[str, Any]:
    raw = vmess_url.replace("vmess://", "", 1)
    if "#" in raw:
        raw = raw.split("#")[0]
    padding = 4 - len(raw) % 4
    if padding != 4:
        raw += "=" * padding
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        cfg = json.loads(decoded)
    except Exception:
        return {}
    cfg["protocol"] = "vmess"
    return cfg


def parse_trojan_url(trojan_url: str) -> Dict[str, Any]:
    parsed = urlparse(trojan_url)
    params = parse_qs(parsed.query)
    return {
        "protocol": "trojan",
        "password": unquote(parsed.username or ""),
        "server": parsed.hostname,
        "port": parsed.port or 443,
        "sni": params.get("sni", [""])[0],
        "type": params.get("type", ["tcp"])[0],
        "host": params.get("host", [""])[0],
        "path": params.get("path", [""])[0],
    }


def parse_ss_url(ss_url: str) -> Dict[str, Any]:
    raw = ss_url.replace("ss://", "", 1)
    if "#" in raw:
        raw = raw.split("#")[0]
    try:
        if "@" in raw:
            encoded_part, server_part = raw.split("@", 1)
            padding = 4 - len(encoded_part) % 4
            if padding != 4:
                encoded_part += "=" * padding
            try:
                decoded = base64.b64decode(encoded_part).decode()
            except Exception:
                decoded = unquote(encoded_part)
            method, password = decoded.split(":", 1)
            sp = urlparse(f"ss://{server_part}")
            return {
                "protocol": "shadowsocks",
                "method": method,
                "password": password,
                "server": sp.hostname,
                "port": sp.port or 8388,
            }
        else:
            padding = 4 - len(raw) % 4
            if padding != 4:
                raw += "=" * padding
            decoded = base64.b64decode(raw).decode()
            method_pass, server_port = decoded.rsplit("@", 1)
            method, password = method_pass.split(":", 1)
            server, port = server_port.rsplit(":", 1)
            return {
                "protocol": "shadowsocks",
                "method": method,
                "password": password,
                "server": server,
                "port": int(port),
            }
    except Exception:
        return {}


def parse_yaml_config(yaml_str: str) -> Dict[str, Any]:
    import re

    config: Dict[str, Any] = {}
    patterns = {
        "server": r"server:\s*([^\s,}]+)",
        "port": r"port:\s*(\d+)",
        "uuid": r"uuid:\s*([^\s,}]+)",
        "flow": r"flow:\s*([^\s,}]+)",
        "sni": r"servername:\s*([^\s,}]+)",
        "pbk": r"public-key:\s*([^\s,}]+)",
        "sid": r"short-id:\s*([^\s,}]+)",
        "fp": r"client-fingerprint:\s*([^\s,}]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, yaml_str)
        if match and match.group(1) != "null":
            config[key] = match.group(1)

    if "reality-opts" in yaml_str or "pbk" in config:
        config["security"] = "reality"
    elif "tls: true" in yaml_str:
        config["security"] = "tls"
    else:
        config["security"] = "none"
    config["type"] = "tcp"
    config["protocol"] = "vless"
    return config


# ---------------------------------------------------------------------------
# sing-box outbound builders
# ---------------------------------------------------------------------------

def _build_vless_outbound(cfg: Dict[str, Any], tag: str = "proxy") -> Dict[str, Any]:
    outbound: Dict[str, Any] = {
        "type": "vless",
        "tag": tag,
        "server": cfg.get("server"),
        "server_port": int(cfg.get("port", 443)),
        "uuid": cfg.get("uuid"),
    }
    flow = cfg.get("flow", "")
    if flow:
        outbound["flow"] = flow
    security = cfg.get("security", "none")
    if security == "reality":
        tls_cfg: Dict[str, Any] = {
            "enabled": True,
            "server_name": cfg.get("sni", ""),
            "utls": {"enabled": True, "fingerprint": cfg.get("fp", "chrome")},
            "reality": {"enabled": True, "public_key": cfg.get("pbk", "")},
        }
        sid = cfg.get("sid", "")
        if sid:
            tls_cfg["reality"]["short_id"] = sid
        outbound["tls"] = tls_cfg
    elif security == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": cfg.get("sni", outbound["server"]),
            "utls": {"enabled": True, "fingerprint": cfg.get("fp", "chrome")},
        }
    return outbound


def _build_vmess_outbound(cfg: Dict[str, Any], tag: str = "proxy") -> Dict[str, Any]:
    outbound: Dict[str, Any] = {
        "type": "vmess",
        "tag": tag,
        "server": cfg.get("add", ""),
        "server_port": int(cfg.get("port", 443)),
        "uuid": cfg.get("id", ""),
        "security": cfg.get("scy", "auto"),
        "alter_id": int(cfg.get("aid", 0)),
    }
    if cfg.get("tls") == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": cfg.get("sni") or cfg.get("host") or cfg.get("add", ""),
        }
    net = cfg.get("net", "tcp")
    if net == "ws":
        transport: Dict[str, Any] = {
            "type": "ws",
            "path": cfg.get("path", "/"),
        }
        host = cfg.get("host", "")
        if host:
            transport["headers"] = {"Host": host}
        outbound["transport"] = transport
    elif net == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": cfg.get("path", ""),
        }
    elif net == "h2":
        transport_h2: Dict[str, Any] = {"type": "http"}
        host = cfg.get("host", "")
        if host:
            transport_h2["host"] = [host]
        path = cfg.get("path", "")
        if path:
            transport_h2["path"] = path
        outbound["transport"] = transport_h2
    return outbound


def _build_trojan_outbound(cfg: Dict[str, Any], tag: str = "proxy") -> Dict[str, Any]:
    outbound: Dict[str, Any] = {
        "type": "trojan",
        "tag": tag,
        "server": cfg.get("server", ""),
        "server_port": int(cfg.get("port", 443)),
        "password": cfg.get("password", ""),
        "tls": {
            "enabled": True,
            "server_name": cfg.get("sni") or cfg.get("server", ""),
        },
    }
    net = cfg.get("type", "tcp")
    if net == "ws":
        transport: Dict[str, Any] = {"type": "ws", "path": cfg.get("path", "/")}
        host = cfg.get("host", "")
        if host:
            transport["headers"] = {"Host": host}
        outbound["transport"] = transport
    elif net == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": cfg.get("path", ""),
        }
    return outbound


def _build_ss_outbound(cfg: Dict[str, Any], tag: str = "proxy") -> Dict[str, Any]:
    return {
        "type": "shadowsocks",
        "tag": tag,
        "server": cfg.get("server", ""),
        "server_port": int(cfg.get("port", 8388)),
        "method": cfg.get("method", "aes-256-gcm"),
        "password": cfg.get("password", ""),
    }


def _build_outbound_from_url(url: str, tag: str = "proxy") -> Optional[Dict[str, Any]]:
    try:
        if url.startswith("vless://"):
            cfg = parse_vless_url(url)
            if cfg.get("server") and cfg.get("uuid"):
                return _build_vless_outbound(cfg, tag)
        elif url.startswith("vmess://"):
            cfg = parse_vmess_url(url)
            if cfg.get("add") and cfg.get("id"):
                return _build_vmess_outbound(cfg, tag)
        elif url.startswith("trojan://"):
            cfg = parse_trojan_url(url)
            if cfg.get("server") and cfg.get("password"):
                return _build_trojan_outbound(cfg, tag)
        elif url.startswith("ss://"):
            cfg = parse_ss_url(url)
            if cfg.get("server") and cfg.get("password"):
                return _build_ss_outbound(cfg, tag)
    except Exception as exc:
        logger.warning(f"节点解析失败: {exc}")
    return None


# ---------------------------------------------------------------------------
# sing-box config generation & process management
# ---------------------------------------------------------------------------

def generate_singbox_config(
    vless_config: Dict[str, Any],
    http_port: int = LOCAL_HTTP_PORT,
    socks_port: int = LOCAL_SOCKS_PORT,
) -> Dict[str, Any]:
    outbound = _build_vless_outbound(vless_config)
    return _wrap_singbox_config(outbound, http_port, socks_port)


def _wrap_singbox_config(
    outbound: Dict[str, Any], http_port: int, socks_port: int
) -> Dict[str, Any]:
    return {
        "log": {"level": "warn"},
        "inbounds": [
            {
                "type": "http",
                "tag": "http-in",
                "listen": "127.0.0.1",
                "listen_port": http_port,
            },
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": socks_port,
            },
        ],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
    }


def _test_proxy(proxy_url: str, timeout: int = 10) -> bool:
    import requests as _req

    try:
        resp = _req.get(
            "https://www.google.com/generate_204",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def start_singbox(
    config: Dict[str, Any],
    config_path: Optional[str] = None,
    http_port: int = LOCAL_HTTP_PORT,
    wait_seconds: float = 5,
) -> Optional[subprocess.Popen]:
    if config_path is None:
        config_path = os.path.join(tempfile.gettempdir(), "singbox_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    process = subprocess.Popen(
        ["sing-box", "run", "-c", config_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    time.sleep(wait_seconds)

    if process.poll() is None:
        return process

    stderr_pipe = process.stderr
    stderr = stderr_pipe.read().decode() if stderr_pipe else ""
    logger.error(f"sing-box 启动失败 ({config_path}): {stderr[:300]}")
    return None


def _has_singbox() -> bool:
    return shutil.which("sing-box") is not None


# ---------------------------------------------------------------------------
# Subscription URL handling
# ---------------------------------------------------------------------------

_SUB_PATH_KEYWORDS = [
    "sub", "subscribe", "client", "api", "link", "clash",
    "nodelist", "server", "user", "token",
]


def _is_subscription_url(entry: str) -> bool:
    if not entry.startswith(("http://", "https://")):
        return False
    parsed = urlparse(entry)
    path_lower = parsed.path.lower()
    query_lower = parsed.query.lower()
    has_sub_path = any(kw in path_lower for kw in _SUB_PATH_KEYWORDS)
    has_token = "token=" in query_lower or "key=" in query_lower
    has_long_path = len(parsed.path) > 10
    return (has_sub_path and has_long_path) or has_token


def _is_node_url(entry: str) -> bool:
    return entry.startswith(("vless://", "vmess://", "trojan://", "ss://"))


def _try_base64_decode(text: str) -> str:
    try:
        raw = text.strip()
        padding = 4 - len(raw) % 4
        if padding != 4:
            raw += "=" * padding
        decoded = base64.b64decode(raw).decode("utf-8").strip()
        if any(p in decoded for p in ("://",)):
            return decoded
    except Exception:
        pass
    return ""


def _extract_node_urls(text: str) -> List[str]:
    nodes: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if _is_node_url(line):
            nodes.append(line)
    return nodes


def _parse_clash_proxies(content: str) -> List[Dict[str, str]]:
    """Extract proxy configs from Clash YAML without a YAML library."""
    import re

    if "proxies:" not in content:
        return []

    results: List[Dict[str, str]] = []

    for m in re.finditer(r"-\s*\{([^}]+)\}", content):
        pairs: Dict[str, str] = {}
        for kv in re.finditer(
            r"""([\w-]+)\s*:\s*(?:"([^"]*)"|'([^']*)'|(\S+?))\s*(?:,|$)""",
            m.group(1),
        ):
            pairs[kv.group(1)] = kv.group(2) or kv.group(3) or kv.group(4) or ""
        if pairs.get("type") and pairs.get("server"):
            results.append(pairs)

    if results:
        return results

    lines = content.split("\n")
    in_proxies = False
    current: Dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if stripped == "proxies:":
            in_proxies = True
            continue
        if not in_proxies:
            continue
        if line and not line[0].isspace() and ":" in stripped and not stripped.startswith("-"):
            break
        if stripped.startswith("- "):
            if current and current.get("type") and current.get("server"):
                results.append(current)
            current = {}
            rest = stripped[2:].strip()
            kv_m = re.match(r"([\w-]+)\s*:\s*(?:\"([^\"]*)\"|'([^']*)'|(.*))", rest)
            if kv_m:
                current[kv_m.group(1)] = (
                    kv_m.group(2) or kv_m.group(3) or kv_m.group(4) or ""
                ).strip()
        elif stripped and in_proxies:
            kv_m = re.match(r"([\w-]+)\s*:\s*(?:\"([^\"]*)\"|'([^']*)'|(.*))", stripped)
            if kv_m:
                current[kv_m.group(1)] = (
                    kv_m.group(2) or kv_m.group(3) or kv_m.group(4) or ""
                ).strip()

    if current and current.get("type") and current.get("server"):
        results.append(current)

    return results


def _clash_proxy_to_url(proxy: Dict[str, str]) -> Optional[str]:
    proxy_type = proxy.get("type", "").lower()
    server = proxy.get("server", "")
    port = proxy.get("port", "443")
    if not server:
        return None

    if proxy_type == "vless":
        uuid = proxy.get("uuid", "")
        if not uuid:
            return None
        params = [f"type={proxy.get('network', 'tcp')}"]
        tls = proxy.get("tls", "")
        if tls in ("true", "True", "1"):
            params.append("security=tls")
            sni = proxy.get("servername", "")
            if sni:
                params.append(f"sni={sni}")
        flow = proxy.get("flow", "")
        if flow:
            params.append(f"flow={flow}")
        fp = proxy.get("client-fingerprint", "")
        if fp:
            params.append(f"fp={fp}")
        return f"vless://{uuid}@{server}:{port}?{'&'.join(params)}"

    if proxy_type == "vmess":
        vmess_cfg = {
            "v": "2",
            "ps": proxy.get("name", ""),
            "add": server,
            "port": str(port),
            "id": proxy.get("uuid", ""),
            "aid": str(proxy.get("alterId", "0")),
            "scy": proxy.get("cipher", "auto"),
            "net": proxy.get("network", "tcp"),
            "type": "none",
            "host": "",
            "path": "",
            "tls": "tls" if proxy.get("tls") in ("true", "True", "1") else "",
            "sni": proxy.get("servername", ""),
        }
        return f"vmess://{base64.b64encode(json.dumps(vmess_cfg).encode()).decode()}"

    if proxy_type == "trojan":
        pw = proxy.get("password", "")
        if not pw:
            return None
        sni = proxy.get("sni") or proxy.get("servername", "")
        url = f"trojan://{pw}@{server}:{port}"
        if sni:
            url += f"?sni={sni}"
        return url

    if proxy_type in ("ss", "shadowsocks"):
        method = proxy.get("cipher", "aes-256-gcm")
        pw = proxy.get("password", "")
        if not pw:
            return None
        encoded = base64.b64encode(f"{method}:{pw}".encode()).decode()
        return f"ss://{encoded}@{server}:{port}"

    return None


def fetch_subscription(url: str, timeout: int = 20) -> List[str]:
    import requests as _req

    logger.info(f"正在拉取订阅链接: {url[:60]}...")

    user_agents = [
        "v2rayN/6.42",
        "V2rayU/4.0.0",
        "Mozilla/5.0",
    ]

    content = ""
    for ua in user_agents:
        try:
            resp = _req.get(url, timeout=timeout, headers={"User-Agent": ua})
            resp.raise_for_status()
            content = resp.text.strip()
            if content:
                logger.info(f"订阅拉取成功 (UA={ua[:15]})，内容长度: {len(content)}")
                break
        except Exception as exc:
            logger.warning(f"订阅拉取失败 (UA={ua[:15]}): {exc}")

    if not content:
        logger.error("订阅链接所有尝试均失败")
        return []

    nodes = _extract_node_urls(content)
    if nodes:
        logger.info(f"订阅解析完成 (明文): {len(nodes)} 个节点")
        return nodes

    decoded = _try_base64_decode(content)
    if decoded:
        nodes = _extract_node_urls(decoded)
        if nodes:
            logger.info(f"订阅解析完成 (base64): {len(nodes)} 个节点")
            return nodes

    clash_proxies = _parse_clash_proxies(content)
    if clash_proxies:
        logger.info(f"检测到 Clash YAML 格式，解析到 {len(clash_proxies)} 个节点")
        for proxy_dict in clash_proxies:
            node_url = _clash_proxy_to_url(proxy_dict)
            if node_url:
                nodes.append(node_url)
        if nodes:
            logger.info(f"Clash YAML 转换完成: {len(nodes)} 个可用节点")
            return nodes

    logger.warning(
        f"订阅内容无法解析为代理节点 (前200字: {content[:200]})"
    )
    return []


# ---------------------------------------------------------------------------
# Proxy pool setup (main entry point)
# ---------------------------------------------------------------------------

def setup_proxy_pool(
    pool_entries: List[str],
    max_nodes: int = 0,
) -> Tuple[List[str], List[subprocess.Popen]]:
    """Process proxy pool entries.

    Supported entry formats:
      - http://host:port  (direct HTTP proxy, pass through)
      - socks5://host:port  (direct SOCKS5 proxy, pass through)
      - vless://...  vmess://...  trojan://...  ss://...  (node URL → sing-box)
      - https://sub.example.com/...?token=xxx  (subscription URL → fetch → nodes)
      - host:port  (treated as http://host:port)

    Returns (usable_proxy_list, sing_box_processes).
    """
    if max_nodes <= 0:
        max_nodes = MAX_POOL_NODES

    has_sb = _has_singbox()

    direct_proxies: List[str] = []
    node_urls: List[str] = []

    for entry in pool_entries:
        entry = entry.strip()
        if not entry:
            continue

        if _is_subscription_url(entry):
            logger.info(f"[代理池] 检测为订阅链接: {entry[:60]}...")
            fetched = fetch_subscription(entry)
            node_urls.extend(fetched)
        elif _is_node_url(entry):
            logger.info(f"[代理池] 检测为节点 URL: {entry[:40]}...")
            node_urls.append(entry)
        elif entry.startswith(("http://", "https://", "socks5://")):
            parsed_e = urlparse(entry)
            if parsed_e.port:
                logger.info(f"[代理池] 检测为直连代理: {entry[:50]}")
                direct_proxies.append(entry)
            else:
                logger.warning(
                    f"[代理池] HTTP(S) URL 无端口号，可能是订阅链接: {entry[:60]}..."
                )
                fetched = fetch_subscription(entry)
                if fetched:
                    node_urls.extend(fetched)
                else:
                    logger.warning(f"[代理池] 当作直连代理保留: {entry[:50]}")
                    direct_proxies.append(entry)
        else:
            direct_proxies.append(f"http://{entry}")

    if not node_urls:
        if not direct_proxies:
            logger.warning("代理池为空，未找到可用节点")
        return direct_proxies, []

    if not has_sb:
        logger.error(
            f"解析到 {len(node_urls)} 个代理节点，但 sing-box 未安装！"
            "请确保 workflow 中安装了 sing-box。"
        )
        return direct_proxies, []

    if len(node_urls) > max_nodes:
        logger.info(
            f"节点数 ({len(node_urls)}) 超过上限 ({max_nodes})，随机选取子集"
        )
        node_urls = random.sample(node_urls, max_nodes)

    logger.info(f"正在启动 {len(node_urls)} 个 sing-box 代理实例...")

    processes: List[subprocess.Popen] = []
    started_proxies: List[str] = []
    pending: List[Tuple[subprocess.Popen, str, int]] = []

    for idx, url in enumerate(node_urls):
        outbound = _build_outbound_from_url(url)
        if outbound is None:
            logger.warning(f"节点 #{idx} 解析失败，跳过: {url[:50]}...")
            continue
        http_port = 7900 + idx * 2
        socks_port = 7901 + idx * 2
        sb_config = _wrap_singbox_config(outbound, http_port, socks_port)
        cfg_path = os.path.join(tempfile.gettempdir(), f"singbox_pool_{idx}.json")
        with open(cfg_path, "w") as f:
            json.dump(sb_config, f, indent=2)

        proc = subprocess.Popen(
            ["sing-box", "run", "-c", cfg_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pending.append((proc, f"http://127.0.0.1:{http_port}", http_port))

    if not pending:
        return direct_proxies, []

    logger.info(f"等待 sing-box 实例启动 ({len(pending)} 个)...")
    time.sleep(6)

    for proc, proxy_url, port in pending:
        if proc.poll() is not None:
            logger.warning(f"sing-box 实例 (port {port}) 启动失败")
            continue
        if _test_proxy(proxy_url, timeout=8):
            logger.info(f"代理可用: {proxy_url}")
            started_proxies.append(proxy_url)
            processes.append(proc)
        else:
            logger.warning(f"代理连接测试失败: {proxy_url}，但保留进程")
            started_proxies.append(proxy_url)
            processes.append(proc)

    logger.info(
        f"sing-box 代理池就绪: {len(started_proxies)} 个节点代理 + "
        f"{len(direct_proxies)} 个直连代理"
    )
    return direct_proxies + started_proxies, processes


# ---------------------------------------------------------------------------
# Single VLESS setup (backward compatible)
# ---------------------------------------------------------------------------

def setup_proxy() -> Optional[subprocess.Popen]:
    vless_config_str = os.environ.get("VLESS_CONFIG", "")
    if not vless_config_str:
        logger.info("未配置 VLESS_CONFIG，跳过代理设置")
        return None

    logger.info("正在解析 VLESS 配置...")
    vless_config_str = vless_config_str.strip()

    if vless_config_str.startswith("vless://"):
        config = parse_vless_url(vless_config_str)
    else:
        config = parse_yaml_config(vless_config_str)

    if not config.get("server") or not config.get("uuid"):
        logger.error("VLESS 配置解析失败")
        return None

    logger.info("VLESS 配置解析成功")
    singbox_config = generate_singbox_config(config)
    process = start_singbox(singbox_config)

    if process:
        proxy_url = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
        os.environ["PROXY"] = proxy_url
        return process

    return None


if __name__ == "__main__":
    process = setup_proxy()
    if process:
        print("代理已启动，按 Ctrl+C 停止")
        try:
            process.wait()
        except KeyboardInterrupt:
            process.terminate()
