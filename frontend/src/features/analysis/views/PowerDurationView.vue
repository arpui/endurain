<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'

import type { WprimePoint } from '@/features/analysis/types'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  usePowerDurationQuery,
  useWprimeBalanceQuery,
} from '@/features/analysis/composables/useAnalysis'
import { SPORT_GROUPS } from '@/features/analysis/constants'
import { formatClock, formatDurationLabel } from '@/features/analysis/utils/format'
import { formatMediumDate } from '@/utils/datetime'
import PowerDurationChart from '@/features/analysis/components/PowerDurationChart.vue'
import WprimeBalanceChart from '@/features/analysis/components/WprimeBalanceChart.vue'

const { t, locale } = useI18n()
const route = useRoute()

const sport = ref('run')
const fitFrom = ref('')
const fitTo = ref('')

/** Preview window typed into the form; empty ends keep the stored defaults. */
const previewWindow = computed(() => ({
  fitFromS: fitFrom.value === '' ? null : Number(fitFrom.value),
  fitToS: fitTo.value === '' ? null : Number(fitTo.value),
}))

const durationQuery = usePowerDurationQuery(sport, previewWindow)

const curve = computed(() => durationQuery.data.value ?? null)
const hasCurve = computed(() => (curve.value?.points.length ?? 0) > 0)

/** The threshold the sport's loads actually use; the fit answers when none is stored. */
const effectiveCpW = computed(
  () => curve.value?.threshold?.valueW ?? curve.value?.fit?.criticalPowerW ?? null,
)
const effectiveWPrimeJ = computed(
  () => curve.value?.threshold?.wPrimeJ ?? curve.value?.fit?.wPrimeJ ?? null,
)
const fitCvPct = computed(() => curve.value?.fit?.cvPct ?? curve.value?.threshold?.cvPct ?? null)

const selectedActivityId = ref<number | null>(null)
const balanceQuery = useWprimeBalanceQuery(selectedActivityId)

/** Keeps a valid selection as sessions load or the sport changes. */
function onSessionsChange(activityId: string): void {
  selectedActivityId.value = activityId === '' ? null : Number(activityId)
}

const balancePoints = computed<WprimePoint[]>(() => balanceQuery.data.value?.points ?? [])
const balance = computed(() => balanceQuery.data.value ?? null)

