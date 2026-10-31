import type { Schemas } from '@/types'

import { kmToMiles } from '@/utils/units'

/** The viewer's measurement system. */
type Units = Schemas['Units']

/**
 * Formats an effort duration as `H:MM:SS`, or `MM:SS` under an hour — the same
 * shape the activity and lap tables use, so a segment time reads like the rest
 * of the app.
 *
 * @param seconds - Elapsed seconds.
 * @returns The formatted duration, or `--` when unknown.
 */
export function formatEffortDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return '--'
  }
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = String(total % 60).padStart(2, '0')
  const mmss = `${String(minutes).padStart(2, '0')}:${rest}`
  return hours > 0 ? `${hours}:${mmss}` : mmss
}

/**
 * Formats a segment length. Segment distance is always road/trail distance, so
 * imperial readers get miles.
 *
 * @param meters - Length in metres.
 * @param units - The viewer's measurement system.
 * @returns The formatted length with its unit.
 */
export function formatSegmentDistance(meters: number, units: Units): string {
  const km = meters / 1000
  const value = units === 'imperial' ? kmToMiles(km) : km
  return `${value.toFixed(2)} ${units === 'imperial' ? 'mi' : 'km'}`
}

/**
 * Formats an elevation figure (gain, or a profile altitude).
 *
 * @param meters - Height in metres.
 * @param units - The viewer's measurement system.
 * @returns The formatted height with its unit.
 */
export function formatSegmentElevation(meters: number, units: Units): string {
  if (units === 'imperial') {
    return `${Math.round(meters * 3.28084)} ft`
  }
  return `${Math.round(meters)} m`
}

/**
 * Formats metres-per-second as a speed. A segment belongs to no single sport, so
 * the page shows speed rather than guessing whether the reader prefers pace.
 *
 * @param mps - Average speed in metres per second.
 * @param units - The viewer's measurement system.
 * @returns The formatted speed with its unit.
 */
export function formatSegmentSpeed(mps: number | null, units: Units): string {
  if (mps === null || !Number.isFinite(mps) || mps <= 0) {
    return '--'
  }
  if (units === 'imperial') {
    return `${(mps * 2.23694).toFixed(1)} mph`
  }
  return `${(mps * 3.6).toFixed(1)} km/h`
}
