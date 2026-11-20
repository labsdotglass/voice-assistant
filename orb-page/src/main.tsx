import { MicOff, MicSignal } from 'lucide-react'
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
    setMicDead?: (isDead: boolean) => void // the mic gives no sound while voice is on: a mic-signal icon stays until it does
    setMuted?: (isMuted: boolean) => void // voice is muted (for a call): faded, with a mic-off icon, until you unmute
    setWaiting?: (isWaiting: boolean) => void // the session waits on you (a permission or a question): a steady ring until it's answered
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
  const [isMicDead, setMicDead] = useState(false)
  const [isMuted, setMuted] = useState(false)
  const [isWaiting, setWaiting] = useState(false)

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
    window.setMicDead = setMicDead
    window.setMuted = setMuted
    window.setWaiting = setWaiting
    say({ ready: true, webgpu: 'gpu' in navigator })
    // AiAvatar picks its WebGPU renderer when it can; report which one actually drew.
    setTimeout(() => say({ renderer: document.querySelector('canvas') ? 'webgpu canvas' : 'css fallback' }), 1500)
  }, [])

  return (
    <div className="stage" data-voice={voice} data-busy={isBusy || undefined}
      data-opening={isOpening || undefined}
      data-hover={isHovered || undefined}
      data-mic-dead={isMicDead || undefined}
      data-muted={isMuted || undefined}
      data-waiting={isWaiting || undefined}>
      <div className="orb">
        <AiAvatar className="avatar" state={isMuted ? 'idle' : AVATAR[voice]} level={isMuted ? 0 : levelFor(voice, playback, mic)}
          label={isMuted ? 'Voice: muted' : `Voice: ${voice}`} />
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
        {/* The mic gives no sound: Lucide's mic-signal, in the dots' place, until it works again. Something is wrong,
            unlike muting, which you chose. */}
        <MicSignal className="mic-dead" role="img" aria-label="The microphone gives no sound" aria-hidden={!isMicDead} />
        {/* Muted: Lucide's mic-off, in the same place, until you unmute (the menu, or a click on the orb). */}
        <MicOff className="muted" role="img" aria-label="Voice is muted" aria-hidden={!isMuted} />
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