/** Session labels: name (date), newest first as the server sends them. */
const sessionOptions = computed(() =>
  (curve.value?.sessions ?? []).map((session) => ({
    activityId: session.activityId,
    label: `${session.name || `#${session.activityId}`}${
      session.startTime ? ` · ${formatMediumDate(session.startTime, locale.value)}` : ''
    }`,
  })),
)
</script>

<template>
  <section class="flex flex-col gap-3">
    <header class="flex flex-col gap-1">
      <h1 class="text-page-title">{{ t('analysis.title') }}</h1>
      <p class="text-body">{{ t('analysis.power.subtitle') }}</p>
    </header>

    <nav class="flex gap-1" :aria-label="t('analysis.sections')">
      <RouterLink v-slot="{ href, navigate }" :to="{ name: 'analysis' }" custom>
        <Button
          as="a"
          :href="href"
          :variant="route.name === 'analysis' ? 'default' : 'ghost'"
          @click="navigate"
        >
          {{ t('analysis.sectionLoad') }}
        </Button>
      </RouterLink>
      <RouterLink v-slot="{ href, navigate }" :to="{ name: 'analysis-power' }" custom>
        <Button
          as="a"
          :href="href"
          :variant="route.name === 'analysis-power' ? 'default' : 'ghost'"
          @click="navigate"
        >
          {{ t('analysis.sectionPower') }}
        </Button>
      </RouterLink>
    </nav>

    <div class="flex flex-wrap items-end gap-2">
      <Select v-model="sport" :aria-label="t('analysis.sport.label')" class="w-40">
        <option v-for="option in SPORT_GROUPS" :key="option" :value="option">
          {{ t(`home.sports.${option}`) }}
        </option>
      </Select>
      <FormField :label="t('analysis.settings.fitFromS')" class="w-32">
        <template #default="{ fieldId }">
          <Input
            :id="fieldId"
            v-model="fitFrom"
            type="number"
            min="10"
            max="7200"
            :placeholder="t('analysis.power.previewDefault')"
          />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.fitToS')" class="w-32">
        <template #default="{ fieldId }">
          <Input
            :id="fieldId"
            v-model="fitTo"
            type="number"
            min="30"
            max="21600"
            :placeholder="t('analysis.power.previewDefault')"
          />
        </template>
      </FormField>
    </div>

    <ErrorState v-if="durationQuery.isError.value" :title="t('analysis.loadFailed')" />
    <Skeleton v-else-if="durationQuery.isPending.value" class="h-64 w-full" />
    <EmptyState
      v-else-if="curve === null || !hasCurve"
      :title="t('analysis.power.empty.title')"
      :description="t('analysis.power.empty.description')"
    />
    <template v-else>
      <Card class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div class="flex flex-col gap-1">
          <span class="text-body text-muted-foreground">{{ t('analysis.power.cp') }}</span>
          <span class="text-card-heading tabular-nums">
            {{ effectiveCpW === null ? '—' : `${Math.round(effectiveCpW)} W` }}
          </span>
          <span v-if="curve && effectiveCpW !== null" class="text-xs text-muted-foreground">
            {{
              curve.threshold?.source === 'manual'
                ? t('analysis.settings.thresholds.sourceManual')
                : t('analysis.settings.thresholds.sourceAuto')
            }}
          </span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-body text-muted-foreground">{{ t('analysis.power.wprime') }}</span>
          <span class="text-card-heading tabular-nums">
            {{ effectiveWPrimeJ === null ? '—' : `${Math.round(effectiveWPrimeJ / 100) / 10} kJ` }}
          </span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-body text-muted-foreground">{{ t('analysis.power.cv') }}</span>
          <span class="text-card-heading tabular-nums">
            {{ fitCvPct === null ? '—' : `${fitCvPct}%` }}
          </span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-body text-muted-foreground">{{ t('analysis.power.window') }}</span>
          <span class="text-card-heading tabular-nums">
            {{
              curve.fit
                ? `${formatDurationLabel(curve.fit.fitFromS)} – ${formatDurationLabel(curve.fit.fitToS)}`
                : '—'
            }}
          </span>
        </div>
      </Card>

      <PowerDurationChart :points="curve.points" :overlays="curve.overlays" />

      <Card class="flex min-w-0 flex-col gap-3">
        <h3 class="text-card-heading">{{ t('analysis.power.balanceHeader') }}</h3>
        <div class="flex flex-wrap items-end gap-2">
          <Select
            :aria-label="t('analysis.power.sessionLabel')"
            class="w-full sm:w-72"
            :model-value="selectedActivityId === null ? '' : String(selectedActivityId)"
            @update:model-value="onSessionsChange"
          >
            <option value="">{{ t('analysis.power.sessionPlaceholder') }}</option>
            <option
              v-for="session in sessionOptions"
              :key="session.activityId"
              :value="String(session.activityId)"
            >
              {{ session.label }}
            </option>
          </Select>
        </div>

        <template v-if="selectedActivityId !== null">
          <div v-if="balanceQuery.isPending.value"><Skeleton class="h-8 w-full" /></div>
          <ErrorState v-else-if="balanceQuery.isError.value" :title="t('analysis.loadFailed')" />
          <template v-else-if="balance">
            <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <div class="flex flex-col gap-1">
                <span class="text-body text-muted-foreground">{{
                  t('analysis.power.workAboveCp')
                }}</span>
                <span class="text-card-heading tabular-nums"
                  >{{ Math.round(balance.workAboveCpJ / 1000) }} kJ</span
                >
              </div>
              <div class="flex flex-col gap-1">
                <span class="text-body text-muted-foreground">{{
                  t('analysis.power.timeAboveCp')
                }}</span>
                <span class="text-card-heading tabular-nums">{{
                  formatClock(balance.timeAboveCpS)
                }}</span>
              </div>
              <div class="flex flex-col gap-1">
                <span class="text-body text-muted-foreground">{{ t('analysis.power.tau') }}</span>
                <span class="text-card-heading tabular-nums">
                  {{ balance.tauS === null ? '—' : formatClock(balance.tauS) }}
                </span>
              </div>
            </div>
            <WprimeBalanceChart v-if="balancePoints.length > 0" :points="balancePoints" />
            <p v-else class="text-body text-muted-foreground">
              {{ t('analysis.power.balanceEmpty') }}
            </p>
          </template>
        </template>
        <p v-else class="text-body text-muted-foreground">{{ t('analysis.power.balanceHint') }}</p>
      </Card>
    </template>
  </section>
</template>
