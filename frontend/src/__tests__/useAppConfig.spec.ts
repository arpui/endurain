import { beforeEach, describe, expect, it, vi } from 'vitest'

import { HttpError } from '@/services/http'
import { getApiBaseUrl } from '@/services/runtime'
import { DEFAULT_APP_CONFIG, fetchAppConfig } from '@/features/config/services/config'
import { loadAppConfig, useAppConfig } from '@/features/config/composables/useAppConfig'

vi.mock('@/features/config/services/config', async (importActual) => {
  const actual = await importActual<typeof import('@/features/config/services/config')>()
  return { ...actual, fetchAppConfig: vi.fn<typeof actual.fetchAppConfig>() }
})

vi.mock('@/composables/useTelemetry', () => ({
  useTelemetry: () => ({ captureError: vi.fn<() => void>() }),
}))

describe('loadAppConfig configuration-error detection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.env = undefined
  })

  it('raises configError when the backend host is unreachable (non-HTTP failure)', async () => {
    vi.mocked(fetchAppConfig).mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await loadAppConfig()

    expect(useAppConfig().configError.value).toBe(true)
  })

  it('raises configError when the request times out', async () => {
    vi.mocked(fetchAppConfig).mockRejectedValueOnce(
      new DOMException('The operation timed out.', 'TimeoutError'),
    )

    await loadAppConfig()

    expect(useAppConfig().configError.value).toBe(true)
  })

  it('stays quiet when the backend is reachable but answers with an HTTP error', async () => {
    vi.mocked(fetchAppConfig).mockRejectedValueOnce(new HttpError(500, 'Internal Server Error'))

    await loadAppConfig()

    expect(useAppConfig().configError.value).toBe(false)
  })

  it('probes again on same-origin when the configured host is unreachable, last-resort overlay only if both fail', async () => {
    // multi-homed install: env.js names a LAN address the current browser
    // cannot route to, though the page itself came from this same backend.
    window.env = { ENDURAIN_HOST: 'http://192.168.31.118:6174' }
    vi.mocked(fetchAppConfig)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await loadAppConfig()

    expect(useAppConfig().configError.value).toBe(true)
    expect(getApiBaseUrl()).toBe('/api/v1') // the session pinned same-origin along the way
  })

  it('recovers silently when the same-origin retry succeeds', async () => {
    window.env = { ENDURAIN_HOST: 'http://192.168.31.118:6174' }
    vi.mocked(fetchAppConfig)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(DEFAULT_APP_CONFIG)

    await loadAppConfig()

    expect(useAppConfig().configError.value).toBe(false)
    expect(fetchAppConfig).toHaveBeenCalledTimes(2)
  })

  it('clears a previous config error once the backend becomes reachable', async () => {
    vi.mocked(fetchAppConfig).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await loadAppConfig()
    expect(useAppConfig().configError.value).toBe(true)

    vi.mocked(fetchAppConfig).mockResolvedValueOnce(DEFAULT_APP_CONFIG)
    await loadAppConfig()
    expect(useAppConfig().configError.value).toBe(false)
  })
})
