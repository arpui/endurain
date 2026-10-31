import type { SegmentDetail } from '../types'

/** Metres per degree of latitude (equirectangular approximation). */
const DEG_TO_M = 111_320

/**
 * Cumulative metres along the polyline, index-aligned with `geometry`. Uses the
 * same approximation as the backend matcher, whose tolerance is far larger than
 * the difference; the values only position the profile's x-axis.
 *
 * @param geometry - Ordered `[[lat, lng], ...]` points.
 * @returns One distance per point, starting at 0.
 */
export function cumulativeDistancesMeters(geometry: [number, number][]): number[] {
  const totals: number[] = []
  let running = 0
  geometry.forEach((point, index) => {
    if (index > 0) {
      const previous = geometry[index - 1]
      if (previous) {
        const refLat = (((point[0] + previous[0]) / 2) * Math.PI) / 180
        const dLat = (point[0] - previous[0]) * DEG_TO_M
        const dLng = (point[1] - previous[1]) * DEG_TO_M * Math.cos(refLat)
        running += Math.hypot(dLat, dLng)
      }
    }
    totals.push(running)
  })
  return totals
}

/** One plotted point of the elevation profile. */
export interface ProfilePoint {
  /** Distance from the segment start, in kilometres. */
  km: number
  /** Altitude in metres. */
  meters: number
}

/**
 * Builds the profile series: altitude per geometry point, thinned to at most
 * `maxPoints` samples so a 20k-point segment does not hand the chart library
 * more samples than it can draw. `null` when the segment has no altitudes, which
 * is what hides the chart.
 *
 * @param detail - The segment definition.
 * @param maxPoints - Upper bound on plotted samples.
 * @returns The plotted points, or `null`.
 */
export function buildElevationProfile(
  detail: SegmentDetail,
  maxPoints = 120,
): ProfilePoint[] | null {
  const { geometry, altitudes } = detail
  if (!altitudes || altitudes.length !== geometry.length || geometry.length < 2) {
    return null
  }
  const distances = cumulativeDistancesMeters(geometry)
  const stride = Math.max(1, Math.ceil(geometry.length / maxPoints))
  const points: ProfilePoint[] = []
  for (let index = 0; index < geometry.length; index += stride) {
    const altitude = altitudes[index]
    const distance = distances[index]
    if (altitude !== undefined && distance !== undefined) {
      points.push({ km: distance / 1000, meters: altitude })
    }
  }
  const lastIndex = altitudes.length - 1
  const lastAltitude = altitudes[lastIndex]
  const lastDistance = distances[lastIndex]
  const lastPlotted = points.length > 0 ? points[points.length - 1] : undefined
  if (
    lastAltitude !== undefined &&
    lastDistance !== undefined &&
    lastPlotted?.meters !== lastAltitude
  ) {
    points.push({ km: lastDistance / 1000, meters: lastAltitude })
  }
  return points
}
