<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'

import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { FormField } from '@/components/ui/form-field'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import AnalysisSectionsNav from '@/features/analysis/components/AnalysisSectionsNav.vue'
import { useBestTimesQuery } from '@/features/analysis/composables/useAnalysis'
import { formatHmsDuration } from '@/features/activities/utils/format'

const { t, locale } = useI18n()

/** Windows offered next to the table; ``null`` means the whole history. */
const PERIODS = [30, 60, 90, 180, 365, null] as const

const period = ref<string>(String(365))
const months = computed<number | null>(() => (period.value === '' ? null : Number(period.value)))

const bestTimesQuery = useBestTimesQuery(months)
const table = computed(() => bestTimesQuery.data.value ?? null)

/** Canonical label of one distance: metres below a kilometre, kilometres above. */
function distanceLabel(distanceM: number): string {
  if (distanceM < 1000) {
    return `${distanceM} m`
  }
  const km = distanceM === 21097 ? 21.1 : distanceM / 1000
  const value = new Intl.NumberFormat(locale.value, { maximumFractionDigits: 1 }).format(km)
  return `${value} km`
}

/** Day of the activity that holds the split, in the reader's locale. */
function dayLabel(startIso: string | null): string {
  if (startIso === null) {
    return '--'
  }
  return new Date(startIso).toLocaleDateString(locale.value)
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <h1 class="text-page-title">{{ t('analysis.bestTimes.title') }}</h1>
      <p class="text-body">{{ t('analysis.bestTimes.subtitle') }}</p>
    </header>

    <AnalysisSectionsNav />

    <div class="flex flex-wrap items-end gap-2">
      <FormField :label="t('analysis.bestTimes.period.label')" class="w-40">
        <template #default="{ fieldId }">
          <Select :id="fieldId" v-model="period" class="w-40">
            <option v-for="option in PERIODS" :key="String(option)" :value="String(option)">
              {{ t(`analysis.bestTimes.period.${option === null ? 'all' : option}`) }}
            </option>
          </Select>
        </template>
      </FormField>
    </div>

    <ErrorState v-if="bestTimesQuery.isError.value" :title="t('analysis.loadFailed')" />

    <Skeleton v-else-if="bestTimesQuery.isPending.value" class="h-64 w-full" />

    <Card v-else-if="table" class="flex flex-col gap-3">
      <EmptyState
        v-if="table.records.length === 0"
        :title="t('analysis.bestTimes.empty.title')"
        :description="t('analysis.bestTimes.empty.description')"
      />
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[30rem] border-collapse text-meta">
          <thead>
            <tr class="text-caption border-b border-border text-left">
              <th class="py-2 pe-3 font-medium">{{ t('analysis.bestTimes.colDistance') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.bestTimes.colTime') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.bestTimes.colPower') }}</th>
              <th class="py-2 pe-3 font-medium">{{ t('analysis.bestTimes.colDay') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="record in table.records"
              :key="record.distanceM"
              class="border-b border-border last:border-0"
            >
              <td class="py-2 pe-3 font-medium">{{ distanceLabel(record.distanceM) }}</td>
              <td class="py-2 pe-3 tabular-nums">{{ formatHmsDuration(record.timeS) }}</td>
              <td class="py-2 pe-3 tabular-nums">
                {{ record.avgPowerW === null ? '--' : `${Math.round(record.avgPowerW)} W` }}
              </td>
              <td class="py-2 pe-3">
                <RouterLink :to="{ name: 'activity', params: { id: record.activityId } }">
                  {{ dayLabel(record.startTime) }}
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-caption text-muted-foreground">{{ t('analysis.bestTimes.note') }}</p>
    </Card>
  </div>
</template>
