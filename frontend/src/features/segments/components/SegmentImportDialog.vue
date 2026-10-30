<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { FileUp } from '@lucide/vue'

import type { ParsedSegment } from '@/features/segments/utils/importParsing'

import { FormDialog } from '@/components/ui/form-dialog'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { inputFieldClass } from '@/components/ui/input/fieldClasses'
import { useToasts } from '@/composables/useToasts'
import { HttpError } from '@/services/http'
import {
  useImportSegmentMutation,
  useSegmentsBackfillMutation,
} from '@/features/segments/composables/useSegments'
import {
  looksLikeGpx,
  parseSegmentGpx,
  parseSegmentJson,
  toImportInput,
} from '@/features/segments/utils/importParsing'
import { formatSegmentDistance } from '@/features/segments/utils/format'
import { cumulativeDistancesMeters } from '@/features/segments/utils/profile'
import type { Schemas } from '@/types'

const open = defineModel<boolean>('open', { required: true })

const props = withDefaults(
  defineProps<{
    /** The viewer's measurement system, used for the preview length. */
    units: Schemas['Units']
  }>(),
  {},
)

const emit = defineEmits<{
  success: [message: string]
  error: [message: string]
}>()

const { t } = useI18n()
const toasts = useToasts()

const importMutation = useImportSegmentMutation()
const backfillMutation = useSegmentsBackfillMutation()

/** The geometry read from the pasted JSON or the chosen file. */
const parsed = ref<ParsedSegment | null>(null)
/** Why the last read failed, as an i18n key. */
const parseErrorKey = ref<string | null>(null)
/** Raw JSON the user can paste instead of picking a file. */
const jsonText = ref('')
const name = ref('')
const segmentId = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

/** Length of the parsed geometry, for the preview line. */
const previewDistanceM = computed(() => {
  const geometry = parsed.value?.latlng ?? []
  if (geometry.length < 2) {
    return 0
  }
  const totals = cumulativeDistancesMeters(geometry)
  return totals[totals.length - 1] ?? 0
})

const canSubmit = computed(() => parsed.value !== null)
const isBusy = computed(() => importMutation.isPending.value || backfillMutation.isPending.value)

/** Clears the parsed geometry and any read error. */
function clearParsed(): void {
  parsed.value = null
  parseErrorKey.value = null
}

/** Reads a text payload (JSON body or GPX XML) into the form. */
function readContent(text: string, filename: string): void {
  const outcome = looksLikeGpx(filename) ? parseSegmentGpx(text) : parseSegmentJson(text)
  if (!outcome.ok) {
    parseErrorKey.value = outcome.error.key
    parsed.value = null
    return
  }
  parseErrorKey.value = null
  parsed.value = outcome.parsed
  // Prefill the text fields only when they are still empty, so an edit the user
  // already made is not overwritten by a second file.
  if (!name.value.trim()) {
    name.value = outcome.parsed.name
  }
  if (!segmentId.value.trim() && outcome.parsed.segmentId !== null) {
    segmentId.value = String(outcome.parsed.segmentId)
  }
}

/** Handles the file picker: GPX or JSON, read as text. */
async function onFilePicked(event: Event): Promise<void> {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) {
    return
  }
  readContent(await file.text(), file.name)
}

/** Parses whatever was pasted into the JSON box. */
function onJsonPasted(): void {
  if (!jsonText.value.trim()) {
    clearParsed()
    return
  }
  readContent(jsonText.value, 'segment.json')
}

/** Resets the form each time the dialog opens. */
function resetForm(): void {
  parsed.value = null
  parseErrorKey.value = null
  jsonText.value = ''
  name.value = ''
  segmentId.value = ''
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

watch(open, (isOpen) => {
  if (isOpen) {
    resetForm()
  }
})

/** Maps a failure into a localized message for the toast/error line. */
function messageFor(error: unknown): string {
  if (error instanceof HttpError) {
    if (error.type === 'urn:endurain:error:conflict') {
      return t('segments.import.duplicate')
    }
    return typeof error.detail === 'string' ? error.detail : t('segments.import.failed')
  }
  return t('segments.import.failed')
}

/**
 * Imports the parsed segment, then queues the full re-match so the activities
 * already recorded get their efforts against the new definition. The recompute is
 * part of the same action because an imported segment that has never been matched
 * would otherwise show an empty effort list.
 */
async function handleSubmit(): Promise<void> {
  if (!parsed.value) {
    return
  }
  const built = toImportInput(parsed.value, { name: name.value, segmentId: segmentId.value })
  if (!built.ok) {
    parseErrorKey.value = built.error.key
    return
  }
  try {
    const stored = await importMutation.mutateAsync(built.input)
    await backfillMutation.mutateAsync()
    emit('success', t('segments.import.done', { name: stored.name }))
    open.value = false
  } catch (error) {
    const message = messageFor(error)
    emit('error', message)
    toasts.error(message)
  }
}
</script>

<template>
  <FormDialog
    v-model:open="open"
    :title="t('segments.import.title')"
    :description="t('segments.import.description')"
    :submit-label="t('segments.import.submit')"
    :cancel-label="t('segments.import.cancel')"
    :close-label="t('segments.import.close')"
    :submitting="isBusy"
    :can-submit="canSubmit"
    content-class="max-w-xl"
    @submit="handleSubmit"
  >
    <div class="flex flex-col gap-4">
      <div class="flex flex-col gap-2">
        <label class="text-caption font-medium text-foreground" for="segment-file">
          {{ t('segments.import.file') }}
        </label>
        <input
          id="segment-file"
          ref="fileInput"
          type="file"
          accept=".gpx,application/gpx+xml,.json,application/json"
          class="text-meta"
          :disabled="isBusy"
          @change="onFilePicked"
        />
        <p class="text-caption text-muted-foreground">{{ t('segments.import.fileHint') }}</p>
      </div>

      <FormField :label="t('segments.import.json')">
        <template #default="{ fieldId }">
          <textarea
            :id="fieldId"
            v-model="jsonText"
            :class="inputFieldClass"
            class="min-h-24 font-mono text-meta"
            :placeholder="t('segments.import.jsonPlaceholder')"
            :disabled="isBusy"
            rows="4"
            @change="onJsonPasted"
          />
        </template>
      </FormField>

      <div v-if="parseErrorKey" role="alert" class="text-meta text-destructive">
        {{ t(parseErrorKey) }}
      </div>

      <p
        v-if="parsed && parseErrorKey === null"
        class="flex flex-wrap items-center gap-2 text-meta text-muted-foreground"
      >
        <FileUp class="size-4" aria-hidden="true" />
        {{
          t('segments.import.parsed', {
            points: parsed.latlng.length,
            distance: formatSegmentDistance(previewDistanceM, props.units),
          })
        }}
      </p>

      <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <FormField :label="t('segments.import.name')">
          <template #default="{ fieldId }">
            <Input
              :id="fieldId"
              v-model="name"
              :class="inputFieldClass"
              :placeholder="parsed?.name || t('segments.import.name')"
              :disabled="isBusy"
              maxlength="255"
            />
          </template>
        </FormField>

        <FormField :label="t('segments.import.id')">
          <template #default="{ fieldId }">
            <Input
              :id="fieldId"
              v-model="segmentId"
              :class="inputFieldClass"
              inputmode="numeric"
              :placeholder="t('segments.import.idPlaceholder')"
              :disabled="isBusy"
            />
          </template>
        </FormField>
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        :disabled="!jsonText.trim()"
        @click="onJsonPasted"
      >
        {{ t('segments.import.parseJson') }}
      </Button>
    </div>
  </FormDialog>
</template>
