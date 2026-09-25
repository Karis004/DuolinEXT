# DuolinEXT 工程交接

> 本文面向后续开发者与 AI。开始修改代码前应同时阅读根目录 `README.md`。最后更新：2026-09-20。

## 1. 产品边界

DuolinEXT 是单用户、本地优先的法语辅助学习应用。用户继续在多邻国学习；DuolinEXT 只在用户主动同步时读取多邻国进度和已学词汇，然后在本地按 `Skill -> Session` 组织内容，并通过 AI 补充语法、词汇和发音课程。

产品重点：

- 词汇与语法同步学习，而不是只做单词卡。
- 语法应从句子结构、人称变位、性数一致、否定和疑问等基础逐步推进。
- 发音使用 IPA、节奏、连读和省音等真实规则，不使用中文谐音教学。
- 课程详细但易读，避免密集长文和无目的重复。
- AI 辅导只解决当前小问题，不重新生成整节课。
- 词汇表只展示初学阶段需要识别的核心词形，不做完整词典。

当前不支持多用户、账号体系、云端同步或公开部署。

## 2. 代码范围

- `website/`：唯一正式应用目录。
- `website/backend/app/`：后端实现。
- `website/backend/tests/`：当前有效的后端回归测试。
- `website/frontend/src/`：前端实现。
- 根目录插件文件与探测脚本：早期研究材料，不属于网站运行链路，不要在网站重构中顺带修改。
- 根目录只维护两份正式文档：`README.md` 与 `PROJECT_HANDOFF.md`。

本地私密或生成内容：

- `website/.env`
- `website/backend/data/duolinext.db`
- `node_modules/`、`dist/`、`__pycache__/`、`.pytest_cache/`、`*.tsbuildinfo`

这些路径均不得提交。

## 3. 技术结构

### 后端

- Python 3.11+、FastAPI、Uvicorn。
- SQLModel/SQLAlchemy 与 SQLite。
- `httpx` 请求 OpenAI-compatible API。
- 系统 `curl` 请求多邻国接口。
- FastAPI `BackgroundTasks` 执行课程和词形批处理。

主要模块：

| 文件 | 职责 |
| --- | --- |
| `main.py` | API、生命周期、CORS、后台任务编排 |
| `models.py` | 当前数据库模型 |
| `database.py` | 引擎、建表与 SQLite 兼容迁移 |
| `duolingo.py` | 多邻国请求、课程路径和 learned-lexemes 解析 |
| `sync_service.py` | Skill/Session/Word 差分同步 |
| `session_service.py` | 课程路径、Session 详情、完成状态 |
| `generation_service.py` | 课程与累计大纲生成、版本和批量任务 |
| `vocabulary_service.py` | Lexeme、核心词形和词汇任务 |
| `ai.py` | prompt、AI 请求、JSON 解析与输出校验 |

### 前端

- React 19、TypeScript、Vite、`lucide-react`。
- `App.tsx` 当前包含主布局、课程路径、课程内容、大纲、词汇表和 AI 辅导。
- `types.ts` 定义前后端响应结构。
- `audio/frenchAudio.ts` 统一处理多邻国音频和浏览器法语 TTS。
- `styles.css` 包含桌面与移动端布局。

`App.tsx` 体积较大。小改动应沿用现有结构；涉及多个独立视图的大功能可以按课程、词汇表、辅导和大纲边界拆分组件，但不要为了单一改动先做无关重构。

## 4. 数据模型

当前业务表：

- `Word`：表面词形、翻译、音频 URL 和学习计数。
- `CourseSkill`：课程路径中的 canonical Skill。
- `SkillSession`：Skill 内的 canonical Session。
- `SessionWord`：Session 与新增词汇关联。
- `SessionLesson`：课程正文、大纲、生成版本和学习状态。
- `SyncRun`：多邻国同步记录。
- `GenerationTask`：课程批量生成任务。
- `Lexeme`：词汇原型、词性、意义和生成状态。
- `LexemeForm`：原型的核心词形。
- `WordLexeme`：表面词形与原型关联。
- `VocabularyTask`：词形整理任务。

