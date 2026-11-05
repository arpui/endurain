<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { TrailSection, TrailSections } from '@/features/analysis/types'
import type { Units } from '@/features/activities/utils/format'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { combineMetric, formatDistance } from '@/features/activities/utils/format'
import { gradeTrailSection } from '@/features/analysis/utils/trailDegree'
import { themeColor } from '@/lib/themeColor'

const props = defineProps<{
  /** The technical-terrain pass the server computed for this activity. */
  sections: TrailSections
  /** Numeric activity type; decides pace-vs-speed and distance units. */
  activityType: number
  units: Units
  /** Section picked on the map (or the table): its row reads selected. */
  selected?: number | null
}>()

const emit = defineEmits<{
  /** The viewer picked a row: the map should travel to that section. */
  select: [section: TrailSection]
}>()

const { t } = useI18n()

/** Technical share of the ground covered, for the summary line. */
const technicalPct = computed(() =>
  props.sections.totalM > 0
    ? Math.round((props.sections.technicalM / props.sections.totalM) * 100)
    : 0,
)

/** Where inside a long stretch the hard ground actually is. */
function coreLabel(section: TrailSection): string {
  if (section.coreScore === null || section.coreStartM === null || section.coreEndM === null) {
    return ''
  }
  return t('analysis.trail.core', {
    score: section.coreScore.toFixed(2),
    range: range(section.coreStartM, section.coreEndM - section.coreStartM),
  })
}

/** Kilometre range a section covers, in the units the rest of the page uses. */
function range(startM: number, lengthM: number): string {
  return `${combineMetric(formatDistance(startM, props.activityType, props.units))}–${combineMetric(
    formatDistance(startM + lengthM, props.activityType, props.units),
  )}`
}

/**
 * What the cartography claims about the same ground, shown as it claims it.
 *
 * Deliberately raw: these are OpenStreetMap's own vocabularies (``T3``, ``rock``,
 * ``grade4``), and translating them into our words would quietly invent the
 * correspondence between the two scales that the calibration is meant to measure.
 */
function mapClaim(osm: TrailSection['osm']): string {
  if (!osm) {
    return t('analysis.trail.mapMissing')
  }
  const parts = [osm.sacScale, osm.mtbScale, osm.surface, osm.tracktype, osm.smoothness]
    .filter(Boolean)
    .slice(0, 2)
  return parts.length > 0 ? parts.join(' · ') : (osm.name ?? `OSM ${osm.wayId}`)
}

/** Every claim the map makes about the stretch, for the row's tooltip. */
function mapDetail(osm: TrailSection['osm']): string {
  if (!osm) {
    return t('analysis.trail.mapMissingHint')
  }
  const pairs = [
    ['sac_scale', osm.sacScale],
    ['mtb:scale', osm.mtbScale],
    ['surface', osm.surface],
    ['smoothness', osm.smoothness],
    ['tracktype', osm.tracktype],
    ['trail_visibility', osm.trailVisibility],
    ['width', osm.width],
    ['incline', osm.incline],
  ].filter((pair): pair is [string, string] => pair[1] !== null)
  const where = `${osm.startM.toFixed(0)}–${osm.endM.toFixed(0)} m`
  return [pairs.map(([key, value]) => `${key}=${value}`).join(' · '), where, osm.cacheDate]
    .filter(Boolean)
    .join('\n')
}

/**
 * Rows ordered by how assertively the detector claims them, while each row
 * keeps the number of its ground order — the number the map draws beside it.
 */
const rows = computed(() =>
  props.sections.sections
    .map((section, index) => ({
      section,
      /** Shared with the map overlay label, so a judgement is one character. */
      number: index + 1,
      grade: gradeTrailSection(section, {
        threshold: props.sections.threshold,
        technicalRatio: props.sections.technicalRatio,
        hardRatio: props.sections.hardRatio,
        veryMaxLengthM: props.sections.veryMaxLengthM,
      }),
    }))
    .sort((left, right) => right.section.score - left.section.score),
)
</script>

<template>
  <Card class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-card-heading">{{ t('analysis.trail.title') }}</h2>
      <Badge variant="secondary">{{ t('analysis.trail.experimental') }}</Badge>
    </div>

    <div class="flex flex-col gap-3">
      <p class="text-meta text-muted-foreground">
        {{
          t('analysis.trail.summary', {
            technical: technicalPct,
            metres: Math.round(sections.technicalM),
            of: Math.round(sections.totalM),
          })
        }}
      </p>

      <div class="overflow-x-auto">
        <table class="w-full min-w-[34rem] border-collapse text-meta">
          <thead>
            <tr class="text-caption border-b border-border text-left">
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colTram') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colRange') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colDegree') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colDirection') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colGrade') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colMap') }}</th>
              <th class="py-2 font-medium">{{ t('analysis.trail.colScore') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rows"
              :key="row.number"
              class="cursor-pointer border-b border-border/60 hover:bg-muted/50"
              :class="{ 'bg-muted/60': selected === row.section.startM }"
              @click="emit('select', row.section)"
            >
              <td class="py-2 pe-3 font-medium">#{{ row.number }}</td>
              <td class="py-2 pe-3">{{ range(row.section.startM, row.section.lengthM) }}</td>
              <td class="py-2 pe-3">
                <span class="flex items-center gap-2">
                  <span
                    class="inline-block size-2.5 rounded-full"
                    :style="{ backgroundColor: themeColor(row.grade.colorToken) }"
                    aria-hidden="true"
                  />
                  <span>
                    {{ t(row.grade.labelKey) }}
                    <span
                      v-if="coreLabel(row.section)"
                      class="block text-caption text-muted-foreground"
                    >
                      {{ coreLabel(row.section) }}
                    </span>
                  </span>
                </span>
              </td>
              <td class="py-2 pe-3">
                {{ t(`analysis.trail.directions.${row.section.direction}`) }}
              </td>
              <td class="py-2 pe-3">{{ row.section.gradePct.toFixed(1) }} %</td>
              <td class="py-2 pe-3 text-muted-foreground" :title="mapDetail(row.section.osm)">
                {{ mapClaim(row.section.osm) }}
              </td>
              <td class="py-2">{{ row.section.score.toFixed(2) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-caption text-muted-foreground">
        {{ t('analysis.trail.hint') }} {{ t('analysis.trail.clickHint') }}
      </p>
    </div>
  </Card>
</template>
