import { useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import {
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Headphones,
  Layers3,
  ListTree,
  LoaderCircle,
  LockKeyhole,
  Menu,
  RotateCcw,
  RefreshCw,
  ScrollText,
  Settings as SettingsIcon,
  Sparkles,
  AlertCircle,
  Server,
  Play,
  Volume2,
  X,
  MessageCircle,
  Library,
} from 'lucide-react'
import {
  getFrenchVoiceStatus,
  playFrench,
  setFrenchVoicePreference,
} from './audio/frenchAudio'
import type { FrenchVoiceStatus } from './audio/frenchAudio'
import type {
  ConfigurationStatus,
  ConnectionTestResult,
  CourseSkill,
  DashboardData,
  LessonContent,
  SessionDetail,
  SkillSessionSummary,
  Word,
  TutorMessage,
  VocabularyData,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.PROD ? '' : 'http://127.0.0.1:8000')
const SELECTED_SESSION_KEY = 'duolinext.selectedSessionId'
const LEGACY_SELECTED_SESSION_KEY = 'duolinex.selectedSessionId'

type Operation = 'sync' | 'load' | 'generate' | 'upgrade' | 'retry' | 'generate-missing' | 'cancel' | 'complete' | null
type LessonMode = 'pronunciation' | 'study'
type ConnectionTarget = 'duolingo' | 'ai'

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, options)
  } catch {
    throw new Error('无法连接本地后端。请确认 DuolinEXT 开发服务仍在运行。')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message ?? `请求失败（HTTP ${response.status}）`)
  }
  return response.json()
}

function FrenchAudio({ text, audioUrl }: { text: string; audioUrl?: string }) {
  const [playing, setPlaying] = useState(false)
  const [playbackError, setPlaybackError] = useState('')

  const play = async () => {
    setPlaybackError('')
    setPlaying(true)
    try {
      await playFrench(text, audioUrl)
    } catch (caught) {
      setPlaybackError(caught instanceof Error ? caught.message : '法语语音播放失败。')
    } finally {
      setPlaying(false)
    }
  }

  const title = playbackError || `播放 ${text}`
  return (
    <button
      className={`audio-button ${playing ? 'playing' : ''} ${playbackError ? 'failed' : ''}`}
      onClick={() => void play()}
      title={title}
      aria-label={title}
    >
      {playing ? <LoaderCircle size={16} className="spinning" /> : <Volume2 size={16} />}
    </button>
  )
}

function FrenchLine({
  text,
  ipa,
  audioUrl,
  className = '',
}: {
  text: string
  ipa?: string
  audioUrl?: string
  className?: string
}) {
  return (
    <div className={`french-line ${className}`}>
      <FrenchAudio text={text} audioUrl={audioUrl} />
      <span lang="fr">{text}</span>
      {ipa && <small className="ipa-text">{ipa}</small>}
    </div>
  )
}

