/**
 * MARIS Real-Data Experiment API client.
 *
 * Wraps all /api/experiment/* endpoints.
 * Reuses getApiBaseUrl() from investigationApi.ts for consistent base URL resolution.
 *
 * This module is completely isolated from the G1 investigation API client.
 */

import { getApiBaseUrl, ApiError, DEFAULT_REQUEST_TIMEOUT_MS } from '../api/investigationApi'
import type {
  ExperimentConfig,
  SentinelDiscoverRequest,
  SentinelDiscoverResponse,
  SentinelCharacterizeRequest,
  SentinelCharacterizeResponse,
  SarAcquisitionRequest,
  SarAcquisitionResponse,
  SlickCharacterization,
  EnvironmentSelectRequest,
  SelectedEnvironment,
  AisPositionsRequest,
  AisPositionsResponse,
  AisSearchRequest,
  AisSearchResponse,
  ExperimentRunRequest,
  ExperimentRunResult,
  ExperimentListResponse,
  ForwardPredictionRequest,
  ForwardPredictionResponse,
  SyntheticScenario,
  SyntheticRunResult,
  ActiveModelInfo,
  EvaluatorReferenceObservation,
  EvaluatorDriftPreviewRequest,
  EvaluatorDriftPreviewResponse,
  EvaluatorFilterVesselsRequest,
  EvaluatorFilterVesselsResponse,
  EvaluatorRunRequest,
  EvaluatorInvestigationRecord,
  EvaluatorInvestigationSummary,
  EvidenceComponent,
  EvidenceBreakdown,
  VesselFeatures,
} from './experimentTypes'

export type {
  EvidenceComponent,
  EvidenceBreakdown,
  VesselFeatures,
}

// ---------------------------------------------------------------------------
// Internal request helper (same pattern as investigationApi.ts)
// ---------------------------------------------------------------------------

async function experimentRequest<T>(
  path: string,
  options: RequestInit & { timeoutMs?: number } = {}
): Promise<T> {
  const baseUrl = getApiBaseUrl()
  const url = `${baseUrl}/api/experiment${path}`

  const headers = new Headers(options.headers || {})
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')
  if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      ...options,
      headers,
      signal: controller.signal,
    })
    clearTimeout(timer)

    if (!response.ok) {
      let detail = `HTTP ${response.status}`
      try {
        const body = await response.json()
        if (body?.detail) {
          if (typeof body.detail === 'string') {
            detail = body.detail
          } else if (Array.isArray(body.detail)) {
            detail = body.detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ')
          } else {
            detail = JSON.stringify(body.detail)
          }
        }
      } catch { /* ignore */ }
      throw new ApiError(response.status, detail)
    }

    return (await response.json()) as T
  } catch (err) {
    clearTimeout(timer)
    if (err instanceof ApiError) throw err
    throw new ApiError(0, err instanceof Error ? err.message : 'Network error')
  }
}

// ---------------------------------------------------------------------------
// Public API functions
// ---------------------------------------------------------------------------

/**
 * Fetch experiment configuration status — which credentials are configured.
 * Called once on wizard mount to determine which steps are available.
 */
export async function fetchExperimentConfig(): Promise<ExperimentConfig> {
  return experimentRequest<ExperimentConfig>('/config')
}

/**
 * Step 1 — Query CDSE catalogue for Sentinel-1 products.
 * Does NOT download any data.
 */
export async function discoverSentinelProducts(
  body: SentinelDiscoverRequest
): Promise<SentinelDiscoverResponse> {
  return experimentRequest<SentinelDiscoverResponse>('/sentinel/discover', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 60_000, // catalogue query; typically <5s
  })
}

/**
 * Step 1b — Automated detection & characterization of oil slick / anomaly (Step 10).
 */
export async function characterizeSentinelObservation(
  body: SentinelCharacterizeRequest
): Promise<SentinelCharacterizeResponse> {
  return experimentRequest<SentinelCharacterizeResponse>('/sentinel/characterize', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 15_000,
  })
}

/**
 * Step 1b.5 — Acquire lightweight calibrated SAR subscene GeoTIFF via CDSE Process API.
 */
export async function acquireSarSubscene(
  body: SarAcquisitionRequest
): Promise<SarAcquisitionResponse> {
  return experimentRequest<SarAcquisitionResponse>('/sentinel/acquire-subscene', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 60_000,
  })
}

/**
 * Step 2 — Acquire ERA5 + CMEMS data for the selected observation.
 * May take several minutes for large spatial/temporal extents.
 */
export async function selectEnvironment(
  body: EnvironmentSelectRequest
): Promise<SelectedEnvironment> {
  return experimentRequest<SelectedEnvironment>('/environment/select', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 15 * 60_000, // environmental acquisition: up to 15 min
  })
}

/**
 * Step 4 — Search for AIS vessel tracks near the source zone.
 */
export async function searchAisVessels(
  body: AisSearchRequest
): Promise<AisSearchResponse> {
  return experimentRequest<AisSearchResponse>('/ais/search', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 5 * 60_000,
  })
}

