import { describe, expect, it } from 'vitest'

import { gradeTrailSection } from '@/features/analysis/utils/trailDegree'

/**
 * The grading exists so the map can show how strongly the detector claims a
 * stretch: the bands are multiples of the threshold the detector itself used, so
 * they move with the model instead of becoming a second, hand-set constant.
 */
describe('gradeTrailSection', () => {
  it('reads a barely-cleared section as light', () => {
    const grade = gradeTrailSection(0.42, 0.38)
    expect(grade.degree).toBe('light')
    expect(grade.weight).toBeLessThan(gradeTrailSection(0.8, 0.38).weight)
  })

  it('grades a firmly-over-threshold section as technical', () => {
    // Between 1.3 and 1.8 times the cut: the Cabdella 1025 sections the athlete
    // called ordinary (0.55/0.57) read one grade below the ones he confirmed.
    expect(gradeTrailSection(0.55, 0.38).degree).toBe('technical')
    expect(gradeTrailSection(0.6, 0.38).degree).toBe('technical')
    expect(gradeTrailSection(0.55, 0.38).colorToken).toBe('--color-effort')
  })

  it('grades the most assertive sections as very technical, in the danger colour', () => {
    const grade = gradeTrailSection(0.9, 0.38)
    expect(grade.degree).toBe('very')
    expect(grade.colorToken).toBe('--color-hr')
    expect(grade.weight).toBe(7)
  })

  it('keeps the same score at the same grade whatever the session spread', () => {
    // A session whose sections all score alike must not invent differences:
    // the grade depends on the score against the threshold, nothing else.
    expect(gradeTrailSection(0.55, 0.38).degree).toBe(gradeTrailSection(0.55, 0.38).degree)
    expect(gradeTrailSection(0.55, 0.38).labelKey).toBe('analysis.trail.degree.technical')
  })

  it('answers light for a zero or missing threshold rather than dividing by it', () => {
    expect(gradeTrailSection(0.83, 0).degree).toBe('light')
  })
})
