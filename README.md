# Bot-Hosting.net Auto Renew

自动续期 bot-hosting.net 免费订阅（每 7 天需手动续期）。

## 工作原理

使用你的 session cookie 调用 bot-hosting.net 后台 API，自动完成免费计划续期。

## 使用方法

### 1️⃣ 获取 Session Cookie

1. 在 Chrome/Firefox 登录 https://bot-hosting.net（用 GitHub/Discord）
2. 按 F12 打开 DevTools → **Application**（Chrome）或 **Storage**（Firefox）
3. Cookie → `bot-hosting.net`
4. 找一个值很长的 cookie（可能是 `__session` 或 `session`），复制它的 Value

### 2️⃣ 设置 GitHub Secret

1. 打开这个仓库的 Settings → Secrets and variables → Actions
2. 点 **New repository secret**
3. Name: `SESSION_COOKIE`
4. Value: 粘贴你复制的 cookie 值
5. 点 Add secret

### 3️⃣ 触发续期

- 点 **Actions** → **Auto Renew** → **Run workflow** 手动测试
- 或者等每天 16:00（北京时间）自动运行

## 文件说明

| 文件 | 说明 |
|------|------|
| `renew.py` | 主脚本，纯 requests，轻量 |
| `renew_playwright.py` | Playwright 版，能处理复杂 SPA 页面 |
| `.github/workflows/renew.yml` | GitHub Actions 定时任务 |

## 常见问题

**Q: 提示 cookie 无效？**
A: cookie 有过期时间，需要重新登录获取新 cookie 并更新 Secret。

**Q: 续期失败了？**
A: 先手动登录看看 dashboard 有没有异常。然后跑一次手动 workflow，看日志里的错误信息。
