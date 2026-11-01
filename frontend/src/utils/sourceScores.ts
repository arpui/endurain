/**
 * Shared display order and shaping for the intensity-source benchmark:
 * the same window scored by watts, adjusted pace and heart rate at once.
 */

/** Fixed order the sources read in, matching the chain's own preference. */
export const SOURCE_ORDER = ['power', 'pace', 'hr'] as const

export type SourceKey = (typeof SOURCE_ORDER)[number]

export interface SourceScore {
  key: SourceKey
  value: number
}

/** Return the scores the benchmark holds, in chain order, dropping the absent. */
export function orderedSourceScores(scores: Record<string, number> | null): SourceScore[] {
  if (!scores) {
    return []
  }
  return SOURCE_ORDER.filter((key) => scores[key] !== undefined).map((key) => ({
    key,
    value: scores[key] as number,
  }))
}
