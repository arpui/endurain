import { describe, expect, it } from 'vitest'

import type { TrailSection } from '@/features/analysis/types'

import { gradeTrailSection, pointsWithin } from '@/features/analysis/utils/trailDegree'

const RATIOS = { threshold: 0.38, technicalRatio: 1.3, hardRatio: 1.8 }

/** Build a measured section, overriding only what a case cares about. */
function section(overrides: Partial<TrailSection> = {}): TrailSection {
  return {
    startM: 5000,
    lengthM: 200,
    gradePct: -22,
    avgSpeedMs: 1.3,
    gapDeficit: 0.7,
    cadenceJitter: 0.45,
    brakeScore: 0.9,
    score: 0.83,
    coreStartM: null,
    coreEndM: null,
    coreScore: null,
    direction: 'descent',
    points: [],
    osm: null,
    ...overrides,
  }
}

describe('gradeTrailSection', () => {
  it('reads a barely-cleared section as light', () => {
    expect(gradeTrailSection(section({ score: 0.42 }), RATIOS).degree).toBe('light')
  })

  it('grades a firmly-over-threshold section as technical', () => {
    const grade = gradeTrailSection(section({ score: 0.55 }), RATIOS)
    expect(grade.degree).toBe('technical')
    expect(grade.colorToken).toBe('--color-effort')
  })

  it('grades a short stretch whose whole length is hard as very technical', () => {
    const short = section({
      lengthM: 150,
      score: 0.83,
      coreStartM: 5000,
      coreEndM: 5140,
      coreScore: 0.83,
    })
    const grade = gradeTrailSection(short, RATIOS)
    expect(grade.degree).toBe('very')
    expect(grade.colorToken).toBe('--color-hr')
  })

  it('does not call a long stretch extreme because a block inside it is', () => {
    // the athlete's own objection: 1,5 km of mixed ground cannot be "very technical"
    const long = section({
      lengthM: 1625,
      score: 0.87,
      coreStartM: 5600,
      coreEndM: 5850,
      coreScore: 0.91,
    })
    const grade = gradeTrailSection(long, RATIOS)
    expect(grade.degree).toBe('mixed')
    expect(grade.colorToken).toBe('--color-effort')
    expect(grade.labelKey).toBe('analysis.trail.degree.mixed')
  })

  it('answers light for a zero threshold rather than dividing by it', () => {
    expect(gradeTrailSection(section({ score: 0.83 }), { ...RATIOS, threshold: 0 }).degree).toBe(
      'light',
    )
  })
})

describe('pointsWithin', () => {
  const line = [
    { lat: 42.47, lng: 0.99 },
    { lat: 42.47 + 100 / 111195, lng: 0.99 },
    { lat: 42.47 + 200 / 111195, lng: 0.99 },
    { lat: 42.47 + 300 / 111195, lng: 0.99 },
  ]

  it('keeps the points of the requested ground range', () => {
    const inside = pointsWithin(line, 300, 90, 210)
    expect(inside).toEqual([line[1], line[2]])
  })

  it('falls back to the whole polyline when the range selects nothing', () => {
    expect(pointsWithin(line, 300, 500, 600)).toEqual(line)
  })

  it('returns the points untouched for a degenerate polyline', () => {
    expect(pointsWithin([line[0] as (typeof line)[0]], 300, 0, 100)).toEqual([line[0]])
  })
})
