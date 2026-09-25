type PlaybackHandle = { stop: () => void }

export type FrenchVoiceInfo = {
  voiceURI: string
  name: string
  lang: string
  local: boolean
}

export type FrenchVoiceStatus = {
  supported: boolean
  voices: FrenchVoiceInfo[]
  selectedVoice?: FrenchVoiceInfo
  preference: string
}

const VOICE_PREFERENCE_KEY = 'duolinext.frenchVoicePreference'
const AUTOMATIC_PREFERENCE = 'auto'

let activePlayback: PlaybackHandle | null = null

function stopActivePlayback() {
  activePlayback?.stop()
  activePlayback = null
}

function normalisedLanguage(voice: SpeechSynthesisVoice) {
  return voice.lang.replace('_', '-').toLowerCase()
}

function frenchVoices(synthesis: SpeechSynthesis) {
  return synthesis.getVoices().filter((voice) => normalisedLanguage(voice).startsWith('fr'))
}

function automaticFrenchVoice(voices: SpeechSynthesisVoice[]) {
  const isFranceFrench = (voice: SpeechSynthesisVoice) => normalisedLanguage(voice) === 'fr-fr'
  return voices.find((voice) => isFranceFrench(voice) && !voice.localService)
    ?? voices.find((voice) => !voice.localService)
    ?? voices.find((voice) => isFranceFrench(voice) && voice.localService)
    ?? voices.find((voice) => voice.localService)
    ?? voices[0]
}

function storedPreference() {
  return window.localStorage.getItem(VOICE_PREFERENCE_KEY) || AUTOMATIC_PREFERENCE
}

function selectFrenchVoice(synthesis: SpeechSynthesis) {
  const voices = frenchVoices(synthesis)
  const preference = storedPreference()
  if (preference !== AUTOMATIC_PREFERENCE) {
    const selected = voices.find((voice) => voice.voiceURI === preference)
    if (selected) return selected
  }
  return automaticFrenchVoice(voices)
}

async function waitForFrenchVoice(synthesis: SpeechSynthesis) {
  const readyVoice = selectFrenchVoice(synthesis)
  if (readyVoice || synthesis.getVoices().length > 0) return readyVoice

  await new Promise<void>((resolve) => {
    let settled = false
    let timeoutId = 0
    const finish = () => {
      if (settled) return
      settled = true
      synthesis.removeEventListener('voiceschanged', onVoicesChanged)
      window.clearTimeout(timeoutId)
      resolve()
    }
    const onVoicesChanged = () => {
      if (synthesis.getVoices().length > 0) finish()
    }
    timeoutId = window.setTimeout(finish, 1800)
    synthesis.addEventListener('voiceschanged', onVoicesChanged)
  })

  return selectFrenchVoice(synthesis)
}

function voiceInfo(voice: SpeechSynthesisVoice): FrenchVoiceInfo {
  return {
    voiceURI: voice.voiceURI,
    name: voice.name,
    lang: voice.lang,
    local: voice.localService,
  }
}

export function setFrenchVoicePreference(preference: string) {
  window.localStorage.setItem(VOICE_PREFERENCE_KEY, preference || AUTOMATIC_PREFERENCE)
}

export async function getFrenchVoiceStatus(): Promise<FrenchVoiceStatus> {
  if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
    return {
      supported: false,
      voices: [],
      preference: AUTOMATIC_PREFERENCE,
    }
  }

  const synthesis = window.speechSynthesis
  const selected = await waitForFrenchVoice(synthesis)
  const voices = frenchVoices(synthesis)
  const preference = storedPreference()
  const validPreference = preference === AUTOMATIC_PREFERENCE
    || voices.some((voice) => voice.voiceURI === preference)

  return {
    supported: true,
    voices: voices.map(voiceInfo),
    selectedVoice: selected ? voiceInfo(selected) : undefined,
    preference: validPreference ? preference : AUTOMATIC_PREFERENCE,
  }
}

async function playSpeech(text: string) {
  if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
    throw new Error('当前浏览器不支持语音朗读。')
  }

  const synthesis = window.speechSynthesis
  const voice = await waitForFrenchVoice(synthesis)
  if (!voice) {
    throw new Error('没有找到法语语音。请在设置中查看安装指引。')
  }

  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = voice.lang
  utterance.voice = voice
  utterance.pitch = 1
  utterance.volume = 1

  await new Promise<void>((resolve, reject) => {
    let settled = false
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      if (activePlayback?.stop === stop) activePlayback = null
      error ? reject(error) : resolve()
    }
    const stop = () => {
      synthesis.cancel()
      finish()
    }

    utterance.onend = () => finish()
    utterance.onerror = (event) => {
      if (event.error === 'canceled' || event.error === 'interrupted') finish()
      else finish(new Error(`法语语音播放失败（${event.error}）。`))
    }

    synthesis.cancel()
    synthesis.resume()
    activePlayback = { stop }
    synthesis.speak(utterance)
  })
}

async function playAudioUrl(audioUrl: string) {
  const audio = new Audio(audioUrl)
  audio.preload = 'auto'

  await new Promise<void>((resolve, reject) => {
    let settled = false
    const cleanup = () => {
      audio.removeEventListener('ended', onEnded)
      audio.removeEventListener('error', onError)
    }
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      cleanup()
      if (activePlayback?.stop === stop) activePlayback = null
      error ? reject(error) : resolve()
    }
    const onEnded = () => finish()
    const onError = () => finish(new Error('多邻国原始音频加载失败。'))
    const stop = () => {
      audio.pause()
      audio.currentTime = 0
      finish()
    }

    audio.addEventListener('ended', onEnded)
    audio.addEventListener('error', onError)
    activePlayback = { stop }
    void audio.play().catch(() => finish(new Error('多邻国原始音频无法播放。')))
  })
}

export async function playFrench(text: string, audioUrl?: string) {
  stopActivePlayback()

  if (audioUrl) {
    try {
      await playAudioUrl(audioUrl)
      return
    } catch {
      // Missing Duolingo clips fall back to the selected sentence voice.
    }
  }

  await playSpeech(text)
}
