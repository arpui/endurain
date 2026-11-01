<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from '@lucide/vue'

import type { AnalysisSettings, AnalysisSettingsUpdateDto } from '@/features/analysis/types'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { LOAD_METHODS, SPORT_GROUPS } from '@/features/analysis/constants'
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

/** Knobs of the per-activity interval split, all relative to the athlete's numbers. */
const INTERVAL_KEYS = [
  'iv_enter_base',
  'iv_effort_if',
  'iv_min_segment_s',
  'iv_climb_grade_pct',
] as const

const parameters = reactive({
  acuteDays: String(props.settings.acuteDays),
  chronicDays: String(props.settings.chronicDays),
  primaryMethod: props.settings.primaryMethod,
  fitFromS: props.settings.fitFromS === null ? '' : String(props.settings.fitFromS),
  fitToS: props.settings.fitToS === null ? '' : String(props.settings.fitToS),
  restingHr: props.settings.restingHr === null ? '' : String(props.settings.restingHr),
})

/** The athlete's interval knobs; empty means the server's default applies. */
const intervals = reactive<Record<string, string>>(
  Object.fromEntries(
    INTERVAL_KEYS.map((key) => [
      key,
      props.settings.methodParams[key] === undefined
        ? ''
        : String(props.settings.methodParams[key]),
    ]),
  ),
)