/**
 * Step 4b — Retrieve authentic AIS positions for specific MMSIs.
 */
export async function fetchAisPositions(
  body: AisPositionsRequest
): Promise<AisPositionsResponse> {
  return experimentRequest<AisPositionsResponse>('/ais/positions', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 30_000,
  })
}

/**
 * Step 5 — Execute the full attribution experiment.
 * Runs backward drift + vessel scoring + ranking.
 */
export async function runExperiment(
  body: ExperimentRunRequest
): Promise<ExperimentRunResult> {
  return experimentRequest<ExperimentRunResult>('/run', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 10 * 60_000, // drift integration can take time for long backtrack
  })
}

/**
 * Step 11 — Predict forward drift from observed slick position into future time.
 * Hits /api/experiment/drift/forward-predict.
 */
export async function runForwardPrediction(
  body: ForwardPredictionRequest
): Promise<ForwardPredictionResponse> {
  return experimentRequest<ForwardPredictionResponse>('/drift/forward-predict', {
    method: 'POST',
    body: JSON.stringify(body),
    timeoutMs: 3 * 60_000,
  })
}

/**
 * Step 7 — List persisted experiment runs.
 */
export async function listExperimentRuns(limit = 50): Promise<ExperimentListResponse> {
  return experimentRequest<ExperimentListResponse>(`/runs?limit=${limit}`)
}

/**
 * Retrieve a specific persisted experiment run by ID.
 */
export async function getExperimentRun(runId: string): Promise<ExperimentRunResult> {
  return experimentRequest<ExperimentRunResult>(`/runs/${encodeURIComponent(runId)}`)
}

/**
 * Step 8 — Retrieve structured scientific report data (JSON) from the backend.
 * Uses getApiBaseUrl() to target port 8000, not the Vite dev server.
 */
export async function fetchScientificReport(runId: string): Promise<Record<string, any>> {
  return experimentRequest<Record<string, any>>(`/runs/${encodeURIComponent(runId)}/report?format=json`)
}

/**
 * Step 8 — Download publication-grade PDF report via fetch+blob.
 * MUST use fetch+blob — direct anchor href would resolve against the Vite dev
 * server origin (port 5173) and return the SPA HTML instead of real PDF bytes.
 */
export async function downloadReportPdf(runId: string): Promise<void> {
  const baseUrl = getApiBaseUrl()
  const url = `${baseUrl}/api/experiment/runs/${encodeURIComponent(runId)}/report?format=pdf`
  const response = await fetch(url, { headers: { Accept: 'application/pdf' } })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { const body = await response.json(); if (body?.detail) detail = String(body.detail) } catch { /* ignore */ }
    throw new ApiError(response.status, detail)
  }
  const blob = await response.blob()
  const blobUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = `MARIS_Scientific_Report_${runId.slice(0, 8)}.pdf`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  setTimeout(() => URL.revokeObjectURL(blobUrl), 5000)
}

/**
 * Step 8 — Download the full experiment record as structured JSON via fetch+blob.
 * MUST use fetch+blob — direct anchor href would resolve against the Vite dev
 * server origin (port 5173) and return the SPA HTML instead of real JSON data.
 */
export async function downloadExportJson(runId: string): Promise<void> {
  const baseUrl = getApiBaseUrl()
  const url = `${baseUrl}/api/experiment/runs/${encodeURIComponent(runId)}/export`
  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try { const body = await response.json(); if (body?.detail) detail = String(body.detail) } catch { /* ignore */ }
    throw new ApiError(response.status, detail)
  }
  const blob = await response.blob()
  const blobUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = `MARIS_Experiment_${runId.slice(0, 8)}.json`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  setTimeout(() => URL.revokeObjectURL(blobUrl), 5000)
}

/** @deprecated Use downloadReportPdf() instead. */
export function getReportPdfUrl(runId: string): string {
  return `${getApiBaseUrl()}/api/experiment/runs/${encodeURIComponent(runId)}/report?format=pdf`
}

/** @deprecated Use downloadExportJson() instead. */
export function getExportJsonUrl(runId: string): string {
  return `${getApiBaseUrl()}/api/experiment/runs/${encodeURIComponent(runId)}/export`
}

// ---------------------------------------------------------------------------
// Synthetic Scenarios & ML Attribution API
// ---------------------------------------------------------------------------

export interface GenerateSyntheticParams {
  seed?: number;
  origin_lat?: number;
  origin_lon?: number;
  observation_time?: string;
  wind_speed_ms?: number;
  wind_direction_deg?: number;
  current_speed_ms?: number;
  current_direction_deg?: number;
  candidate_count?: number;
  backtrack_hours?: number;
  step_hours?: number;
  spill_area_m2?: number;
}

export async function generateSyntheticScenario(
  params: GenerateSyntheticParams = {}
): Promise<SyntheticScenario> {
  return experimentRequest<SyntheticScenario>('/synthetic/generate', {
    method: 'POST',
    body: JSON.stringify(params),
    timeoutMs: 30_000,
  })
}

