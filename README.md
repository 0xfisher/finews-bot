# 📊 财经新闻 AI 分析机器人

> 自动抓取 30+ RSS 源 → Gemini 3.1 Pro 深度分析 → 推送 Discord + 保存本地

---

## 本地快速启动

### 1. 安装依赖（需 Python 3.11+）

```bash
pip install -r requirements.txt
```

### 2. 设置环境变量（推荐，不要硬写密钥）

**macOS / Linux：**
```bash
export GEMINI_API_KEY="你的Gemini API Key"
export DISCORD_WEBHOOK="https://discord.com/api/webhooks/xxx/yyy"
```

**Windows PowerShell：**
```powershell
$env:GEMINI_API_KEY="你的Gemini API Key"
$env:DISCORD_WEBHOOK="https://discord.com/api/webhooks/xxx/yyy"
```

### 3. 运行

```bash
python finews_bot.py
```

分析报告会保存到 `~/Downloads/财经新闻_日期时间.txt`
已读记录存在 `~/Downloads/.news_seen.json`，重复运行自动去重并标注 🆕

---

## 获取密钥

| 密钥 | 获取方式 |
|------|----------|
| **Gemini API Key** | https://aistudio.google.com/app/apikey（免费，每天1500次请求） |
| **Discord Webhook** | Discord 频道 → 编辑频道 → 整合 → Webhook → 新建 Webhook → 复制 URL |

---

## 部署到 GitHub Actions（每天自动运行，免费）

### 第一步：创建仓库
将所有文件推送到一个 GitHub 私有仓库（建议 Private）。

### 第二步：配置 Secrets
进入仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

添加以下两个 Secret：

| Secret 名称 | 值 |
|-------------|-----|
| `GEMINI_API_KEY` | 你的 Gemini API Key |
| `DISCORD_WEBHOOK` | Discord Webhook URL |

### 第三步：启用 Actions
进入仓库的 **Actions** 标签页，点击 **Enable workflows**。

工作流会在每天 **08:00** 和 **20:00（北京时间）** 自动运行。

### 手动触发
Actions → 左侧选 "财经新闻 AI 日报" → **Run workflow**

### 下载报告
每次运行完成后，在 Actions 运行记录的 **Artifacts** 区域可下载 `.txt` 分析报告（保留7天）。

---

## 项目结构

```
finews_bot.py               # 主脚本
requirements.txt            # Python 依赖
.github/
  workflows/
    finews.yml              # GitHub Actions 定时任务
~/Downloads/
  财经新闻_20241214_0800.txt  # 自动生成的分析报告
  .news_seen.json           # 已读记录（去重用）
```

---

## 自定义

- **修改关注主题**：编辑 `finews_bot.py` 中的 `POSITIONS` 变量
- **添加 RSS 源**：在 `RSS_FEEDS` 字典中添加新条目
- **调整推送时间**：修改 `.github/workflows/finews.yml` 中的 `cron` 表达式
- **更换 AI 模型**：修改 `call_gemini()` 函数中的 `model=` 参数