旧的按日期 `Lesson`/`LessonWord` 体系已退出运行链路，源码模型和服务已删除。为了保护已有数据库，迁移代码不会主动删除真实数据库中的旧表。

数据库变更规则：

1. 不删除或重建用户数据库。
2. 新表由 `SQLModel.metadata.create_all` 创建。
3. 新字段和数据修复写入 `database.py`，迁移必须可重复执行。
4. 涉及数据库的改动至少在临时 SQLite 上测试，并谨慎验证真实数据库。

品牌改名后默认数据库名称为 `duolinext.db`。`database.py` 只在使用默认路径、新文件不存在且旧文件存在时，将旧数据库原位改名；不得删除这项兼容逻辑，除非已明确结束旧版迁移支持。

## 5. 多邻国同步

同步只能由 `POST /api/sync/duolingo` 触发。页面加载、轮询、打开课程、查看词汇表和生成课程不得隐式请求多邻国。

同步流程：

1. 获取用户当前课程及 `pathSectioned`。
2. 提取 `type=skill`、`subtype=regular`、`state=passed|active` 的路径节点。
3. 按 `pathLevelMetadata.skillId` 分组，仅保留 `levelIndex=0` 的 canonical Skill。
4. 将 `finishedSessions` 转为 Session 进度。
5. 对尚未同步的已完成 Session，以单 Skill `progressedSkills` payload 请求 learned-lexemes。
6. 按此前路径词汇做差分并写入 `Word` 与 `SessionWord`。
7. 空 Session 标记为已完成。

重复 crown level 不是第三层课程结构，不要重新引入。

## 6. 课程与大纲

`SessionLesson` 的正文与累计大纲是原子绑定关系：

- 有正文必须有大纲，有大纲也必须有正文。
- 一个流程内先生成正文，再生成大纲。
- 大纲失败时不能留下只有正文的半成品。
- 较早 Session 的大纲上下文必须按该 Session 的路径位置截断，后续课程不能倒灌。

当前版本常量位于 `ai.py`：

- `LESSON_PROMPT_VERSION = 3`
- `OUTLINE_PROMPT_VERSION = 2`
- `VOCABULARY_PROMPT_VERSION = 1`

修改 prompt、JSON 协议或影响既有结果语义时，必须提升对应版本号并检查批量升级逻辑。旧课程数据应保留，除非用户明确执行升级。

课程生成状态包括 `pending`、`queued`、`generating`、`outlining`、`ready`、`error` 和 `outline_error`。批量任务必须有明确的 queued/running/completed/partial/cancelled/error 结果。

## 7. AI 即时辅导

前端在课程正文中读取 Selection，并通过右侧抽屉调用 `POST /api/tutor/ask`。切换 Session、完成课程、关闭抽屉或刷新页面会清空临时对话。

请求只应包含：

- 当前 Skill 与 Session。
- 当前累计大纲。
- 用户选中的文本。
- 当前问题。
- 最近 6 条消息。

不要加入完整课程正文、全部词汇或完整历史。辅导默认中文优先、短回答，输出上限为 320 tokens，超时最多 35 秒。

## 8. 词汇表与 TTS

词汇入口为 `GET /api/vocabulary`。当前批次每次处理 4 个词，异常时必须 rollback 后再标记错误，避免事务进入 `PendingRollbackError`。

- 已是当前 prompt version 且 `ready` 的 Lexeme 不重复生成。
- 写入新 profile 时先删除旧 `LexemeForm` 并 `flush()`，再插入新词形。
- 超过 3 分钟无进度的 running 任务会标记为 cancelled。
- 打开词汇表且存在待处理词时，可自动创建词形任务。

