<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { TrailSections, TrailSectionDirection } from '@/features/analysis/types'
import type { Units } from '@/features/activities/utils/format'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { combineMetric, formatDistance } from '@/features/activities/utils/format'

const props = defineProps<{
  /** The technical-terrain pass the server computed for this activity. */
  sections: TrailSections
  /** Numeric activity type; decides pace-vs-speed and distance units. */
  activityType: number
  units: Units
}>()

const { t } = useI18n()

/** Technical share of the ground covered, for the summary line. */
const technicalPct = computed(() =>
  props.sections.totalM > 0
    ? Math.round((props.sections.technicalM / props.sections.totalM) * 100)
    : 0,
)

/** Direction label reusing the interval wording where it exists. */
function directionLabel(direction: TrailSectionDirection): string {
  return t(`analysis.trail.directions.${direction}`)
}

/** Kilometre marker where the stretch starts, matching the map overlays. */
function startKm(startM: number): string {
  return `${(startM / 1000).toFixed(2)} km`
}

const rows = computed(() =>
  props.sections.sections.map((section, index) => ({
    key: `${section.startM}-${index}`,
    start: startKm(section.startM),
    length: combineMetric(formatDistance(section.lengthM, props.activityType, props.units)),
    grade: `${section.gradePct.toFixed(1)} %`,
    direction: directionLabel(section.direction),
    score: section.score.toFixed(2),
  })),
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
        <table class="w-full min-w-[26rem] border-collapse text-meta">
          <thead>
            <tr class="text-caption border-b border-border text-left">
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colStart') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colLength') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colDirection') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.trail.colGrade') }}</th>
              <th class="py-2 font-medium">{{ t('analysis.trail.colScore') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.key" class="border-b border-border/60">
              <td class="py-2 pe-3">{{ row.start }}</td>
              <td class="py-2 pe-3">{{ row.length }}</td>
              <td class="py-2 pe-3">{{ row.direction }}</td>
              <td class="py-2 pe-3">{{ row.grade }}</td>
              <td class="py-2">{{ row.score }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-caption text-muted-foreground">{{ t('analysis.trail.hint') }}</p>
    </div>
  </Card>
</template>
