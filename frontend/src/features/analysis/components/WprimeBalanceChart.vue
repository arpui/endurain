<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { WprimePoint } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'

const props = defineProps<{
  /** Balance samples, oldest first, as returned by the backend. */
  points: WprimePoint[]
}>()

const { t } = useI18n()

const BALANCE_COLOR = themeColor('--color-effort')

/** Axis label: minutes into the activity, matching the thinned sample spacing. */
function minuteLabel(seconds: number): string {
  return `${Math.round(seconds / 60)}′`
}

/** Render options: W' remaining over elapsed time. */
const chartRender = computed<ChartRenderOptions>(() => {
  const labels = props.points.map((point) => minuteLabel(point.tS))
  return {
    kind: 'line',
    series: [
      {
        label: t('analysis.power.seriesBalance'),
        data: props.points.map((point) => Math.round(point.remainingJ / 100) / 10),
        color: BALANCE_COLOR,
      },
    ],
    labels,
    valueFormat: (value) => `${value} kJ`,
  }
})

useMetricChart(chartRender)
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-3">
    <div class="flex items-center gap-2">
      <span
        class="size-2.5 rounded-full"
        :style="{ backgroundColor: BALANCE_COLOR }"
        aria-hidden="true"
      />
      <h3 class="text-card-heading">{{ t('analysis.power.balanceTitle') }}</h3>
    </div>
    <div ref="chartContainer" class="relative h-48 w-full min-w-0" />
  </Card>
</template>
