/**
 * Stage G1/G2 — Investigation API Client Layer.
 *
 * Provides typed methods for all G1 endpoints without scattering raw fetch calls.
 * Uses VITE_API_BASE_URL with a local fallback that matches the active backend port in this workspace.
 */

import type {
  ArtifactSummary,
  CandidateRanking,
  ExplainabilityReport,
  InvestigationCreateRequest,
  InvestigationListItem,
  InvestigationResponse,
  InvestigationRunRequest,
  InvestigationRunResponse,
  InvestigationStatusResponse,
} from '../types/investigationApi'

export class ApiError extends Error {
  readonly status: number
  readonly stage?: string
  readonly errorType?: string
  readonly details?: unknown

  constructor(status: number, message: string, stage?: string, errorType?: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.stage = stage
    this.errorType = errorType
    this.details = details
  }

  get userMessage(): string {
    if (this.stage && this.errorType) {
      return `Stage: ${this.stage} | Error: ${this.errorType} — ${this.message}`
    }
    return this.message
  }
}

export function getApiBaseUrl(): string {
  // Use import.meta.env when available. Otherwise match the active backend port for this workspace.
  const envUrl = typeof import.meta !== 'undefined' && import.meta.env ? (import.meta.env.VITE_API_BASE_URL as string | undefined) : undefined
  return (envUrl && envUrl.trim().length > 0) ? envUrl.replace(/\/+$/, '') : 'http://127.0.0.1:8001'
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes

export interface RequestOptions extends RequestInit {
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const baseUrl = getApiBaseUrl()
  const url = `${baseUrl}${path}`

  const headers = new Headers(options.headers || {})
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  const controller = new AbortController()
  let isTimedOut = false

  const timer = setTimeout(() => {
    isTimedOut = true
    controller.abort()
  }, timeoutMs)

  if (options.signal) {
    options.signal.addEventListener('abort', () => controller.abort())
  }

  const { timeoutMs: _ignored, ...fetchOptions } = options

  let response: Response
  try {
    response = await fetch(url, { ...fetchOptions, headers, signal: controller.signal })
  } catch (networkErr: unknown) {
    if (isTimedOut || (networkErr instanceof Error && networkErr.name === 'AbortError' && isTimedOut)) {
      throw new ApiError(
        0,
        `Request to ${url} timed out after ${Math.round(timeoutMs / 1000)} seconds.`,
        undefined,
        'NETWORK_TIMEOUT',
        networkErr
      )
    }
    const errorMsg = networkErr instanceof Error ? networkErr.message : String(networkErr)
    throw new ApiError(
      0,
      `Network request failed when connecting to ${url}: ${errorMsg}. Ensure the MARIS backend server is running.`,
      undefined,
      'NETWORK_FAILURE',
      networkErr
    )
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`
    let stage: string | undefined
    let errorType: string | undefined
    let parsedBody: unknown

    try {
      parsedBody = await response.json()
      if (parsedBody && typeof parsedBody === 'object') {
        const bodyObj = parsedBody as Record<string, unknown>
        if (typeof bodyObj.detail === 'string') {
          errorDetail = bodyObj.detail
        } else if (Array.isArray(bodyObj.detail)) {
          // FastAPI validation error list
          errorDetail = bodyObj.detail.map((d: { msg?: string; loc?: string[] }) => `${d.loc?.join('.')}: ${d.msg}`).join(', ')
        } else if (typeof bodyObj.message === 'string') {
          errorDetail = bodyObj.message
        }
        if (typeof bodyObj.stage === 'string') stage = bodyObj.stage
        if (typeof bodyObj.error === 'string') errorType = bodyObj.error
      }
    } catch {
      // Body not JSON; use default errorDetail
    }

    throw new ApiError(response.status, errorDetail, stage, errorType, parsedBody)
  }

  if (response.status === 204) {
    return {} as T
  }

  return response.json() as Promise<T>
}

/**
 * 1. Create a new investigation record (POST /api/v1/investigations)
 */
export async function createInvestigation(
  payload: InvestigationCreateRequest
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>('/api/v1/investigations', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/**
 * 2. List all registered investigations (GET /api/v1/investigations)
 */
export async function listInvestigations(): Promise<InvestigationListItem[]> {
  return request<InvestigationListItem[]>('/api/v1/investigations', {
    method: 'GET',
  })
}

/**
 * 3. Retrieve structured details of an investigation (GET /api/v1/investigations/{id})
 */
export async function getInvestigation(
  investigationId: string
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>(`/api/v1/investigations/${encodeURIComponent(investigationId)}`, {
    method: 'GET',
  })
}

/**
 * 4. Retrieve authoritative workflow status for an investigation (GET /api/v1/investigations/{id}/status)
 */
export async function getInvestigationStatus(
  investigationId: string
): Promise<InvestigationStatusResponse> {
  return request<InvestigationStatusResponse>(`/api/v1/investigations/${encodeURIComponent(investigationId)}/status`, {
    method: 'GET',
  })
}

/**
 * 5. Run scientific pipeline workflow B1 through F3 synchronously (POST /api/v1/investigations/{id}/run)
 */
export async function runInvestigation(
  investigationId: string,
  payload?: InvestigationRunRequest
): Promise<InvestigationRunResponse> {
  return request<InvestigationRunResponse>(`/api/v1/investigations/${encodeURIComponent(investigationId)}/run`, {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  })
}

/**
 * 6. List registered artifacts with provenance and metadata (GET /api/v1/investigations/{id}/artifacts)
 */
export async function getInvestigationArtifacts(
  investigationId: string
): Promise<ArtifactSummary[]> {
  return request<ArtifactSummary[]>(`/api/v1/investigations/${encodeURIComponent(investigationId)}/artifacts`, {
    method: 'GET',
  })
}

/**
 * Stage F2 Candidate Ranking inspection
 * POST /api/v1/investigations/{id}/spills/{spill_id}/candidate-ranking
 */
export async function getCandidateRanking(
  investigationId: string,
  spillId: string,
  candidateRankingId?: string
): Promise<CandidateRanking> {
  const payload = candidateRankingId ? { candidate_ranking_id: candidateRankingId } : {}
  return request<CandidateRanking>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/spills/${encodeURIComponent(spillId)}/candidate-ranking`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    }
  )
}

/**
 * Stage F3 Explainability & Uncertainty inspection
 * POST /api/v1/investigations/{id}/spills/{spill_id}/explainability
 */
export async function getExplainabilityReport(
  investigationId: string,
  spillId: string,
  candidateRankingId?: string
): Promise<ExplainabilityReport> {
  const payload = candidateRankingId ? { candidate_ranking_id: candidateRankingId } : {}
  return request<ExplainabilityReport>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/spills/${encodeURIComponent(spillId)}/explainability`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    }
  )
}

/**
 * Real-Data Experiment Config
 * Exported here for compatibility with existing imports.
 */
export { fetchExperimentConfig } from '../real-experiment/experimentApi'
