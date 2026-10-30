import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'

import type { SegmentDetailDto } from '@/features/segments/types'

import { apiFetch } from '@/services/http'
import i18n from '@/i18n'
import SegmentImportDialog from '@/features/segments/components/SegmentImportDialog.vue'

// Replace only the network seam; keep a real `HttpError` for the dialog's
// `instanceof` check in its error path.
vi.mock('@/services/http', () => {
  class HttpError extends Error {
    constructor(
      public status: number,
      message?: string,
    ) {
      super(message)
    }
  }
  return { apiFetch: vi.fn<typeof apiFetch>(), HttpError }
})

/** Passthrough stub so the reka-ui portal/focus-trap is skipped and the form
 * renders inline where the test can drive it. */
const passthrough = { template: '<div><slot /></div>' }

const stored: SegmentDetailDto = {
  id: 11,
  name: 'Pujada a Puig Vicenç',
  source: 'strava',
  source_id: '39270619',
  distance_m: 84,
  elevation_gain_m: 10,
  created_by: 1,
  created_at: '2026-10-30T06:00:00Z',
  updated_at: '2026-10-30T06:00:00Z',
  min_lat: 41,
  min_lng: 2,
  max_lat: 41,
  max_lng: 2.001,
  geometry: [
    [41, 2],
    [41, 2.0005],
    [41, 2.001],
  ],
  altitudes: [100, 110, 105],
}

/** A segment file as the extraction script exports it: id, name and polyline. */
const segmentFile = JSON.stringify({
  segment_id: 39270619,
  name: 'Pujada a Puig Vicenç',
  latlng: [
    [41, 2],
    [41, 2.0005],
    [41, 2.001],
  ],
})

function mountDialog() {
  return mount(SegmentImportDialog, {
    props: { open: true, units: 'metric' as const },
    global: {
      plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
      stubs: {
        Dialog: passthrough,
        DialogContent: passthrough,
        DialogHeader: passthrough,
        DialogTitle: passthrough,
        DialogDescription: passthrough,
        DialogFooter: passthrough,
      },
    },
  })
}

/** Pretends the user chose `file` in the (hidden) native picker. */
async function pickFile(wrapper: ReturnType<typeof mountDialog>, file: File): Promise<void> {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

describe('SegmentImportDialog file import', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('imports a segment from a file on disk, with no pasting involved', async () => {
    vi.mocked(apiFetch).mockResolvedValue(stored)

    const wrapper = mountDialog()

    // The JSON textarea is the folded alternative: opening the dialog must not
    // look like a copy/paste exercise.
    expect(wrapper.find('textarea').exists()).toBe(false)

    await pickFile(wrapper, new File([segmentFile], 'segment.json', { type: 'application/json' }))

    expect(wrapper.text()).toContain('segment.json')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const posted = vi
      .mocked(apiFetch)
      .mock.calls.filter(([path, init]) => path === '/segments' && init?.method === 'POST')
    expect(posted).toHaveLength(1)
    expect(JSON.parse(String(posted[0]?.[1]?.body))).toMatchObject({
      segment_id: 39270619,
      name: 'Pujada a Puig Vicenç',
      source: 'strava',
    })
    expect(wrapper.emitted('success')).toBeTruthy()
  })

  it('reads a GPX file and takes its track name', async () => {
    vi.mocked(apiFetch).mockResolvedValue(stored)

    const wrapper = mountDialog()
    const gpx = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Strava" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>Camperola</name>
    <trkseg>
      <trkpt lat="41.0" lon="2.0"><ele>100</ele></trkpt>
      <trkpt lat="41.0" lon="2.0005"><ele>110</ele></trkpt>
      <trkpt lat="41.0" lon="2.001"><ele>105</ele></trkpt>
    </trkseg>
  </trk>
</gpx>`

    await pickFile(wrapper, new File([gpx], 'segment.gpx', { type: 'application/gpx+xml' }))

    // A GPX has no external id, so the form still needs one before submitting.
    await wrapper.get('input[inputmode="numeric"]').setValue('1234')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const body = JSON.parse(
      String(
        vi
          .mocked(apiFetch)
          .mock.calls.find(([path, init]) => path === '/segments' && init?.method === 'POST')?.[1]
          ?.body,
      ),
    )
    expect(body).toMatchObject({ segment_id: 1234, name: 'Camperola', source: 'manual' })
  })
})
