<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import type { SegmentEffort } from '@/features/segments/types'
import type { Schemas } from '@/types'

import { Badge } from '@/components/ui/badge'
import { formatMediumDateTime } from '@/utils/datetime'
import { formatLapTempo } from '@/features/activities/utils/metrics'
import { formatEffortDuration } from '@/features/segments/utils/format'

const props = defineProps<{
  /** The viewer's efforts on one segment, fastest first as the server returns them. */
  efforts: SegmentEffort[]
  units: Schemas['Units']
}>()

const { t, locale } = useI18n()

interface EffortRow {
  key: string
  activityId: number
  /** 1-based rank among the listed efforts (the page is ordered fastest first). */
  rank: number
  date: string
  time: string
  tempo: string
  power: string
  hr: string
  rssMin: string
  isReverse: boolean
  isBest: boolean
}

/**
 * Rank is positional, not stored: the server orders the page fastest first, so
 * the first forward row is the viewer's best time on the segment. When reverse
 * passes are included they are numbered among themselves and labelled, because a
 * pass against the stored direction is not a rankable effort.
 */
const rows = computed<EffortRow[]>(() => {
  let forwardRank = 0
  let reverseRank = 0
  return props.efforts.map((effort) => {
    const isReverse = effort.direction === 'reverse'
    if (isReverse) {
      reverseRank += 1
    } else {
      forwardRank += 1
    }
    return {
      key: `${effort.activityId}-${effort.direction}-${effort.passNumber}`,
      activityId: effort.activityId,
      rank: isReverse ? reverseRank : forwardRank,
      date: formatMediumDateTime(effort.timeStart, locale.value),
      time: formatEffortDuration(effort.elapsedSeconds),
      // The same tempo the laps and intervals tables show: pace for sports that
      // read in pace, speed for the rest.
      tempo:
        effort.activityType === null
          ? '--'
          : formatLapTempo(
              effort.avgSpeedMps && effort.avgSpeedMps > 0 ? 1 / effort.avgSpeedMps : null,
              effort.avgSpeedMps,
              effort.activityType,
              props.units,
            ),
      power: effort.avgPowerW === null ? '--' : `${Math.round(effort.avgPowerW)} W`,
      hr: effort.avgHrBpm === null ? '--' : `${Math.round(effort.avgHrBpm)} bpm`,
      rssMin: effort.rssPerMin === null ? '--' : effort.rssPerMin.toFixed(2),
      isReverse,
      isBest: !isReverse && forwardRank === 1,
    }
  })
})
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[38rem] border-collapse text-meta">
      <thead>
        <tr class="text-caption border-b border-border text-left">
          <th class="py-2 pe-3 font-medium">{{ t('segments.efforts.rank') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('segments.efforts.activity') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('segments.efforts.date') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('segments.efforts.time') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('activities.laps.tempo') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('analysis.laps.power') }}</th>
          <th class="py-2 pe-3 font-medium">{{ t('activities.laps.hr') }}</th>
          <th class="py-2 font-medium">{{ t('analysis.laps.rssMin') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.key"
          class="border-b border-border/60 last:border-0"
          :class="row.isReverse ? 'text-muted-foreground opacity-60' : ''"
        >
          <td class="py-2 pe-3 font-medium text-foreground">
            {{ row.rank }}
            <Badge v-if="row.isBest" class="ms-2" variant="secondary">
              {{ t('segments.efforts.best') }}
            </Badge>
          </td>
          <td class="py-2 pe-3">
            <RouterLink
              class="text-brand underline-offset-4 hover:underline"
              :to="{ name: 'activity', params: { id: row.activityId } }"
            >
              {{ t('segments.efforts.activityLabel', { id: row.activityId }) }}
            </RouterLink>
            <span v-if="row.isReverse" class="text-caption ms-2 text-muted-foreground">
              {{ t('segments.efforts.reverse') }}
            </span>
          </td>
          <td class="py-2 pe-3 text-muted-foreground">{{ row.date }}</td>
          <td class="py-2 pe-3 font-medium text-foreground">{{ row.time }}</td>
          <td class="py-2 pe-3 text-foreground">{{ row.tempo }}</td>
          <td class="py-2 pe-3 tabular-nums text-foreground">{{ row.power }}</td>
          <td class="py-2 pe-3 tabular-nums text-muted-foreground">{{ row.hr }}</td>
          <td class="py-2 tabular-nums text-muted-foreground">{{ row.rssMin }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
