# DuolinEx 最新开发交接文档

> 这是一份给后续 AI 或开发者读取的工程交接，不是面向普通用户的产品说明。开始改代码前应先阅读本文件和根目录 `README.md`。

## 1. 项目定位

DuolinEx 是一个单用户、本地优先的法语辅助学习 Web 应用。用户继续使用多邻国中文法语课程学习，DuolinEx 在用户主动同步后读取多邻国的课程进度和已学词汇，并在本地按照多邻国的 `Skill -> Session` 结构组织课程，再通过 AI 生成更完整的语法、词汇和发音补充内容。

产品目标不是替代多邻国，也不是做一个完整法语词典，而是解决“背过单词但不知道为什么这样造句、词形为什么变化、发音规则是什么”的问题。

用户的核心学习偏好：

- 词汇和语法同步学习。
- 语法从主谓宾、句子结构、人称变位、性数一致、否定和疑问逐步推进，不能只写空泛总结。
- 发音讲 IPA、重音/节奏、连读、省音等真实规则，不要用中文谐音或幼儿式口型教学。
- 课程正文应该详细但易读，避免大段密集文字和不必要的重复复习。
- AI 即时辅导要轻、快、上下文少；词汇表只展示初学者当前最需要识别的核心词形。

## 2. 工作区和发布范围

本地工作区：`C:\Users\28623\Documents\ForFun\DuolinEx`

当前 GitHub 目标仓库：`https://github.com/Karis004/DuolinEXT.git`

目录职责：

- `website/` 是当前主项目。
- 根目录 `fetch_duolingo_words.py`、`probe_single_skill_words.py`、`server_get_probe.py` 等是前期接口研究和单 Skill payload 测试脚本，网站运行不依赖它们。
- `content.js` 和 `manifest.json` 是早期浏览器插件实验，保留作历史参考。
- `website/backend/data/duolinex.db` 是本地个人数据库，已在根 `.gitignore` 中排除。
- `website/.env` 和 `website/frontend/.env.local` 是本地私密配置，已排除。

注意：原工作区曾经向上继承到 `C:\Users\28623\.git`，而该 Git 目录存在 ownership/safe-directory 问题。发布时应以 `DuolinEx` 作为独立仓库根目录，不能直接在用户目录的 Git 仓库中提交整个用户目录。

## 3. 当前技术栈

### 后端

- Python 3.11。
- FastAPI + Uvicorn。
- SQLModel/SQLAlchemy。
- SQLite。
- `httpx` 请求 OpenAI-compatible AI 服务。
- 多邻国请求通过系统 `curl`，不是 Python `urllib`。

### 前端

- React 19。
- TypeScript。
- Vite。
- `lucide-react` 图标。
- 浏览器 `SpeechSynthesis` 本地 TTS。

### 运行端口

- 前端开发服务：`127.0.0.1:5173`。
- 后端 API：`127.0.0.1:8000`。
- Vite 可通过 `VITE_API_BASE_URL` 覆盖 API 地址，默认 `http://127.0.0.1:8000`。

不要同时启动多个共享同一 SQLite 文件的 `uvicorn --reload` 实例。`lifespan` 启动时会执行数据库迁移，并会将未完成的后台生成任务标为取消；多个实例会互相取消任务，造成前端任务进度不断从 `0 / n` 重新开始。

## 4. 当前功能状态

### 4.1 多邻国同步

用户点击“同步多邻国”才触发同步。页面刷新、打开课程、生成讲解和打开词汇表不应触发多邻国请求。

`duolingo.py` 的流程：

1. 请求 `2023-05-23/users/{user_id}?fields=currentCourse,currentCourseId,learningLanguage,fromLanguage`。
2. 从 `currentCourse.pathSectioned[].units[].levels[]` 提取 `type=skill`、`subtype=regular`、`state=passed|active` 的 Skill。
3. 以 `pathLevelMetadata.skillId` 分组，选择 `levelIndex=0` 的 canonical Skill；重复 crown level 不是应用课程的第三层。
4. 将 `finishedSessions` 转成 Session 进度。
5. 以单 Skill payload 请求 learned-lexemes：

   ```json
   {
     "lastTotalLexemeCount": 0,
     "progressedSkills": [
       {
         "finishedLevels": 0,
         "finishedSessions": 3,
         "skillId": {"id": "..."}
       }
     ]
   }
   ```

