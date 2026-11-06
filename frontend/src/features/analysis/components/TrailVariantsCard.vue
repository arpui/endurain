<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Check, Route as RouteIcon, X } from '@lucide/vue'

import type { TrailVariant } from '@/features/analysis/types'

defineProps<{
  /** The variant analysis payload. */
  variants: TrailVariant[]
  /** The junction the viewer picked, to mirror on the map. */
  selected: number | null
}>()

const emit = defineEmits<{
  /** The viewer picked a variant: the map should draw it. */
  select: [variant: TrailVariant]
  /** Judge a variant: real ground, or a suggestion to bury (``null`` undoes). */
  mark: [splitM: number, verdict: 'real' | 'falsa' | null]
  /** Save this activity's track as a designed route. */
  saveRoute: []
}>()

const { t, locale } = useI18n()

const MARKS: { verdict: 'real' | 'falsa'; icon: typeof Check }[] = [
  { verdict: 'real', icon: Check },
  { verdict: 'falsa', icon: X },
]

const km = computed(
  () => (m: number) =>
    new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }).format(m / 1000),
)
</script>

<template>
  <Card class="p-3">
    <div class="mb-2 flex items-center justify-between gap-2">
      <h3 class="flex items-center gap-2 text-card-heading">
        {{ t('analysis.variants.title') }}
        <Badge variant="secondary">{{ t('analysis.trail.experimental') }}</Badge>
      </h3>
      <Button size="sm" variant="outline" @click="emit('saveRoute')">
        <RouteIcon class="me-1 size-4" />
        {{ t('analysis.variants.saveAsRoute') }}
      </Button>
    </div>

    <div v-if="variants.length === 0" class="text-caption text-muted-foreground">
      {{ t('analysis.variants.none') }}
    </div>
    <div v-else class="overflow-x-auto">
      <table class="w-full min-w-[38rem] border-collapse text-meta">
        <thead>
          <tr class="text-caption border-b border-border text-left">
            <th class="py-2 pe-3 font-medium">{{ t('analysis.variants.colJunction') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.variants.colSide') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.variants.colDelta') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.variants.colGain') }}</th>
            <th class="py-2 pe-3 font-medium">{{ t('analysis.variants.colStatus') }}</th>
            <th class="py-2 font-medium">{{ t('analysis.variants.colMark') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="variant in variants"
            :key="variant.splitM"
            class="cursor-pointer border-b border-border last:border-0 hover:bg-muted/50"
            :class="{ 'bg-muted/60': selected === variant.splitM }"
            @click="emit('select', variant)"
          >
            <td class="py-2 pe-3 tabular-nums">
              {{ km(variant.splitM) }} → {{ km(variant.rejoinM) }} km
            </td>
            <td class="py-2 pe-3">
              {{
                variant.side === 'left'
                  ? t('analysis.variants.side.left')
                  : variant.side === 'right'
                    ? t('analysis.variants.side.right')
                    : t('analysis.variants.side.straight')
              }}
            </td>
            <td class="py-2 pe-3 tabular-nums">
              {{ variant.deltaM > 0 ? '+' : '' }}{{ variant.deltaM }} m
            </td>
            <td class="py-2 pe-3 tabular-nums">
              {{ variant.deltaGainM > 0 ? '+' : '' }}{{ variant.deltaGainM }} m
            </td>
            <td class="py-2 pe-3">
              <Badge :variant="variant.status === 'confirmada' ? 'default' : 'secondary'">
                {{ t(`analysis.variants.status.${variant.status}`) }}
              </Badge>
              <span v-if="variant.grade" class="ms-1 text-caption text-muted-foreground">
                {{
                  variant.grade === 'hard'
                    ? t('analysis.variants.grade.hard')
                    : t('analysis.variants.grade.tech')
                }}
              </span>
            </td>
            <td class="py-2">
              <div class="flex items-center gap-1">
                <button
                  v-for="option in MARKS"
                  :key="option.verdict"
                  type="button"
                  class="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  :class="{ 'bg-muted text-foreground': variant.mark === option.verdict }"
                  :aria-pressed="variant.mark === option.verdict"
                  :aria-label="t(`analysis.variants.mark.${option.verdict}`)"
                  :title="t(`analysis.variants.mark.${option.verdict}`)"
                  @click.stop="
                    emit(
                      'mark',
                      variant.splitM,
                      variant.mark === option.verdict ? null : option.verdict,
                    )
                  "
                >
                  <component :is="option.icon" class="size-4" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="mt-2 text-caption text-muted-foreground">{{ t('analysis.variants.hint') }}</p>
  </Card>
</template>
