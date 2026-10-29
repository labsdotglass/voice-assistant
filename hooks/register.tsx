import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { FinishedSession, QueuedPrompt, TranscriptLine, TranscriptWho, VoiceTarget } from '../types'


// The voice daemon (open mic, Haiku talker, local Whisper + Kokoro) lives in this plugin's own
// folder, $.plugin.root, run with uv.
// Its runtime files and logs live in ~/.voice-assistant, private to you (0700), not in the shared /tmp where
// another user on the Mac could read or claim them. As home.py's RUN and LOGS.
type RunPaths = {
  sock: string
  restart: string
  owner: string // the session that had voice last, written by the daemon
  finished: string
  waiting: string
  log: string
  modLog: string
}
let runPaths: Promise<RunPaths> | undefined
function paths($: Dollar): Promise<RunPaths> {
  runPaths ??= $.process
    .run(['/bin/sh', '-c', 'umask 077; d="$HOME/.voice-assistant"; mkdir -p "$d/run/finished" "$d/run/waiting" "$d/logs" && chmod 700 "$d/run" "$d/run/finished" "$d/run/waiting" "$d/logs" && echo "$d"'])
    .then(res => {
      const d = res.stdout.trim()
      if (res.exitCode !== 0 || !d) throw new Error(`couldn't make ~/.voice-assistant/run: ${res.stderr.trim()}`)
      return {
        sock: `${d}/run/voice.sock`,
        restart: `${d}/run/restart`,
        owner: `${d}/run/owner`,
        finished: `${d}/run/finished`,
        waiting: `${d}/run/waiting`,
        log: `${d}/logs/voice.log`,
        modLog: `${d}/logs/mod.log`,
      }
    })
    .catch(err => {
      runPaths = undefined // tried again next time
      throw err
    })
  return runPaths
}

const isOn = atom({ plugin: 'voice-mode', key: 'isOn' } as const, false)
const voice = atom({ plugin: 'voice-mode', key: 'voice' } as const, 'off')
const isSessionBusy = atom({ plugin: 'voice-mode', key: 'isSessionBusy' } as const, false)
const transcript = atom({ plugin: 'voice-mode', key: 'transcript' } as const, [] as TranscriptLine[])
const isPanelOpen = atom({ plugin: 'voice-mode', key: 'isPanelOpen' } as const, false)
const draft = atom({ plugin: 'voice-mode', key: 'draft' } as const, '') // what you're saying, before the final pass
// The session voice talks to: the one you focused last in the app.
const target = atom({ plugin: 'voice-mode', key: 'target' } as const, null as VoiceTarget | null) // null: voice is off
// The session's plan (Claude's task list), as the update_plan calls that built it: kept in the session's
// state, not the module, so a reload (every save of this file) doesn't lose it.
const plan = atom({ plugin: 'voice-mode', key: 'plan' } as const, [] as unknown[])
// The first run's window while it's hidden: setup's progress in a few words for the footer, '' while it shows.
// In the session's state too, so a reload keeps the footer's item and starts its poll again.
const hiddenSetup = atom({ plugin: 'voice-mode', key: 'hiddenSetup' } as const, '')

// The transcript side panel: what you said, what the voice said, what went to the session.
const PANE = 'voice-transcript'
// Your messages' bubbles in the panel: a see-through mid gray, so they sit right in a light or a dark
// theme, with the theme's own text color.
const BUBBLE = 'rgba(128, 128, 128, 0.12)'
const HISTORY_CHARS = 6000 // how much of a session's recent conversation voice gets when it arrives there
const ANSWER_CHARS = 20000 // and of its last reply in full, which voice reads only when you ask for all of it
const VOICE_LINES = 20 // and of voice's own conversation there: its last lines (the talker keeps the last quarter hour after a restart)

// A passage split into its sentences, each kept whole.
const sentences = (text: string) => text.match(/[^.?!]+[.?!]+/g)?.map(x => x.trim()) ?? [text]
const BUBBLE_INSET = 6 // cells kept clear on a bubble's left
const STATE_LABEL: Record<string, string> = {
  off: 'Voice off',
  starting: 'Starting…',
  listening: 'Listening',
  hearing: 'Hearing you',
  thinking: 'Thinking',
  speaking: 'Speaking',
}

type Command =
  | { type: 'state'; voice: string }
  | { type: 'transcript'; who: TranscriptWho; text: string }
  | { type: 'draft'; text: string }
  | { type: 'status'; text: string }
  | { type: 'stop' }
  | { type: 'clear' }
  | { type: 'panel'; show: boolean }
  | { type: 'submit'; text: string; id?: string }
  | { type: 'cancel'; ids: string[] | 'all'; replaced?: boolean } // replaced: an interrupt sends these words now
  // A follow-up to a prompt still waiting: the two go as one. `addition` alone if that prompt has already gone.
  | { type: 'merge'; id: string; text: string; addition: string; newId: string }
  | { type: 'interrupt'; then: string }
  | { type: 'answer'; id: string; answers: Record<string, string> } // your spoken answer to an open question form
  | { type: 'dismiss'; id: string } // you said to leave an open question form unanswered
  | { type: 'tour' } // More > Show the tour, from the orb's menu
  | { type: 'enroll' } // More > Learn my voice again (or a language voice hasn't heard you in): the window at Your voice

type Dollar = EngineInterface

let stop: (() => void) | undefined // set in the session that started the daemon, for its life
let isTalker = false // this is the talker's own Claude process, not a session
let polling = false // registered with the daemon and polling it: every session does while voice is on
let hasVoice = false // this session is the one voice talks to: the one you focused last in the app
let sessionId: string | undefined
let client: string | undefined // this load of the mod, as the daemon knows it
let mainTurn: string | undefined
// The origins of a prompt you sent yourself: typed at the terminal, from a phone, or in the desktop app.
const PERSON = new Set(['composer', 'bridge', 'sdk', 'slack-ping'])
// Your words alone: the app puts its own notes (<system-reminder>…) into the prompt it hands on.
const withoutTags = (text: string) => text.replace(/<([a-z][\w-]*)[^>]*>[\s\S]*?<\/\1>/g, '').trim()
// Voice's prompts waiting for the session to finish its turn. Kept here, not in the engine's
// queue, so voice can take one back before it starts; the next one goes in when a turn ends.
let queued: QueuedPrompt[] = []
// Question forms open in this session that voice may answer: the call's id -> takes voice's answers,
// or null when you said to dismiss it.
const openForms = new Map<string, (answers: Record<string, string> | null) => void>()
// What this session is waiting on you for, kept whether voice is here or not, so voice learns of it when
// it arrives: open question forms (id -> their questions, as voice gets them) and the closing question
// card of its last turn (until new input reaches the session).
const waitingForms = new Map<string, unknown[]>()
let waitingCard: Record<string, unknown> | undefined
// And a permission request on screen (the dialog asking to allow a tool): what it asks to do. Noted when
// Claude Code is about to show the dialog (its PermissionRequest event, after auto mode's classifier had
// its say), cleared as soon as the session moves on: a tool done, a new step, the turn's end.
let waitingPermission: string | undefined
// The session's plan as its update_plan calls built it, kept whether voice is here or not: the call that
// set its steps, then the ones that moved through them. Voice gets them again when it arrives mid-task.

function permissionSettled($: Dollar) {
  if (waitingPermission === undefined) return
  waitingPermission = undefined
  post($, { type: 'permission_done' })
  void dropWaitingNote($)
}
let panelPoll: { cancel: () => void } | undefined