`playFrench(text, audioUrl?)` 有多邻国音频时优先播放，失败后回退浏览器语音。词汇表故意不传音频 URL。语音不再通过 `frontend/.env.local` 配置：前端自动检测 `SpeechSynthesis` 法语语音，自动模式优先 `localService=false` 的浏览器在线语音，再回退 `localService=true` 的设备语音。用户选择保存在 `duolinext.frenchVoicePreference`。

设置页使用以下只读/检测接口：

- `GET /api/settings/status`：只返回配置完整度、脱敏用户 ID、无凭据的 AI endpoint 和模型信息。
- `POST /api/settings/test-duolingo`：读取当前课程概要，不写数据库。
- `POST /api/settings/test-ai`：发送最多 16 tokens 的短连接测试，只有用户点击时调用。
- `POST /api/settings/reload`：清除后端配置缓存并重新读取 `.env`，返回脱敏后的配置状态；不返回 JWT/API key。数据库引擎等启动资源不在运行时切换范围内。

浏览器无法替用户安装系统语音包。若未检测到法语语音，前端应显示对应操作系统的安装路径，并在 `voiceschanged` 后自动刷新状态。

## 9. 前端状态与轮询

关键状态：

- `data` 与 `data.selectedSession`：Dashboard 和当前 Session。
- `operation`：同步、加载、生成、升级、重试、取消、完成等互斥操作。
- `tutorOpen`、`tutorText`、`tutorRect`、`tutorMessages`：Session 内临时辅导。
- `vocabularyOpen`、`vocabulary`：词汇表及任务进度。
- `pathOpen`、`outlineOpen`：移动端/抽屉 UI。

课程存在运行中任务或待生成内容时，Dashboard 每 3 秒刷新；词汇表打开时每 3 秒刷新 `/api/vocabulary`。轮询只能访问本地 API。

Session 选择保存在 `duolinext.selectedSessionId`。前端会读取并清理旧的 `duolinex.selectedSessionId`，保证名称升级后保留用户位置。

## 10. API 概览

- `GET /api/health`
- `GET /api/settings/status`
- `POST /api/settings/test-duolingo`
- `POST /api/settings/test-ai`
- `GET /api/dashboard`
- `POST /api/sync/duolingo`
- `GET /api/sessions/{id}`
- `POST /api/sessions/{id}/generate`
- `POST /api/sessions/{id}/complete`
- `POST /api/lessons/upgrade`
- `POST /api/lessons/generate-missing`
- `POST /api/lessons/retry-failed`
- `POST /api/lessons/generation/cancel`
- `POST /api/tutor/ask`
- `GET /api/vocabulary`
- `GET /api/words`

课程生成、词汇整理和同步之间存在互斥检查。新增写操作前先确认不会与现有后台任务并发破坏 SQLite 状态。

## 11. 本地运行与验证

在 `website/`：

```powershell
npm.cmd run setup
npm.cmd run dev
```

验证命令：

```powershell
cd website/backend
python -m pytest
python -m compileall app

cd ..
npm --prefix frontend run build
```

端口：前端 `127.0.0.1:5173`，后端 `127.0.0.1:8000`。不要同时启动多个共享同一数据库的 `uvicorn --reload` 实例。

## 12. 安全与发布

- 不读取、输出或提交真实 JWT、AI key、完整 `.env` 或个人数据库。
- 当前 CORS 只允许本地 Vite 地址。
- 当前没有认证、用户隔离和限流，不得直接公开部署。
- 支持多人之前必须先给所有业务模型增加 user/account 维度。
- 公开部署需要认证、HTTPS、反向代理、限流、密钥管理和更严格的 CORS。

## 13. 后续优先级

1. 为 `ask_tutor` 增加独立单元测试，覆盖选中文本、空大纲、消息截断和 AI 错误。
2. 完善词汇任务取消与重试测试，覆盖缺词、无效 JSON、数据库失败和应用重启。
3. 按实际部署目标补充进程管理、反向代理、认证与 HTTPS。
4. 大型前端升级时逐步拆分 `App.tsx`，保持 API 和用户数据兼容。
5. 未经用户确认，不把词汇表扩展为完整法语词典级变位表。
