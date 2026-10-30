<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type { ChartInstance, ChartRenderOptions } from '@/composables/useChartProvider'
import type { SegmentDetail } from '@/features/segments/types'
import type { Schemas } from '@/types'

import { Card } from '@/components/ui/card'
import { useChartProvider } from '@/composables/useChartProvider'
import { useRenderProvidersReady } from '@/providers'
import { formatSegmentElevation } from '@/features/segments/utils/format'
import { buildElevationProfile } from '@/features/segments/utils/profile'

const props = defineProps<{
  segment: SegmentDetail
  /** The viewer's measurement system, for the axis labels. */
  units: Schemas['Units']
}>()

const { t } = useI18n()

const ready = useRenderProvidersReady()
const container = ref<HTMLElement | null>(null)
let instance: ChartInstance | null = null

/** Downsampled altitude series; `null` when the segment carries no elevations. */
const points = computed(() => buildElevationProfile(props.segment))

const render = computed<ChartRenderOptions>(() => {
  const series = points.value ?? []
  return {
    kind: 'area',
    series: [{ label: t('segments.elevation'), data: series.map((point) => point.meters) }],
    labels: series.map((point) => point.km.toFixed(1)),
    xUnit: 'km',
    valueFormat: (value: number) => formatSegmentElevation(value, props.units),
  }
})

/** Mounts the chart once the provider is ready and the container exists. */
function mountChart(): void {
  if (instance || !container.value || !ready.value || !points.value) {
    return
  }
  const { provider } = useChartProvider()
  instance = provider.mount(container.value, render.value)
}

onMounted(mountChart)
watch(ready, mountChart)
watch(render, (options) => {
  if (!instance) {
    mountChart()
    return
  }
  instance.update(options)
})

onBeforeUnmount(() => {
  instance?.destroy()
  instance = null
})
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-3">
    <h3 class="text-card-heading">{{ t('segments.profile.title') }}</h3>
    <p v-if="!points" class="text-caption text-muted-foreground">
      {{ t('segments.profile.unavailable') }}
    </p>
    <div v-else ref="container" class="relative h-56 w-full min-w-0" />
  </Card>
</template>