// [lifecycle] lines: how voice starts, stops and restarts, as each session sees it, next to the
// daemon's in ~/.voice-assistant/logs/voice.log. Read both with
//   tail -f ~/.voice-assistant/logs/voice.log ~/.voice-assistant/logs/mod.log | grep --line-buffered lifecycle
let logLabel = '' // this session, as the log names it: its id's start and its project
function vlog($: Dollar, line: string) {
  void paths($)
    .then(p => $.process.run(['/bin/sh', '-c', 'echo "$(date +%H:%M:%S) [lifecycle] [mod $1] $2" >> "$3"', 'sh', logLabel || '?', line, p.modLog]))
    .catch(() => undefined)
}

const sleep = ($: Dollar, ms: number) => new Promise<void>(done => $.clock.after(ms, done))

// Other sessions finishing. A session that ends a turn you didn't stop, while you're in another session,
// leaves a note: one file per session, so no two sessions write the same file. While voice is off, every
// other session shows the notes in a band above the prompt, with Go and Dismiss; when voice starts, it
// tells you about them (new_session.finished_elsewhere). Visiting the session, Go or Dismiss removes its note.
const FINISHED_POLL_MS = 3000
const NOTE_LIFE_MS = 24 * 3600_000 // a note nobody acted on for a day is dropped
const finished = atom({ plugin: 'voice-mode', key: 'finished' } as const, [] as FinishedSession[])
type FinishedNote = { id: string; record: string; at: number; answer: string }
type AppRecord = {
  cliSessionId?: string; title?: string; cwd?: string; originCwd?: string; isArchived?: boolean
  lastFocusedAt?: number; lastAssistantUuid?: string; postTurnSummaryFor?: string
  postTurnSummary?: { status_category?: string; status_detail?: string; needs_action?: string }
}
let home: string | undefined
let ownRecord: string | undefined // this session's record in the desktop app, once found
let finishedPoll: { cancel: () => void } | undefined

const readJson = <T,>($: Dollar, path: string) =>
  $.fs.read(path).then(text => JSON.parse(text as string) as T).catch(() => undefined)
const dropNote = ($: Dollar, id: string) =>
  paths($).then(p => $.process.run(['/bin/rm', '-f', `${p.finished}/${id}.json`])).catch(() => undefined)

// The app's session records changed last, newest first (the app writes one at every turn's end and focus change).
async function recentRecords($: Dollar, count: number) {
  home ??= (await $.process.run(['/bin/sh', '-c', 'echo $HOME'])).stdout.trim()
  const root = `${home}/Library/Application Support/Claude/claude-code-sessions`
  const files: { path: string; mtimeMs: number }[] = []
  for (const a of await $.fs.list(root).catch(() => [])) {
    if (a.kind !== 'dir') continue
    for (const b of await $.fs.list(`${root}/${a.name}`).catch(() => [])) {
      if (b.kind !== 'dir') continue
      for (const f of await $.fs.list(`${root}/${a.name}/${b.name}`).catch(() => [])) {
        if (f.kind === 'file' && /^local_.*\.json$/.test(f.name)) files.push({ path: `${root}/${a.name}/${b.name}/${f.name}`, mtimeMs: f.mtimeMs })
      }
    }
  }
  files.sort((x, y) => y.mtimeMs - x.mtimeMs)
  const out: { path: string; d: AppRecord }[] = []
  for (const f of files.slice(0, count)) {
    const d = await readJson<AppRecord>($, f.path)
    if (d) out.push({ path: f.path, d })
  }
  return out
}

// This session's record in the desktop app (undefined for one the app doesn't show, as claude -p's), with
// the records changed last, newest first.
async function findOwnRecord($: Dollar, id: string) {
  const recent = await recentRecords($, 30)
  ownRecord ??= recent.find(r => r.d.cliSessionId === id)?.path
  return { record: ownRecord, recent }
}

// At the end of a turn you didn't stop: a note, unless you're in this session (it's the one focused last).
async function leaveNote($: Dollar, answer: string) {
  const id = await $.session.id()
  const { record, recent } = await findOwnRecord($, id)
  if (!record) return // a session the app doesn't show (claude -p): nothing to go to
  const focused = recent.reduce<AppRecord | undefined>((best, r) => ((r.d.lastFocusedAt ?? 0) > (best?.lastFocusedAt ?? 0) ? r.d : best), undefined)
  if (focused?.cliSessionId === id) return void dropNote($, id) // you watched it finish
  const note: FinishedNote = { id, record, at: await $.clock.now(), answer: answer.slice(0, 1500) }
  await paths($).then(p => $.fs.write(`${p.finished}/${id}.json`, JSON.stringify(note))).catch(() => undefined)
}

// Waiting for you while voice is off (a permission, a question form; a closing question comes with the turn's
// end, whose finished note covers it): a note, so voice
// tells you when it starts (new_session.waiting_elsewhere). While voice is on, the event itself tells it.
const WAIT_WORDS = 160 // how much of a question goes into "an answer: ..." (as new_session.WAIT_WORDS)
const askedFor = (question: string | undefined) => (question ? `an answer: ${question.slice(0, WAIT_WORDS)}` : 'an answer to its question')
let noteWanted = false // still waiting: a note written after the wait ended is taken back
async function leaveWaitingNote($: Dollar, what: string) {
  if (polling) return
  noteWanted = true
  const id = await $.session.id()
  const { record } = await findOwnRecord($, id)
  if (!record || !noteWanted) return // not a session the app shows, or answered meanwhile
  const note = { id, record, at: await $.clock.now(), what }
  await paths($).then(p => $.fs.write(`${p.waiting}/${id}.json`, JSON.stringify(note))).catch(() => undefined)
  if (!noteWanted) await dropWaitingNote($) // the wait ended while the note was being written
}
// Answered, or the session moved on: no longer waiting.
async function dropWaitingNote($: Dollar) {
  noteWanted = false
  const id = await $.session.id()
  await paths($).then(p => $.process.run(['/bin/rm', '-f', `${p.waiting}/${id}.json`])).catch(() => undefined)
}

// The notes other sessions left, as the band shows them, newest first; none while voice is on (it tells you instead).
async function checkFinished($: Dollar) {
  const me = await $.session.id()
  const now = await $.clock.now()
  const shown: FinishedSession[] = []
  const dir = (await paths($).catch(() => undefined))?.finished
  for (const f of polling || !dir ? [] : await $.fs.list(dir).catch(() => [])) {
    if (f.kind !== 'file' || !f.name.endsWith('.json') || f.name === `${me}.json`) continue
    const note = await readJson<FinishedNote>($, `${dir}/${f.name}`)
    if (!note) continue
    const d = await readJson<AppRecord>($, note.record)
    // Gone from the app, a day old, or you've been there since: nothing to tell.
    if (!d || d.isArchived || now - note.at > NOTE_LIFE_MS || (d.lastFocusedAt ?? 0) > note.at) {
      await dropNote($, note.id)
      continue
    }
    // The app's summary of the turn once it has written it (a moment after the turn ends); until then,
    // the answer's first sentence.
    const summary = d.postTurnSummaryFor === d.lastAssistantUuid ? d.postTurnSummary : undefined
    const isBlocked = summary?.status_category === 'blocked'
    const detail = (isBlocked && summary?.needs_action) || summary?.status_detail || sentences(note.answer.replace(/\s+/g, ' ').trim())[0] || ''
    shown.push({
      id: note.id, appId: note.record.split('/').pop()!.replace(/\.json$/, ''), title: d.title || 'Untitled session',
      project: (d.originCwd || d.cwd || '').split('/').pop() || '', at: note.at, isBlocked, detail: detail.slice(0, 200),
    })
  }
  shown.sort((x, y) => y.at - x.at)
  const { value: before = [] } = await $.state.get({ plugin: 'voice-mode', key: 'finished' })
  if (JSON.stringify(before) !== JSON.stringify(shown)) await update($, finished, () => shown)
}

