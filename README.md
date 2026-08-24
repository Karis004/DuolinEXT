# DuolinEx

DuolinEx 是一个面向中文母语学习者的法语辅助学习系统。它读取用户在多邻国中文课程中的学习进度，将课程按 `Skill -> Session` 组织起来，再用 AI 生成保存版本的词汇、语法、发音和练习内容。

它不是多邻国客户端，也不会自动操作多邻国。用户只有主动点击“同步多邻国”时，系统才会使用本地配置的 JWT 请求多邻国接口；平时打开页面、切换课程和学习内容都只访问本地数据库。

当前仓库包含两部分：

- `website/`：当前正在使用的完整 Web 应用，是后续开发的主目录。
- 根目录脚本：前期用于研究多邻国接口、动态课程 payload 和单 Skill 请求的测试工具，运行网站不依赖这些脚本。

## 功能概览

### 课程同步与课程路径

- 从多邻国当前课程进度提取已完成的 Skill 和 Session。
- 使用动态 `progressedSkills` payload，按 Skill 单独请求单词，而不是每次重复抓取整棵课程树。
- 本地数据库按照两层结构保存课程：`CourseSkill` 对应 Skill，`SkillSession` 对应 Skill 内的 Session。
- 根据本地已同步进度做差分，只处理新增或尚未同步的 Session。
- 空 Session 会自动标记为完成。
- 页面显示课程总数、完成进度、上次完成位置和下一节建议学习位置。

### AI 课程

- 每个 Session 保存独立课程正文，不需要每次进入页面重新生成。
- 每节课程绑定一份累计大纲，正文和大纲必须同时存在；任一生成失败都会整体失败，避免后续课程读取不完整上下文。
- 课程包含词汇用法、循序渐进的法语语法、句型变换、逐步造句、典型错误、例句和轻量检查。
- 发音是独立模块，重点放在 IPA、法语语音规则、连读、省音和与英语容易混淆的部分。
- 支持生成缺失课程、更新旧版本课程、重试失败课程、取消批量生成。
- 课程和大纲都有 prompt version，后续修改课程设计时可以识别旧版本并批量升级。

### AI 即时辅导

- 右下角固定入口，不随正文滚动消失。
- 在课程正文中选中文字后出现“问 AI”和“解释这段”。
- 右侧固定抽屉显示引用内容、问题和回答。
- 仅把当前 Skill/Session、近期课程大纲、选中文本和最近少量对话发给 AI，目标是快速解决当前小问题。
- 对话只保留在当前 Session；切换课程、完成课程、关闭抽屉或刷新页面都会清空临时上下文。

### 词汇表

- 顶部导航进入词汇表，支持搜索和词性筛选。
- 每个单词关联原型、词性、阴阳性、常见含义、简短识别提示和适合初学者的核心词形。
- 动词优先生成不定式、现在时核心人称和过去分词；名词、形容词优先生成阴阳性及单复数，不追求完整词典级变位。
- 点击单词或词形播放本地法语 TTS，不依赖多邻国音频。
- 同步新单词后自动生成词形；启动时也会自动补全已有单词。

### 发音

- 课程正文中保留多邻国单词音频作为优先来源。
- 例句、语法短语、词形和词汇表使用浏览器 `SpeechSynthesis` 的本地法语语音。
- 默认选择系统 `localService: true` 的法语语音，避免依赖 Google 网络 TTS。
- 可通过前端环境变量切换为浏览器可用的远程/非本地法语语音。

## 技术栈

- 前端：React 19、TypeScript、Vite、`lucide-react`。
- 后端：Python 3.11+、FastAPI、SQLModel、SQLite、Uvicorn。
- AI：OpenAI-compatible Chat Completions API；课程生成使用 JSON 输出，辅导使用短文本输出。
- 多邻国请求：后端通过 `curl` 发起请求，并带有浏览器常用 headers；Windows 下自动加入 `--ssl-no-revoke` 兼容本机证书吊销检查问题。
- 数据库：SQLite。应用启动时使用 `SQLModel.metadata.create_all` 和轻量迁移逻辑补充新表/新列。

## 目录结构

```text
DuolinEx/
├─ website/
│  ├─ backend/app/              # FastAPI、SQLModel、同步、AI 和后台任务
│  ├─ backend/tests/            # 后端测试
│  ├─ frontend/src/             # React 页面、样式和 TTS
│  ├─ .env.example
│  └─ package.json              # 同时启动前后端
├─ fetch_duolingo_words.py      # 前期独立抓词脚本
├─ probe_single_skill_words.py  # 单 Skill payload 测试
├─ server_get_probe.py          # 服务端请求可达性测试
├─ content.js / manifest.json   # 前期浏览器插件测试
└─ PROJECT_HANDOFF.md           # 给后续 AI/开发者的详细交接文档
```

## 开发环境部署

### 前置条件

- Python 3.11 或更高版本。
- Node.js 20 或更高版本，建议使用 LTS。
- 可用的 `curl`。Windows 10/11 通常已经自带。
- 一个 OpenAI-compatible AI 服务；如果只测试同步和数据库，可以先不配置 AI。
- 用户自己的多邻国 JWT。JWT 是登录凭证，不要提交到 GitHub。

### 首次安装

```powershell
git clone https://github.com/Karis004/DuolinEXT.git
cd DuolinEXT/website
Copy-Item .env.example .env
# 编辑 .env，填写 DUOLINGO_JWT 和需要的 AI 配置
npm.cmd run setup
```

