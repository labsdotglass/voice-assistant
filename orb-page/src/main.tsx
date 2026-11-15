import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AiAvatar, type AiAvatarState } from './ui/components/ai-avatar'
import './ui/styles/index.css'
import './orb.css'

type Voice = 'off' | 'starting' | 'listening' | 'hearing' | 'thinking' | 'speaking'

declare global {
  interface Window {
    // isOpening: a session voice started or went to is opening (a spinner in place of the dots)
    setVoiceState?: (voice: Voice, isSessionBusy: boolean, isOpening?: boolean) => void
    setPlaybackLevel?: (level: number) => void
    setMicLevel?: (level: number) => void
    setHover?: (isHovered: boolean) => void // the pointer is over the orb (the helper tracks it)
    flashDropped?: () => void // voice dropped what was likely you: the ring flashes once
    setMicDead?: (isDead: boolean) => void // the mic gives no sound while voice is on: a mic-off icon stays until it does
    webkit?: { messageHandlers?: Record<string, { postMessage: (m: unknown) => void }> }
  }
}

// AiAvatar has four states; "hearing you" is listening that leans in harder.
const AVATAR: Record<Voice, AiAvatarState> = {
  off: 'idle',
  starting: 'idle',
  listening: 'listening',
  hearing: 'listening',
  thinking: 'thinking',
  speaking: 'speaking',
}

// What drives the waves (AiAvatar's `level`):
// - hearing: your live mic level, so waves follow your syllables and stay small when the
//   mic barely picks you up;
// - listening: a faint wave from the mic level, so any sound it picks up shows the mic works;
// - speaking: the assistant's own playback, so waves push out on its syllables and
//   hold still in its pauses;
// - thinking and idle: AiAvatar's own motion (no waves).
const LISTENING_SHARE = 0.25 // how much of the mic level shows before speech is detected

function levelFor(voice: Voice, playback: number, mic: number): number | undefined {
  if (voice === 'hearing') return mic
  if (voice === 'listening') return mic * LISTENING_SHARE
  if (voice === 'speaking') return playback
  return undefined
}

// The opening spinner's petals, lit in turn around the orb's middle like a flower.
const PETALS = Array.from({ length: 10 }, (_, i) => i)

function Orb() {
  const [voice, setVoice] = useState<Voice>('starting')
  const [isBusy, setBusy] = useState(false)
  const [isOpening, setOpening] = useState(false)
  const [playback, setPlayback] = useState(0)
  const [mic, setMic] = useState(0)
  const [isHovered, setHovered] = useState(false)
  const [drops, setDrops] = useState(0) // each one flashes the ring once (a new key restarts it)
  const [isMicDead, setMicDead] = useState(false)

  useEffect(() => {
    const say = (m: unknown) => window.webkit?.messageHandlers?.orb?.postMessage(m)
    window.setVoiceState = (v, busy, opening = false) => {
      setVoice(v)
      setBusy(busy)
      setOpening(opening)
      say({ state: v, busy, opening })
    }
    // The assistant's playback loudness, 0..1, from the audio helper ~30x/s.
    window.setPlaybackLevel = setPlayback
    // The mic's loudness, 0..1, from the audio helper ~30x/s.
    window.setMicLevel = setMic
    window.setHover = setHovered
    window.flashDropped = () => setDrops(n => n + 1)
    window.setMicDead = setMicDead
    say({ ready: true, webgpu: 'gpu' in navigator })
    // AiAvatar picks its WebGPU renderer when it can; report which one actually drew.
    setTimeout(() => say({ renderer: document.querySelector('canvas') ? 'webgpu canvas' : 'css fallback' }), 1500)
  }, [])

  return (
    <div className="stage" data-voice={voice} data-busy={isBusy || undefined}
      data-opening={isOpening || undefined}
      data-hover={isHovered || undefined}
      data-mic-dead={isMicDead || undefined}>
      <div className="orb">
        {/* A ring around the orb, voice's way to say something about itself: here, a brief flash when
            it dropped what you said ("I didn't catch that"), with a short sound from the helper. */}
        {drops > 0 && <div key={drops} className="ring" data-ring="dropped" aria-hidden />}
        <AiAvatar className="avatar" state={AVATAR[voice]} level={levelFor(voice, playback, mic)} label={`Voice: ${voice}`} />
        {/* A drag handle in the middle, shown while the pointer is over the orb (in the loader's place). */}
        <div className="handle" aria-hidden>
          <span />
          <span />
        </div>
        <div className="working" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        {/* The mic gives no sound: a crossed-out mic in the middle, in the dots' place, until it works again. */}
        <svg className="mic-off" viewBox="0 0 24 24" role="img" aria-label="The microphone gives no sound"
          aria-hidden={!isMicDead}>
          <rect x="9" y="2.5" width="6" height="11" rx="3" />
          <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" />
          <path d="M4 3.5l16 17" />
        </svg>
        <div className="opening" aria-hidden>
          {PETALS.map(i => (
            <span key={i} style={{ transform: `rotate(${i * (360 / PETALS.length)}deg)`, animationDelay: `${((i - PETALS.length) * 1.2) / PETALS.length}s` }} />
          ))}
        </div>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<Orb />)