async function goToFinished($: Dollar, s: FinishedSession) {
  await dropNote($, s.id)
  await update($, finished, list => list.filter(x => x.id !== s.id))
  // The app's own link to a session opens it whether it's running or not (as new_session.go_to does).
  const opened = await $.process.run(['/usr/bin/open', `claude://claude.ai/epitaxy/${s.appId}`]).catch(() => undefined)
  if (opened?.exitCode !== 0) $.ui.toast(`Couldn't open ${s.title}.`)
}

// Session events for the talker, so it always knows what the session is doing. The daemon
// passes on only the focused session's; the rest just keep its busy flags right.
function post($: Dollar, ev: Record<string, unknown>) {
  if (!polling) return
  // paths() is settled by now (connect needed it), so events still go out in the order they're posted.
  void paths($)
    .then(p => $.http.fetch('http://voice/event', {
      method: 'POST',
      socketPath: p.sock,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...ev, session: sessionId }),
    }))
    .catch(err => {
      // A lost turn event leaves voice thinking the session is busy (or idle) until the next one.
      if (String(ev.type).startsWith('turn_')) vlog($, `[turn] ${ev.type} not delivered to the daemon: ${err}`)
    })
}

// Whether the Voice panel is up, from the app's own record of panes, sent to the orb so its
// right-click menu offers Show or Hide transcript. Only changes are sent.
let sentPanel: boolean | undefined
async function syncPanel($: Dollar, force = false) {
  const isUp = (await $.ui.panes()).some(pane => pane.id === PANE)
  await update($, isPanelOpen, () => isUp)
  if (force || isUp !== sentPanel) {
    sentPanel = isUp
    post($, { type: 'panel', visible: isUp })
  }
}

// Registers this session with a running daemon and starts polling it. False when none runs.
async function connect($: Dollar): Promise<boolean> {
  if (isTalker) return false
  sessionId ??= await $.session.id()
  client ??= `${sessionId}:${Date.now()}`
  const res = await $.http
    .fetch('http://voice/register', {
      method: 'POST',
      socketPath: (await paths($)).sock,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ session: sessionId, client, busy: await read($, isSessionBusy) }),
    })
    .catch(() => undefined)
  if (!res?.ok) return false
  await update($, isOn, () => true)
  if (!polling) {
    vlog($, 'registered with the daemon; polling it')
    void pollCommands($, client)
  }
  return true
}

// Whether voice talks to this session; the daemon decides, from which session the app has in focus.
async function setVoice($: Dollar, isMine: boolean) {
  if (isMine === hasVoice) return
  hasVoice = isMine
  vlog($, isMine ? 'voice now talks to this session' : 'voice no longer talks to this session')
  panelPoll?.cancel()
  panelPoll = undefined
  sentPanel = undefined
  if (isMine) {
    await update($, voice, () => 'listening') // until the daemon's next state line
    await syncPanel($, true)
    postQueue($) // the talker learns what this session has waiting
    void postHistory($) // and what was said here before voice came, so it starts with this session's context
    // And what this session is waiting on you for: an open question form, or its closing question card.
    for (const [id, questions] of waitingForms) post($, { type: 'question', id, questions, arrived: true })
    if (waitingCard) post($, { ...waitingCard, arrived: true })
    if (waitingPermission) post($, { type: 'permission', what: waitingPermission, arrived: true })
    // And where its plan stands, working on it or done with it.
    const updates = await read($, plan)
    if (updates.length) post($, { type: 'plan', updates, arrived: true, busy: await read($, isSessionBusy) })
    // Catches a close by the panel's own button even if no event reaches this module.
    panelPoll = $.clock.every(2000, () => void syncPanel($))
  }
}

// This session's recent conversation, for the talker when voice comes to this session (it starts
// afresh on a switch): your requests and Claude's replies, newest kept, tool output and tags left out.
async function postHistory($: Dollar) {
  const rows = await $.session.messages().catch(() => [])
  if (!Array.isArray(rows)) return
  const clean = (text: string) => text.replace(/<([a-z][\w-]*)[^>]*>[\s\S]*?<\/\1>/g, '').replace(/\s+/g, ' ').trim()
  // A long message keeps its start and its end, where a question or conclusion usually is (as talker.clip_answer).
  const clip = (text: string, max: number) => {
    if (text.length <= max) return text
    const head = Math.round(max * 0.4)
    return `${text.slice(0, head).trimEnd()} […] ${text.slice(text.length - (max - head)).trimStart()}`
  }
  const picked: { role: string; text: string }[] = []
  let room = HISTORY_CHARS
  for (const row of [...rows].reverse()) {
    const text = clip(clean(row.text), row.role === 'user' ? 400 : 700)
    if (!text) continue // a row of tool calls or results only
    if (text.length > room) break
    room -= text.length
    picked.unshift({ role: row.role, text })
  }
  // And the session's last reply in full (its text since your last message), for when you ask voice for all of it.
  const lastAsk = rows.map(r => r.role === 'user' && clean(r.text) !== '').lastIndexOf(true)
  const answer = rows.slice(lastAsk + 1).filter(r => r.role !== 'user').map(r => r.text.replace(/<([a-z][\w-]*)[^>]*>[\s\S]*?<\/\1>/g, '').trim()).filter(Boolean).join('\n\n')
  // And voice's own last conversation here, each line with its time, however long ago: after a restart it
  // carries on from it; otherwise voice knows what you two said last, and when.
  const said = (await read($, transcript)).slice(-VOICE_LINES).map(l => ({ ...l, text: clip(l.text, 400) }))
  // Sent even when empty: the greeting waits for it.
  post($, { type: 'history', messages: picked, answer: answer.slice(-ANSWER_CHARS), voice: said })
  vlog($, `sent voice this session's recent conversation (${picked.length} messages, ${said.length} voice lines)`)
}

// What a tool call asks permission to do, in a few words: "edit register.tsx", "run git push".
function describeCall(tool: string, input: unknown): string {
  const args = (input ?? {}) as Record<string, unknown>
  const file = typeof args.file_path === 'string' ? args.file_path.split('/').pop() : undefined
  if (file && /Edit|Write/.test(tool)) return `edit ${file}`
  if (tool === 'Bash') return `run ${String(args.description || args.command || 'a command').slice(0, 80)}`
  if (tool === 'WebFetch' && typeof args.url === 'string') return `fetch ${args.url.split('/')[2] ?? args.url}`
  return `use ${tool.replace(/^mcp__[^_]+(?:_[^_]+)*__/, '').replace(/_/g, ' ')}`
}

