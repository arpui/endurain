<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { ActivityLoad } from '../types'

import { useActivityLoadsSummariesQuery } from '../composables/useAnalysis'

/**
 * The score strip of one activity: its aRSS family first (the total stands, the
 * up/mid/low parts read small beside it), then Daniels points, TRIMP and TSS.
 * Owner-only data, fetched in the same batched endpoint the activities list
 * uses; the strip hides itself when nothing was scored.
 */
const props = defineProps<{
  /** The activity whose stored loads are shown; the strip waits for it. */
  activityId: number | null | undefined
}>()

const { t } = useI18n()

const { data } = useActivityLoadsSummariesQuery(() => (props.activityId ? [props.activityId] : []))

const summary = computed(() => (props.activityId ? data.value?.get(props.activityId) : undefined))

function load(method: string): ActivityLoad | undefined {
  return summary.value?.loads[method as keyof typeof summary.value.loads]
}

/** One score as a whole number, or `null` when the formula did not score it. */
function score(loadValue: ActivityLoad | undefined): string | null {
  return loadValue ? String(Math.round(loadValue.load)) : null
}

const rss = computed(() => load('rss_total'))
const daniels = computed(() => load('daniels_points'))
const trimp = computed(() => load('trimp'))
const tss = computed(() => load('coggan_tss'))

/** The three aRSS parts, as one small "↑ 16 ↔ 43 ↓ 6" tail. */
const rssParts = computed(() => {
  const parts: Array<[string, ActivityLoad | undefined]> = [
    ['↑', load('rss_up')],
    ['↔', load('rss_mid')],
    ['↓', load('rss_low')],
  ]
  return parts
    .filter(([, value]) => value)
    .map(([symbol, value]) => `${symbol} ${score(value)}`)
    .join(' ')
})

const hasScores = computed(() => !!(rss.value || daniels.value || trimp.value || tss.value))

/**
 * Tooltip telling how a score was derived: its intensity factor and the
 * threshold it was divided by, when the row carries them.
 *
 * @param loadValue - The stored load to describe.
 * @returns A one-line explanation, or `undefined` when there is nothing to say.
 */
function tooltip(loadValue: ActivityLoad | undefined): string | undefined {
  if (!loadValue || (loadValue.intensityFactor === null && loadValue.thresholdUsed === null)) {
    return undefined
  }
  const bits: string[] = []
  if (loadValue.intensityFactor !== null) {
    bits.push(`IF ${loadValue.intensityFactor.toFixed(2)}`)
  }
  if (loadValue.thresholdUsed !== null) {
    bits.push(`CP ${Math.round(loadValue.thresholdUsed)} W`)
  }
  return bits.join(' · ')
}
</script>

<template>
  <div
    v-if="summary && hasScores"
    class="flex flex-wrap items-baseline gap-x-5 gap-y-1 pt-3 text-sm"
  >
    <span v-if="rss" class="flex items-baseline gap-1.5" :title="tooltip(rss)">
      <span class="text-caption text-muted-foreground">{{ t('analysis.scores.rss') }}</span>
      <span class="font-semibold">{{ score(rss) }}</span>
      <span v-if="rssParts" class="text-caption text-muted-foreground">{{ rssParts }}</span>
    </span>
    <span v-if="daniels" class="flex items-baseline gap-1.5" :title="tooltip(daniels)">
      <span class="text-caption text-muted-foreground">{{ t('analysis.scores.daniels') }}</span>
      <span class="font-semibold">{{ score(daniels) }}</span>
    </span>
    <span v-if="trimp" class="flex items-baseline gap-1.5" :title="tooltip(trimp)">
      <span class="text-caption text-muted-foreground">{{ t('analysis.scores.trimp') }}</span>
      <span class="font-semibold">{{ score(trimp) }}</span>
    </span>
    <span v-if="tss" class="flex items-baseline gap-1.5" :title="tooltip(tss)">
      <span class="text-caption text-muted-foreground">{{ t('analysis.scores.tss') }}</span>
      <span class="font-semibold">{{ score(tss) }}</span>
      <span v-if="summary.powerRecalibrated" class="text-caption text-muted-foreground">*</span>
    </span>
  </div>
</template>