function WordList({ words }: { words: Word[] }) {
  return (
    <div className="word-list">
      {words.map((word, index) => (
        <div className="word-row" key={word.id}>
          <span className="word-index">{String(index + 1).padStart(2, '0')}</span>
          <div className="word-copy">
            <FrenchLine text={word.text} audioUrl={word.audioUrl} />
            <span>{word.translations.slice(0, 3).join(' · ')}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

function PronunciationModule({ content, words }: { content: LessonContent; words: Word[] }) {
  const audioByWord = new Map(words.map((word) => [word.text.toLocaleLowerCase('fr'), word.audioUrl]))
  const wordIpa = content.pronunciation?.wordIpa?.length
    ? content.pronunciation.wordIpa
    : content.wordNotes.map((note) => ({ word: note.word, ipa: note.ipa ?? '' }))
  const rules = content.pronunciation?.rules ?? []
  const legacyItems = rules.length === 0 ? content.pronunciation?.items ?? [] : []
  const ruleType: Record<string, string> = {
    spelling: '拼写与读音',
    sound: '音值',
    liaison: '联诵',
    enchaînement: '辅音连音',
    elision: '省音',
    rhythm: '节奏组',
  }

  return (
    <div className="pronunciation-module">
      <div className="module-intro">
        <Headphones size={18} />
        <p>{content.pronunciation?.focus ?? '先听清元音和词尾，再跟读整词。'}</p>
      </div>
      <section className="ipa-inventory">
        <h3>本课词汇 IPA</h3>
        <div className="ipa-grid">
          {wordIpa.map((item, index) => (
            <FrenchLine
              key={`${item.word}-${index}`}
              text={item.word}
              ipa={item.ipa}
              audioUrl={audioByWord.get(item.word.toLocaleLowerCase('fr'))}
            />
          ))}
        </div>
      </section>
      {rules.length > 0 && (
        <section className="pronunciation-rules">
          <h3>本课语音规则</h3>
          {rules.map((rule, index) => (
            <div className="pronunciation-rule" key={`${rule.title}-${index}`}>
              <div className="rule-heading"><span>{ruleType[rule.type] ?? rule.type}</span><strong>{rule.title}</strong></div>
              <p>{rule.explanation}</p>
              <div className="rule-examples">
                {rule.examples.map((example, exampleIndex) => (
                  <FrenchLine text={example.french} ipa={example.ipa} key={`${example.french}-${exampleIndex}`} />
                ))}
              </div>
              {rule.contrastWithEnglish && <small><strong>英法对比</strong>{rule.contrastWithEnglish}</small>}
            </div>
          ))}
        </section>
      )}
      {legacyItems.length > 0 && (
        <div className="pronunciation-list legacy-pronunciation">
        {legacyItems.map((item, index) => (
          <section className="pronunciation-row" key={`${item.word}-${index}`}>
            <div className="pronunciation-sound">
              <FrenchLine
                text={item.word}
                ipa={item.ipa}
                audioUrl={audioByWord.get(item.word.toLocaleLowerCase('fr'))}
              />
              {item.syllables && <span className="syllables">{item.syllables}</span>}
            </div>
            <div className="pronunciation-notes">
              {item.stress && <p><strong>节奏</strong><span>{item.stress}</span></p>}
              <p><strong>发音</strong><span>{item.tips}</span></p>
              {item.contrast && <p><strong>辨音</strong><span>{item.contrast}</span></p>}
            </div>
          </section>
        ))}
        </div>
      )}
      {content.examples.length > 0 && (
        <section className="sentence-practice lesson-block">
          <h3>放进句子里</h3>
          {content.examples.slice(0, 3).map((example, index) => (
            <div className="example" key={`${example.french}-${index}`}>
              <FrenchLine text={example.french} ipa={example.ipa} className="example-french" />
              <span>{example.chinese}</span>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}

function GrammarLesson({ content }: { content: LessonContent }) {
  const grammar = content.grammarLesson
  if (!grammar) {
    return (
      <section className="lesson-block">
        <h3>语法重点</h3>
        {content.grammarPoints.map((point) => (
          <div className="explanation" key={point.title}>
            <strong>{point.title}</strong>
            {point.french && <FrenchLine text={point.french} className="grammar-french" />}
            <p>{point.explanation}</p>
          </div>
        ))}
      </section>
    )
  }

  return (
    <section className="grammar-module">
      <header className="grammar-heading">
        <span>{grammar.stage}</span>
        <h3>{grammar.primaryTopic}</h3>
        <p>{grammar.objective}</p>
      </header>

      {grammar.prerequisites.length > 0 && (
        <div className="grammar-prerequisites"><strong>先修</strong><span>{grammar.prerequisites.join(' · ')}</span></div>
      )}

      <div className="sentence-pattern">
        <span>句型骨架</span>
        <strong>{grammar.sentencePattern}</strong>
      </div>
      <p className="grammar-core">{grammar.coreExplanation}</p>

      <div className="grammar-rules">
        <h4>规则与词形</h4>
        {grammar.rules.map((rule, index) => (
          <section className="grammar-rule" key={`${rule.title}-${index}`}>
            <h5>{rule.title}</h5>
            <p>{rule.explanation}</p>
            {rule.forms.length > 0 && (
              <div className="form-table">
                {rule.forms.map((form, formIndex) => (
                  <div className="form-row" key={`${form.french}-${formIndex}`}>
                    <FrenchLine text={form.french} />
                    <span>{form.chinese}</span>
                    <small>{form.note}</small>
                  </div>
                ))}
              </div>
            )}
          </section>
        ))}
      </div>

      {grammar.transformations.length > 0 && (
        <section className="grammar-transformations">
          <h4>结构变换</h4>
          {grammar.transformations.map((item, index) => (
            <div className="transformation" key={`${item.label}-${index}`}>
              <strong>{item.label}</strong>
              <div className="transformation-line">
                <FrenchLine text={item.before} />
                <ChevronRight size={17} />
                <FrenchLine text={item.after} />
              </div>
              <p>{item.explanation}</p>
            </div>
          ))}
        </section>
      )}

      <section className="sentence-builder">
        <h4>逐步造句</h4>
        {grammar.buildSteps.map((item, index) => (
          <div className="build-step" key={`${item.step}-${index}`}>
            <span>{item.step}</span>
            <div><FrenchLine text={item.french} /><small>{item.chinese}</small><p>{item.explanation}</p></div>
          </div>
        ))}
      </section>

      {grammar.commonMistakes.length > 0 && (
        <section className="common-mistakes">
          <h4>典型错误</h4>
          {grammar.commonMistakes.map((item, index) => (
            <div className="mistake-row" key={`${item.wrong}-${index}`}>
              <div><span>错误</span><FrenchLine text={item.wrong} /></div>
              <div><span>正确</span><FrenchLine text={item.correct} /></div>
              <p>{item.explanation}</p>
            </div>
          ))}
        </section>
      )}

      {grammar.reviewPoint && <div className="review-point"><strong>旧知识复用</strong><span>{grammar.reviewPoint}</span></div>}
    </section>
  )
}

function StudyLesson({ content, words, onSelectText }: { content: LessonContent; words: Word[]; onSelectText: (event: MouseEvent<HTMLElement>) => void }) {
  const audioByWord = new Map(words.map((word) => [word.text.toLocaleLowerCase('fr'), word.audioUrl]))
  return (
    <div className="generated-lesson" onMouseUp={onSelectText}>
      <p className="lesson-overview">{content.overview}</p>
      <section className="lesson-block">
        <h3>词汇用法</h3>
        {content.wordNotes.map((note) => (
          <div className="note-row" key={note.word}>
            <FrenchLine
              text={note.word}
              ipa={note.ipa}
              audioUrl={audioByWord.get(note.word.toLocaleLowerCase('fr'))}
              className="note-word"
            />
            <div className="word-note-copy">
              {(note.partOfSpeech || note.forms) && <small>{[note.partOfSpeech, note.forms].filter(Boolean).join(' · ')}</small>}
              <p>{note.explanation}</p>
            </div>
          </div>
        ))}
      </section>
      <GrammarLesson content={content} />
      <section className="lesson-block">
        <h3>例句</h3>
        {content.examples.map((example, index) => (
          <div className="example" key={`${example.french}-${index}`}>
            <FrenchLine text={example.french} ipa={example.ipa} className="example-french" />
            <span>{example.chinese}</span>
            {example.note && <small>{example.note}</small>}
          </div>
        ))}
      </section>
      <section className="lesson-block">
        <h3>轻量检查</h3>
        {content.exercises.map((exercise, index) => (
          <details className="exercise" key={`${exercise.question}-${index}`}>
            <summary><span>{exercise.type ?? '练习'}</span>{exercise.question}</summary>
            {exercise.french && <FrenchLine text={exercise.french} className="exercise-french" />}
            {exercise.constraints && exercise.constraints.length > 0 && <small className="exercise-constraints">要求：{exercise.constraints.join(' · ')}</small>}
            {exercise.answerFrench && <FrenchLine text={exercise.answerFrench} className="exercise-answer" />}
            <p><strong>{exercise.answer}</strong> {exercise.explanation}</p>
          </details>
        ))}
      </section>
    </div>
  )
}

function TutorDrawer({
  session,
  selectedText,
  messages,
  onClose,
  onClearSelection,
  onMessagesChange,
  autoQuestion,
}: {
  session: SessionDetail
  selectedText: string
  messages: TutorMessage[]
  onClose: () => void
  onClearSelection: () => void
  onMessagesChange: (messages: TutorMessage[]) => void
  autoQuestion?: string
}) {
  const [question, setQuestion] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const autoSentRef = useRef('')
  const send = async (preset?: string) => {
    const text = (preset ?? question).trim()
    if (!text || sending) return
    const next = [...messages, { role: 'user' as const, content: text }]
    setQuestion('')
    setError('')
    setSending(true)
    onMessagesChange(next)
    try {
      const result = await api<{ answer: string }>('/api/tutor/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId: session.id,
          selectedText,
          question: text,
          messages,
        }),
      })
      onMessagesChange([...next, { role: 'assistant', content: result.answer }])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'AI 辅导请求失败')
    } finally {
      setSending(false)
    }
  }
  useEffect(() => {
    if (autoQuestion && autoSentRef.current !== autoQuestion) {
      autoSentRef.current = autoQuestion
      void send(autoQuestion)
    }
  }, [autoQuestion])
  return (
    <aside className="tutor-drawer" aria-label="AI 辅导">
      <header className="tutor-heading">
        <div><MessageCircle size={17} /><strong>AI 辅导</strong></div>
        <div className="tutor-heading-actions">
          <button className="drawer-close" onClick={() => { onMessagesChange([]); onClearSelection() }} title="清空对话"><RotateCcw size={16} /></button>
          <button className="drawer-close" onClick={onClose} title="关闭 AI 辅导"><X size={19} /></button>
        </div>
      </header>
      {selectedText && <blockquote className="tutor-quote">“{selectedText}”</blockquote>}
      <div className="tutor-messages">
        {messages.length === 0 && <p className="tutor-empty">选中文字后可以直接提问，也可以从这里开始。</p>}
        {messages.map((message, index) => (
          <div className={`tutor-message ${message.role}`} key={`${message.role}-${index}`}>
            <span>{message.role === 'user' ? '你' : 'AI'}</span>
            <p>{message.content}</p>
          </div>
        ))}
        {sending && <div className="tutor-message assistant"><span>AI</span><p><LoaderCircle size={15} className="spinning" />正在回答</p></div>}
      </div>
      {error && <div className="inline-error">{error}</div>}
      <form className="tutor-form" onSubmit={(event) => { event.preventDefault(); void send() }}>
        {selectedText && messages.length === 0 && <button type="button" className="tutor-suggestion" onClick={() => void send('请简短解释这段内容。')}>解释这段</button>}
        <div><input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="问一个具体问题" disabled={sending} /><button className="primary-button" type="submit" disabled={!question.trim() || sending} title="发送"><ChevronRight size={17} /></button></div>
      </form>
    </aside>
  )
}

function SelectionActions({
  rect,
  onAsk,
  onExplain,
}: {
  rect: DOMRect
  onAsk: () => void
  onExplain: () => void
}) {
  return (
    <div className="selection-actions" style={{ left: `${Math.max(12, Math.min(window.innerWidth - 220, rect.left + rect.width / 2 - 100))}px`, top: `${Math.max(12, rect.top - 48)}px` }}>
      <button onClick={onAsk}><MessageCircle size={14} />问 AI</button>
      <button onClick={onExplain}><Sparkles size={14} />解释这段</button>
    </div>
  )
}

function SettingsPanel({
  voiceStatus,
  onVoiceStatusChange,
  onClose,
}: {
  voiceStatus: FrenchVoiceStatus | null
  onVoiceStatusChange: (status: FrenchVoiceStatus) => void
  onClose: () => void
}) {
  const [configuration, setConfiguration] = useState<ConfigurationStatus | null>(null)
  const [configurationError, setConfigurationError] = useState('')
  const [testing, setTesting] = useState<ConnectionTarget | null>(null)
  const [testResults, setTestResults] = useState<Partial<Record<ConnectionTarget, ConnectionTestResult>>>({})
  const [testErrors, setTestErrors] = useState<Partial<Record<ConnectionTarget, string>>>({})
  const [voiceTesting, setVoiceTesting] = useState(false)
  const [voiceMessage, setVoiceMessage] = useState('')
  const [reloading, setReloading] = useState(false)
  const [reloadMessage, setReloadMessage] = useState('')

  useEffect(() => {
    void api<ConfigurationStatus>('/api/settings/status')
      .then(setConfiguration)
      .catch((caught) => setConfigurationError(caught instanceof Error ? caught.message : '配置状态加载失败'))
  }, [])

  const testConnection = async (target: ConnectionTarget) => {
    setTesting(target)
    setTestErrors((current) => ({ ...current, [target]: undefined }))
    setTestResults((current) => ({ ...current, [target]: undefined }))
    try {
      const result = await api<ConnectionTestResult>(`/api/settings/test-${target}`, { method: 'POST' })
      setTestResults((current) => ({ ...current, [target]: result }))
    } catch (caught) {
      setTestErrors((current) => ({
        ...current,
        [target]: caught instanceof Error ? caught.message : '连接检测失败',
      }))
    } finally {
      setTesting(null)
    }
  }

  const reloadConfiguration = async () => {
    setReloading(true)
    setReloadMessage('')
    setConfigurationError('')
    setTestErrors({})
    setTestResults({})
    try {
      const result = await api<{ configuration: ConfigurationStatus; message: string }>('/api/settings/reload', { method: 'POST' })
      setConfiguration(result.configuration)
      setReloadMessage(result.message)
    } catch (caught) {
      setConfigurationError(caught instanceof Error ? caught.message : '配置重新读取失败')
    } finally {
      setReloading(false)
    }
  }

  const changeVoice = async (preference: string) => {
    setFrenchVoicePreference(preference)
    setVoiceMessage('')
    onVoiceStatusChange(await getFrenchVoiceStatus())
  }

  const testVoice = async () => {
    setVoiceTesting(true)
    setVoiceMessage('')
    try {
      await playFrench('Bonjour, bienvenue dans DuolinEXT.')
      setVoiceMessage('法语语音播放正常。')
    } catch (caught) {
      setVoiceMessage(caught instanceof Error ? caught.message : '语音测试失败。')
    } finally {
      setVoiceTesting(false)
    }
  }

  const onlineVoiceCount = voiceStatus?.voices.filter((voice) => !voice.local).length ?? 0
  const localVoiceCount = voiceStatus?.voices.filter((voice) => voice.local).length ?? 0
  const userAgent = navigator.userAgent.toLowerCase()
  const installHint = userAgent.includes('windows')
    ? '打开 Windows 设置 > 时间和语言 > 语言和区域，添加法语并安装“语音”，完成后重启浏览器。'
    : userAgent.includes('mac os')
      ? '打开系统设置 > 辅助功能 > 朗读内容 > 系统声音，在声音管理中下载法语声音，然后重启浏览器。'
      : '请在系统语言或辅助功能设置中安装法语语音，然后重启浏览器。'

  return (
    <div className="settings-overlay" role="presentation">
      <div className="settings-panel" role="dialog" aria-modal="true" aria-label="设置">
        <header className="settings-heading">
          <div><span className="eyebrow">Configuration</span><h2>设置</h2></div>
          <button className="drawer-close" onClick={onClose} title="关闭设置"><X size={20} /></button>
        </header>

        <div className="settings-body">
          <section className="settings-section">
            <div className="settings-section-heading"><Server size={18} /><div><h3>服务连接</h3><p>状态来自后端当前加载的 `.env`。修改文件后点击重新读取即可，无需重启；数据库路径变更仍需重启服务。</p></div></div>
            <div className="settings-actions">
              <button className="secondary-button" onClick={() => void reloadConfiguration()} disabled={reloading || testing !== null}>
                {reloading ? <LoaderCircle size={15} className="spinning" /> : <RefreshCw size={15} />}重新读取配置
              </button>
              {reloadMessage && <small className="test-success">{reloadMessage}</small>}
            </div>
            {configurationError && <div className="inline-error">{configurationError}</div>}
            {!configuration && !configurationError && <div className="settings-loading"><LoaderCircle size={16} className="spinning" />正在读取配置</div>}
            {configuration && (
              <div className="connection-list">
                <div className="connection-row">
                  <div className={`status-icon ${configuration.duolingo.configured ? 'ready' : 'missing'}`}>
                    {configuration.duolingo.configured ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
                  </div>
                  <div className="connection-copy">
                    <strong>多邻国</strong>
                    <span>
                      JWT {configuration.duolingo.jwtConfigured ? '已配置' : '未配置'} · 用户 {configuration.duolingo.userIdHint ?? '未配置'} · {configuration.duolingo.courseId ?? '—'} ← {configuration.duolingo.fromLanguage ?? '—'}
                    </span>
                    {testResults.duolingo && <small className="test-success">连接正常 · {testResults.duolingo.latencyMs} ms · 读取到 {testResults.duolingo.skillCount ?? 0} 个 Skill</small>}
                    {testErrors.duolingo && <small className="test-error">{testErrors.duolingo}</small>}
                  </div>
                  <button className="secondary-button" disabled={!configuration.duolingo.configured || testing !== null} onClick={() => void testConnection('duolingo')}>
                    {testing === 'duolingo' ? <LoaderCircle size={15} className="spinning" /> : <RefreshCw size={15} />}检测
                  </button>
                </div>

                <div className="connection-row">
                  <div className={`status-icon ${configuration.ai.configured ? 'ready' : 'missing'}`}>
                    {configuration.ai.configured ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
                  </div>
                  <div className="connection-copy">
                    <strong>AI 服务</strong>
                    <span>Key {configuration.ai.apiKeyConfigured ? '已配置' : '未配置'} · {configuration.ai.endpoint ?? 'URL 未配置'} · {configuration.ai.model ?? '模型未配置'} · reasoning {configuration.ai.reasoningEffort}</span>
                    {testResults.ai && <small className="test-success">连接正常 · {testResults.ai.latencyMs} ms · {testResults.ai.message}</small>}
                    {testErrors.ai && <small className="test-error">{testErrors.ai}</small>}
                  </div>
                  <button className="secondary-button" disabled={!configuration.ai.configured || testing !== null} onClick={() => void testConnection('ai')}>
                    {testing === 'ai' ? <LoaderCircle size={15} className="spinning" /> : <RefreshCw size={15} />}检测
                  </button>
                </div>
              </div>
            )}
          </section>

          <section className="settings-section">
            <div className="settings-section-heading"><Volume2 size={18} /><div><h3>法语语音</h3><p>单词优先使用多邻国原音；例句和无原音内容使用这里选择的语音。</p></div></div>
            {voiceStatus ? (
              <div className="voice-settings">
                <div className="voice-summary">
                  <div className={`status-icon ${voiceStatus.supported && voiceStatus.voices.length ? 'ready' : 'missing'}`}>
                    {voiceStatus.supported && voiceStatus.voices.length ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
                  </div>
                  <div>
                    <strong>{voiceStatus.selectedVoice ? voiceStatus.selectedVoice.name : '未找到法语语音'}</strong>
                    <span>{onlineVoiceCount} 个浏览器在线语音 · {localVoiceCount} 个设备本地语音</span>
                  </div>
                </div>
                {voiceStatus.voices.length > 0 ? (
                  <div className="voice-controls">
                    <label htmlFor="french-voice">朗读语音</label>
                    <select id="french-voice" value={voiceStatus.preference} onChange={(event) => void changeVoice(event.target.value)}>
                      <option value="auto">自动（在线优先，本地回退）</option>
                      {voiceStatus.voices.map((voice) => (
                        <option value={voice.voiceURI} key={voice.voiceURI}>{voice.name} · {voice.lang} · {voice.local ? '本地' : '在线'}</option>
                      ))}
                    </select>
                    <button className="secondary-button" onClick={() => void testVoice()} disabled={voiceTesting}>
                      {voiceTesting ? <LoaderCircle size={15} className="spinning" /> : <Play size={15} />}试听
                    </button>
                  </div>
                ) : (
                  <div className="voice-guidance"><AlertCircle size={17} /><p>{installHint}</p></div>
                )}
                {voiceMessage && <div className={voiceMessage.includes('正常') ? 'test-success voice-message' : 'test-error voice-message'}>{voiceMessage}</div>}
              </div>
            ) : <div className="settings-loading"><LoaderCircle size={16} className="spinning" />正在检测浏览器语音</div>}
          </section>
        </div>
      </div>
    </div>
  )
}

function VocabularyPanel({ data, onClose }: { data: VocabularyData | null; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('全部')
  const items = (data?.items ?? []).filter((item) => {
    const matchesQuery = !query.trim() || `${item.word} ${item.lemma} ${item.meaning}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())
    const matchesFilter = filter === '全部' || item.partOfSpeech === filter
    return matchesQuery && matchesFilter
  })
  const filters = ['全部', '动词', '名词', '形容词', '其他']
  return (
    <div className="vocabulary-overlay">
      <div className="vocabulary-panel">
        <header className="vocabulary-heading"><div><span className="eyebrow">Vocabulary</span><h2>词汇表</h2></div><button className="drawer-close" onClick={onClose} title="关闭词汇表"><X size={20} /></button></header>
        <div className="vocabulary-toolbar"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索单词或含义" /> <div className="vocabulary-filters">{filters.map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div></div>
        {data?.task && ['queued', 'running'].includes(data.task.status) && <div className="outline-progress"><LoaderCircle size={15} className="spinning" />正在整理词形 {data.task.completed} / {data.task.total}</div>}
        {data?.pending ? <p className="vocabulary-pending">还有 {data.pending} 个词形资料尚未生成。</p> : null}
        <div className="vocabulary-list">
          {items.map((item) => <details className="vocabulary-row" key={item.word}><summary><button className="vocabulary-word" onClick={(event) => { event.preventDefault(); void playFrench(item.word) }}>{item.word}</button><span>{item.lemma}{item.gender && item.gender !== '不适用' ? ` · ${item.gender}` : ''}</span><small>{item.meaning || item.translations.slice(0, 2).join(' · ')}</small><ChevronDown size={15} /></summary><div className="vocabulary-detail">{item.usageNote && <p>{item.usageNote}</p>}{item.forms.map((form) => <div key={`${form.form}-${form.label}`}><button className="vocabulary-form" onClick={() => void playFrench(form.form)}>{form.form}</button><span>{form.label}</span><small>{form.note}</small></div>)}</div></details>)}
          {!items.length && <div className="outline-empty"><Library size={25} /><p>没有匹配的词汇。</p></div>}
        </div>
      </div>
    </div>
  )
}

function SessionIcon({ session }: { session: SkillSessionSummary }) {
  if (!session.isCompleted) return <LockKeyhole size={14} />
  if (!session.isSynced) return <RefreshCw size={14} />
  if (session.learningCompleted) return <CheckCircle2 size={15} />
  return <Circle size={14} />
}

function sessionStatus(session: SkillSessionSummary) {
  if (!session.isCompleted) return '未解锁'
  if (!session.isSynced) return '待同步'
  if (session.generationStatus === 'queued') return '课程排队中'
  if (session.generationStatus === 'generating') return '正在生成课程'
  if (session.generationStatus === 'outlining') return '正在更新大纲'
  if (session.generationStatus === 'error') return '课程生成失败'
  if (session.generationStatus === 'outline_error' && !session.hasContent) return '大纲生成失败'
  if (session.wordCount === 0) return '无新增词 · 已完成'
  if (session.learningCompleted) return '已完成'
  if (session.hasContent) return '学习中'
  return '未完成'
}

function SkillPath({
  skills,
  selectedId,
  onSelect,
}: {
  skills: CourseSkill[]
  selectedId?: number
  onSelect: (id: number) => void
}) {
  return (
    <nav className="skill-list" aria-label="课程路径">
      {skills.map((skill, skillIndex) => {
        const containsSelected = skill.sessions.some((session) => session.id === selectedId)
        return (
          <details className="skill-group" key={skill.id} open={containsSelected || skill.state === 'active'}>
            <summary>
              <span className="skill-number">{String(skillIndex + 1).padStart(2, '0')}</span>
              <span className="skill-title"><strong>{skill.title}</strong><small>{skill.learningCompleted ? '已完成 · ' : ''}{skill.sessions.length} sessions</small></span>
              <ChevronDown size={15} className="skill-chevron" />
            </summary>
            <div className="skill-sessions session-nodes">
              {skill.sessions.map((session) => (
                <button
                  key={session.id}
                  className={`session-node ${session.id === selectedId ? 'active' : ''} ${!session.isCompleted ? 'locked' : ''}`}
                  onClick={() => onSelect(session.id)}
                  aria-current={session.id === selectedId ? 'step' : undefined}
                >
                  <span className="session-node-icon"><SessionIcon session={session} /></span>
                  <span><strong>Session {session.sessionIndex}</strong><small>{sessionStatus(session)}</small></span>
                </button>
              ))}
            </div>
          </details>
        )
      })}
    </nav>
  )
}

function SessionWorkspace({
  session,
  aiConfigured,
  operation,
  generationBusy,
  syncBusy,
  onSelectText,
  onGenerate,
  onComplete,
}: {
  session: SessionDetail | null
  aiConfigured: boolean
  operation: Operation
  generationBusy: boolean
  syncBusy: boolean
  onSelectText: (event: MouseEvent<HTMLElement>) => void
  onGenerate: () => void
  onComplete: () => void
}) {
  const [mode, setMode] = useState<LessonMode>('pronunciation')
  const workspaceRef = useRef<HTMLElement>(null)

  useEffect(() => {
    setMode('pronunciation')
  }, [session?.id])

  if (!session) {
    return (
      <div className="empty-state">
        <ListTree size={28} />
        <h2>课程路径尚未同步</h2>
        <p>点击右上角“同步多邻国”读取当前 Skill 与 Session 进度。</p>
      </div>
    )
  }

  const busy = operation !== null
  const generationBlocked = busy || generationBusy || syncBusy
  const unavailable = !session.isCompleted || !session.isSynced
  const generating = ['queued', 'generating', 'outlining'].includes(session.generationStatus)

  const openStudy = () => {
    setMode('study')
    window.requestAnimationFrame(() => {
      workspaceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  return (
    <section ref={workspaceRef} className="session-workspace-content">
      <header className="session-heading">
        <div>
          <span className="session-breadcrumb">{session.skill.title} / Session {session.sessionIndex}</span>
          <h1>{session.content?.title ?? `Session ${session.sessionIndex}`}</h1>
          <span className="session-meta">
            {session.wordCount} 个新词 · {sessionStatus(session)}
            {session.contentVersion > 0 && ` · 课程 v${session.contentVersion}`}
            {session.contentGeneratedAt && ` · ${formatDateTime(session.contentGeneratedAt)}`}
          </span>
        </div>
        {!unavailable && (
          <div className="lesson-tabs" role="tablist" aria-label="课程阶段">
            <button className={mode === 'pronunciation' ? 'active' : ''} onClick={() => setMode('pronunciation')}><Headphones size={15} />发音</button>
            <button className={mode === 'study' ? 'active' : ''} onClick={() => setMode('study')}><BookOpen size={15} />讲解</button>
          </div>
        )}
      </header>

      {unavailable ? (
        <div className="locked-state">
          {session.isCompleted ? <RefreshCw size={25} /> : <LockKeyhole size={25} />}
          <h2>{session.isCompleted ? '这个 Session 等待同步' : '这个 Session 尚未完成'}</h2>
        </div>
      ) : session.words.length === 0 ? (
        <div className="empty-state compact">
          <CheckCircle2 size={26} />
          <h2>本 Session 没有新增词汇</h2>
          <p>该 Session 已自动标记为完成。</p>
        </div>
      ) : generating && !session.content ? (
        <div className="generation-state">
          <LoaderCircle size={26} className="spinning" />
          <h2>{session.generationStatus === 'outlining' ? '课程已保存，正在整理大纲' : '正在自动生成课程'}</h2>
          <p>完成后会自动显示，无需重复操作。</p>
        </div>
      ) : !session.content ? (
        <>
          <WordList words={session.words} />
          {session.generationError && <div className="inline-error">{session.generationError}</div>}
          <div className="lesson-actions">
            <button className="primary-button" onClick={onGenerate} disabled={!aiConfigured || generationBlocked}>
              {operation === 'generate' ? <LoaderCircle size={16} className="spinning" /> : <Sparkles size={16} />}
              {aiConfigured ? operation === 'generate' ? '正在生成' : '生成本 Session 课程' : 'AI 待配置'}
            </button>
          </div>
        </>
      ) : (
        <>
          {mode === 'pronunciation' && <PronunciationModule content={session.content} words={session.words} />}
          {mode === 'study' && <StudyLesson content={session.content} words={session.words} onSelectText={onSelectText} />}
          <div className="lesson-actions">
            <button className="secondary-button" onClick={onGenerate} disabled={!aiConfigured || generationBlocked} title="覆盖并重新生成当前课程">
              {operation === 'generate' ? <LoaderCircle size={16} className="spinning" /> : <RotateCcw size={16} />}
              重新生成
            </button>
            {mode === 'pronunciation' && (
              <button className="primary-button" onClick={openStudy}><BookOpen size={16} />进入讲解</button>
            )}
            {mode === 'study' && (
              <button className="primary-button" onClick={onComplete} disabled={session.learningCompleted || busy}>
                {operation === 'complete' ? <LoaderCircle size={16} className="spinning" /> : <Check size={16} />}
                {session.learningCompleted ? '课程已完成' : '完成课程'}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  )
}

function formatDateTime(value?: string) {
  if (!value) return '尚未同步'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function CourseOutlinePanel({
  data,
  operation,
  onClose,
  onUpgrade,
  onRetry,
  onGenerateMissing,
  onCancel,
  syncBusy,
}: {
  data: DashboardData | null
  operation: Operation
  onClose: () => void
  onUpgrade: () => void
  onRetry: () => void
  onGenerateMissing: () => void
  onCancel: () => void
  syncBusy: boolean
}) {
  const outline = data?.courseOutline
  const generation = data?.generation
  const content = outline?.content
  const task = generation?.task
  const taskRunning = task !== null && task !== undefined && ['queued', 'running'].includes(task.status)
  const controlsBusy = operation !== null || taskRunning || syncBusy || Boolean(data?.syncInProgress)
  const taskLabel = task?.kind === 'upgrade' ? '升级旧课程' : task?.kind === 'retry' ? '重试失败课程' : '生成未生成课程'
  return (
    <aside className="outline-drawer" aria-label="课程大纲">
      <header className="outline-heading">
        <div><span className="eyebrow">Course overview</span><h2>课程大纲</h2></div>
        <button className="drawer-close" onClick={onClose} title="关闭课程大纲"><X size={19} /></button>
      </header>

      <div className="generation-summary">
        <div><span>当前课程版本</span><strong>v{generation?.lessonVersion ?? 1}</strong></div>
        <div><span>已就绪</span><strong>{generation?.readyLessons ?? 0} / {generation?.totalLessons ?? 0}</strong></div>
      </div>
      {taskRunning && task && (
        <div className="outline-progress">
          <LoaderCircle size={15} className="spinning" />
          <span>{task.cancelRequested ? '正在停止' : `${taskLabel}：已完成 ${task.completed} / ${task.total}${task.failed ? `，失败 ${task.failed}` : ''}`}{task.currentSessionId ? ` · Session ${task.currentSessionId}` : ''}</span>
        </div>
      )}
      {!taskRunning && task?.status === 'partial' && <div className="inline-warning">上次批量任务已结束，成功 {task.completed} 节，失败 {task.failed} 节。</div>}
      {!taskRunning && task?.status === 'cancelled' && <div className="inline-warning">批量生成已中断，已完成的课程已保留。</div>}
      {taskRunning && (
        <button className="retry-button" onClick={onCancel} disabled={operation !== null || task.cancelRequested}>
          {operation === 'cancel' ? <LoaderCircle size={16} className="spinning" /> : <X size={16} />}
          {task.cancelRequested ? '正在中断' : '中断生成'}
        </button>
      )}
      {(generation?.failedLessons ?? 0) > 0 && (
        <button className="retry-button" onClick={onRetry} disabled={controlsBusy || !data?.aiConfigured}>
          {operation === 'retry' ? <LoaderCircle size={16} className="spinning" /> : <RotateCcw size={16} />}
          重试 {generation?.failedLessons} 节失败课程
        </button>
      )}
      {(generation?.upgradeableLessons ?? 0) > 0 && (
        <button className="upgrade-button" onClick={onUpgrade} disabled={controlsBusy || !data?.aiConfigured}>
          {operation === 'upgrade' ? <LoaderCircle size={16} className="spinning" /> : <RefreshCw size={16} />}
          升级 {generation?.upgradeableLessons} 节旧课程
        </button>
      )}
      {(generation?.incompleteLessons ?? 0) > 0 && (
        <button className="upgrade-button" onClick={onGenerateMissing} disabled={controlsBusy || !data?.aiConfigured}>
          {operation === 'generate-missing' ? <LoaderCircle size={16} className="spinning" /> : <Sparkles size={16} />}
          生成 {generation?.incompleteLessons} 节未生成课程
        </button>
      )}

      {content ? (
        <div className="outline-content">
          <section className="outline-lead">
            <span>{content.currentPosition}</span>
            <p>{content.courseSummary}</p>
            <small>更新至 {outline.through.skill} / Session {outline.through.sessionIndex}</small>
          </section>
          {content.learnedThemes.length > 0 && <OutlineList title="主题能力" items={content.learnedThemes} />}
          {content.grammarLedger && content.grammarLedger.length > 0 ? (
            <GrammarLedger items={content.grammarLedger} />
          ) : content.grammarProgress.length > 0 ? (
            <OutlineList title="语法进度" items={content.grammarProgress} />
          ) : null}
          {content.pronunciationProgress.length > 0 && <OutlineList title="发音进度" items={content.pronunciationProgress} />}
          {content.recentSessions.length > 0 && (
            <section className="outline-section">
              <h3>最近课程</h3>
              {content.recentSessions.map((item, index) => (
                <div className="outline-session" key={`${item.position}-${index}`}><strong>{item.position}</strong><span>{item.focus}</span></div>
              ))}
            </section>
          )}
          {content.nextFocus.length > 0 && <OutlineList title="接下来" items={content.nextFocus} />}
        </div>
      ) : (
        <div className="outline-empty"><ScrollText size={25} /><p>生成第一节课程后，这里会形成累计学习大纲。</p></div>
      )}
    </aside>
  )
}

function OutlineList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="outline-section">
      <h3>{title}</h3>
      <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
    </section>
  )
}

function GrammarLedger({
  items,
}: {
  items: NonNullable<DashboardData['courseOutline']>['content']['grammarLedger']
}) {
  const statusLabel = {
    introduced: '已引入',
    practiced: '练习中',
    usable: '可使用',
  }
  return (
    <section className="outline-section grammar-ledger">
      <h3>语法能力</h3>
      {items?.map((item, index) => (
        <div className="ledger-row" key={`${item.topic}-${index}`}>
          <div><strong>{item.topic}</strong><span className={`ledger-status ${item.status}`}>{statusLabel[item.status]}</span></div>
          <p>{item.canDo}</p>
          <small>{item.lastPosition}</small>
        </div>
      ))}
    </section>
  )
}

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [operation, setOperation] = useState<Operation>(null)
  const [syncActive, setSyncActive] = useState(false)
  const [tutorOpen, setTutorOpen] = useState(false)
  const [tutorText, setTutorText] = useState('')
  const [tutorRect, setTutorRect] = useState<DOMRect | null>(null)
  const [tutorMessages, setTutorMessages] = useState<TutorMessage[]>([])
  const [tutorAutoQuestion, setTutorAutoQuestion] = useState('')
  const [vocabularyOpen, setVocabularyOpen] = useState(false)
  const [vocabulary, setVocabulary] = useState<VocabularyData | null>(null)
  const [error, setError] = useState('')
  const [pathOpen, setPathOpen] = useState(false)
  const [outlineOpen, setOutlineOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [voiceStatus, setVoiceStatus] = useState<FrenchVoiceStatus | null>(null)

  const today = useMemo(
    () => new Intl.DateTimeFormat('zh-CN', { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date()),
    [],
  )

  useEffect(() => {
    const savedSessionId = window.localStorage.getItem(SELECTED_SESSION_KEY)
      ?? window.localStorage.getItem(LEGACY_SELECTED_SESSION_KEY)
    if (savedSessionId) {
      window.localStorage.setItem(SELECTED_SESSION_KEY, savedSessionId)
      window.localStorage.removeItem(LEGACY_SELECTED_SESSION_KEY)
    }
    const query = savedSessionId ? `?selected_session_id=${encodeURIComponent(savedSessionId)}` : ''
    void api<DashboardData>(`/api/dashboard${query}`)
      .then((payload) => setData(payload))
      .catch((caught) => setError(caught instanceof Error ? caught.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    let active = true
    const refreshVoices = () => {
      void getFrenchVoiceStatus().then((status) => {
        if (active) setVoiceStatus(status)
      })
    }
    refreshVoices()
    if (!('speechSynthesis' in window)) return () => { active = false }
    window.speechSynthesis.addEventListener('voiceschanged', refreshVoices)
    return () => {
      active = false
      window.speechSynthesis.removeEventListener('voiceschanged', refreshVoices)
    }
  }, [])

  useEffect(() => {
    if (data?.selectedSession?.id) {
      window.localStorage.setItem(SELECTED_SESSION_KEY, String(data.selectedSession.id))
    }
  }, [data?.selectedSession?.id])

  useEffect(() => {
    if (!tutorRect) return
    const clearSelectionAction = () => setTutorRect(null)
    const clearSelectionOnChange = () => {
      if (!window.getSelection()?.toString().trim()) setTutorRect(null)
    }
    const clearSelectionOnPointerDown = (event: PointerEvent) => {
      if (!(event.target as Element | null)?.closest('.selection-actions')) {
        setTutorRect(null)
      }
    }
    window.addEventListener('scroll', clearSelectionAction, true)
    window.addEventListener('resize', clearSelectionAction)
    document.addEventListener('selectionchange', clearSelectionOnChange)
    document.addEventListener('pointerdown', clearSelectionOnPointerDown)
    return () => {
      window.removeEventListener('scroll', clearSelectionAction, true)
      window.removeEventListener('resize', clearSelectionAction)
      document.removeEventListener('selectionchange', clearSelectionOnChange)
      document.removeEventListener('pointerdown', clearSelectionOnPointerDown)
    }
  }, [tutorRect])

  useEffect(() => {
    setTutorOpen(false)
    setTutorText('')
    setTutorRect(null)
    setTutorMessages([])
    setTutorAutoQuestion('')
  }, [data?.selectedSession?.id])

  useEffect(() => {
    const task = data?.generation.task
    const taskRunning = task !== null && task !== undefined && ['queued', 'running'].includes(task.status)
    if (!taskRunning && !(data?.generation.pendingLessons ?? 0)) return
    const timer = window.setInterval(() => {
      const selectedId = data?.selectedSession?.id
      const query = selectedId ? `?selected_session_id=${selectedId}` : ''
      void api<DashboardData>(`/api/dashboard${query}`)
        .then((payload) => setData(payload))
        .catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [data?.generation.pendingLessons, data?.generation.task?.id, data?.generation.task?.status, data?.selectedSession?.id])

  useEffect(() => {
    if (!vocabularyOpen) return
    const loadVocabulary = () => {
      void api<VocabularyData>('/api/vocabulary').then(setVocabulary).catch(() => undefined)
    }
    loadVocabulary()
    const timer = window.setInterval(loadVocabulary, 3000)
    return () => window.clearInterval(timer)
  }, [vocabularyOpen])

  const runDashboardAction = async (
    nextOperation: Exclude<Operation, null>,
    action: () => Promise<DashboardData>,
  ) => {
    if (nextOperation === 'sync') setSyncActive(true)
    setOperation(nextOperation)
    setError('')
    try {
      setData(await action())
      return true
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '操作失败')
      return false
    } finally {
      if (nextOperation === 'sync') setSyncActive(false)
      setOperation(null)
    }
  }

  const selectSession = async (id: number) => {
    setOperation('load')
    setError('')
    setPathOpen(false)
    try {
      const selectedSession = await api<SessionDetail>(`/api/sessions/${id}`)
      setData((current) => current ? { ...current, selectedSession } : current)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '课程加载失败')
    } finally {
      setOperation(null)
    }
  }

  const selected = data?.selectedSession ?? null
  const task = data?.generation.task
  const taskRunning = task !== null && task !== undefined && ['queued', 'running'].includes(task.status)
  const syncRunning = syncActive || Boolean(data?.syncInProgress)
  const completion = data?.progress.totalSessions
    ? Math.round(((data.progress.completedSessions ?? 0) / data.progress.totalSessions) * 100)
    : 0

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">DXT</span><span>DuolinEXT</span></div>
        <span className="today-label">{today}</span>
        <div className="topbar-actions">
          <button className="outline-button" onClick={() => setOutlineOpen(true)}>
            <ScrollText size={16} />
            <span>课程大纲</span>
            {((data?.generation.outdatedLessons ?? 0) + (data?.generation.failedLessons ?? 0)) > 0 && <b>{(data?.generation.outdatedLessons ?? 0) + (data?.generation.failedLessons ?? 0)}</b>}
          </button>
          <button className="outline-button" onClick={() => setVocabularyOpen(true)} title="打开词汇表"><Library size={16} /><span>词汇表</span></button>
          <button className="settings-button" onClick={() => setSettingsOpen(true)} title="打开设置" aria-label="打开设置"><SettingsIcon size={18} /></button>
          <button className="path-toggle" onClick={() => setPathOpen(true)} title="打开课程路径"><Menu size={18} /></button>
          <button
            className="sync-button"
            disabled={operation !== null || taskRunning || syncRunning}
            onClick={() => {
              const query = selected ? `?selected_session_id=${selected.id}` : ''
              void runDashboardAction('sync', () => api(`/api/sync/duolingo${query}`, { method: 'POST' }))
            }}
          >
            <RefreshCw size={16} className={operation === 'sync' ? 'spinning' : ''} />
            <span>{operation === 'sync' ? '正在同步' : '同步多邻国'}</span>
          </button>
        </div>
      </header>

      {voiceStatus && (!voiceStatus.supported || voiceStatus.voices.length === 0) && (
        <div className="setup-banner">
          <AlertCircle size={17} />
          <span>尚未检测到法语语音，例句朗读暂不可用。</span>
          <button onClick={() => setSettingsOpen(true)}>检查语音设置</button>
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}
      {settingsOpen && <SettingsPanel voiceStatus={voiceStatus} onVoiceStatusChange={setVoiceStatus} onClose={() => setSettingsOpen(false)} />}
      {outlineOpen && (
        <>
          <button className="drawer-scrim" onClick={() => setOutlineOpen(false)} aria-label="关闭课程大纲" />
          <CourseOutlinePanel
            data={data}
            operation={operation}
            onClose={() => setOutlineOpen(false)}
            onUpgrade={() => {
              const query = selected ? `?selected_session_id=${selected.id}` : ''
              void runDashboardAction('upgrade', () => api(`/api/lessons/upgrade${query}`, { method: 'POST' }))
            }}
            onRetry={() => {
              const query = selected ? `?selected_session_id=${selected.id}` : ''
              void runDashboardAction('retry', () => api(`/api/lessons/retry-failed${query}`, { method: 'POST' }))
            }}
            onGenerateMissing={() => {
              const query = selected ? `?selected_session_id=${selected.id}` : ''
              void runDashboardAction('generate-missing', () => api(`/api/lessons/generate-missing${query}`, { method: 'POST' }))
            }}
            onCancel={() => {
              void runDashboardAction('cancel', () => api('/api/lessons/generation/cancel', { method: 'POST' }))
            }}
            syncBusy={syncRunning}
          />
        </>
      )}

      <main className="course-layout">
        <aside className={`course-sidebar ${pathOpen ? 'open' : ''}`}>
          <div className="sidebar-heading">
            <div><span className="eyebrow">Course path</span><h2>法语课程</h2></div>
            <button className="sidebar-close" onClick={() => setPathOpen(false)} title="关闭课程路径"><X size={19} /></button>
          </div>
          <div className="path-summary">
            <div><Layers3 size={16} /><span>{data?.skillCount ?? 0} Skills</span></div>
            <strong>{completion}%</strong>
          </div>
          <div className="path-progress"><span style={{ width: `${completion}%` }} /></div>
          {data?.progress.nextSession && (
            <button
              className="continue-button"
              onClick={() => void selectSession(data.progress.nextSession!.id)}
            >
              <ChevronRight size={16} />
              <span>继续学习 <strong>{data.progress.nextSession.skill} / Session {data.progress.nextSession.sessionIndex}</strong></span>
            </button>
          )}
          {data?.progress.lastCompleted && (
            <div className="last-completed">上次完成：{data.progress.lastCompleted.skill} / Session {data.progress.lastCompleted.sessionIndex}</div>
          )}
          {data?.coursePath.length ? (
            <SkillPath skills={data.coursePath} selectedId={selected?.id} onSelect={(id) => void selectSession(id)} />
          ) : (
            <div className="sidebar-empty">暂无课程路径</div>
          )}
          <footer className="sidebar-footer">
            <span>上次同步</span><strong>{formatDateTime(data?.lastSync?.at)}</strong>
          </footer>
        </aside>
        {pathOpen && <button className="sidebar-scrim" onClick={() => setPathOpen(false)} aria-label="关闭课程路径" />}

        <article className="lesson-workspace">
          {loading || operation === 'load' ? (
            <div className="loading"><Clock3 size={21} /><span>正在准备课程</span></div>
          ) : (
            <SessionWorkspace
              session={selected}
              aiConfigured={data?.aiConfigured ?? false}
              operation={operation}
              generationBusy={taskRunning}
              syncBusy={syncRunning}
              onSelectText={(event) => {
                const selection = window.getSelection()
                const text = selection?.toString().trim() ?? ''
                if (!text) return
                setTutorText(text.slice(0, 1000))
                setTutorRect(selection?.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : null)
              }}
              onGenerate={() => {
                if (selected) void runDashboardAction('generate', () => api(`/api/sessions/${selected.id}/generate`, { method: 'POST' }))
              }}
              onComplete={() => {
                if (selected) {
                  void runDashboardAction('complete', () => api(`/api/sessions/${selected.id}/complete`, { method: 'POST' }))
                    .then((completed) => {
                      if (!completed) return
                      setTutorOpen(false)
                      setTutorText('')
                      setTutorRect(null)
                      setTutorMessages([])
                      setTutorAutoQuestion('')
                    })
                }
              }}
            />
          )}
        </article>

        <aside className="progress-rail">
          <span className="eyebrow">Progress</span>
          <dl>
            <div><dt>课程</dt><dd>{data?.progress.totalSessions ?? 0}</dd></div>
            <div><dt>已完成</dt><dd>{data?.progress.completedSessions ?? 0}</dd></div>
          </dl>
          <div className="word-total"><span>课程词汇</span><strong>{data?.totalWords ?? 0}</strong></div>
        </aside>
      </main>
      <button className="tutor-fab" onClick={() => { setTutorOpen(true); setTutorRect(null) }} title="打开 AI 辅导"><MessageCircle size={19} /><span>AI 辅导</span></button>
      {tutorRect && !tutorOpen && <SelectionActions rect={tutorRect} onAsk={() => { setTutorAutoQuestion(''); setTutorOpen(true); setTutorRect(null) }} onExplain={() => { setTutorAutoQuestion('请简短解释这段内容。'); setTutorOpen(true); setTutorRect(null); setTutorMessages([]) }} />}
      {tutorOpen && selected && <TutorDrawer session={selected} selectedText={tutorText} messages={tutorMessages} autoQuestion={tutorAutoQuestion} onClose={() => { setTutorOpen(false); setTutorRect(null); setTutorMessages([]); setTutorText(''); setTutorAutoQuestion('') }} onClearSelection={() => setTutorText('')} onMessagesChange={setTutorMessages} />}
      {vocabularyOpen && <VocabularyPanel data={vocabulary} onClose={() => setVocabularyOpen(false)} />}
    </div>
  )
}
