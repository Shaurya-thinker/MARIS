/**
 * Stage G4 — Production Hardening Frontend Tests.
 *
 * Validates:
 * 1. WorkspaceErrorBoundary catches render-time exceptions and renders fallback.
 * 2. WorkspaceErrorBoundary provides recovery via Reset Workspace button.
 * 3. WorkspaceErrorBoundary preserves children when no error occurs.
 * 4. Shared API request helper enforces AbortController timeout.
 * 5. Request timeout throws ApiError with NETWORK_TIMEOUT and status 0.
 * 6. Successful requests before timeout resolve cleanly without timeout errors.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import React, { useState } from 'react'
import { WorkspaceErrorBoundary } from '../components/layout/WorkspaceErrorBoundary'
import {
  ApiError,
  DEFAULT_REQUEST_TIMEOUT_MS,
  listInvestigations,
} from '../api/investigationApi'

// Component that conditionally throws a render error for testing
function BuggyComponent({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error('Simulated render error in investigation workspace child')
  }
  return <div data-testid="workspace-content">Active Workspace Content</div>
}

function TestContainer() {
  const [shouldThrow, setShouldThrow] = useState(true)
  return (
    <div>
      <WorkspaceErrorBoundary onReset={() => setShouldThrow(false)}>
        <BuggyComponent shouldThrow={shouldThrow} />
      </WorkspaceErrorBoundary>
    </div>
  )
}

describe('Stage G4 — Production Hardening Frontend Tests', () => {
  const originalFetch = globalThis.fetch
  const originalConsoleError = console.error

  beforeEach(() => {
    // Suppress console.error during expected boundary catch tests
    console.error = vi.fn()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    console.error = originalConsoleError
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  describe('A7: WorkspaceErrorBoundary', () => {
    it('renders children normally when no render exception occurs', () => {
      render(
        <WorkspaceErrorBoundary>
          <div data-testid="child">Healthy Workspace</div>
        </WorkspaceErrorBoundary>
      )

      expect(screen.getByTestId('child')).toBeDefined()
      expect(screen.getByText('Healthy Workspace')).toBeDefined()
    })

    it('catches render error, renders non-blank fallback alert, and displays error details', () => {
      render(
        <WorkspaceErrorBoundary>
          <BuggyComponent shouldThrow={true} />
        </WorkspaceErrorBoundary>
      )

      const alert = screen.getByRole('alert')
      expect(alert).toBeDefined()
      expect(screen.getByText('Workspace Encountered a Render Error')).toBeDefined()
      expect(
        screen.getByText(/Simulated render error in investigation workspace child/)
      ).toBeDefined()
      expect(screen.getByRole('button', { name: /Reset Workspace/i })).toBeDefined()
    })

    it('recovers when Reset Workspace is clicked', () => {
      render(<TestContainer />)

      // Fallback is rendered initially
      expect(screen.getByRole('alert')).toBeDefined()
      expect(screen.getByText('Workspace Encountered a Render Error')).toBeDefined()

      // Click Reset Workspace button
      const resetButton = screen.getByRole('button', { name: /Reset Workspace/i })
      fireEvent.click(resetButton)

      // State resets, healthy content is now rendered
      expect(screen.getByTestId('workspace-content')).toBeDefined()
      expect(screen.getByText('Active Workspace Content')).toBeDefined()
      expect(screen.queryByRole('alert')).toBeNull()
    })
  })

  describe('B2: Frontend Request Timeout', () => {
    it('defines a 5-minute default timeout', () => {
      expect(DEFAULT_REQUEST_TIMEOUT_MS).toBe(300000)
    })

    it('converts AbortController timeout into ApiError with NETWORK_TIMEOUT', async () => {
      // Mock fetch that hangs until aborted
      globalThis.fetch = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
        return new Promise<Response>((_resolve, reject) => {
          if (init?.signal) {
            init.signal.addEventListener('abort', () => {
              const abortErr = new Error('The user aborted a request.')
              abortErr.name = 'AbortError'
              reject(abortErr)
            })
          }
        })
      })

      // Use fake timers to advance time past custom timeout
      vi.useFakeTimers()

      const requestPromise = listInvestigations()

      // Advance past 5 minutes
      vi.advanceTimersByTime(DEFAULT_REQUEST_TIMEOUT_MS + 1000)

      await expect(requestPromise).rejects.toThrow(ApiError)
      try {
        await requestPromise
      } catch (err: unknown) {
        const apiErr = err as ApiError
        expect(apiErr.status).toBe(0)
        expect(apiErr.errorType).toBe('NETWORK_TIMEOUT')
        expect(apiErr.message).toContain('timed out')
      }
    })

    it('resolves normally when response arrives before timeout', async () => {
      const mockInvestigations = [
        {
          id: 'inv-test-1',
          name: 'Fast Investigation',
          status: 'COMPLETED',
          created_at: '2025-06-01T00:00:00Z',
          asset_count: 2,
        },
      ]

      globalThis.fetch = vi.fn(async () => {
        return new Response(JSON.stringify(mockInvestigations), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      })

      const list = await listInvestigations()
      expect(list).toHaveLength(1)
      expect(list[0].id).toBe('inv-test-1')
    })
  })
})