// The daemon's commands, a long poll over the socket. Only the focused session gets any; every
// session learns which one that is, for the panel.
async function pollCommands($: Dollar, me: string) {
  polling = true
  try {
    for (;;) {
      const res = await $.http
        .fetch(`http://voice/commands?client=${encodeURIComponent(me)}`, { socketPath: (await paths($)).sock })
        .catch(() => undefined)
      if (!res?.ok) {
        if (await isDaemonUp($)) {
          await sleep($, 500)
          continue
        }
        // The daemon died: its session closed or reloaded. The session you're in starts it again.
        const wasMine = hasVoice
        const wasOn = await read($, isOn)
        vlog($, `lost the daemon: the socket stopped answering (this session had voice: ${wasMine}, voice on: ${wasOn})`)
        await setVoice($, false)
        if (wasMine && wasOn) {
          vlog($, 'restart attempt: starting voice again here')
          void start($, 'the daemon died while this session had voice', { isRestart: true })
        } else {
          vlog($, 'not restarting it from this session')
          await voiceOff($)
        }
        return
      }
      const r = JSON.parse(res.text) as {
        commands: Command[] | null
        owner: boolean
        title: string
        reachable: boolean
        stopping: boolean
      }
      if (r.stopping) {
        vlog($, 'the daemon says voice is stopping')
        await setVoice($, false)
        await voiceOff($)
        return
      }
      await setVoice($, r.owner)
      await update($, target, () => ({ title: r.title, isHere: r.owner, isReachable: r.reachable }))
      for (const cmd of r.commands ?? []) await handle($, cmd)
    }
  } catch (err) {
    vlog($, `polling failed: ${String(err)}`)
    throw err
  } finally {
    polling = false
    vlog($, 'stopped polling the daemon')
  }
}

const isDaemonUp = ($: Dollar) =>
  paths($)
    .then(p => $.http.fetch('http://voice/commands?client=-&wait=0', { socketPath: p.sock }))
    .then(r => r.ok)
    .catch(() => false)

async function voiceOff($: Dollar) {
  vlog($, 'voice marked off in this session')
  await update($, isOn, () => false)
  await update($, voice, () => 'off')
  await update($, target, () => null) // state holds JSON only: undefined is refused
}

async function handle($: Dollar, cmd: Command) {
  if (cmd.type === 'status' && cmd.text === 'listening') {
    await connect($) // the session that started the daemon registers like any other
  } else if (cmd.type === 'state') {
    await update($, voice, () => cmd.voice) // the orb draws it; the panel's header shows it too
    // Back to listening or on to speaking with no final line (a dropped "thank you"): the draft is stale.
    if (cmd.voice === 'listening' || cmd.voice === 'speaking') await update($, draft, () => '')
  } else if (cmd.type === 'draft') {
    await update($, draft, () => cmd.text)
    scrollToEnd($)
  } else if (cmd.type === 'transcript') {
    const line: TranscriptLine = { who: cmd.who, text: cmd.text, at: await $.clock.now() }
    await update($, transcript, list => [...list, line].slice(-300))
    if (cmd.who === 'you') await update($, draft, () => '') // the final pass replaces the draft
    scrollToEnd($)
  } else if (cmd.type === 'panel') {
    // Show / Hide transcript from the orb's right-click menu.
    vlog($, `orb menu: ${cmd.show ? 'show' : 'hide'} the panel`)
    if (cmd.show) await $.ui.open({ id: PANE, title: 'Voice' })
    else await $.ui.close({ id: PANE }).catch(() => undefined)
    await syncPanel($)
  } else if (cmd.type === 'clear') {
    // "Clear conversation" from the orb's right-click menu
    await update($, transcript, () => [])
    await update($, draft, () => '')
  } else if (cmd.type === 'stop') {
    await turnOff($, "the orb's Stop voice") // "Stop voice" from the orb's right-click menu
  } else if (cmd.type === 'submit') {
    if (await read($, isSessionBusy)) {
      vlog($, `[queue] holding (session busy, main turn ${mainTurn ?? 'none'}): ${cmd.text.slice(0, 60)}`)
      queued.push({ id: cmd.id ?? `q${Date.now()}`, text: cmd.text })
      postQueue($)
    } else submitNow($, cmd.text)
  } else if (cmd.type === 'merge') {
    const waiting = queued.find(q => q.id === cmd.id)
    if (waiting) {
      waiting.text = cmd.text
      vlog($, `[queue] follow-up merged into the waiting prompt ${cmd.id}`)
      postQueue($)
    } else if (await read($, isSessionBusy)) {
      queued.push({ id: cmd.newId, text: cmd.addition }) // it started meanwhile: the follow-up waits on its own
      postQueue($)
    } else submitNow($, cmd.addition)
  } else if (cmd.type === 'cancel') {
    const gone = queued.filter(q => cmd.ids === 'all' || cmd.ids.includes(q.id))
    queued = queued.filter(q => !gone.includes(q))
    for (const q of cmd.replaced ? [] : gone) {
      const line: TranscriptLine = { who: 'cancelled', text: q.text, at: await $.clock.now() }
      await update($, transcript, list => [...list, line].slice(-300))
    }
    postQueue($)
  } else if (cmd.type === 'tour') {
    const uv = await findUv($)
    if (uv) void showTour($, uv, ['--replay']) // not awaited: it stays open as long as you like
  } else if (cmd.type === 'enroll') {
    const uv = await findUv($)
    if (uv) void showTour($, uv, ['--enroll']) // the phrases to read, at Your voice; not awaited
  } else if (cmd.type === 'answer') {
    openForms.get(cmd.id)?.(cmd.answers) // a form already answered on screen is gone: nothing to do
  } else if (cmd.type === 'dismiss') {
    openForms.get(cmd.id)?.(null)
  } else if (cmd.type === 'interrupt') {
    if (mainTurn) await $.turn.abort({ turnId: mainTurn }).catch(() => undefined)
    if (cmd.then) submitNow($, cmd.then)
  }
}

// Brings the newest line into view once the panel has drawn it: scrolling right away goes to the
// end as it was before the new line, which leaves the line cut off at the bottom.
function scrollToEnd($: Dollar) {
  $.clock.after(150, () => void $.ui.scroll({ in: PANE, to: 'end' }).catch(() => undefined))
}

// asUser: your own words, without the "voice-mode plugin sent a message" frame around them.
function submitNow($: Dollar, text: string) {
  vlog($, `[queue] submitting: ${text.slice(0, 60)}`)
  void $.prompt.submit({ text, asUser: true }).then(
    r => {
      if ('drop' in r && r.drop) void refused($, text, r.drop)
      else vlog($, '[queue] submit entered')
    },
    err => vlog($, `[queue] submit failed: ${err}`),
  )
}

// After you stop the session yourself, the engine takes no prompt from a plugin until you send
// something. The request goes in the prompt box instead, after anything already there (Esc
// may have put your stopped message back), for one Enter to send, and voice tells you so.
async function refused($: Dollar, text: string, why: string) {
  const draft = (await $.prompt.read().catch(() => ({ text: '' }))).text
  const box = await $.prompt.fill({ text: draft.trim() ? `\n\n${text}` : text, mode: 'append' }).catch(() => undefined)
  vlog($, `[queue] submit dropped (${why}); ${box?.isFilled ? 'put in the prompt box' : `not in the prompt box either: ${box?.refusal ?? 'unknown'}`}`)
  post($, { type: 'submit_refused', text, why, inBox: Boolean(box?.isFilled) })
}

// What's still waiting, so the talker knows what it can take back.
function postQueue($: Dollar) {
  post($, { type: 'queue', items: queued })
}

// Where uv is: the login shell's PATH first (the app's own PATH often lacks it), then where its
// installers put it. Undefined when it isn't installed.
async function findUv($: Dollar): Promise<string | undefined> {
  const found = await $.process.run(['/bin/sh', '-lc', 'command -v uv']).catch(() => undefined)
  if (found?.exitCode === 0 && found.stdout.trim()) return found.stdout.trim()
  const home = (await $.process.run(['/bin/sh', '-c', 'echo $HOME'])).stdout.trim()
  for (const path of [`${home}/.local/bin/uv`, '/opt/homebrew/bin/uv', '/usr/local/bin/uv', `${home}/.cargo/bin/uv`]) {
    if ((await $.process.run(['/bin/test', '-x', path])).exitCode === 0) return path
  }
  return undefined
}

