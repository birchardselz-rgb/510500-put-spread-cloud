# 510500/588080 Put 价差云端实时看板

部署在 **Streamlit Community Cloud** 的免费版 —— **电脑关机也能访问**，手机 + 网页自适应，云端自己拉行情。

## 公网访问地址（部署后）

部署完成后你会得到一个公网 URL，形如 `https://你的应用名.streamlit.app`，手机/电脑浏览器直接打开即可。

## 部署步骤（一次性，约 5 分钟）

代码已推送到 GitHub 私有仓 `birchardselz-rgb/510500-put-spread-cloud`（分支 `master`）。

### 1. 登录 Streamlit Cloud
- 浏览器打开 <https://share.streamlit.io/> 或 <https://streamlit.io/>
- 用 **GitHub 账号**登录（Sign in with GitHub）
- 授权时**勾选允许访问私有仓库**（Deploy keys 会以只读方式访问你的 `510500-put-spread-cloud` 私有仓）

### 2. 新建 App
- 点 **"New app"**（或 Create app）
- 选 Repository：`birchardselz-rgb/510500-put-spread-cloud`
- Branch：`master`
- Main file path：`app.py`
- 点 **Deploy**

### 3. 等待部署
- 首次部署安装依赖约 1~3 分钟，页面显示 "Building" → 完成后自动打开
- 应用名可自定义（默认与仓库同名），最终 URL 如 `https://510500-put-spread-cloud.streamlit.app`

## 功能

- **双标的概览**：510500 / 588080 最新价 + 各自 Top1 组合（净收/安全垫/评分/状态）
- **Put 价差排行**：切换标的看 Top，过滤评分/安全垫/宽度/Top N
- **自动刷新**：每 30 秒自动重扫行情（同花顺标的价格 + 新浪期权盘口/Greeks）
- **手机自适应**：`layout=wide`，窄屏自动收缩

## 数据源（云端实测优化）

- **标的价**：同花顺优先（`d.10jqka.com.cn/v6/realhead`，海外云实测可用）→ 新浪备用
- **期权盘口/Greeks**：新浪（`OP_UP/DOWN` + `CON_OP` + `CON_SO`，带 Referer + 重试）
- **东财已移除**：`push2.eastmoney.com` 海外云不可达（socket hang up）

## 免费版限制（重要）

| 限制 | 说明 |
|---|---|
| **休眠** | 约 12 小时无真实访问会休眠；唤醒需冷启动 30~60 秒。不是 7x24 常驻 |
| **存储** | 文件系统临时（重启/重部署被擦除），本应用不依赖本地存储，无影响 |
| **刷新** | 仅在有浏览器标签页打开时按 30 秒刷新；无访问时不扫描（免费版无后台常驻） |
| **首次打开** | 唤醒 + 拉行情约 30~60 秒，稍等即可 |

> 若需要"7x24 无人值守后台扫描"，免费版做不到；可考虑付费托管（Render/Railway ~$5-7/月）或保留现有 Tailscale 本机方案。

## 本地开发

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## 常见问题

- **首次打开很慢**：休眠唤醒 + 首次拉行情，等 30~60 秒。
- **行情陈旧提示**：收盘后行情源不更新，正常。
- **无法拉取行情**：免费云在美国，偶尔新浪被风控；已加同花顺优先 + 新浪重试，仍失败会显示错误。
