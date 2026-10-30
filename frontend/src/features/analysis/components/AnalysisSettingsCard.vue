<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from '@lucide/vue'

import type {
  AnalysisSettings,
  AnalysisSettingsUpdateDto,
  LoadMethod,
} from '@/features/analysis/types'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import {
  useQueueRecomputeMutation,
  useQueueThresholdRefreshMutation,
  useUpdateAnalysisSettingsMutation,
} from '@/features/analysis/composables/useAnalysis'
import { useToasts } from '@/composables/useToasts'

const props = defineProps<{
  /** The settings as stored on the server; the form starts from these. */
  settings: AnalysisSettings
}>()

const { t } = useI18n()
const toasts = useToasts()

const METHOD_OPTIONS: LoadMethod[] = ['coggan_tss', 'skiba_score', 'hr_load', 'trimp']

/** The sport groups the activities module groups activity types into. */
const SPORT_OPTIONS = [
  'run',
  'bike',
  'swim',
  'walk',
  'hike',
  'rowing',
  'snow_ski',
  'snowboard',
  'windsurf',
  'stand_up_paddleboarding',
  'surfing',
  'kayaking',
  'sailing',
  'snowshoeing',
  'inline_skating',
]

const parameters = reactive({
  acuteDays: String(props.settings.acuteDays),
  chronicDays: String(props.settings.chronicDays),
  primaryMethod: props.settings.primaryMethod,
  restingHr: props.settings.restingHr === null ? '' : String(props.settings.restingHr),
})

/** Re-seed the form when the server-side settings change underneath it. */
watch(
  () => props.settings,
  (next) => {
    parameters.acuteDays = String(next.acuteDays)
    parameters.chronicDays = String(next.chronicDays)
    parameters.primaryMethod = next.primaryMethod
    parameters.restingHr = next.restingHr === null ? '' : String(next.restingHr)
  },
)

const saveMutation = useUpdateAnalysisSettingsMutation()
const refreshMutation = useQueueThresholdRefreshMutation()
const recomputeMutation = useQueueRecomputeMutation()

/** Whether an entered wattage/bpm value is usable (positive number). */
function isPositive(value: string): boolean {
  const parsed = Number(value)
  return value !== '' && Number.isFinite(parsed) && parsed > 0
}

/** True when the parameter form deviates from the stored settings. */
const parametersDirty = computed(
  () =>
    parameters.acuteDays !== String(props.settings.acuteDays) ||
    parameters.chronicDays !== String(props.settings.chronicDays) ||
    parameters.primaryMethod !== props.settings.primaryMethod ||
    parameters.restingHr !==
      (props.settings.restingHr === null ? '' : String(props.settings.restingHr)),
)

/** Writes the time constants, formula and resting heart rate. */
function saveParameters(): void {
  const payload: AnalysisSettingsUpdateDto = {
    acute_days: Number(parameters.acuteDays),
    chronic_days: Number(parameters.chronicDays),
    primary_method: parameters.primaryMethod,
    resting_hr: parameters.restingHr === '' ? null : Number(parameters.restingHr),
  }
  saveMutation.mutate(payload, {
    onSuccess: () => toasts.success(t('analysis.settings.saved')),
    onError: () => toasts.error(t('analysis.settings.saveFailed')),
  })
}

/** Wattages typed into the threshold rows, keyed by sport. */
const overrides = reactive<Record<string, string>>({})

