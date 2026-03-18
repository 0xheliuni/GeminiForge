# GeminiForge

`GeminiForge` 用于自动注册和刷新 Gemini Business 账号，并把结果同步到 `gemini-business2api`。

## 已支持能力

- 支持 `worker` 邮箱
- 支持 `moemail` 邮箱
- 支持自动上传到 `gemini-business2api`
- 支持刷新已过期或即将过期的账号
- 支持配置文件方式运行
- 支持 GitHub Actions 部署

## 本次配置方式

现在支持两种配置方式：

1. 环境变量 / GitHub Secrets
2. 配置文件 `config.json` / `config.local.json`

配置优先级：

1. 环境变量
2. `config.local.json`
3. `config.json`
4. 代码默认值

这意味着你可以：

- 把大部分配置放进配置文件
- 再用少量 Secrets 覆盖敏感值
- 本地和 GitHub 共用同一套结构

## 配置文件

模板文件：`GeminiForge/config.example.json`

建议：

- 本地复制为 `config.local.json`
- GitHub 上使用 Secret `CONFIG_JSON`

默认会自动查找：

- `config.local.json`
- `config.json`

也可以手动指定：

```text
CONFIG_PATH=config.json
```

### 配置文件示例

```json
{
  "run_mode": "both",
  "email_provider": "moemail",
  "worker_domain": "",
  "email_domain": "",
  "admin_password": "",
  "moemail_base_url": "https://moemail.app",
  "moemail_api_key": "your_api_key",
  "moemail_domain": "",
  "sync_url": "https://your-gemini-business2api.example.com",
  "sync_key": "your_sync_key",
  "register_count": 1,
  "concurrent": 1,
  "refresh_before_hours": 0,
  "refresh_limit": 0,
  "refresh_include_disabled": false,
  "account_expire_hours": 20,
  "proxy": "",
  "proxy_email": false,
  "vless_config": ""
}
```

## 关键参数

### 通用

- `run_mode`：`register` / `refresh` / `both`
- `sync_url`：`gemini-business2api` 地址
- `sync_key`：`gemini-business2api` 管理密钥
- `register_count`：注册数量
- `concurrent`：并发数
- `refresh_before_hours`：提前多少小时刷新，`0` 表示只刷新已过期账号
- `refresh_limit`：单次最多刷新多少个，`0` 表示不限制
- `refresh_include_disabled`：是否包含 `disabled=true` 的账号
- `account_expire_hours`：上传时写入的过期小时数，默认 `20`
- `proxy`：浏览器和同步接口代理
- `proxy_email`：邮箱 API 是否也走代理
- `vless_config`：可选

### `worker` 模式

- `email_provider=worker`
- `worker_domain`
- `email_domain`
- `admin_password`

### `moemail` 模式

- `email_provider=moemail`
- `moemail_base_url`
- `moemail_api_key`
- `moemail_domain`

## 为什么现在能自动刷新

`gemini-business2api` 的刷新功能不只需要 Gemini cookie，还需要邮箱提供商信息和邮箱凭据。

现在上传的账号会自动携带：

- `mail_provider`
- `mail_address`
- `mail_password`
- `mail_base_url`
- `mail_api_key`
- `mail_domain`

其中：

- `moemail` 的 `mail_password` 保存的是 `email_id`
- 这和 `gemini-business2api` 现有刷新逻辑兼容

## GitHub Actions Secrets

### 方案一：拆分多个 Secret

#### `moemail` 推荐方案

```text
EMAIL_PROVIDER=moemail
MOEMAIL_BASE_URL=https://moemail.app
MOEMAIL_API_KEY=你的_moemail_api_key
MOEMAIL_DOMAIN=
SYNC_URL=https://你的_gemini-business2api_地址
SYNC_KEY=你的_gemini_business2api_admin_key
REFRESH_BEFORE_HOURS=0
REFRESH_LIMIT=0
REFRESH_INCLUDE_DISABLED=false
```

#### `worker` 方案

```text
EMAIL_PROVIDER=worker
WORKER_DOMAIN=你的_worker_域名
EMAIL_DOMAIN=你的邮箱域名
ADMIN_PASSWORD=你的_worker_admin_password
SYNC_URL=https://你的_gemini-business2api_地址
SYNC_KEY=你的_gemini_business2api_admin_key
REFRESH_BEFORE_HOURS=0
REFRESH_LIMIT=0
REFRESH_INCLUDE_DISABLED=false
```

### 方案二：只用一个配置文件 Secret

如果你想把配置提取成文件结构，同时仍在 GitHub 上部署，推荐直接使用：

```text
CONFIG_JSON={"run_mode":"both","email_provider":"moemail","moemail_base_url":"https://moemail.app","moemail_api_key":"你的key","moemail_domain":"","sync_url":"https://你的2api地址","sync_key":"你的管理密钥","register_count":1,"concurrent":1,"refresh_before_hours":0,"refresh_limit":0,"refresh_include_disabled":false,"account_expire_hours":20,"proxy":"","proxy_email":false,"vless_config":""}
```

工作流会自动把它写成 `config.json` 再执行。

## 可选代理配置

```text
PROXY=http://user:pass@host:port
PROXY_EMAIL=false
VLESS_CONFIG=
```

说明：

- `PROXY` 给浏览器和同步接口使用
- `PROXY_EMAIL=true` 时，邮箱 API 也会走代理

## 工作流说明

工作流文件：`GeminiForge/.github/workflows/register.yml`

已支持：

- 传统 Secrets 方式
- `CONFIG_JSON` 自动生成配置文件方式

手动触发参数：

- `mode`
- `mail_provider`
- `count`
- `concurrent`
- `refresh_before_hours`

推荐手动输入：

```text
mode=both
mail_provider=moemail
count=1
concurrent=1
refresh_before_hours=0
```

含义：

1. 先刷新过期账号
2. 再注册新账号
3. 最后自动上传到 `gemini-business2api`

定时任务默认每 6 小时执行一次。

## 文件说明

- `GeminiForge/register.py`：主逻辑
- `GeminiForge/config.example.json`：配置文件模板
- `GeminiForge/.github/workflows/register.yml`：GitHub Actions 工作流

## 变更范围

只修改了 `GeminiForge`。

`gemini-business2api` 仅用于参考，没有改动任何一个字符。
