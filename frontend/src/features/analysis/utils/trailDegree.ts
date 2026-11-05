/**
 * Grading of a detected technical section, for the map and the table.
 *
 * A detector that only answers "technical / not technical" cannot show the
 * athlete what it believes strongly and what it barely believes, and the
 * difference is exactly what the calibration loop needs to see. The bands below
 * are therefore expressed as multiples of *the detector's own threshold* (the
 * number it used to decide), never as a fixed watt-like constant and never as a
 * per-session quantile: quantiles would invent differences in a session whose
 * sections all score alike.
 */
import type { ThemeColorToken } from '@/lib/themeColor'

/** The three grades a section can carry. */
export type TrailDegree = 'light' | 'technical' | 'very'

/** How one section should read: grade, colour token, stroke weight and alpha. */
export interface TrailGrade {
  degree: TrailDegree
  /** Design token to resolve for canvas/SVG drawing. */
  colorToken: ThemeColorToken
  /** Stroke width in pixels: the assertiveness of the claim. */
  weight: number
  /** Stroke opacity in [0, 1]. */
  opacity: number
  /** i18n key of the grade's word. */
  labelKey: string
}

/** Above this multiple of the threshold the detector is emphatic. */
const VERY_TECHNICAL_RATIO = 1.8
/** Above this multiple it is firm; below, it only barely cleared the cut. */
const TECHNICAL_RATIO = 1.3

/**
 * Grades one section's index against the threshold that let it through.
 *
 * @param score - The section's peak technicality index.
 * @param threshold - The cut the server used, from the same payload.
 * @returns The grade, in the [light, technical, very] ramp.
 */
export function gradeTrailSection(score: number, threshold: number): TrailGrade {
  const ratio = threshold > 0 ? score / threshold : 0
  if (ratio >= VERY_TECHNICAL_RATIO) {
    return {
      degree: 'very',
      colorToken: '--color-hr',
      weight: 7,
      opacity: 0.95,
      labelKey: 'analysis.trail.degree.veryTechnical',
    }
  }
  if (ratio >= TECHNICAL_RATIO) {
    return {
      degree: 'technical',
      colorToken: '--color-effort',
      weight: 6,
      opacity: 0.85,
      labelKey: 'analysis.trail.degree.technical',
    }
  }
  return {
    degree: 'light',
    colorToken: '--color-effort',
    weight: 4,
    opacity: 0.55,
    labelKey: 'analysis.trail.degree.light',
  }
}