// isRestart: not your start (/talk, Start voice) but voice coming back on its own, so the daemon carries on
// the conversation instead of greeting you afresh.
async function start($: Dollar, why: string, { firstTour = false, isRestart = false } = {}) {
  vlog($, `start requested: ${why}`)
  if (await connect($)) {
    vlog($, 'start: voice was already running; joined it')
    return // already running: it follows whichever session you're in
  }
  vlog($, 'start: no daemon answering; spawning one')
  const uv = await findUv($)
  if (!uv) {
    vlog($, 'start: uv is not installed')
    $.ui.toast('Voice mode needs uv. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh, then start voice again.')
    await voiceOff($)
    return
  }
  void update($, voice, () => 'starting')
  // A first run's window is the one to look at: the panel comes once it's gone (turnOn).
  if (!firstTour && !firstRunOpen) void $.ui.open({ id: PANE, title: 'Voice' })
  // /talk again after Couldn't install: the window goes back to Setting up.
  if (firstTour) await $.process.run([uv, 'run', '--no-project', '--directory', $.plugin.root, 'onboarding.py', '--install-retry']).catch(() => undefined)
  // The first start sets up the Python packages, builds the orb and downloads the speech models
  // (about 3 GB): several minutes. Later starts take a few seconds.
  const child = $.process.spawn({
    argv: [uv, 'run', '--directory', $.plugin.root, 'daemon.py', '--socket', (await paths($)).sock, ...(isRestart ? ['--restart', why] : [])],
    // VOICE_FIRST_TOUR: the first-start tour is open, so voice learns your voice only once it closes.
    env: { PYTHONUNBUFFERED: '1', ...(firstTour ? { VOICE_FIRST_TOUR: '1' } : {}) },
  })
  const it = child[Symbol.asyncIterator]()
  const mine = () => void it.return?.({ code: 0, signal: null })
  stop = mine
  let answered = false // this daemon registered at least once: a later exit isn't a failed start
  let lastError = '' // the daemon's last stderr line: why uv couldn't set it up, if it couldn't
  // Register as soon as the daemon answers (its models take a while to load). Its "listening"
  // line can't be relied on: once any session registers, its output stops coming here.
  void (async () => {
    for (let tries = 0; tries < 240 && stop === mine && !polling; tries++) {
      if (await connect($)) {
        answered = true
        vlog($, `start: the new daemon answered after ${tries} retries`)
        return
      }
      await sleep($, 500)
    }
    if (!polling) vlog($, `start: gave up waiting for the daemon to answer (still running: ${stop === mine})`)
  })()

  // The loop is the daemon's life: leaving it kills the child.
  void (async () => {
    let pending = ''
    try {
      for (;;) {
        const piece = await it.next()
        if (piece.done) {
          vlog($, `the daemon process exited: ${JSON.stringify(piece.value ?? null)}`)
          const code = (piece.value as { code?: number | null } | undefined)?.code
          // Only a start that never got going: not a daemon that ran and was stopped or restarted
          // (exit 143, SIGTERM, is how a restart ends it).
          if (code && !answered && !polling && code !== 143) {
            if (firstTour) {
              // Most likely uv couldn't install voice's packages: the open window says so, and that /talk tries again.
              const reason = (lastError || `uv exited with code ${code}`).slice(0, 200)
              await $.process.run([uv, 'run', '--no-project', '--directory', $.plugin.root, 'onboarding.py', '--install-failed', reason]).catch(() => undefined)
              await voiceOff($)
            } else $.ui.toast("Voice mode couldn't start. What went wrong is in ~/.voice-assistant/logs/voice.log.")
          }
          break
        }
        if (piece.value.stream !== 'stdout') {
          $.ui.log(piece.value.text, { to: 'debug' })
          lastError = piece.value.text.trim().split('\n').filter(Boolean).pop() ?? lastError
          continue
        }
        pending += piece.value.text
        let nl: number
        while ((nl = pending.indexOf('\n')) >= 0) {
          const line = pending.slice(0, nl)
          pending = pending.slice(nl + 1)
          if (line.startsWith('@@VOICE ')) await handle($, JSON.parse(line.slice(8)) as Command)
        }
      }
    } catch (err) {
      vlog($, `the daemon process failed: ${String(err)}`)
      $.ui.toast(`voice mode stopped: ${String(err)}`)
    } finally {
      if (stop === mine) stop = undefined // a newer daemon this session started since keeps its own
      vlog($, 'no longer running a daemon from this session')
    }
  })()
}

// The onboarding window (onboarding.py): language, setup, the tour and learning your voice, in a window
// of its own. Its stdout is read as a stream, one event per line, handed to onLine as it happens: "start"
// (a language was chosen and saved), "later", "joined" (a window is already open), "hidden", "shown",
// "done", "closed". Resolves once the window is gone. Spawned, not run: run gives up after ten minutes,
// and the window waits for you. flags: --replay (the tour only), --enroll (opens at Your voice).
async function showTour($: Dollar, uv: string, flags: string[] = [], onLine: (line: string) => Promise<void> | void = () => undefined) {
  vlog($, `showing the onboarding window ${flags.join(' ')}`.trim())
  const child = $.process.spawn({ argv: [uv, 'run', '--no-project', '--directory', $.plugin.root, 'onboarding.py', ...flags] })
  let pending = ''
  let said = false // a window that fails to open prints nothing on stdout
  // A line that fails (start() throwing) is logged; the stream goes on, or the window would close with it.
  const handled = async (line: string) => {
    try {
      await onLine(line)
    } catch (err) {
      vlog($, `onboarding window: handling "${line}" failed: ${String(err)}`)
    }
  }
  for await (const piece of child) {
    if (piece.stream !== 'stdout') {
      vlog($, `onboarding window stderr: ${piece.text.trim()}`)
      continue
    }
    pending += piece.text
    let nl: number
    while ((nl = pending.indexOf('\n')) >= 0) {
      const line = pending.slice(0, nl).trim()
      pending = pending.slice(nl + 1)
      if (line) {
        said = true
        vlog($, `onboarding window: ${line}`)
        await handled(line)
      }
    }
  }
  if (pending.trim()) {
    said = true
    await handled(pending.trim())
  }
  if (!said) $.ui.toast("The voice setup window didn't open. The reason is in voice's log.")
  vlog($, 'the onboarding window is gone')
}

// Setup's progress in the footer while you've hidden its window: the page's own English words, as the
// footer's other labels are English only. '' when there's nothing to show (Ready brings the window back).
// <setupLabel>
function setupLabel(state: unknown): string {
  const s = (state && typeof state === 'object' ? state : {}) as { phase?: string; done?: number; total?: number; step?: string }
  if (s.phase === 'installing') return 'Setting up'
  if (s.phase === 'failed') return 'Setup stopped'
  if (s.phase !== 'setup') return ''
  if (s.step === 'starting') return 'Starting voice'
  if (!s.total || typeof s.done !== 'number') return 'Setting up'
  const gb = (n: number) => (n / 1e9).toFixed(1)
  return `Setting up ${Math.floor(Math.min(s.done / s.total, 1) * 100)}% · ${gb(s.done)} of ${gb(s.total)} GB`
}
// </setupLabel>

const SETUP_POLL_MS = 1000
let setupPoll: { cancel: () => void } | undefined