export interface RunSyntheticParams extends GenerateSyntheticParams {
  scenario_id?: string;
  model_id?: string;
}

export async function runSyntheticExperiment(
  params: RunSyntheticParams = {}
): Promise<SyntheticRunResult> {
  return experimentRequest<SyntheticRunResult>('/synthetic/run', {
    method: 'POST',
    body: JSON.stringify(params),
    timeoutMs: 60_000,
  })
}

export async function fetchActiveModelInfo(): Promise<ActiveModelInfo> {
  return experimentRequest<ActiveModelInfo>('/synthetic/model', {
    method: 'GET',
  })
}

export interface TrainModelParams {
  num_scenarios?: number;
  base_seed?: number;
  model_type?: string;
  c_regularization?: number;
}

export async function trainAttributionModel(
  params: TrainModelParams = {}
): Promise<Record<string, unknown>> {
  return experimentRequest<Record<string, unknown>>('/synthetic/train', {
    method: 'POST',
    body: JSON.stringify(params),
    timeoutMs: 120_000,
  })
}

export async function listSyntheticRuns(
  limit = 50
): Promise<Array<Record<string, unknown>>> {
  return experimentRequest<Array<Record<string, unknown>>>(`/synthetic/runs?limit=${limit}`, {
    method: 'GET',
  })
}

// ---------------------------------------------------------------------------
// Evaluator Investigation Workflow API Functions
// ---------------------------------------------------------------------------

export async function fetchEvaluatorReferenceObservations(): Promise<EvaluatorReferenceObservation[]> {
  return experimentRequest<EvaluatorReferenceObservation[]>('/evaluator/reference-observations', {
    method: 'GET',
  })
}

export async function previewEvaluatorDrift(
  params: EvaluatorDriftPreviewRequest
): Promise<EvaluatorDriftPreviewResponse> {
  return experimentRequest<EvaluatorDriftPreviewResponse>('/evaluator/drift-preview', {
    method: 'POST',
    body: JSON.stringify(params),
    timeoutMs: 30_000,
  })
}

export async function filterEvaluatorVessels(
  params: EvaluatorFilterVesselsRequest
): Promise<EvaluatorFilterVesselsResponse> {
  return experimentRequest<EvaluatorFilterVesselsResponse>('/evaluator/filter-vessels', {
    method: 'POST',
    body: JSON.stringify(params),
    timeoutMs: 30_000,
  })
}

export async function runEvaluatorInvestigation(
  params: EvaluatorRunRequest
): Promise<EvaluatorInvestigationRecord> {
  return experimentRequest<EvaluatorInvestigationRecord>('/evaluator/run', {
    method: 'POST',
    body: JSON.stringify(params),
    timeoutMs: 60_000,
  })
}

export async function listEvaluatorInvestigations(
  limit = 50
): Promise<EvaluatorInvestigationSummary[]> {
  return experimentRequest<EvaluatorInvestigationSummary[]>(`/evaluator/investigations?limit=${limit}`, {
    method: 'GET',
  })
}

export async function getEvaluatorInvestigation(
  investigationId: string
): Promise<EvaluatorInvestigationRecord> {
  return experimentRequest<EvaluatorInvestigationRecord>(`/evaluator/investigations/${investigationId}`, {
    method: 'GET',
  })
}

// ---------------------------------------------------------------------------
// AIS Fleet Registry
// ---------------------------------------------------------------------------

export interface AisFleetVessel {
  vessel_id: string
  mmsi: string | null
  vessel_name: string | null
  vessel_type: string | null
  flag_country: string | null
  call_sign: string | null
  length: number | null
  width: number | null
  draft: number | null
  imo: string | null
  source_type: string
  is_real_observation: number
  created_at: string
  provider_name: string | null
  geographic_coverage: string | null
  coverage_start: string | null
  coverage_end: string | null
  position_count: number
  first_seen: string | null
  last_seen: string | null
}

export interface AisFleetResponse {
  stats: {
    total_sources: number
    total_batches: number
    total_vessels: number
    total_positions: number
    real_positions: number
    synthetic_or_manual_positions: number
    vessel_provenance: Array<{ source_type: string; is_real_observation: number; cnt: number }>
    position_provenance: Array<{ source_type: string; is_real_observation: number; cnt: number }>
  }
  source_breakdown: Array<{ source_type: string; cnt: number }>
  total_matching: number
  limit: number
  offset: number
  vessels: AisFleetVessel[]
}

export async function fetchAisFleet(params?: {
  limit?: number
  offset?: number
  search?: string
  source_type?: string
}): Promise<AisFleetResponse> {
  const qs = new URLSearchParams()
  if (params?.limit != null) qs.set('limit', String(params.limit))
  if (params?.offset != null) qs.set('offset', String(params.offset))
  if (params?.search) qs.set('search', params.search)
  if (params?.source_type) qs.set('source_type', params.source_type)
  const query = qs.toString() ? `?${qs.toString()}` : ''
  return experimentRequest<AisFleetResponse>(`/ais/fleet${query}`)
}
