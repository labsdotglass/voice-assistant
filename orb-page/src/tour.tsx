// The first run, in the onboarding window (bin/onboarding): Welcome (pick a language), Setting up (its progress
// under the orb), the tour, Ready, Your voice (phrase by phrase) and Done. Nothing here listens or
// speaks; the daemon does, and tells the page through window.firstRun(state) (first-run.json). The page answers
// on the "tour" message handler: {action: "language", language}, {action: "hide"}, {action: "request", value}
// (enroll, later, skip, done, retry: for the daemon), {action: "done"} (fade out) and {action: "later"} (before a
// language: dismissed). --replay (/voice-tour) shows the tour only; --enroll opens at Your voice.
// Every word is in first-run-text.json, in every language (test_translations.py).
// #preview=<screen>&p=<0..1>[&lang=he][&theme=dark][&replay=1] draws any screen with made-up state, for screenshots.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AiAvatar, type AiAvatarState } from './ui/components/ai-avatar'
import { Button } from './ui/components/button'
import { Progress } from './ui/components/progress'
import { Radio, RadioGroup, RadioProvider } from './ui/components/radio-group'
import { Spinner } from './ui/components/spinner'
import './ui/styles/index.css'
import './tour.css'
import TEXT from './first-run-text.json'
import LANGUAGE_FILE from '../../languages.json'

type Language = { code: string; label: string; native: string; download_gb: number; experimental: boolean; current?: boolean }
type Phrase = { text: string; done: boolean }
type State =
  | { phase: 'installing' }
  | { phase: 'setup'; done: number; total: number; eta: number | null; step: 'models' | 'starting' }
  | { phase: 'ready' }
  | { phase: 'failed'; reason: string; kind?: 'install' } // install: voice's packages (the mod writes it; /talk retries)
  | { phase: 'voice'; phrases: Phrase[]; current: number; level: number; hint: 'hearing' | 'louder' | 'keep_going'; again?: boolean }
  | { phase: 'voice-done'; learned: boolean; again?: boolean }
  | { phase: 'done' }
type Screen = 'welcome' | 'setup' | 'tour' | 'ready' | 'voice' | 'done'

declare global {
  interface Window {
    FIRST_RUN?: { replay: boolean; enroll: boolean; languages: Language[] }
    firstRun?: (state: State) => void // the daemon's state, each time first-run.json changes
    TOUR_REPLAY?: boolean // an older window's flag for --replay
    TOUR_START?: number // the slide the tour opens at (onboarding --slide), for checking each one
    firstRunClose?: () => string // the window's close button: what Escape does
    tourGo?: (step: string) => string // voice's commands (the window passes them on): next, back, first, last, a number, done, close
    webkit?: { messageHandlers?: Record<string, { postMessage: (m: unknown) => void }> }
  }
}

const post = (message: object) => window.webkit?.messageHandlers?.tour?.postMessage(message)
const request = (value: 'enroll' | 'later' | 'skip' | 'done' | 'retry') => post({ action: 'request', value })
const LATER_THEN_DONE = 600 // ms: the daemon reads the request file every 0.2 s (first_run.serve_requests)

