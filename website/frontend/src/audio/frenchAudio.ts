export type FrenchTtsProvider = 'local' | 'browser'

type PlaybackHandle = { stop: () => void }

const configuredProvider = import.meta.env.VITE_FRENCH_TTS_PROVIDER?.toLowerCase()

export const frenchTtsProvider: FrenchTtsProvider = configuredProvider === 'browser'
  ? 'browser'
  : 'local'

let activePlayback: PlaybackHandle | null = null

function stopActivePlayback() {
  activePlayback?.stop()
  activePlayback = null
}

function frenchVoices(synthesis: SpeechSynthesis) {
  return synthesis.getVoices().filter((voice) => (
    voice.lang.replace('_', '-').toLowerCase().startsWith('fr-')
  ))
}

function selectFrenchVoice(synthesis: SpeechSynthesis, provider: FrenchTtsProvider) {
  const voices = frenchVoices(synthesis)
  const isFranceFrench = (voice: SpeechSynthesisVoice) => (
    voice.lang.replace('_', '-').toLowerCase() === 'fr-fr'
  )

  if (provider === 'local') {
    return voices.find((voice) => isFranceFrench(voice) && voice.localService)
      ?? voices.find((voice) => voice.localService)
  }

  return voices.find((voice) => isFranceFrench(voice) && voice.localService)
    ?? voices.find(isFranceFrench)
    ?? voices.find((voice) => voice.localService)
    ?? voices[0]
}

async function waitForFrenchVoice(
  synthesis: SpeechSynthesis,
  provider: FrenchTtsProvider,
): Promise<SpeechSynthesisVoice | undefined> {
  const readyVoice = selectFrenchVoice(synthesis, provider)
  if (readyVoice) return readyVoice

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
      if (selectFrenchVoice(synthesis, provider)) finish()
    }
    timeoutId = window.setTimeout(finish, 1800)
    synthesis.addEventListener('voiceschanged', onVoicesChanged)
  })

  return selectFrenchVoice(synthesis, provider)
}

async function playSpeech(text: string, provider: FrenchTtsProvider) {
  if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
    throw new Error('当前浏览器不支持语音朗读。')
  }

  const synthesis = window.speechSynthesis
  const voice = await waitForFrenchVoice(synthesis, provider)
  if (!voice) {
    if (provider === 'local') {
      throw new Error('没有找到本地法语语音，请确认系统法语语音已安装并重启浏览器。')
    }
    throw new Error('没有找到可用的浏览器法语语音。')
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
      // Missing Duolingo clips fall back to the configured sentence voice.
    }
  }

  await playSpeech(text, frenchTtsProvider)
}
