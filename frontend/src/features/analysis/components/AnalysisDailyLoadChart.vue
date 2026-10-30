<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { FitnessPoint, LoadMethod } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'
import { formatMediumDate } from '@/utils/datetime'

const props = defineProps<{
  /** The series points, oldest to newest, as returned by the backend. */
  points: FitnessPoint[]
  /** Which formula the bars measure; TRIMP is noted as a different scale. */
  method: LoadMethod
}>()

const { t, locale } = useI18n()

const LOAD_COLOR = themeColor('--color-brand')

/** Render options: one bar per day, sized in the formula's own units. */
const chartRender = computed<ChartRenderOptions>(() => {
  const labels = props.points.map((point) => formatMediumDate(point.date, locale.value))
  return {
    kind: 'bar',
    series: [
      {
        label: t('analysis.daily.series'),
        data: props.points.map((point) => Math.round(point.load)),
        color: LOAD_COLOR,
      },
    ],
    labels,
    valueFormat: (value) => String(Math.round(value)),
  }
})

useMetricChart(chartRender)
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-3">
    <div class="flex items-center gap-2">
      <span
        class="size-2.5 rounded-full"
        :style="{ backgroundColor: LOAD_COLOR }"
        aria-hidden="true"
      />
      <h3 class="text-card-heading">{{ t('analysis.daily.title') }}</h3>
    </div>
    <div ref="chartContainer" class="relative h-40 w-full min-w-0" />
    <p v-if="method === 'trimp'" class="text-body text-muted-foreground">
      {{ t('analysis.daily.trimpNote') }}
    </p>
  </Card>
</template>
