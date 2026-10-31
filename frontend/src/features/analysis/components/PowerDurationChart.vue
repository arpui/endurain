<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { DurationPeak, PowerDurationOverlay } from '@/features/analysis/types'
import type { ChartRenderOptions } from '@/composables/useChartProvider'

import { themeColor, themeColorAlpha } from '@/lib/themeColor'
import { Card } from '@/components/ui/card'
import { useMetricChart } from '@/composables/useMetricChart'
import { formatDurationLabel } from '@/features/analysis/utils/format'

const props = defineProps<{
  /** Curve points, shortest duration first, as returned by the backend. */
  points: DurationPeak[]
  /** Rolling recent windows overlaid on the record, largest first by the caller. */
  overlays?: PowerDurationOverlay[]
}>()

const { t } = useI18n()

const CURVE_COLOR = themeColor('--color-brand-mid')

/**
 * Translucent shades of the curve's own colour: the record stays solid, the
 * recent windows read as its history without fighting for attention.
 */
const OVERLAY_ALPHAS: Record<number, number> = { 6: 0.5, 3: 0.75 }

/** Every duration any series uses, shortest first: one shared label ladder. */
const durations = computed(() => {
  const found = new Set<number>(props.points.map((point) => point.durationS))
  for (const overlay of props.overlays ?? []) {
    for (const point of overlay.points) {
      found.add(point.durationS)
    }
  }
  return [...found].sort((a, b) => a - b)
})

/** Align one curve to the shared ladder; missing durations break the line. */
function align(points: DurationPeak[]): number[] {
  const byDuration = new Map(points.map((point) => [point.durationS, Math.round(point.value)]))
  return durations.value.map((duration) => byDuration.get(duration) ?? Number.NaN)
}

/** Render options: the best-average curve, its record, with recent windows on top. */
const chartRender = computed<ChartRenderOptions>(() => {
  const series = [
    {
      label: t('analysis.power.seriesCurve'),
      data: align(props.points),
      color: CURVE_COLOR,
    },
    ...(props.overlays ?? []).map((overlay) => ({
      label: t('analysis.power.overlayName', { months: overlay.months }),
      data: align(overlay.points),
      color: themeColorAlpha('--color-brand-mid', OVERLAY_ALPHAS[overlay.months] ?? 0.5),
      dashed: true,
    })),
  ]
  return {
    kind: 'line',
    series,
    labels: durations.value.map((duration) => formatDurationLabel(duration)),
    valueFormat: (value) => `${Math.round(value)} W`,
  }
})

/** One caption naming each window's own CP: the point of the overlays is to
 *  watch the threshold itself move. */
const overlayCpNote = computed(() => {
  const parts = (props.overlays ?? [])
    .filter((overlay) => overlay.fit !== null)
    .map(
      (overlay) =>
        `${t('analysis.power.overlayName', { months: overlay.months })} ${Math.round(
          overlay.fit?.criticalPowerW ?? 0,
        )} W`,
    )
  return parts.length > 0 ? parts.join(' · ') : null
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
    <p v-if="overlayCpNote" class="text-caption text-muted-foreground">
      {{ t('analysis.power.overlayCp', { windows: overlayCpNote }) }}
    </p>
  </Card>
</template>
