/**
 * Grading of a detected technical section, for the map and the table.
 *
 * A detector that only answers "technical / not technical" cannot show the
 * athlete what it believes strongly and what it barely believes, and the
 * difference is exactly what the calibration loop needs to see. The bands are
 * multiples of *the detector's own threshold*, carried in the payload: neither the
 * view nor the model keeps a hand-set constant, and no per-session quantile is
 * used, because quantiles would invent differences in a session whose sections
 * all score alike.
 *
 * "Very technical" is deliberately narrow. A stretch longer than a couple of
 * hundred metres usually holds one block of hard ground and runnable ground around
 * it, so calling the whole stretch extreme reads wrong to anyone who walked it -
 * the block gets the extreme colour, the stretch around it stays amber. And where
 * the cartography describes the ground as ordinary (a marked hiking path, paved,
 * an easy track grade) the measured claim is capped: the body's reason for slowing
 * there is vegetation, navigation or fatigue, not the kind of ground that throws a
 * runner.
 */
import type { TrailClaim, TrailSection } from '@/features/analysis/types'
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
  /** Longest stretch that may be extreme as a whole; beyond it, only its core is. */
  veryMaxLengthM: number
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

/** Surfaces and track grades the map calls rideable-or-walkable ground. */
const EASY_SURFACES = new Set(['asphalt', 'paved', 'concrete', 'compacted', 'cement'])
const EASY_TRACKTYPES = new Set(['grade1', 'grade2'])

/**
 * Whether the cartography describes the ground as ordinary, which caps the claim.
 *
 * @param osm - What the map says about the stretch, when it says anything.
 * @returns `true` when the mapped ground cannot be called extreme.
 */
export function mapSaysEasy(osm: TrailSection['osm']): boolean {
  if (!osm) {
    return false
  }
  return (
    osm.sacScale === 'hiking' ||
    osm.mtbScale === '0' ||
    osm.mtbScale === '1' ||
    (osm.surface !== null && EASY_SURFACES.has(osm.surface)) ||
    (osm.tracktype !== null && EASY_TRACKTYPES.has(osm.tracktype))
  )
}

/**
 * Grades one section against the ratios the detector used.
 *
 * @param section - The measured section.
 * @param ratios - The threshold and band multiples the server answered with.
 * @returns The grade, in the [light, technical, mixed, very] ramp.
 */
export function gradeTrailSection(section: TrailSection, ratios: TrailRatios): TrailGrade {
  const ratio = ratios.threshold > 0 ? section.score / ratios.threshold : 0

  if (section.coreScore !== null) {
    if (section.lengthM <= ratios.veryMaxLengthM && !mapSaysEasy(section.osm)) {
      return {
        degree: 'very',
        colorToken: '--color-hr',
        weight: 7,
        opacity: 0.95,
        labelKey: 'analysis.trail.degree.veryTechnical',
      }
    }
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
 * @returns The points inside the range; all of them when the range selects none.
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

/** How one way of the network reads: colour, stroke and word for its claim. */
export interface TrailWayGrade {
  /** Design token to resolve for canvas/SVG drawing. */
  colorToken: ThemeColorToken
  /** Stroke width in pixels: the assertiveness of the claim. */
  weight: number
  /** Stroke opacity in [0, 1]. */
  opacity: number
  /** i18n key of the claim's word. */
  labelKey: string
}

/**
 * A way earns its colour from the median of its descents, never from a single
 * crossing: one tired day is not a property of the ground. Below the minimum
 * descents it stays grey, whatever the numbers say.
 */
const CLAIM_GRADES: Record<TrailClaim, TrailWayGrade> = {
  hard: {
    colorToken: '--color-hr',
    weight: 6,
    opacity: 0.9,
    labelKey: 'analysis.trailNetwork.claim.hard',
  },
  technical: {
    colorToken: '--color-effort',
    weight: 5,
    opacity: 0.8,
    labelKey: 'analysis.trailNetwork.claim.technical',
  },
  mild: {
    colorToken: '--color-brand',
    weight: 4,
    opacity: 0.6,
    labelKey: 'analysis.trailNetwork.claim.mild',
  },
  thin: {
    colorToken: '--color-muted-foreground',
    weight: 3,
    opacity: 0.45,
    labelKey: 'analysis.trailNetwork.claim.thin',
  },
  plain: {
    colorToken: '--color-ink',
    weight: 3,
    opacity: 0.5,
    labelKey: 'analysis.trailNetwork.claim.plain',
  },
}

/**
 * Grade one network way by the claim its descents support.
 *
 * @param claim - The server's verdict for the way.
 * @returns The grade, in the network's own ramp.
 */
export function gradeTrailWay(claim: TrailClaim): TrailWayGrade {
  return CLAIM_GRADES[claim]
}
