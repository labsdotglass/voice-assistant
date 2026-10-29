export type VoiceOn = boolean
export type TranscriptWho = 'you' | 'typed' | 'talker' | 'sent' | 'queued' | 'interrupt' | 'cancelled'
export type TranscriptLine = { who: TranscriptWho; text: string; at: number }
// The session voice talks to: its title in the app, whether it's this one, whether it can hear voice.
export type VoiceTarget = { title: string; isHere: boolean; isReachable: boolean }
// A prompt voice is holding until the session finishes its turn.
export type QueuedPrompt = { id: string; text: string }
// Another session that finished a turn you haven't seen, for the band above the prompt (while voice is off):
// its id, its record's name in the app (to open it), its title and folder, when it finished, and what it did
// (the app's summary of the turn; blocked: it needs you).
export type FinishedSession = { id: string; appId: string; title: string; project: string; at: number; isBlocked: boolean; detail: string }

declare module 'claude-code' {
  interface PluginState {
    'voice-mode': { isOn: VoiceOn; voice: string; isSessionBusy: boolean; transcript: TranscriptLine[]; draft: string; isPanelOpen: boolean; target: VoiceTarget | null; finished: FinishedSession[]; plan: unknown[]; hiddenSetup: string }
  }
}
