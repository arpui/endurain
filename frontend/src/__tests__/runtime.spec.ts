import { beforeEach, describe, expect, it } from 'vitest'

import {
  getApiBaseUrl,
  getBackendAssetUrl,
  getWebSocketUrl,
  hasAbsoluteApiBase,
  useSameOriginApi,
} from '@/services/runtime'

describe('runtime backend configuration', () => {
  beforeEach(() => {
    window.env = undefined
  })

  it('uses ENDURAIN_HOST from env.js for API calls', () => {
    window.env = { ENDURAIN_HOST: 'https://endurain.example.test/' }

    expect(getApiBaseUrl()).toBe('https://endurain.example.test/api/v1')
  })

  it('uses ENDURAIN_HOST from env.js for backend assets', () => {
    window.env = { ENDURAIN_HOST: 'https://endurain.example.test' }

    expect(getBackendAssetUrl('/server_images/login.png')).toBe(
      'https://endurain.example.test/server_images/login.png',
    )
  })

  it('returns an already-absolute asset URL unchanged', () => {
    // Object storage hands back presigned absolute URLs; prefixing the backend
    // host would corrupt them.
    window.env = { ENDURAIN_HOST: 'https://endurain.example.test' }

    expect(getBackendAssetUrl('https://cdn.test/activity_media/5_a.jpg?sig=x')).toBe(
      'https://cdn.test/activity_media/5_a.jpg?sig=x',
    )
  })

  it('falls back to same-origin once pinned, for API, assets and the socket', () => {
    // multi-homed install: the page loaded over an address the configured
    // ENDURAIN_HOST does not name, and the probe against that host failed.
    // last in the file: the pin lives for the rest of the session.
    window.env = { ENDURAIN_HOST: 'http://192.168.31.118:6174' }
    expect(getApiBaseUrl()).toBe('http://192.168.31.118:6174/api/v1')
    expect(hasAbsoluteApiBase()).toBe(true)

    useSameOriginApi()

    expect(getApiBaseUrl()).toBe('/api/v1')
    expect(hasAbsoluteApiBase()).toBe(false)
    expect(getBackendAssetUrl('/server_images/login.png')).toBe('/server_images/login.png')
    expect(getWebSocketUrl('/ws')).toBe(
      `${window.location.origin.replace(/^http/, 'ws')}/api/v1/ws`,
    )
  })
})