// The window hid or showed again: the footer's item, polled from first-run.json only while it's hidden.
async function watchHiddenSetup($: Dollar, isHidden: boolean) {
  setupPoll?.cancel()
  setupPoll = undefined
  if (!isHidden) {
    await update($, hiddenSetup, () => '')
    return
  }
  await refreshHiddenSetup($)
  if (await read($, hiddenSetup)) setupPoll = $.clock.every(SETUP_POLL_MS, () => void refreshHiddenSetup($).catch(() => undefined))
}

async function refreshHiddenSetup($: Dollar) {
  // Exit 3: the window is gone (its pid file missing or dead), so there's nothing to bring back.
  const res = await $.process.run(['/bin/sh', '-c', 'kill -0 "$(cat "$HOME/.voice-assistant/tour-open" 2>/dev/null)" 2>/dev/null || exit 3; cat "$HOME/.voice-assistant/first-run.json"'])
  let label = ''
  if (res.exitCode === 0) {
    try {
      label = setupLabel(JSON.parse(res.stdout))
    } catch {
      label = await read($, hiddenSetup) // caught mid-write: keep the last one
    }
  }
  if (!label) return watchHiddenSetup($, false)
  if (label !== (await read($, hiddenSetup))) await update($, hiddenSetup, () => label)
}

// The footer's item pressed: the window comes back where it was (onboarding.swift reads tour-command every 0.2 s).
async function showHiddenSetup($: Dollar) {
  vlog($, 'the footer asks the hidden onboarding window to show')
  await $.process.run(['/bin/sh', '-c', 'd="$HOME/.voice-assistant"; printf show > "$d/tour-command.$$" && mv "$d/tour-command.$$" "$d/tour-command"'])
  // Cleared now, not on "shown": after a reload this session no longer reads the window's lines.
  await watchHiddenSetup($, false)
}

// Turning voice on (/talk, the footer's Start voice): the first time, the onboarding window comes first,
// and voice starts only once you've chosen a language in it (onboarding.py has saved the language and set
// the first-run state by then). Closing the window afterwards leaves voice on; dismissing it before
// choosing a language leaves it off.
let firstRunOpen = false // this session has a first-run window opening or open (before onboarding.py's own guard can know)

async function turnOn($: Dollar, why: string) {
  if (firstRunOpen) {
    // Underway already. Once a language was chosen (the state file exists), voice starts normally, e.g. after
    // /talk turned it off; before that there's nothing to do but wait for the choice.
    const chosen = await $.process.run(['/bin/sh', '-c', 'test -e "$HOME/.voice-assistant/first-run.json"'])
    if (chosen.exitCode === 0) {
      await update($, isOn, () => true)
      await start($, why, { firstTour: true })
    }
    return
  }
  const seen = await $.process.run(['/bin/sh', '-c', 'test -e "$HOME/.voice-assistant/onboarded"'])
  if (seen.exitCode !== 0 && !(await connect($))) {
    const uv = await findUv($)
    // Meanwhile, the first start's slow part that needs no audio: installing voice's Python packages.
    if (uv) void $.process.run([uv, 'sync', '--directory', $.plugin.root], { timeoutMs: 600_000 }).catch(() => undefined)
    if (uv) {
      // Not awaited: the window stays open as long as you like. "joined": a first run is already underway.
      firstRunOpen = true
      void showTour($, uv, [], async line => {
        if (line === 'start' || (line === 'joined' && (await $.process.run(['/bin/sh', '-c', 'test -e "$HOME/.voice-assistant/first-run.json"'])).exitCode === 0)) {
          await update($, isOn, () => true)
          await start($, why, { firstTour: true })
        } else if (line === 'later') {
          await turnOff($, "the first run's Not now")
          $.ui.toast('Voice is off. Type /talk or press Start voice when you want it.')
        } else if (line === 'hidden' || line === 'shown') await watchHiddenSetup($, line === 'hidden')
      }).finally(async () => {
        firstRunOpen = false
        await watchHiddenSetup($, false)
        // The window is gone (Start talking, or closed): now the panel, which start() left closed meanwhile.
        if (await read($, isOn).catch(() => false)) void $.ui.open({ id: PANE, title: 'Voice' })
      })
      return
    }
  }
  await update($, isOn, () => true)
  await start($, why)
}

async function turnOff($: Dollar, why: string) {
  vlog($, `stop requested: ${why}`)
  // The daemon first, so nothing in this session's own bookkeeping can keep voice running. It tells
  // every session it's stopping, so none of them starts it again.
  const res = await $.http.fetch('http://voice/stop', { method: 'POST', socketPath: (await paths($)).sock }).catch(() => undefined)
  if (res?.ok) vlog($, 'stop: the daemon accepted it')
  else if (stop) {
    vlog($, "stop: the daemon didn't answer; ending the process this session started")
    stop()
  } else vlog($, "stop: the daemon didn't answer, and this session didn't start one: nothing to stop")
  await voiceOff($)
  await setVoice($, false)
}