/** Stores the manual power threshold typed for one sport. */
function setPowerThreshold(sport: string): void {
  if (!isPositive(overrides[sport] ?? '')) {
    return
  }
  saveMutation.mutate(
    { power_threshold_w: { [sport]: Number(overrides[sport]) } },
    {
      onSuccess: () => {
        overrides[sport] = ''
        toasts.success(t('analysis.settings.saved'))
      },
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

/** Returns a sport's power threshold to the value derived from records. */
function clearPowerThreshold(sport: string): void {
  saveMutation.mutate(
    { power_threshold_w: { [sport]: null } },
    {
      onSuccess: () => toasts.success(t('analysis.settings.saved')),
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

const newSport = ref('run')
const newWatts = ref('')

/** Adds a manual power threshold for a sport that has none (or replaces it). */
function addPowerThreshold(): void {
  if (!isPositive(newWatts.value)) {
    return
  }
  saveMutation.mutate(
    { power_threshold_w: { [newSport.value]: Number(newWatts.value) } },
    {
      onSuccess: () => {
        newWatts.value = ''
        toasts.success(t('analysis.settings.saved'))
      },
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

const hrOverride = ref('')

/** Stores the manual threshold heart rate. */
function setHrThreshold(): void {
  if (!isPositive(hrOverride.value)) {
    return
  }
  saveMutation.mutate(
    { hr_threshold_bpm: Number(hrOverride.value) },
    {
      onSuccess: () => {
        hrOverride.value = ''
        toasts.success(t('analysis.settings.saved'))
      },
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

/** Returns the threshold heart rate to the value derived from records. */
function clearHrThreshold(): void {
  saveMutation.mutate(
    { hr_threshold_bpm: null },
    {
      onSuccess: () => toasts.success(t('analysis.settings.saved')),
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

function queueRefresh(): void {
  refreshMutation.mutate(undefined, {
    onSuccess: () => toasts.success(t('analysis.actions.refreshQueued')),
    onError: () => toasts.error(t('analysis.actions.failed')),
  })
}

function queueRecompute(): void {
  recomputeMutation.mutate(undefined, {
    onSuccess: () => toasts.success(t('analysis.actions.recomputeQueued')),
    onError: () => toasts.error(t('analysis.actions.failed')),
  })
}
</script>

<template>
  <Card class="flex min-w-0 flex-col gap-4">
    <h3 class="text-card-heading">{{ t('analysis.settings.title') }}</h3>

    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <FormField :label="t('analysis.settings.acuteDays')">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.acuteDays" type="number" min="1" max="180" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.chronicDays')">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.chronicDays" type="number" min="1" max="365" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.primaryMethod')">
        <template #default="{ fieldId }">
          <Select v-model="parameters.primaryMethod" :aria-labelledby="fieldId">
            <option v-for="method in METHOD_OPTIONS" :key="method" :value="method">
              {{ t(`analysis.method.${method}`) }}
            </option>
          </Select>
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.restingHr')">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.restingHr" type="number" min="20" max="120" />
        </template>
      </FormField>
    </div>
    <div class="flex justify-start">
      <Button :disabled="!parametersDirty || saveMutation.isPending.value" @click="saveParameters">
        {{ t('analysis.settings.save') }}
      </Button>
    </div>

    <div class="flex flex-col gap-3 border-t border-border pt-4">
      <h4 class="text-card-heading">{{ t('analysis.settings.thresholds.title') }}</h4>

      <ul class="flex flex-col gap-2">
        <li
          v-for="[sport, entry] in Object.entries(settings.powerThresholds)"
          :key="sport"
          class="flex flex-wrap items-center gap-2"
        >
          <span class="text-body font-medium">{{ t(`home.sports.${sport}`) }}</span>
          <span class="text-body tabular-nums">{{ Math.round(entry.valueW) }} W</span>
          <span v-if="entry.wPrimeJ !== null" class="text-body text-muted-foreground">
            W': {{ Math.round(entry.wPrimeJ / 100) / 10 }} kJ
          </span>
          <Badge variant="secondary">
            {{
              entry.source === 'manual'
                ? t('analysis.settings.thresholds.sourceManual')
                : t('analysis.settings.thresholds.sourceAuto')
            }}
          </Badge>
          <Input
            v-model="overrides[sport]"
            type="number"
            class="w-24"
            :placeholder="t('analysis.settings.thresholds.wattsPlaceholder')"
          />
          <Button
            variant="ghost"
            size="sm"
            :disabled="!isPositive(overrides[sport] ?? '')"
            @click="setPowerThreshold(sport)"
          >
            {{ t('analysis.settings.thresholds.set') }}
          </Button>
          <Button
            v-if="entry.source === 'manual'"
            variant="ghost"
            size="sm"
            @click="clearPowerThreshold(sport)"
          >
            {{ t('analysis.settings.thresholds.clear') }}
          </Button>
        </li>
      </ul>

      <div class="flex flex-wrap items-center gap-2">
        <Label for="analysis-new-threshold-sport">{{
          t('analysis.settings.thresholds.add')
        }}</Label>
        <Select id="analysis-new-threshold-sport" v-model="newSport">
          <option v-for="sport in SPORT_OPTIONS" :key="sport" :value="sport">
            {{ t(`home.sports.${sport}`) }}
          </option>
        </Select>
        <Input
          v-model="newWatts"
          type="number"
          class="w-24"
          :placeholder="t('analysis.settings.thresholds.wattsPlaceholder')"
        />
        <Button
          variant="ghost"
          size="sm"
          :disabled="!isPositive(newWatts)"
          @click="addPowerThreshold"
        >
          {{ t('analysis.settings.thresholds.set') }}
        </Button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <span class="text-body font-medium">{{ t('analysis.settings.thresholds.hr') }}</span>
        <span v-if="settings.hrThreshold" class="text-body tabular-nums">
          {{ Math.round(settings.hrThreshold.valueBpm) }} bpm
        </span>
        <Badge v-if="settings.hrThreshold" variant="secondary">
          {{
            settings.hrThreshold.source === 'manual'
              ? t('analysis.settings.thresholds.sourceManual')
              : t('analysis.settings.thresholds.sourceAuto')
          }}
        </Badge>
        <Input
          v-model="hrOverride"
          type="number"
          class="w-24"
          :placeholder="t('analysis.settings.thresholds.bpmPlaceholder')"
        />
        <Button
          variant="ghost"
          size="sm"
          :disabled="!isPositive(hrOverride)"
          @click="setHrThreshold"
        >
          {{ t('analysis.settings.thresholds.set') }}
        </Button>
        <Button
          v-if="settings.hrThreshold?.source === 'manual'"
          variant="ghost"
          size="sm"
          @click="clearHrThreshold"
        >
          {{ t('analysis.settings.thresholds.clear') }}
        </Button>
      </div>
    </div>

    <div class="flex flex-wrap gap-2 border-t border-border pt-4">
      <Button variant="ghost" :disabled="refreshMutation.isPending.value" @click="queueRefresh">
        <RefreshCw class="size-4" aria-hidden="true" />
        {{ t('analysis.actions.refreshThresholds') }}
      </Button>
      <Button variant="ghost" :disabled="recomputeMutation.isPending.value" @click="queueRecompute">
        {{ t('analysis.actions.recompute') }}
      </Button>
    </div>
  </Card>
</template>
