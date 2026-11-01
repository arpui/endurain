/** Shared vocabularies of the analysis feature, mirroring the backend's. */

import type { LoadMethod } from '@/features/analysis/types'

/** Load formulas the server can measure with, in display order. */
export const LOAD_METHODS: LoadMethod[] = [
  'coggan_tss',
  'skiba_score',
  'hr_load',
  'trimp',
  'rss_total',
  'rss_up',
  'rss_high',
  'rss_mid',
  'rss_low',
  'daniels_points',
]

/** True for the exponential family's bands, which split one total into areas. */
export function isRssBandMethod(method: LoadMethod): boolean {
  return (
    method === 'rss_up' || method === 'rss_high' || method === 'rss_mid' || method === 'rss_low'
  )
}

/** The sport groups the activities module groups activity types into. */
export const SPORT_GROUPS: string[] = [
  'run',
  'bike',
  'swim',
  'walk',
  'hike',
  'rowing',
  'snow_ski',
  'snowboard',
  'windsurf',
  'stand_up_paddleboarding',
  'surfing',
  'kayaking',
  'sailing',
  'snowshoeing',
  'inline_skating',
]