6. 按分页抓取单词。

`sync_service.py` 只处理尚未 `synced_at` 的已完成 Session，并根据先前路径中的单词做差分。重复词不会重复插入 `Word` 或当前 Session 的 `SessionWord`。

### 4.2 数据库模型

核心表：

- `Word`：表面词形、中文翻译、多邻国音频 URL、学习计数。
- `CourseSkill`：Skill 层。
- `SkillSession`：Skill 内 Session 层，应用只使用 `level_index=0` 的 canonical Session。
- `SessionWord`：Session 和 Word 的关联。
- `SessionLesson`：Session 课程正文、大纲、版本和生成状态。
- `SyncRun`：同步记录。
- `GenerationTask`：课程/大纲批量生成任务。

词汇表新增表：

- `Lexeme`：原型、词性、阴阳性、含义、生成状态和 prompt version。
- `LexemeForm`：一个原型的核心词形。
- `WordLexeme`：表面词形和词汇原型的关联。
- `VocabularyTask`：批量词形整理任务。

不得删除或重建生产数据库。数据库迁移集中在 `website/backend/app/database.py`，使用 SQLite 的 `ALTER TABLE` 和数据修复语句。新表通过 `SQLModel.metadata.create_all` 创建。

### 4.3 课程正文和大纲

`SessionLesson` 中正文和大纲是绑定关系：

- 有正文必须有大纲。
- 有大纲必须有正文。
- 一个生成流程内先生成课程正文，再生成累计大纲；大纲失败时不能留下只有正文的半成品。
- `generation_status` 可能是 `pending`、`queued`、`generating`、`outlining`、`ready`、`error`、`outline_error`。
- 当前版本常量位于 `ai.py`：`LESSON_PROMPT_VERSION=3`、`OUTLINE_PROMPT_VERSION=2`、`VOCABULARY_PROMPT_VERSION=1`。修改 prompt 或输出协议时要增加对应版本号，并同步检查升级逻辑。

大纲不是“生成时间之后的全局状态快照”。生成某个较早 Session 时，大纲上下文应按照该 Session 的课程路径截断；后生成的后续课程不能倒灌到前面课程的历史视角。

### 4.4 AI 即时辅导

前端实现集中在 `website/frontend/src/App.tsx`：

- 固定右下角 `AI 辅导`按钮。
- 正文 `onMouseUp` 读取浏览器 Selection。
- `.selection-actions` 提供“问 AI”和“解释这段”。
- `.tutor-drawer` 固定在右侧，移动端全屏。
- `selectionchange`、`pointerdown`、滚动和 resize 会关闭已失效的选区浮框。
- 切换 Session、完成 Session、关闭抽屉、刷新页面会清空 `tutorMessages` 和 `tutorText`。

后端 `/api/tutor/ask` 调用 `ask_tutor`：

- system prompt 要求中文优先、短回答、准确直接、通常不超过 120 字。
- user payload 只包含当前课程位置、当前累计大纲、选中文本、问题和最近 6 条消息。
- 最大输出 `max_tokens=320`。
- 辅导 timeout 最多 35 秒。

不要把完整课程正文、全部词汇、全部历史消息重新塞回即时辅导请求。这个功能的产品目标是快速解决一个小问题，不是重新生成课程。

### 4.5 词汇表和词形任务

词汇表位于前端顶部导航，数据入口是 `/api/vocabulary`。点击词形使用 `playFrench(form.form)`，不传多邻国音频 URL，因此走本地法语 TTS。

`vocabulary_service.py`：

- 先检查 `Lexeme` 是否已经是当前 prompt version 的 `ready`。
- 一个批次目前 4 个词，减少单次 AI JSON 过大和超时概率。
- 词形生成 timeout 最多 30 秒。
- `_store_profile` 删除同一 Lexeme 的旧 `LexemeForm`，先 `session.flush()`，再插入新词形，避免 SQLite 唯一键冲突。
- 批次异常必须 `session.rollback()`，再将该批词标记为 error 并提交；不能让事务留在 `PendingRollbackError` 状态。
- `active_vocabulary_task` 会将超过 3 分钟没有完成进度的 running 任务标记为 cancelled。
- `/api/vocabulary` 在没有活动任务但仍有待处理词且 AI 已配置时，会自动创建并后台启动新的词形任务。

