/** Formatting helpers for the analysis surfaces. */

/**
 * Renders a duration in seconds as the curve's compact axis label.
 *
 * @param seconds - The peak duration (5, 60, 900, ...).
 * @returns A short human label, e.g. `90 s`, `5 min`, `1 h`.
 */
export function formatDurationLabel(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  if (seconds < 3600) {
    const minutes = seconds / 60
    return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} min`
  }
  const hours = seconds / 3600
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`
}

/**
 * Renders an elapsed time in seconds as `M:SS` (or `H:MM:SS` past an hour),
 * for the above-CP totals.
 *
 * @param seconds - Elapsed seconds.
 * @returns The clock-formatted duration.
 */
export function formatClock(seconds: number): string {
  const whole = Math.round(seconds)
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = whole % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}
