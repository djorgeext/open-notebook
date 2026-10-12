import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import apiClient from '@/lib/api/client'
import { SOURCE_STATUS_TIMEOUT_MS } from '@/lib/api/sources'
import { useSourceStatus } from './use-sources'

vi.mock('@/lib/api/client', () => ({
  default: { get: vi.fn() },
}))

const SOURCE_ID = 'source:abc'

function status(value: string) {
  return { data: { status: value, message: '' } }
}

function wrapper() {
  const client = new QueryClient()
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  Object.defineProperty(document, 'hidden', { value: state === 'hidden', configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
  // let the fetch started at the end of the window resolve and re-render
  // (react-query batches notifications on short timers)
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
  }
}

describe('useSourceStatus polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(apiClient.get).mockReset()
  })

  afterEach(() => {
    setVisibility('visible')
    vi.useRealTimers()
  })

  it('keeps polling while the tab is hidden and stops after completed', async () => {
    setVisibility('hidden')
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(status('running'))
      .mockResolvedValueOnce(status('running'))
      .mockResolvedValueOnce(status('completed'))

    const { result } = renderHook(() => useSourceStatus(SOURCE_ID), { wrapper: wrapper() })

    await tick(0)
    await tick(2000)
    await tick(2000)
    expect(result.current.data?.status).toBe('completed')
    expect(apiClient.get).toHaveBeenCalledTimes(3)

    await tick(10000)
    expect(apiClient.get).toHaveBeenCalledTimes(3)
  })

  it('calls the status endpoint with a short timeout', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(status('completed'))

    renderHook(() => useSourceStatus(SOURCE_ID), { wrapper: wrapper() })
    await tick(0)

    expect(SOURCE_STATUS_TIMEOUT_MS).toBe(15000)
    expect(apiClient.get).toHaveBeenCalledWith(`/sources/${SOURCE_ID}/status`, {
      timeout: 15000,
    })
  })

  it('keeps polling after a timed-out status request', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(status('running'))
      .mockRejectedValueOnce(new Error('timeout of 15000ms exceeded'))
      .mockResolvedValueOnce(status('completed'))

    const { result } = renderHook(() => useSourceStatus(SOURCE_ID), { wrapper: wrapper() })

    await tick(0)
    await tick(2000) // poll fails
    await tick(2000) // retry after fixed 2 s delay
    expect(result.current.data?.status).toBe('completed')
    expect(apiClient.get).toHaveBeenCalledTimes(3)
  })
})