之前真实数据库出现过 `LexemeForm` 唯一键冲突，表现为 `0 / 54` 长时间不动；现已修复。若再次看到任务长期 0 进度，先查看后端终端 traceback 和 `/api/vocabulary` 的 task id，不要直接删除数据库。

## 5. TTS 约定

文件：`website/frontend/src/audio/frenchAudio.ts`。

- 默认 `local`：选择 `fr-FR` 或其他法语 `localService=true` 语音。
- `browser`：允许使用浏览器提供的非本地/远程语音。
- `playFrench(text, audioUrl?)`：有 `audioUrl` 时先尝试多邻国音频，失败后回退浏览器语音；词汇表故意不传 `audioUrl`。
- 播放前取消上一个播放，处理 `voiceschanged`，避免浏览器语音列表尚未初始化。
- 不要把 `rate` 作为产品层面的“快慢修复”加入；之前的异常快读来自浏览器倍速插件，不是应用逻辑。

## 6. 关键前端状态

`App.tsx` 中：

- `data`：DashboardData。
- `data.selectedSession`：当前 Session 详情。
- `operation`：同步、加载、生成、升级、重试、取消、完成等 UI 操作。
- `tutorOpen`、`tutorText`、`tutorRect`、`tutorMessages`：当前 Session 临时辅导状态。
- `vocabularyOpen`、`vocabulary`：词汇表 overlay 和轮询数据。

轮询：课程生成任务存在或有待生成课程时，Dashboard 每 3 秒刷新；词汇表打开时，`/api/vocabulary` 每 3 秒刷新。轮询不能触发同步多邻国。

## 7. 环境配置

模板：`website/.env.example`。

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

不要把真实 JWT、API key、完整 `.env` 或请求日志提交到仓库。

## 8. 本地启动和验证

在 `website/`：

```powershell
npm.cmd run setup
npm.cmd run dev
```

独立验证：

```powershell
cd website/backend
python -m pytest
python -m compileall app

cd ..
npm --prefix frontend run build
```

当前最新验证：后端 37 个测试通过，前端 TypeScript/Vite 构建通过。

## 9. 本地数据库迁移原则

仓库不提交 `website/backend/data/duolinex.db`。迁移用户已有数据：

1. 停止应用，避免复制时 SQLite 仍在写入。
2. 备份旧 `duolinex.db`。
3. 新环境安装依赖，复制 `.env.example` 为 `.env` 并重新填写密钥。
4. 将旧 `duolinex.db` 复制到 `website/backend/data/duolinex.db`。
5. 启动后端一次，应用自动补充 schema。
6. 检查 `/api/health`、`/api/dashboard`、`/api/vocabulary`。
7. 确认数据无误后启动前端。

不要把数据库中的个人数据导出到 README、测试 fixture 或 GitHub issue。不要为了修复单个词形任务删除整库；优先查看任务状态、回滚事务和重跑剩余任务。

## 10. 后续开发优先级

1. 为 `ask_tutor` 增加独立后端单元测试，覆盖选中文本、空大纲、最近消息截断和 AI 错误。
2. 为词汇任务增加任务取消/重试策略测试，覆盖 AI 返回缺词、JSON 无效、数据库写入失败和应用重启。
3. 补充生产部署的进程管理、反向代理、HTTPS、认证和 CORS 配置。
4. 修改课程 prompt 时增加版本号并保留旧课程数据，除非用户明确执行升级。
5. 词汇表目前只生成核心初学形态，不要未经用户确认扩展成完整法语词典级变位表。
6. 若未来支持多人，必须首先重构数据模型加入 user/account 维度，不能在现有单用户表上直接开放公网。

## 11. 代码修改约定

- 优先沿用当前 FastAPI、SQLModel、React、TypeScript 模式。
- 不删除用户未要求删除的旧数据或旧表。
- 数据库新增字段必须提供兼容迁移，并考虑已有 SQLite 文件。
- 后台任务需要有明确的 queued/running/completed/partial/cancelled/error 状态，异常必须 rollback。
- 不要在每次页面刷新时触发多邻国同步或 AI 生成。
- 不要把密钥写入源码、测试输出、commit message 或文档示例。
- 修改后至少运行相关后端测试和前端 build；涉及数据库时应在临时 SQLite 和真实数据库上各验证一次。
