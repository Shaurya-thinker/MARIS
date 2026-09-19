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
  EnvironmentSelectRequest,
  SelectedEnvironment,
  AisSearchRequest,
  AisSearchResponse,
  ExperimentRunRequest,
  ExperimentRunResult,
  ExperimentListResponse,
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
} from './experimentTypes'

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
        if (body?.detail) detail = String(body.detail)
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

