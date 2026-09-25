# DuolinEXT

DuolinEXT 是一个面向中文母语学习者的本地法语辅助学习 Web 应用。它在用户主动同步后读取多邻国中文法语课程的学习进度，按 `Skill -> Session` 组织已学内容，并使用 OpenAI-compatible API 生成可持久保存的词汇、语法、发音和练习课程。

DuolinEXT 不替代多邻国，也不会自动操作多邻国。日常浏览课程、查看词汇表和完成学习只访问本地 SQLite 数据库；只有点击“同步多邻国”才会请求多邻国接口。

## 核心功能

- 按 Skill 和 Session 同步多邻国课程路径及新增词汇。
- 差分同步已完成 Session，避免重复抓取和重复写入。
- 为每个 Session 生成并保存课程正文与累计学习大纲。
- 支持缺失课程生成、失败重试、旧 prompt 版本升级和批量任务取消。
- 提供发音模块、逐步语法讲解、句型变换、造句和典型错误说明。
- 提供选中文本提问和右侧 AI 即时辅导抽屉。
- 提供可搜索、按词性筛选并支持法语 TTS 的词汇表。
- 保存学习完成状态，并显示课程总进度、上次位置和下一节建议。

## 仓库结构

```text
DuolinEXT/
├─ website/                     # 正式 Web 应用，后续开发的主目录
│  ├─ backend/
│  │  ├─ app/                   # FastAPI、同步、AI、数据库和后台任务
│  │  └─ tests/                 # 当前后端回归测试
│  ├─ frontend/
│  │  ├─ public/                # 静态资源
│  │  └─ src/                   # React、TypeScript、样式和 TTS
│  ├─ .env.example              # 本地配置模板
│  └─ package.json              # 前后端联合启动脚本
├─ README.md                    # 项目展示、安装和使用说明
├─ PROJECT_HANDOFF.md           # 面向后续开发者的工程交接
├─ content.js / manifest.json   # 早期浏览器插件实验，仅保留作历史参考
└─ *_probe.py / fetch_*.py      # 早期多邻国接口研究工具，网站不依赖
```

正式应用只位于 `website/`。根目录实验脚本不参与网站构建、测试或运行。

## 技术栈

- 前端：React 19、TypeScript、Vite、`lucide-react`。
- 后端：Python 3.11+、FastAPI、SQLModel、SQLite、Uvicorn。
- AI：OpenAI-compatible Chat Completions API。
- 发音：多邻国单词音频与浏览器 `SpeechSynthesis` 法语语音。
- 多邻国请求：系统 `curl`；Windows 自动使用 `--ssl-no-revoke`。

## 本地运行

前置条件：Python 3.11+、Node.js 20+、npm 和可用的 `curl`。

```powershell
git clone https://github.com/Karis004/DuolinEXT.git
cd DuolinEXT/website
Copy-Item .env.example .env
# 编辑 .env，填写多邻国 JWT 和可选的 AI 配置
npm.cmd run setup
npm.cmd run dev
```

打开 <http://127.0.0.1:5173/>。后端 API 位于 `http://127.0.0.1:8000`，健康检查为 <http://127.0.0.1:8000/api/health>。

Linux/macOS 使用：

```bash
cp .env.example .env
npm run setup
npm run dev
```

也可以分别启动：

```powershell
# website/
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

# 另一个终端，website/
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

不要同时运行多个指向同一 SQLite 文件的后端实例。应用启动会执行兼容迁移，并取消上一次进程遗留的未完成后台任务。

## 配置

将 `website/.env.example` 复制为 `website/.env`：

```env
DUOLINGO_JWT=
DUOLINGO_USER_ID=
DUOLINGO_COURSE_ID=fr
DUOLINGO_FROM_LANGUAGE=zh

APP_TIMEZONE=Asia/Shanghai

AI_API_KEY=
AI_BASE_URL=https://api.example.com/v1
AI_MODEL=
AI_REASONING_EFFORT=low
```

请求分页、AI 超时、最大重试次数和重试间隔由应用使用保守默认值，不需要用户配置。修改 `.env` 后需重启后端。

## 设置与连接检测

网站顶部的设置入口会显示多邻国和 AI 配置是否完整，但不会把 JWT 或 API key 返回给前端。用户可以主动执行：

- 多邻国检测：只读取一次当前课程概要，不同步词汇、不修改学习数据。
- AI 检测：发送一个最多 16 tokens 的短请求，确认 URL、密钥和模型是否可用。
- 重新读取配置：修改 `website/.env` 后，在设置页点击“重新读取配置”即可让后端重新加载服务配置；数据库路径等启动资源变更仍需重启服务。

## 法语语音

- 有多邻国原始音频 URL 的单词优先播放原音。
- 例句、语法短语和没有原音的内容使用浏览器 `SpeechSynthesis`。
- 应用自动检测浏览器可见的法语语音，优先在线语音，并回退到设备本地语音。
- 用户可以在网站设置中查看语音来源、选择具体语音并试听，不需要编辑前端环境文件。
- 没有法语语音时，设置页会根据操作系统显示安装路径；安装完成后重启浏览器即可重新检测。

## 数据与迁移

默认数据库为 `website/backend/data/duolinext.db`，保存课程路径、词汇、AI 课程、大纲、学习进度和任务记录。数据库和 `.env` 均已被 Git 忽略，不得提交到公共仓库。

从旧版升级时，如果仍存在默认名称 `duolinex.db` 且新文件不存在，后端会在启动时自动原位改名为 `duolinext.db`；自定义 `DATABASE_URL` 不受影响。

迁移到新电脑或服务器时：

1. 停止前后端服务，确认没有后台任务写入数据库。
2. 备份 `website/backend/data/duolinext.db`。
3. 在新环境重新创建 `.env`，不要通过 Git 携带密钥。
4. 将数据库复制到相同路径后启动一次后端。
5. 检查 `/api/health`、`/api/dashboard` 和 `/api/vocabulary`。

应用使用 `SQLModel.metadata.create_all` 和 `website/backend/app/database.py` 中的增量迁移兼容已有数据库。不要为修复单个任务而删除或重建数据库。

## 验证

```powershell
cd website/backend
python -m pytest
python -m compileall app

