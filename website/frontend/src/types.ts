export type Word = {
  id: number
  text: string
  translations: string[]
  audioUrl?: string
  studyCount: number
  reviewCount: number
}

export type VocabularyItem = {
  word: string
  translations: string[]
  lemma: string
  partOfSpeech: string
  gender?: string
  meaning: string
  usageNote: string
  status: 'pending' | 'queued' | 'generating' | 'ready' | 'error'
  forms: Array<{ form: string; label: string; note: string }>
}

export type VocabularyData = {
  items: VocabularyItem[]
  pending: number
  task: null | {
    id: number
    status: 'queued' | 'running' | 'completed' | 'partial' | 'cancelled'
    total: number
    completed: number
    failed: number
  }
}

export type TutorMessage = {
  role: 'user' | 'assistant'
  content: string
}

export type LessonContent = {
  title: string
  overview: string
  wordNotes: Array<{
    word: string
    ipa?: string
    partOfSpeech?: string
    forms?: string
    explanation: string
  }>
  pronunciation?: {
    focus: string
    wordIpa?: Array<{ word: string; ipa: string }>
    rules?: Array<{
      title: string
      type: 'spelling' | 'sound' | 'liaison' | 'enchaînement' | 'elision' | 'rhythm'
      explanation: string
      examples: Array<{ french: string; ipa?: string }>
      contrastWithEnglish?: string
    }>
    items?: Array<{
      word: string
      ipa?: string
      syllables?: string
      stress?: string
      tips: string
      contrast?: string
    }>
  }
  grammarLesson?: null | {
    stage: string
    primaryTopic: string
    objective: string
    prerequisites: string[]
    sentencePattern: string
    coreExplanation: string
    rules: Array<{
      title: string
      explanation: string
      forms: Array<{ french: string; chinese: string; note?: string }>
    }>
    transformations: Array<{
      label: string
      before: string
      after: string
      explanation: string
    }>
    buildSteps: Array<{
      step: number
      french: string
      chinese: string
      explanation: string
    }>
    commonMistakes: Array<{
      wrong: string
      correct: string
      explanation: string
    }>
    reviewPoint?: string
  }
  grammarPoints: Array<{ title: string; french?: string; explanation: string }>
  examples: Array<{ french: string; ipa?: string; chinese: string; note?: string }>
  exercises: Array<{
    type?: 'identify' | 'transform' | 'build' | 'translate'
    question: string
    french?: string
    constraints?: string[]
    answerFrench?: string
    answer: string
    explanation: string
  }>
  summary: string[]
}

export type SkillSessionSummary = {
  id: number
  sessionIndex: number
  totalSessions: number
  isCompleted: boolean
  isSynced: boolean
  wordCount: number
  hasContent: boolean
  contentVersion: number
  outlineVersion: number
  generationStatus: 'pending' | 'queued' | 'generating' | 'outlining' | 'ready' | 'error' | 'outline_error'
  generationError?: string
  learningCompleted: boolean
  completedAt?: string
}

export type CourseSkill = {
  id: number
  duolingoSkillId: string
  title: string
  debugName: string
  state: string
  currentSessions: number
  totalSessions: number
  learningCompleted: boolean
  sessions: SkillSessionSummary[]
}

export type SessionDetail = SkillSessionSummary & {
  skill: {
    id: number
    duolingoSkillId: string
    title: string
    debugName: string
  }
  content: LessonContent | null
  contentGeneratedAt?: string
  words: Word[]
}

export type CourseOutlineContent = {
  title: string
  currentPosition: string
  courseSummary: string
  learnedThemes: string[]
  grammarProgress: string[]
  grammarLedger?: Array<{
    topic: string
    status: 'introduced' | 'practiced' | 'usable'
    canDo: string
    lastPosition: string
  }>
  pronunciationProgress: string[]
  recentSessions: Array<{ position: string; focus: string }>
  nextFocus: string[]
}

export type DashboardData = {
  totalWords: number
  skillCount: number
  aiConfigured: boolean
  generation: {
    lessonVersion: number
    outlineVersion: number
    totalLessons: number
    readyLessons: number
    outdatedLessons: number
    upgradeableLessons: number
    pendingLessons: number
    failedLessons: number
    incompleteLessons: number
    task: null | {
      id: number
      kind: 'missing' | 'upgrade' | 'retry'
      status: 'queued' | 'running' | 'completed' | 'partial' | 'cancelled'
      total: number
      completed: number
      failed: number
      currentSessionId?: number
      cancelRequested: boolean
    }
  }
  syncInProgress: boolean
  courseOutline: null | {
    content: CourseOutlineContent
    version: number
    generatedAt: string
    through: {
      skill: string
      sessionIndex: number
    }
  }
  lastSync: null | {
    at: string
    fetchedCount: number
    newCount: number
  }
  progress: {
    totalSessions: number
    completedSessions: number
    lastCompleted: null | { id: number; skill: string; sessionIndex: number }
    nextSession: null | { id: number; skill: string; sessionIndex: number }
  }
  coursePath: CourseSkill[]
  selectedSession: SessionDetail | null
}