export const register: Register = on => {
  // The desktop app attaches to a session when you open it: a session that missed the daemon
  // starting (it was asleep, or loaded later) registers then, so voice can follow you there.
  on('session.attach', { surface: 'desktop' }, async ($, e, next) => {
    const attached = await next(e)
    if (!polling && !isTalker) void connect($)
    return attached
  })

  on('session.start', async ($, e, next) => {
    // The talker's own Claude process (Agent SDK) loads mods too: it's not a session you talk to.
    if ((await $.process.run(['/usr/bin/printenv', 'CLAUDE_VOICE_TALKER'])).exitCode === 0) {
      isTalker = true
      return next(e)
    }
    logLabel = `${(await $.session.id()).slice(0, 8)} ${(await $.session.cwd()).split('/').pop()}`
    vlog($, 'mod loaded')
    await $.command.register({
      name: 'talk',
      description: 'Toggle open-mic voice mode: talk to this session while it works',
      immediate: true,
    })
    await $.command.register({ name: 'voice-panel', description: 'Open the voice transcript panel', immediate: true })
    await $.command.register({ name: 'voice-tour', description: 'Show the Voice Assistant tour again', immediate: true })
    await $.command.register({
      name: 'voice-enroll',
      description: 'Teach voice mode your voice again, so it answers only you',
      immediate: true,
    })
    const started = await next(e)
    $.ui.status(undefined) // clear the "voice: listening" line an early version pinned
    finishedPoll?.cancel()
    finishedPoll = $.clock.every(FINISHED_POLL_MS, () => void checkFinished($).catch(() => undefined))
    // A reload while the first run's window was hidden: its footer item polls again (it clears itself once the window's gone).
    if (await read($, hiddenSetup)) void watchHiddenSetup($, true).catch(() => undefined)
    // A reload kills the daemon with the old module; the session that had voice last brings it
    // back (every session reloads at once, and only one may start it). A restart file (touched by
    // whoever changed the daemon) turns it on once regardless.
    if (await connect($)) return started // voice is already on: register with it
    const p = await paths($)
    const owner = (await $.process.run(['/bin/cat', p.owner])).stdout.trim()
    if (owner && owner !== (await $.session.id())) return started
    const restart = await $.process.run(['/bin/rm', p.restart]).then(r => r.exitCode === 0)
    if (restart) await update($, isOn, () => true)
    const wasOn = await read($, isOn)
    vlog($, `at load: no daemon answering; last owner of voice: ${owner ? 'this session' : 'none'}, restart file: ${restart}, voice on: ${wasOn}`)
    if (wasOn) void start($, restart ? 'the restart file' : 'voice was on before this load', { isRestart: true })
    return started
  })

  on('command.run', { command: 'talk' }, async $ => {
    if (await read($, isOn)) {
      await turnOff($, '/talk')
      return {} // no transcript line: the orb appearing or leaving says it
    }
    await turnOn($, '/talk')
    return {}
  }).catch(($, e, next) => (next.called ? next(e) : { text: 'Voice mode failed to toggle.' }))

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      if (!isTalker) vlog($, `[turn] start ${e.turnId} (queued: ${queued.length})`)
      mainTurn = e.turnId
      waitingCard = undefined // new input reached the session: its closing question is answered or gone
      void dropWaitingNote($)
      await update($, isSessionBusy, () => true)
      post($, { type: 'turn_start', text: e.text })
    }
    return next(e)
  })

  // What you type in the chat yourself: voice hears it as yours and the Voice panel shows it, so the
  // conversation holds together when you switch between talking and typing. Voice's own prompts are
  // the plugin's; a phone through Remote Control, or the desktop app (an SDK host), is you too.
  on('prompt.submit', async ($, e, next) => {
    const done = await next(e)
    const text = withoutTags(e.text).slice(0, 2000)
    if (!isTalker && hasVoice && PERSON.has(e.origin.kind) && !('drop' in done && done.drop) && text) {
      vlog($, `[typed] ${e.origin.kind}: ${text.slice(0, 60)}`)
      const line: TranscriptLine = { who: 'typed', text, at: await $.clock.now() }
      await update($, transcript, list => [...list, line].slice(-300))
      scrollToEnd($)
      post($, { type: 'typed', text })
    }
    return done
  })

  on('tool.call', async ($, e, next) => {
    if (e.agentId === undefined && e.tool !== 'AskUserQuestion') { // a question form reaches voice whole, below
      const { tool, tool_use_id: _id, ...args } = e as unknown as Record<string, unknown>
      post($, { type: 'tool', tool, summary: JSON.stringify(args).slice(0, 200) })
      // Claude's plan (the task list under its message): voice speaks its progress.
      if (String(tool).endsWith('update_plan')) {
        const input = (args.input ?? args) as { items?: unknown[] }
        await update($, plan, list => (input.items?.length ? [input] : [...list, input])) // new steps start the plan over
        post($, { type: 'plan', input })
      }
      // A closing question with buttons (the app's next-step box): voice reads it out, and picking a
      // button out loud sends what the button sends. Only reply buttons: an app action (commit, PR...)
      // is the app's to run, so it isn't offered by voice.
      if (String(tool).endsWith('wrap_up')) {
        const input = (args.input ?? args) as { ask?: string; actions?: { label?: string; message?: string; action?: string }[] }
        const buttons = (input.actions ?? []).filter(b => b.label && !b.action).map(b => ({ label: b.label!, message: b.message || b.label! }))
        if (input.ask && buttons.length && _id) {
          waitingCard = { type: 'question', id: _id, buttons, questions: [{ question: input.ask, options: buttons.map(b => b.label), multiSelect: false }] }
          post($, waitingCard)
        }
      }
    }
    try {
      return await next(e)
    } finally {
      if (e.agentId === undefined) permissionSettled($) // the dialog, if one was up, is behind it now
    }
  }).catch(($, e, next) => next(e)) // never block a tool over voice bookkeeping

  // A permission request on screen (the dialog asking you to allow a tool): voice is told the session
  // waits for you, so "is it waiting for me?" gets a true answer. It's approved on screen only, never by
  // voice. Claude Code raises PermissionRequest only for a dialog it's about to show.
  on('classic.PermissionRequest', async ($, e, next) => {
    const answer = await next(e)
    if (!answer?.decision && e.agent_id === undefined) { // no hook above answered it: the dialog shows
      waitingPermission = describeCall(e.tool_name, e.tool_input)
      vlog($, `permission request on screen: ${waitingPermission}`)
      post($, { type: 'permission', what: waitingPermission }) // from any session: one elsewhere is announced as waiting
      void leaveWaitingNote($, `your permission to ${waitingPermission}`)
    }
    return answer
  }).catch(($, e, next) => next(e)) // bookkeeping only: the answer is the engine's

  // A question form (the session asks you, with options): voice reads it out and you can answer
  // either way, on screen or out loud. The form opens as always; an answer from voice closes it
  // (the engine aborts the form's request) and reaches the session as if you'd clicked it.
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    const id = e.tool_use_id
    if (e.agentId !== undefined || !id) return next(e)
    type Question = { question: string; header?: string; options?: { label: string; description?: string }[]; multiSelect?: boolean }
    const questions = (e as unknown as { questions: Question[] }).questions ?? []
    const forVoice = questions.map(q => ({ question: q.question, options: (q.options ?? []).map(o => o.label), multiSelect: !!q.multiSelect }))
    // Kept even while voice is elsewhere: voice can answer it from the moment it arrives here.
    waitingForms.set(id, forVoice)
    const byVoice = new Promise<Record<string, string> | null>(answer => openForms.set(id, answer))
    if (polling && !hasVoice) post($, { type: 'question', id, questions: forVoice }) // elsewhere: announced as waiting
    void leaveWaitingNote($, askedFor(forVoice[0]?.question))
    if (polling && hasVoice) {
      post($, { type: 'question', id, questions: forVoice })
      vlog($, `question form open (${questions.length} question(s)): voice may answer it`)
      $.clock.after(300, () => {
        try { $.ui.notice(id, 'You can also answer out loud.') } catch { /* the form is already gone */ }
      })
    }
    const onScreen = next(e)
    try {
      const won = await Promise.race([onScreen.then(r => ({ r })), byVoice.then(answers => ({ answers }))])
      if ('answers' in won) {
        onScreen.catch(() => undefined) // the form's own request ends aborted
        if (won.answers === null) {
          // Dismissed out loud: as the form's close button, the question goes unanswered.
          vlog($, 'question form dismissed by voice')
          return { deny: 'The user dismissed the question without answering it.' } as Awaited<ReturnType<typeof next>>
        }
        vlog($, 'question form answered by voice')
        return { result: { questions, answers: won.answers } } as unknown as Awaited<ReturnType<typeof next>>
      }
      const answers = (won.r as { result?: { answers?: unknown } }).result?.answers
      post($, { type: 'question_done', id, answers: answers ?? null })
      vlog($, 'question form answered on screen')
      return won.r
    } finally {
      openForms.delete(id)
      waitingForms.delete(id)
      void dropWaitingNote($) // answered (on screen or out loud) or dismissed: no longer waiting
    }
  })

  // What Claude writes in the chat while it works (a step with text that also calls tools: the
  // final answer comes with turn.complete), so voice can say what's going on instead of
  // going quiet through a long turn. The daemon decides whether to speak it.
  // A streaming event: the hook passes every chunk through untouched and reads the step's result.
  on('turn.step', async function* ($, e, next) {
    if (e.agentId === undefined) permissionSettled($) // a new step: nothing is waiting on you any more
    const step = yield* next(e)
    if (e.agentId === undefined && step.answer.trim() && step.toolUses.length > 0) {
      post($, { type: 'progress', text: step.answer.slice(0, 600) }) // fire and forget: never holds up the turn
    }
    return step
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined && !isTalker) vlog($, `[turn] complete ${e.turnId}: ${e.reason} (main turn ${mainTurn ?? 'none'}, queued: ${queued.length})`)
    const done = await next(e).catch(err => {
      vlog($, `[turn] complete ${e.turnId}: the chain beneath failed: ${err}`)
      throw err
    })
    if (e.agentId === undefined) permissionSettled($)
    if (e.agentId === undefined) {
      if (mainTurn === e.turnId) mainTurn = undefined
      await update($, isSessionBusy, () => false)
      post($, { type: 'turn_complete', answer: e.answer, durationMs: e.durationMs, isAborted: e.isAborted })
      if (!e.isAborted) void leaveNote($, e.answer).catch(() => undefined) // for the other sessions' band and voice
      const waiting = queued.shift() // voice's next prompt, now that the session is free
      if (waiting) {
        postQueue($)
        post($, { type: 'queue_started', id: waiting.id, text: waiting.text }) // the orb's task panel: now running
        submitNow($, waiting.text)
      }
    }
    return done
  })


  // Whether the Voice panel is showing, so the orb's right-click menu offers only the
  // right toggle (Show or Hide transcript). Closing it with its own close button counts too.
  // Opens and closes from anywhere (the app, the orb, /voice-panel) re-read the pane record.
  on('ui.open', { id: PANE }, async ($, e, next) => {
    const opened = await next(e)
    vlog($, 'panel opened')
    await syncPanel($)
    return opened
  })

  on('ui.close', { id: PANE }, async ($, e, next) => {
    const closed = await next(e)
    vlog($, 'panel closed')
    await syncPanel($)
    return closed
  })

  on('command.run', { command: 'voice-enroll' }, async $ => {
    const res = await $.http.fetch('http://voice/enroll', { method: 'POST', socketPath: (await paths($)).sock }).catch(() => undefined)
    if (!res?.ok) return { text: 'Start voice first (/talk), then run /voice-enroll.' }
    const uv = await findUv($)
    if (uv) void showTour($, uv, ['--enroll']) // the window opens at Your voice; not awaited
    return {}
  })

  on('command.run', { command: 'voice-tour' }, async $ => {
    const uv = await findUv($)
    if (!uv) return { text: 'Voice mode needs uv. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh' }
    void showTour($, uv, ['--replay']) // not awaited: the slideshow stays open as long as you like
    return {}
  })

  on('command.run', { command: 'voice-panel' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Voice' })
    return {}
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    // Hebrew (or Arabic) reads right to left: without it, a line's final period lands at its start.
    const dir = (text: string) => (/^[^A-Za-z\u0590-\u06FF]*[\u0590-\u06FF]/.test(text) ? 'rtl' : 'ltr')
    // What you said: a gray bubble on the right.
    const bubble = (key: string, text: string, isDraft = false) => (
      // The API has no corner radius: borderStyle "round" is what rounds the corners. The border itself is
      // transparent, so only the rounding shows (a border in the bubble's own see-through gray drew a darker ring).
      // The left margin keeps a bubble off the panel's full width (the layout has no max width).
      <Box key={key} alignSelf="flex-end" marginLeft={BUBBLE_INSET} backgroundColor={BUBBLE} borderStyle="round" borderColor="rgba(0, 0, 0, 0)" paddingX="md" paddingY="xs">
        <Text wrap="wrap" dir={dir(text)} dimColor={isDraft}>{text}</Text>
      </Box>
    )
    // What you typed in the chat: your bubble, with a small label over it saying it was typed.
    const typed = (key: string, text: string) => (
      <Box key={key} flexDirection="column" alignItems="flex-end" gap="xs">
        <Text size="footnote" dimColor>typed</Text>
        {bubble(`${key}-b`, text)}
      </Box>
    )
    // A note about what happened to the session (stopped, a prompt taken back): small and dim.
    const note = (key: string, text: string) => (
      <Text key={key} wrap="wrap" dir={dir(text)} size="footnote" dimColor>{text}</Text>
    )
    const lines = (await read($, transcript)).slice(-200)
    const drafting = await read($, draft)

    // Only the conversation; the pane scrolls it natively. Start and stop are the footer's button,
    // the orb's menu and /talk. What went to the session isn't shown: it's in the session itself.
    // Room under the last line, almost half the pane's height, so the newest message sits well clear of the bottom edge.
    const bottomRoom = Math.round(e.props.scroll.bodyRows * 0.45)
    return (
      <Box flexDirection="column" gap="sm" paddingBottom={bottomRoom}>
        {lines.length === 0 && !drafting && <Text dimColor>Nothing said yet. Start voice and talk.</Text>}
        {lines.map((line, i) => {
          const key = `l${line.at}-${i}`
          if (line.who === 'you') return bubble(key, line.text)
          if (line.who === 'typed') return typed(key, line.text)
          // Right to left: the API has no text alignment, so the line's box goes right (a long one's last line stays left).
          if (line.who === 'talker') return dir(line.text) === 'rtl'
            ? <Box key={key} alignSelf="flex-end"><Text wrap="wrap" dir="rtl">{line.text}</Text></Box>
            : <Text key={key} wrap="wrap">{line.text}</Text>
          if (line.who === 'interrupt') return note(key, line.text ? `■ Stopped the session: ${line.text}` : '■ Stopped the session')
          if (line.who === 'cancelled') return note(key, `✕ Took back: ${line.text}`)
          return null // sent / queued: the prompt itself shows in the session
        })}
        {drafting && bubble('draft', `${drafting}…`, true)}
      </Box>
    )
  })

  // While voice is off: the other sessions that finished a turn you haven't seen, above the prompt, each
  // with what it did (or what it needs from you), Go and Dismiss.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const done = await read($, finished)
    if (e.props.hasSurvey || done.length === 0) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" gap="xs">
        {done.slice(0, 3).map(s => (
          <Box key={s.id} flexDirection="row" alignItems="center" gap="sm">
            <Box flexGrow={1} flexShrink={1}>
              <Text wrap="truncate-end">
                {s.isBlocked ? '◐ ' : '● '}
                <Text weight="semibold">{s.title}</Text>
                {s.project ? ` · ${s.project}` : ''}
                {s.isBlocked ? ' needs you: ' : ' finished: '}
                {s.detail}
              </Text>
            </Box>
            <Button key={`go-${s.id}`} label="Go" variant="secondary" onPress={() => goToFinished($, s)} />
            <Button
              key={`dismiss-${s.id}`}
              label="Dismiss"
              variant="secondary"
              dimColor
              onPress={async () => {
                await dropNote($, s.id)
                await update($, finished, list => list.filter(x => x.id !== s.id))
              }}
            />
          </Box>
        ))}
        {done.length > 3 && <Text dimColor size="footnote">{`and ${done.length - 3} more`}</Text>}
      </Box>
    )
  })

  // One small text button at the right of the prompt footer: "Start voice" / "Stop voice".
  // The floating orb is the voice UI itself (right-click it to stop, too). Before it, while the first run's
  // window is hidden, setup's progress: pressing it brings the window back.
  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const modes = await next(e)
    const isVoiceOn = await read($, isOn)
    const setup = await read($, hiddenSetup)
    const { Box, Button } = $.ui.resolve(e)

    return (
      <Box flexDirection="row" alignItems="center" gap="sm">
        {modes}
        {setup && <Button key="voice-setup" label={setup} variant="secondary" dimColor onPress={() => showHiddenSetup($)} />}
        <Button
          key="voice-toggle"
          label={isVoiceOn ? 'Stop voice' : 'Start voice'}
          variant="secondary" // the app's own button, with its normal padding (plain had none)
          dimColor
          onPress={async () => {
            if (isVoiceOn) return turnOff($, "the footer's Stop voice")
            await turnOn($, "the footer's Start voice")
          }}
        />
      </Box>
    )
  })
}
