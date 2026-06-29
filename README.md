# Bot-Hosting.net 自动续期 🚀

自动续期 bot-hosting.net 免费计划（每 7 天需手动续期一次）。

## 原理

1. 用你的 session cookie 登录
2. 检查续期窗口是否开放
3. 通过 Playwright 打开续期页面
4. 解决 Cloudflare Turnstile 验证码
5. 点击 Renew 按钮完成续期

## 设置方法

### 1. Session Cookie

登录 https://bot-hosting.net 后，打开 DevTools → Application → Cookies → `bot-hosting.net`，复制 `session_token` 的值。

加到仓库 Secrets：
- **Settings → Secrets and variables → Actions → New repository secret**
- Name: `SESSION_COOKIE`
- Value: 粘贴 session_token 的值

### 2. （可选）Turnstile 验证码全自动解决

想要连验证码都自动过，去 [capsolver.com](https://capsolver.com) 注册充值（很便宜，几分钱一次），拿到 API key 后：

- Name: `CAPSOLVER_API_KEY`
- Value: 你的 capsolver API key

不加也行，脚本会尝试 Turnstile 自动模式，如果不行则续期按钮点不了。

### 3. 触发

- **自动**：每 6 小时检查一次，续期窗口开放后自动执行
- **手动**：Actions → Auto Renew → Run workflow

## 文件说明

| 文件 | 说明 |
|------|------|
| `renew.py` | 主脚本：检查状态 + Playwright 自动化续期 |
| `.github/workflows/renew.yml` | GitHub Actions 定时任务 |