// ── The words ────────────────────────────────────────────────────────────────
type Key = keyof typeof TEXT
const text = (lang: string, key: Key, vars: Record<string, string | number> = {}) => {
  const by = TEXT[key] as Record<string, string>
  return ltrSizes((by[lang] ?? by.en).replace(/\{(\w+)\}/g, (_, v) => String(vars[v] ?? '')))
}
// A size with its unit kept left to right (an LTR isolate), so Hebrew shows "2.8 GB", not "GB 2.8".
// (The failure's reason, from Python or uv in English, is a first-strong isolate for the same reason.)
const ltrSizes = (s: string) => s.replace(/\d[\d.,\u00a0\u202f]*\s(?:GB|Go)/g, m => `\u2066${m}\u2069`)
const gb = (lang: string, n: number) => new Intl.NumberFormat(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(n)

// The tour, seven slides; each has its title and words in first-run-text.json (tour.N.title, tour.N.body).
const SLIDE_ORB: AiAvatarState[] = ['listening', 'speaking', 'thinking', 'speaking', 'listening', 'idle', 'listening']
const SLIDES = SLIDE_ORB.length
const slideKey = (n: number, part: 'title' | 'body') => `tour.${n + 1}.${part}` as Key
// The "Right-click the orb" slide shows its menu as the audio helper draws it (audio_helper/main.swift, willOpenMenu):
// the same items, order and dividers, in English as the helper has them; › opens a submenu. One shown picked.
const MENU_SLIDE = 5
const MENU = ['Hide transcript', 'Clear conversation', '-', 'Language ›', 'Voice ›', 'Pass on my words ›', 'More ›', '-', 'Stop voice']
const PICKED = 'Language ›'

// ── What the window was opened for ───────────────────────────────────────────
const preview = new URLSearchParams(location.hash.slice(1))
const PREVIEW = preview.get('preview') as Screen | 'failed' | 'install-failed' | 'wait' | null
const REPLAY = preview.get('replay') === '1' || (!PREVIEW && Boolean(window.FIRST_RUN?.replay ?? window.TOUR_REPLAY))
const ENROLL = !PREVIEW && Boolean(window.FIRST_RUN?.enroll)
// The languages in the menu's order (onboarding.languages(): experimental ones last); languages.json without the window.
const LANGUAGES: Language[] = window.FIRST_RUN?.languages?.length
  ? window.FIRST_RUN.languages
  : [...LANGUAGE_FILE.languages].sort((a, b) => Number(a.experimental) - Number(b.experimental))
const has = (code?: string | null) => (code && LANGUAGES.some(l => l.code === code) ? code : undefined)
// A first run preselects the Mac's language; shown again, the window speaks the one voice is set to.
const MAC = has(navigator.language.slice(0, 2).toLowerCase())
const START_LANGUAGE = has(preview.get('lang')) ?? (REPLAY || ENROLL ? has(LANGUAGES.find(l => l.current)?.code) : undefined) ?? MAC ?? 'en'

// Light and dark follow the Mac (the styles' .dark); a preview can ask for either.
const dark = matchMedia('(prefers-color-scheme: dark)')
const theme = () => document.documentElement.classList.toggle('dark', preview.get('theme') ? preview.get('theme') === 'dark' : dark.matches)
theme()
dark.addEventListener('change', theme)
if (PREVIEW) document.documentElement.classList.add('still') // a screenshot shows where each motion ends

// Made-up state for each preview screen: p (0..1) is how far along it is.
function previewSetup(): { screen: Screen; state: State | null; slide: number; afterTour?: boolean } {
  const p = Math.min(Math.max(Number(preview.get('p') ?? 0.5), 0), 1)
  const total = 3.5e9
  const setup: State = { phase: 'setup', done: p * total, total, eta: Math.round((1 - p) * 520), step: p >= 1 ? 'starting' : 'models' }
  const all = TEXT_PHRASES[START_LANGUAGE] ?? TEXT_PHRASES.en
  const phrases = all.map((t, i) => ({ text: t, done: i < Math.round(p * all.length) }))
  switch (PREVIEW) {
    case 'tour': return { screen: 'tour', state: REPLAY ? null : { ...setup, done: 0.54 * total, eta: 240 }, slide: Math.round(p * (SLIDES - 1)) }
    case 'wait': return { screen: 'setup', state: setup, slide: SLIDES - 1, afterTour: true }
    case 'failed': return { screen: 'setup', state: { phase: 'failed', reason: 'urlopen error [Errno 8] nodename nor servname provided' }, slide: 0 }
    case 'install-failed': return { screen: 'setup', state: { phase: 'failed', reason: 'Failed to download `numpy`', kind: 'install' }, slide: 0 }
    case 'ready': return { screen: 'ready', state: { phase: 'ready' }, slide: 0 }
    case 'voice': return { screen: 'voice', state: { phase: 'voice', phrases, current: Math.min(Math.round(p * all.length), all.length - 1), level: 0.5, hint: 'hearing' }, slide: 0 }
    case 'done': return { screen: 'done', state: { phase: 'voice-done', learned: p > 0 }, slide: 0 }
    case 'setup': return { screen: 'setup', state: setup, slide: 0 }
    default: return { screen: 'welcome', state: null, slide: 0 }
  }
}
// The phrases the preview shows (speaker.py's PHRASES has the real ones; the daemon sends them).
const TEXT_PHRASES: Record<string, string[]> = {
  en: ["Hi, it's me. Yes. No. Do it. Stop.", "Let's get started. Open the project and run the tests.", 'If something fails, tell me what broke.',
    'Then fix it and show me the change.', "Looks good to me. Let's commit and push to the main branch.", "What's taking so long? Give me a quick update.", "Find the bug on the login page and explain it simply.", "Thanks, I'll check back in a few minutes."],
  he: ['היי, זה אני. כן. לא. יאללה. רגע, די.', 'מתחילים. אפשר לפתוח את הפרויקט ולהריץ את הבדיקות?', 'אם משהו נכשל, אני רוצה לדעת מה נשבר.',
    'אחר כך צריך לתקן את זה ולהראות לי את השינוי.', "נראה לי טוב. עושים קומיט ודוחפים לבראנץ' הראשי.", "מה לוקח כל כך הרבה זמן? אפשר עדכון קצר?", "צריך למצוא את הבאג בדף ההתחברות ולהסביר אותו בפשטות.", 'תודה, אני אבדוק שוב בעוד כמה דקות.'],
}

function initial(): { screen: Screen; state: State | null; slide: number; afterTour?: boolean } {
  if (PREVIEW) return previewSetup()
  if (REPLAY) return { screen: 'tour', state: null, slide: Math.min(Math.max(window.TOUR_START ?? 0, 0), SLIDES - 1) }
  if (ENROLL) return { screen: 'voice', state: null, slide: 0 }
  return { screen: 'welcome', state: null, slide: 0 }
}

// ── The page ─────────────────────────────────────────────────────────────────
function FirstRun() {
  const start = useRef(initial()).current
  const [screen, setScreen] = useState<Screen>(start.screen)
  const [slide, setSlide] = useState(start.slide)
  const [direction, setDirection] = useState(1) // which way the right side slides in
  const [state, setState] = useState<State | null>(start.state)
  const [language, setLanguage] = useState(START_LANGUAGE)
  const [chosen, setChosen] = useState(Boolean(PREVIEW) || REPLAY || ENROLL) // a language was sent: closing now hides
  const [afterTour, setAfterTour] = useState(Boolean(start.afterTour)) // finished the tour before setup: wait on setup
  const lastFraction = useRef(0) // the bar where it was, for Couldn't download
  const t = (key: Key, vars?: Record<string, string | number>) => text(language, key, vars)
  const rtl = language === 'he'

  const settingUp = !REPLAY && !ENROLL && (!state || ['installing', 'setup', 'failed'].includes(state.phase))
  const failed = state?.phase === 'failed'
  const installFailed = state?.phase === 'failed' && state.kind === 'install'
  const learned = state?.phase === 'voice-done' && state.learned
  const again = ENROLL || ((state?.phase === 'voice' || state?.phase === 'voice-done') && Boolean(state.again))

  const go = (to: Screen, dir = 1) => {
    setDirection(dir)
    setScreen(to)
  }

  // The daemon's state. Shown again, the tour has none to show; learning again ignores what an earlier run
  // left in the file until its own first "voice" state; a first run listens only once a language is chosen.
  const chosenRef = useRef(chosen)
  chosenRef.current = chosen
  useEffect(() => {
    if (PREVIEW) return
    let fresh = false
    window.firstRun = s => {
      // done: the first run is over (Start talking, or the window gone); the window is fading out, nothing to show.
      if (REPLAY || !s || typeof s.phase !== 'string' || s.phase === 'done') return
      if (ENROLL) {
        fresh ||= s.phase === 'voice'
        if (!fresh) return
      } else if (!chosenRef.current) return
      setState(s)
    }
  }, [])

  // Where the state takes the window: Ready once setup is over, wherever you were in the tour (the window comes
  // back if hidden); back to the setup view if it fails; Done when your voice is learned or set aside.
  useEffect(() => {
    if (PREVIEW || !state) return
    if (state.phase === 'setup' && state.total) lastFraction.current = Math.min(state.done / state.total, 1)
    if (state.phase === 'ready' && (screen === 'setup' || screen === 'tour')) go('ready')
    if (state.phase === 'failed' && screen === 'tour') go('setup', -1)
    if (state.phase === 'voice' && screen !== 'voice' && screen !== 'done' && !REPLAY) go('voice')
    if (state.phase === 'voice-done' && screen === 'voice') go('done')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state])

  // Your voice starts it: the daemon begins listening for the phrases (again, from /voice-enroll: no change there).
  useEffect(() => {
    if (screen === 'voice' && !PREVIEW) request('enroll')
  }, [screen])

  const chooseLanguage = () => {
    post({ action: 'language', language })
    chosenRef.current = true // the "installing" state may come before the next render
    setChosen(true)
    go('setup')
  }
  // Escape and the close button (the window asks, through firstRunClose): before a language, dismissed; while
  // voice sets up (setup, the tour), the window hides and comes back on Ready. Once setup is over, closing ends
  // the first run: what's unfinished is put off (Later, offered again next start) and voice greets you, since a
  // hidden window would never come back. Shown again or learning again, the window is simply done with.
  const close = () => {
    if (REPLAY) return post({ action: 'done' }), 'closed'
    if (ENROLL) {
      if (screen === 'voice' && !learned) request('later')
      return post({ action: 'done' }), 'closed'
    }
    if (!chosenRef.current) return post({ action: 'later' }), 'closed'
    // Setup stopped stays up: nothing would bring a hidden window back, and Retry (or /talk) is the way on.
    if (failed) return 'setup stopped: the window stays open until it is retried'
    if (settingUp) return post({ action: 'hide' }), 'closed' // voice says the tour is closed either way
    const finish = () => (request('done'), post({ action: 'done' }))
    if (screen === 'ready' || (screen === 'voice' && !learned)) {
      request('later') // as the Later button
      setTimeout(finish, LATER_THEN_DONE) // one request file: done must not overwrite later before the daemon reads it
    } else finish()
    return 'closed'
  }
  const toSlide = (k: number) => {
    const to = Math.min(Math.max(k, 0), SLIDES - 1)
    setDirection(to >= slideRef.current ? 1 : -1)
    setSlide(to)
    setScreen('tour')
    return to
  }
  // Past the tour's last slide: Ready if setup is over, else wait on the setup view.
  const pastTour = () => {
    if (REPLAY) return post({ action: 'done' })
    if (settingUp) {
      setAfterTour(true)
      return go('setup')
    }
    go(state?.phase === 'voice' ? 'voice' : 'ready')
  }
  const back = () => (slide > 0 ? toSlide(slide - 1) : !REPLAY && go('setup', -1))

  const slideRef = useRef(slide)
  slideRef.current = slide
  const screenRef = useRef(screen)
  screenRef.current = screen
  const handlers = useRef({ close, back, pastTour, chooseLanguage })
  handlers.current = { close, back, pastTour, chooseLanguage }

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const now = screenRef.current
      if (e.key === 'Escape') handlers.current.close()
      else if (now === 'tour' && e.key === 'ArrowRight') slideRef.current < SLIDES - 1 ? toSlide(slideRef.current + 1) : handlers.current.pastTour()
      else if (now === 'tour' && e.key === 'ArrowLeft') handlers.current.back()
      else if (now === 'welcome' && e.key === 'Enter') handlers.current.chooseLanguage()
    }
    window.addEventListener('keydown', key)
    window.firstRunClose = () => handlers.current.close()
    // What you can click, said instead (voice, while the tour is shown again): -> what happened, as plain text for
    // voice to read out (the slide's number, title and words).
    window.tourGo = step => {
      if (step === 'done' || step === 'close') return handlers.current.close()
      if (!REPLAY && screenRef.current !== 'tour') return "the window isn't showing the tour"
      const to = (i: number) => {
        const k = toSlide(i)
        const lang = languageRef.current
        return `slide ${k + 1} of ${SLIDES}: ${text(lang, slideKey(k, 'title'))}. ${text(lang, slideKey(k, 'body'))}`
      }
      if (step === 'next') return to(slideRef.current + 1)
      if (step === 'back') return to(slideRef.current - 1)
      if (step === 'first') return to(0)
      if (step === 'last') return to(SLIDES - 1)
      if (/^\d+$/.test(step)) return to(Number(step) - 1)
      return 'unknown'
    }
    return () => window.removeEventListener('keydown', key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const languageRef = useRef(language)
  languageRef.current = language

  // ── The left column: the orb, and setup's bar or what it hears under it ──
  const orbState: AiAvatarState =
    screen === 'welcome' ? 'idle' : screen === 'tour' ? SLIDE_ORB[slide] : screen === 'setup' ? 'thinking' : screen === 'done' ? 'speaking' : 'listening'
  const level = screen === 'voice' && state?.phase === 'voice' ? state.level : 0
  const showBar = settingUp && screen !== 'welcome'

  let fraction = lastFraction.current
  let line = t('progress.installing')
  let left = ''
  if (state?.phase === 'setup') {
    fraction = state.total ? Math.min(state.done / state.total, 1) : 0
    if (state.step === 'starting') line = t('progress.starting')
    else {
      line = t('progress.bytes', { done: gb(language, state.done / 1e9), total: gb(language, state.total / 1e9) })
      const minutes = state.eta == null ? null : Math.max(1, Math.ceil(state.eta / 60))
      left = minutes == null ? t('progress.estimating') : minutes === 1 ? t('progress.minute') : t('progress.minutes', { n: minutes })
    }
  } else if (failed) line = t('progress.failed')
  else fraction = 0

  return (
    <div className={`window ${screen === 'welcome' ? 'centred' : 'columns'}`}>
      <div className="left" aria-hidden={screen === 'welcome'} />
      <div className="orb">
        <AiAvatar className="colors" state={orbState} level={screen === 'voice' ? level : undefined} label={`Voice: ${orbState}`} />
      </div>
      <OrbMenu shown={screen === 'tour' && slide === MENU_SLIDE} />
      <div className={`under ${showBar || screen === 'voice' ? 'shown' : ''}`} dir={rtl ? 'rtl' : undefined}>
        {showBar && (
          <div className={`progress ${failed ? 'failed' : ''}`}>
            <div className="line">{line}</div>
            <Progress className="bar" value={fraction * 100} label={line} />
            <div className="left-time">{left || ' '}</div>
          </div>
        )}
      </div>

      {!REPLAY && !ENROLL && (
        <Welcome
          shown={screen === 'welcome'} language={language} setLanguage={setLanguage} t={t} rtl={rtl} onContinue={chooseLanguage}
        />
      )}

      {screen !== 'welcome' && (
        <div className="right" dir={rtl ? 'rtl' : undefined}>
          <div className={`page ${direction < 0 ? 'from-left' : ''}`} key={`${screen}-${slide}-${afterTour}-${failed}`}>
            {screen === 'setup' && (
              <>
                <div className="eyebrow">{t('step.setup')}</div>
                <h1>{failed ? t('failed.title') : afterTour ? t('setup.wait_title') : t('setup.title')}</h1>
                <div className="body">
                  {failed ? (
                    <>
                      <p className="reason">{t(installFailed ? 'failed.install_reason' : 'failed.reason', { reason: state.phase === 'failed' ? `\u2068${state.reason}\u2069` : '' })}</p>
                      <p>{t(installFailed ? 'failed.install_body' : 'failed.body')}</p>
                    </>
                  ) : (
                    <>
                      <p>{afterTour ? t('setup.after_tour') : t('setup.body')}</p>
                      <SetupSteps state={state} t={t} />
                    </>
                  )}
                </div>
                {failed ? (
                  <div className="foot end">
                    {!installFailed && <Button onClick={() => request('retry')}>{t('failed.retry')}</Button>}
                  </div>
                ) : (
                  <div className="foot">
                    <Button variant="ghost" onClick={close}>{t('setup.hide')}</Button>
                    <Button onClick={() => toSlide(0)}>{afterTour ? t('setup.tour_again') : t('setup.tour')}</Button>
                  </div>
                )}
              </>
            )}
            {screen === 'tour' && (
              <>
                <div className="eyebrow">{t('tour.eyebrow', { n: slide + 1, total: SLIDES })}</div>
                <h1>{t(slideKey(slide, 'title'))}</h1>
                <div className="body"><p>{t(slideKey(slide, 'body'))}</p></div>
                <div className="foot end">
                  {(slide > 0 || !REPLAY) && <Button variant="ghost" onClick={back}>{t('tour.back')}</Button>}
                  {slide < SLIDES - 1
                    ? <Button onClick={() => toSlide(slide + 1)}>{t('tour.next')}</Button>
                    : <Button onClick={pastTour}>{REPLAY ? t('tour.done') : t('tour.next')}</Button>}
                </div>
              </>
            )}
            {screen === 'ready' && (
              <>
                <div className="eyebrow">{t('ready.eyebrow')}</div>
                <h1>{t('ready.title')}</h1>
                <div className="body"><p>{t('ready.body')}</p></div>
                <div className="foot end">
                  <Button variant="ghost" onClick={() => (request('later'), go('done'))}>{t('ready.later')}</Button>
                  <Button onClick={() => go('voice')}>{t('ready.learn')}</Button>
                </div>
              </>
            )}
            {screen === 'voice' && (
              <>
                <div className="eyebrow">{t('voice.eyebrow')}</div>
                <h1>{t('voice.title')}</h1>
                <Phrases state={state} t={t} />
                <div className="foot">
                  {again
                    ? <Button variant="ghost" onClick={close}>{t('voice.cancel')}</Button>
                    : (
                      <>
                        <Button variant="ghost" onClick={() => (request('skip'), go('done'))}>{t('voice.skip')}</Button>
                        <Button variant="outline" onClick={() => (request('later'), go('done'))}>{t('ready.later')}</Button>
                      </>
                    )}
                </div>
              </>
            )}
            {screen === 'done' && (
              <>
                <div className="eyebrow">{learned ? t('voice.eyebrow') : t('ready.eyebrow')}</div>
                <h1>{learned ? t('done.title') : t('done.later_title')}</h1>
                <div className="body"><p>{learned ? (again ? t('done.again_body') : t('done.body')) : t('done.later_body')}</p></div>
                <div className="foot end">
                  {again
                    ? <Button onClick={() => post({ action: 'done' })}>{t('done.close')}</Button>
                    : <Button onClick={() => (request('done'), post({ action: 'done' }))}>{t('done.start')}</Button>}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

type T = (key: Key, vars?: Record<string, string | number>) => string

// Welcome: the language list (the Mac's preselected, experimental ones under a divider), its download size, Continue.
function Welcome({ shown, language, setLanguage, t, rtl, onContinue }: {
  shown: boolean; language: string; setLanguage: (code: string) => void; t: T; rtl: boolean; onContinue: () => void
}) {
  const list = useRef<HTMLDivElement>(null)
  // The preselected language in view (it may be far down the list).
  useLayoutEffect(() => {
    const row = list.current?.querySelector<HTMLElement>('[aria-checked="true"], input:checked')?.closest('label')
    if (row && list.current) list.current.scrollTop = row.offsetTop - list.current.clientHeight / 2 + row.offsetHeight / 2
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const chosen = LANGUAGES.find(l => l.code === language) ?? LANGUAGES[0]
  const row = (l: Language) => (
    <Radio key={l.code} value={l.code} className="language">
      <span className="name">{l.experimental ? t('welcome.experimental', { language: l.label }) : l.label}</span>
      {l.native !== l.label && <span className="native" dir="auto">{l.native}</span>}
    </Radio>
  )
  return (
    <div className={`welcome ${shown ? '' : 'gone'}`} aria-hidden={!shown}>
      <div className="welcome-text" dir={rtl ? 'rtl' : undefined}>
        <h1>{t('welcome.title')}</h1>
        <p className="prompt">{t('welcome.prompt')}</p>
      </div>
      <RadioProvider value={language} setValue={v => setLanguage(String(v))}>
        <RadioGroup className="languages" ref={list} aria-label={t('welcome.prompt')}>
          {LANGUAGES.filter(l => !l.experimental).map(row)}
          {LANGUAGES.some(l => l.experimental) && <hr />}
          {LANGUAGES.filter(l => l.experimental).map(row)}
        </RadioGroup>
      </RadioProvider>
      <p className="size" dir={rtl ? 'rtl' : undefined}>{t('welcome.size', { language: chosen.native, gb: gb(language, chosen.download_gb) })}</p>
      <Button className="continue" onClick={onContinue} disabled={!shown}>{t('welcome.continue')}</Button>
    </div>
  )
}

// Setup's three steps: installing, the speech models, starting voice.
function SetupSteps({ state, t }: { state: State | null; t: T }) {
  const at = state?.phase === 'setup' ? (state.step === 'starting' ? 2 : 1) : 0
  return (
    <ol className="steps">
      {(['setup.installing', 'setup.models', 'setup.starting'] as Key[]).map((key, i) => (
        <li key={key} className={i < at ? 'done' : i === at ? 'now' : 'todo'}>
          <span className="mark">{i < at ? <Check /> : i === at ? <Spinner size={16} /> : null}</span>
          {t(key)}
        </li>
      ))}
    </ol>
  )
}

// Your voice: the phrases, each checked once voice recognizes it; the one to read now stands out.
function Phrases({ state, t }: { state: State | null; t: T }) {
  const list = useRef<HTMLOListElement>(null)
  const phrases = state?.phase === 'voice' ? state.phrases : []
  const current = state?.phase === 'voice' ? state.current : -1
  useEffect(() => {
    list.current?.querySelector<HTMLElement>('.now')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [current])
  if (!phrases.length) return <div className="body"><p className="waiting">{t('voice.waiting')}</p></div>
  return (
    <ol className="phrases" ref={list}>
      {phrases.map((p, i) => (
        <li key={i} className={p.done ? 'done' : i === current ? 'now' : 'todo'} dir="auto">
          <span className="mark" key={String(p.done)}>{p.done && <Check />}</span>
          <span>{p.text}</span>
        </li>
      ))}
    </ol>
  )
}

// The orb's menu as if just opened by a right-click on it: over its lower right, the orb itself unmoved.
function OrbMenu({ shown }: { shown: boolean }) {
  return (
    <div className={`orb-menu ${shown ? 'shown' : ''}`} dir="ltr" aria-hidden>
      {MENU.map((item, i) => item === '-'
        ? <hr key={i} />
        : (
          <div key={i} className={item === PICKED ? 'picked' : undefined}>
            {item.replace(' ›', '')}
            {item.endsWith('›') && <svg viewBox="0 0 16 16"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>}
          </div>
        ))}
    </div>
  )
}

const Check = () => (
  <svg viewBox="0 0 16 16" aria-hidden><path d="M4 8.5l2.6 2.6L12 5.6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
)

createRoot(document.getElementById('root')!).render(<FirstRun />)