/** Re-seed the form when the server-side settings change underneath it. */
watch(
  () => props.settings,
  (next) => {
    parameters.acuteDays = String(next.acuteDays)
    parameters.chronicDays = String(next.chronicDays)
    parameters.primaryMethod = next.primaryMethod
    parameters.fitFromS = next.fitFromS === null ? '' : String(next.fitFromS)
    parameters.fitToS = next.fitToS === null ? '' : String(next.fitToS)
    parameters.restingHr = next.restingHr === null ? '' : String(next.restingHr)
    for (const key of INTERVAL_KEYS) {
      intervals[key] = next.methodParams[key] === undefined ? '' : String(next.methodParams[key])
    }
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
    parameters.fitFromS !==
      (props.settings.fitFromS === null ? '' : String(props.settings.fitFromS)) ||
    parameters.fitToS !== (props.settings.fitToS === null ? '' : String(props.settings.fitToS)) ||
    parameters.restingHr !==
      (props.settings.restingHr === null ? '' : String(props.settings.restingHr)),
)

/** The interval knobs as a full parameter document, keeping the other formulas' keys. */
function buildMethodParams(): Record<string, number> {
  const merged: Record<string, number> = { ...props.settings.methodParams }
  for (const key of INTERVAL_KEYS) {
    const raw = intervals[key]
    if (raw === '') {
      delete merged[key]
      continue
    }
    const parsed = Number(raw)
    if (Number.isFinite(parsed) && parsed > 0) {
      merged[key] = parsed
    }
  }
  return merged
}

/** True when the interval knobs deviate from what is stored. */
const intervalsDirty = computed(() =>
  INTERVAL_KEYS.some(
    (key) =>
      intervals[key] !==
      (props.settings.methodParams[key] === undefined
        ? ''
        : String(props.settings.methodParams[key])),
  ),
)

/** Writes the time constants, formula, fit window and resting heart rate. */
function saveParameters(): void {
  const payload: AnalysisSettingsUpdateDto = {
    acute_days: Number(parameters.acuteDays),
    chronic_days: Number(parameters.chronicDays),
    primary_method: parameters.primaryMethod,
    fit_from_s: parameters.fitFromS === '' ? null : Number(parameters.fitFromS),
    fit_to_s: parameters.fitToS === '' ? null : Number(parameters.fitToS),
    resting_hr: parameters.restingHr === '' ? null : Number(parameters.restingHr),
    method_params: buildMethodParams(),
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

/** Parses a typed min/km pace ("4:30" or decimal minutes) into m/s; null when unusable. */
function paceToMs(value: string): number | null {
  const trimmed = value.trim()
  const colon = /^(\d{1,2}):([0-5]?\d)$/.exec(trimmed)
  const secondsPerKm = colon ? Number(colon[1]) * 60 + Number(colon[2]) : Number(trimmed) * 60
  if (!Number.isFinite(secondsPerKm) || secondsPerKm < 120 || secondsPerKm > 1200) {
    return null
  }
  return 1000 / secondsPerKm
}

/** Shows a critical speed the way runners read pace: min:km. */
function formatPace(ms: number): string {
  const seconds = Math.round(1000 / ms)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** Pace text typed into the speed threshold rows, keyed by sport. */
const speedOverrides = reactive<Record<string, string>>({})

/** Stores the manual critical speed typed for one sport. */
function setSpeedThreshold(sport: string): void {
  const ms = paceToMs(speedOverrides[sport] ?? '')
  if (ms === null) {
    return
  }
  saveMutation.mutate(
    { speed_threshold_ms: { [sport]: ms } },
    {
      onSuccess: () => {
        speedOverrides[sport] = ''
        toasts.success(t('analysis.settings.saved'))
      },
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

/** Returns a sport's critical speed to the value derived from records. */
function clearSpeedThreshold(sport: string): void {
  saveMutation.mutate(
    { speed_threshold_ms: { [sport]: null } },
    {
      onSuccess: () => toasts.success(t('analysis.settings.saved')),
      onError: () => toasts.error(t('analysis.settings.saveFailed')),
    },
  )
}

const newSpeedSport = ref('run')
const newPace = ref('')

/** Adds a manual critical speed for a sport that has none (or replaces it). */
function addSpeedThreshold(): void {
  const ms = paceToMs(newPace.value)
  if (ms === null) {
    return
  }
  saveMutation.mutate(
    { speed_threshold_ms: { [newSpeedSport.value]: ms } },
    {
      onSuccess: () => {
        newPace.value = ''
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
    <div class="flex flex-wrap items-end gap-2">
      <FormField :label="t('analysis.settings.acuteDays')" class="w-20">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.acuteDays" type="number" min="1" max="180" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.chronicDays')" class="w-20">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.chronicDays" type="number" min="1" max="365" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.restingHr')" class="w-20">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.restingHr" type="number" min="20" max="120" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.fitFromS')" class="w-20">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.fitFromS" type="number" min="10" max="7200" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.fitToS')" class="w-20">
        <template #default="{ fieldId }">
          <Input :id="fieldId" v-model="parameters.fitToS" type="number" min="30" max="21600" />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.primaryMethod')" class="w-56">
        <template #default="{ fieldId }">
          <Select v-model="parameters.primaryMethod" :aria-labelledby="fieldId">
            <option v-for="method in LOAD_METHODS" :key="method" :value="method">
              {{ t(`analysis.method.${method}`) }}
            </option>
          </Select>
        </template>
      </FormField>
      <Button
        :disabled="(!parametersDirty && !intervalsDirty) || saveMutation.isPending.value"
        @click="saveParameters"
      >
        {{ t('analysis.settings.save') }}
      </Button>
    </div>

    <div class="flex flex-wrap items-center gap-2 border-t border-border pt-4">
      <FormField :label="t('analysis.settings.ivEnterBase')" class="w-24">
        <template #default="{ fieldId }">
          <Input
            :id="fieldId"
            v-model="intervals.iv_enter_base"
            type="number"
            step="0.05"
            min="1"
            max="3"
          />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.ivEffortIf')" class="w-24">
        <template #default="{ fieldId }">
          <Input
            :id="fieldId"
            v-model="intervals.iv_effort_if"
            type="number"
            step="0.01"
            min="1"
            max="1.5"
          />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.ivMinSegmentS')" class="w-20">
        <template #default="{ fieldId }">
          <Input
            :id="fieldId"
            v-model="intervals.iv_min_segment_s"
            type="number"
            min="5"
            max="600"
          />
        </template>
      </FormField>
      <FormField :label="t('analysis.settings.ivClimbGrade')" class="w-20">
        <template #default="{ fieldId }">
          <Input
            :id="fieldId"
            v-model="intervals.iv_climb_grade_pct"
            type="number"
            step="0.5"
            min="1"
            max="25"
          />
        </template>
      </FormField>
      <p class="text-xs text-muted-foreground">{{ t('analysis.settings.ivHint') }}</p>
    </div>

    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-4">
      <div
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
          class="w-20"
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
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <Label for="analysis-new-threshold-sport">{{
          t('analysis.settings.thresholds.add')
        }}</Label>
        <Select id="analysis-new-threshold-sport" v-model="newSport">
          <option v-for="sport in SPORT_GROUPS" :key="sport" :value="sport">
            {{ t(`home.sports.${sport}`) }}
          </option>
        </Select>
        <Input
          v-model="newWatts"
          type="number"
          class="w-20"
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

      <div
        v-for="[sport, entry] in Object.entries(settings.speedThresholds)"
        :key="`speed-${sport}`"
        class="flex flex-wrap items-center gap-2"
      >
        <span class="text-body font-medium">
          {{ t(`home.sports.${sport}`) }} · {{ t('analysis.settings.thresholds.speed') }}
        </span>
        <span class="text-body tabular-nums">{{ formatPace(entry.valueMs) }} /km</span>
        <Badge variant="secondary">
          {{
            entry.source === 'manual'
              ? t('analysis.settings.thresholds.sourceManual')
              : t('analysis.settings.thresholds.sourceAuto')
          }}
        </Badge>
        <Input
          v-model="speedOverrides[sport]"
          class="w-20"
          :placeholder="t('analysis.settings.thresholds.pacePlaceholder')"
        />
        <Button
          variant="ghost"
          size="sm"
          :disabled="paceToMs(speedOverrides[sport] ?? '') === null"
          @click="setSpeedThreshold(sport)"
        >
          {{ t('analysis.settings.thresholds.set') }}
        </Button>
        <Button
          v-if="entry.source === 'manual'"
          variant="ghost"
          size="sm"
          @click="clearSpeedThreshold(sport)"
        >
          {{ t('analysis.settings.thresholds.clear') }}
        </Button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <Label for="analysis-new-speed-sport">{{
          t('analysis.settings.thresholds.addSpeed')
        }}</Label>
        <Select id="analysis-new-speed-sport" v-model="newSpeedSport">
          <option v-for="sport in SPORT_GROUPS" :key="sport" :value="sport">
            {{ t(`home.sports.${sport}`) }}
          </option>
        </Select>
        <Input
          v-model="newPace"
          class="w-20"
          :placeholder="t('analysis.settings.thresholds.pacePlaceholder')"
        />
        <Button
          variant="ghost"
          size="sm"
          :disabled="paceToMs(newPace) === null"
          @click="addSpeedThreshold"
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
          class="w-20"
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

    <div class="flex flex-col gap-2 border-t border-border pt-4">
      <div class="flex flex-wrap gap-2">
        <Button variant="ghost" :disabled="refreshMutation.isPending.value" @click="queueRefresh">
          <RefreshCw class="size-4" aria-hidden="true" />
          {{ t('analysis.actions.refreshThresholds') }}
        </Button>
        <p class="text-xs text-muted-foreground">{{ t('analysis.actions.refreshHint') }}</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <Button
          variant="ghost"
          :disabled="recomputeMutation.isPending.value"
          @click="queueRecompute"
        >
          {{ t('analysis.actions.recompute') }}
        </Button>
        <p class="text-xs text-muted-foreground">{{ t('analysis.actions.recomputeHint') }}</p>
      </div>
    </div>
  </Card>
</template>