Linux/macOS：

```bash
cp .env.example .env
npm run setup
```

`setup` 会安装后端 Python 依赖和前端 npm 依赖。生产环境建议在虚拟环境中安装 Python 依赖：

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
npm --prefix frontend install
```

### 启动开发服务

在 `website/` 目录执行：

```powershell
npm.cmd run dev
```

打开 <http://127.0.0.1:5173/>。后端 API 为 `127.0.0.1:8000`，健康检查为 <http://127.0.0.1:8000/api/health>。

也可以分别启动：

```powershell
# website/
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

# 另一个终端，website/
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

不要同时启动多个指向同一个 SQLite 数据库的 `uvicorn --reload` 实例。后台课程/词形任务是数据库中的共享任务，多个实例会互相触发启动迁移并取消对方任务。

### 构建前端

```powershell
npm --prefix frontend run build
```

构建结果在 `website/frontend/dist/`，该目录不提交。部署时可以使用 Nginx、Caddy 或其他静态文件服务器提供前端，并将 `/api` 反向代理到 FastAPI；当前项目没有内置用户认证层，不建议直接暴露到公网。

## 配置说明

复制 `website/.env.example` 为 `website/.env`。主要配置包括：

```env
DUOLINGO_JWT=
DUOLINGO_USER_ID=1148050773
DUOLINGO_COURSE_ID=fr
DUOLINGO_FROM_LANGUAGE=zh
DUOLINGO_PAGE_SIZE=50
DAILY_WORD_LIMIT=6
APP_TIMEZONE=Asia/Shanghai
AI_API_KEY=
AI_BASE_URL=https://api.example.com/v1
AI_MODEL=
AI_TIMEOUT_SECONDS=150
AI_MAX_ATTEMPTS=3
AI_RETRY_BASE_SECONDS=2
AI_REASONING_EFFORT=low
```

前端 TTS 可选配置写入 `website/frontend/.env.local`：

```env
VITE_FRENCH_TTS_PROVIDER=local
# VITE_FRENCH_TTS_PROVIDER=browser
```

## 数据库迁移与备份

SQLite 默认位置：`website/backend/data/duolinex.db`。该文件包含个人学习进度、课程正文、累计大纲、词形资料和任务记录，已被 `.gitignore` 排除，不能上传到公共 GitHub 仓库。

把当前本地学习数据迁移到新电脑或服务器：

1. 停止前后端服务，确保没有 AI 任务正在写数据库。
2. 备份整个 `website/backend/data/duolinex.db`。
3. 在新仓库复制 `website/.env.example` 为 `website/.env`，重新填写 JWT 和 AI 密钥。
4. 将旧数据库复制到新环境的 `website/backend/data/duolinex.db`，没有 `data` 目录就手动创建。
5. 安装依赖并启动一次后端；应用会自动执行 SQLite 兼容迁移，不需要手工执行 SQL。
6. 检查 `/api/health`、`/api/dashboard` 和 `/api/vocabulary`。
7. 确认课程、词汇和任务状态正确后，再启动前端。

SQLite 文件可以在 Windows、Linux 和 macOS 之间复制，但必须在应用停止后复制，避免复制到半写入状态。不要复制 `node_modules`、`frontend/dist` 或 Python 缓存，它们在新环境重新安装/构建即可。

数据和密钥分开迁移：

- 数据库：`website/backend/data/duolinex.db`
- 私密配置：`website/.env`
- 可选前端配置：`website/frontend/.env.local`

以上文件均不应提交到 GitHub，仓库只提供 `.env.example` 模板。

## 测试

```powershell
cd website/backend
python -m pytest
python -m compileall app

cd ..
npm --prefix frontend run build
```

测试覆盖 AI 响应解析、课程生成、大纲绑定、数据库兼容迁移、同步差分、学习完成状态和词汇形态任务。

## API 概览

- `GET /api/health`：健康检查。
- `GET /api/dashboard`：课程路径、进度、生成任务和当前 Session。
- `POST /api/sync/duolingo`：主动同步多邻国进度。
- `GET /api/sessions/{id}`：读取 Session。
- `POST /api/sessions/{id}/generate`：强制重新生成 Session。
- `POST /api/sessions/{id}/complete`：标记 Session 完成。
- `POST /api/lessons/upgrade`：批量升级旧版本课程。
- `POST /api/lessons/generate-missing`：只生成缺失课程。
- `POST /api/lessons/retry-failed`：重试失败课程。
- `POST /api/lessons/generation/cancel`：取消课程生成任务。
- `POST /api/tutor/ask`：当前 Session 的轻量 AI 辅导。
- `GET /api/vocabulary`：词汇表和词形生成进度。

## 当前限制与安全提醒

- 当前默认是单用户本地应用，没有登录、用户隔离或权限系统。
- `DUOLINGO_JWT` 和 AI API key 必须只放在本地 `.env` 或服务器密钥管理系统中。
- 多邻国接口属于非公开客户端接口，字段和鉴权行为可能变化。
- AI 生成依赖网络和所配置的中转服务；课程正文与词形资料会保存到 SQLite。
- 浏览器本地 TTS 是否可用取决于操作系统已安装的法语语音。
- 部署到公网前需要补充认证、HTTPS、限流和更严格的 CORS 配置。

## License

当前仓库尚未确定开源许可证。公开仓库不代表他人自动获得代码再分发和商业使用许可；正式开放协作前请补充合适的 LICENSE 文件。
