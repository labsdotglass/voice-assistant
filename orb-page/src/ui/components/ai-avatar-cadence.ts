/**
 * Speech-cadence math for the AiAvatar cloudglass renderer, ported from
 * the AiAvatar module CSS `@keyframes speak`. Time is in seconds; output
 * is a pulse amplitude in root units (the orb's square container spans
 * 0..1).
 */

/** --_cadence-period at speed 1, seconds. */
export const CADENCE_PERIOD = 2.2
/** The per-beat pulse ceiling, root units. */
export const PULSE_PEAK = 0.18

/** @keyframes speak: [fraction of period, fraction of peak]. */
const SPEAK_STOPS = [
  [0, 0], [0.04, 1], [0.13, 0.12], [0.17, 0.7], [0.27, 0.06], [0.32, 0.92],
  [0.44, 0.04], [0.49, 0.5], [0.57, 0.08], [0.62, 0.85], [0.74, 0],
  [0.79, 0.42], [0.87, 0.05], [0.92, 0.28], [1, 0],
] as const

function mod1(x: number): number {
  return x - Math.floor(x)
}

/** The 7-beat speech cadence: pulse amplitude at time t (seconds). */
export function pulseAt(t: number): number {
  const phase = mod1(t / CADENCE_PERIOD)
  let k = 0
  while (k < SPEAK_STOPS.length - 2 && SPEAK_STOPS[k + 1][0] < phase) k++
  const [t0, v0] = SPEAK_STOPS[k]
  const [t1, v1] = SPEAK_STOPS[k + 1]
  return PULSE_PEAK * (v0 + ((phase - t0) / (t1 - t0)) * (v1 - v0))
}
