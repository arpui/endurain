/**
 * Grading of a detected technical section, for the map and the table.
 *
 * A detector that only answers "technical / not technical" cannot show the
 * athlete what it believes strongly and what it barely believes, and the
 * difference is exactly what the calibration loop needs to see. The bands below
 * are multiples of *the detector's own threshold*, which the payload carries:
 * neither the view nor the model keeps a hand-set constant, and no per-session
 * quantile is used, because quantiles would invent differences in a session
 * whose sections all score alike.
 *
 * "Very technical" is deliberately narrow. A stretch over a few hundred metres
 * usually holds one block of stones and runnable ground around it, so calling the
 * whole stretch extreme reads wrong to anyone who walked it. Where the detector
 * found a hard *core* inside a stretch, the stretch is graded "technical, with a
 * hard core" and the core itself is what gets painted extreme - as a place, not
 * as a bigger adjective over a kilometre and a half.
 */
import type { TrailSection } from '@/features/analysis/types'
import type { ThemeColorToken } from '@/lib/themeColor'

/** The four grades a section can carry. */
export type TrailDegree = 'light' | 'technical' | 'mixed' | 'very'

/** The ratios the server measured with, from the same payload. */
export interface TrailRatios {
  /** The index cut that made a stretch technical at all. */
  threshold: number
  /** Multiple of the cut that reads as firmly technical. */
  technicalRatio: number
  /** Multiple of the cut that reads as a hard core. */
  hardRatio: number
}

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

/** A share of a stretch above which a core that fills it makes the whole extreme. */
const CORE_IS_THE_WHOLE_SHARE = 0.8

/**
 * Grades one section's index against the ratios the detector used.
 *
 * @param section - The measured section.
 * @param ratios - The threshold and band multiples the server answered with.
 * @returns The grade, in the [light, technical, mixed, very] ramp.
 */
export function gradeTrailSection(section: TrailSection, ratios: TrailRatios): TrailGrade {
  const ratio = ratios.threshold > 0 ? section.score / ratios.threshold : 0
  const coreLength =
    section.coreStartM !== null && section.coreEndM !== null
      ? section.coreEndM - section.coreStartM
      : 0
  const fillsTheStretch =
    section.lengthM > 0 && coreLength / section.lengthM >= CORE_IS_THE_WHOLE_SHARE

  if (section.coreScore !== null && fillsTheStretch) {
    return {
      degree: 'very',
      colorToken: '--color-hr',
      weight: 7,
      opacity: 0.95,
      labelKey: 'analysis.trail.degree.veryTechnical',
    }
  }
  if (section.coreScore !== null) {
    // the extreme ground is a block inside; the stretch around it is only technical
    return {
      degree: 'mixed',
      colorToken: '--color-effort',
      weight: 6,
      opacity: 0.85,
      labelKey: 'analysis.trail.degree.mixed',
    }
  }
  if (ratio >= ratios.technicalRatio) {
    return {
      degree: 'technical',
      colorToken: '--color-effort',
      weight: 5,
      opacity: 0.8,
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

/**
 * The points of a section that fall inside a ground range, for drawing its core.
 *
 * The section's polyline is thinned for transfer, so the core is located by
 * walking the polyline's own distances rather than by re-reading the streams.
 *
 * @param points - The section's polyline, in order.
 * @param sectionLengthM - Ground the section covers, from its own measurement.
 * @param fromM - Ground position where the range starts, in the section's frame.
 * @param toM - Ground position where it ends.
 * @returns The points inside the range; all of them when the range is empty.
 */
export function pointsWithin<T extends { lat: number; lng: number }>(
  points: T[],
  sectionLengthM: number,
  fromM: number,
  toM: number,
): T[] {
  if (points.length < 2 || sectionLengthM <= 0 || toM <= fromM) {
    return points
  }
  let walked = 0
  const scaled: number[] = [0]
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    if (!previous || !current) {
      continue
    }
    walked += groundDistanceM(previous, current)
    scaled.push(walked)
  }
  const factor = sectionLengthM / Math.max(walked, 1)
  const inside = points.filter((_, index) => {
    const at = scaled[index]
    return at !== undefined && at * factor >= fromM && at * factor <= toM
  })
  return inside.length > 0 ? inside : points
}

/** Approximate ground distance between two coordinates, in metres. */
function groundDistanceM(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const latM = (b.lat - a.lat) * 111195
  const lngM = (b.lng - a.lng) * 111320 * Math.cos(((a.lat + b.lat) / 2) * (Math.PI / 180))
  return Math.hypot(latM, lngM)
}
