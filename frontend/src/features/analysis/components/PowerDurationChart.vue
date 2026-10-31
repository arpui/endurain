<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { DurationPeak } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'
import { formatDurationLabel } from '@/features/analysis/utils/format'

const props = defineProps<{
  /** Curve points, shortest duration first, as returned by the backend. */
  points: DurationPeak[]
}>()

const { t } = useI18n()

const CURVE_COLOR = themeColor('--color-brand-mid')

/** Render options: the best-average curve over the stored duration ladder. */
const chartRender = computed<ChartRenderOptions>(() => {
  const labels = props.points.map((point) => formatDurationLabel(point.durationS))
  return {
    kind: 'line',
    series: [
      {
        label: t('analysis.power.seriesCurve'),
        data: props.points.map((point) => Math.round(point.value)),
        color: CURVE_COLOR,
      },
    ],
    labels,
    valueFormat: (value) => `${Math.round(value)} W`,
  }
})

useMetricChart(chartRender)
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-3">
    <div class="flex items-center gap-2">
      <span
        class="size-2.5 rounded-full"
        :style="{ backgroundColor: CURVE_COLOR }"
        aria-hidden="true"
      />
      <h3 class="text-card-heading">{{ t('analysis.power.chartTitle') }}</h3>
    </div>
    <div ref="chartContainer" class="relative h-64 w-full min-w-0" />
  </Card>
</template>