cd ..
npm --prefix frontend run build
```

构建结果位于 `website/frontend/dist/`，依赖、缓存、构建产物和本地数据库均不提交 Git。

## 部署说明

前端可以由 Nginx、Caddy 或其他静态文件服务器托管，并将 `/api` 反向代理到 FastAPI。当前应用是单用户本地工具，没有登录、用户隔离和权限系统；部署到公网前必须补充认证、HTTPS、限流和严格的 CORS 配置。

### Docker 与 GitHub Actions 自动部署

仓库根目录的 `compose.yaml` 使用两个容器：Nginx 托管前端并转发 `/api`，FastAPI 仅在 Docker 内部网络监听。主机只使用 `8085` 端口。默认绑定 `127.0.0.1:8085`，适合由服务器已有的 HTTPS 反向代理接入；Nginx 还会对整个网站执行 HTTP Basic Auth。**不要将没有 HTTPS 保护的 `8085` 直接开放到公网**，否则登录口令会在传输中暴露。

首次在 Linux 服务器安装 Docker Engine、Compose 插件、Git 和 OpenSSL 后，以有 Docker 权限的部署用户执行：

```bash
git clone https://github.com/Karis004/DuolinEXT.git /home/ubuntu/DuolinEXT
cd /home/ubuntu/DuolinEXT
cp website/.env.example website/.env
# 编辑 website/.env，填入多邻国和 AI 配置；不要提交这个文件
mkdir -p deploy website/backend/data
printf 'DUOLINEXT_UID=%s\nDUOLINEXT_GID=%s\n' "$(id -u)" "$(id -g)" > .env
printf 'duolinext:' > deploy/htpasswd
openssl passwd -apr1 >> deploy/htpasswd  # 交互输入网站访问口令
chmod 600 website/.env
chmod 644 deploy/htpasswd
docker compose up --build --wait --wait-timeout 180
```

访问 `http://127.0.0.1:8085/healthz` 可检查前端容器；通过服务器的 HTTPS 反向代理访问网站，并将代理目标设为 `127.0.0.1:8085`。服务器仓库根目录不提交的 `.env` 记录运行容器的用户 ID，也可设置 `DUOLINEXT_BIND_IP` 和 `DUOLINEXT_PORT`。学习数据库保存在服务器的 `website/backend/data/`，重新构建容器不会删除它。

`.github/workflows/deploy.yml` 在每次推送 `main` 后先执行后端测试、前端构建和两个 Docker 镜像构建。当前工作流固定部署到 `ubuntu@40.233.65.88:/home/ubuntu/DuolinEXT`；完成以下 GitHub 仓库设置后，它会通过 SSH 登录服务器、执行 `git pull --ff-only origin main`，然后重新构建并启动容器：

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Repository variable | `DEPLOY_ENABLED` | `true`；完成服务器准备后再设置 |
| Repository secret | `SSH_PRIVATE_KEY` | 与服务器 `ubuntu` 用户已配置的 SSH 私钥全文；可沿用旧项目使用的本机密钥 |

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中设置上述值。服务器公开 host key 已经核对并固定在 `deploy/known_hosts`，SSH 连接会严格校验它。不要把私钥、网站口令或 `website/.env` 提交到仓库。未设置 `DEPLOY_ENABLED=true` 时，工作流只验证代码，不会尝试连接服务器。首次配置完成后可在 **Actions → Verify and deploy → Run workflow** 手动触发一次。

## 安全提醒

- `DUOLINGO_JWT` 是登录凭证，只能存放在本地 `.env` 或密钥管理系统。
- AI API key、数据库、真实请求日志和个人学习数据不得提交到 GitHub。
- 多邻国接口是非公开客户端接口，字段和鉴权行为可能变化。
- AI 生成依赖配置的第三方服务，课程与词形结果会保存在本地数据库。

## License

项目暂未确定开源许可证。公开可见不代表自动授予再分发或商业使用权。
