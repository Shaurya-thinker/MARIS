/**
 * Stage G1/G2 — Investigation API TypeScript Contracts.
 *
 * Directly mirrors backend Pydantic models in app.models.investigation_api,
 * app.models.common, app.models.candidate_ranking, and app.models.explainability.
 * Zero invented fields.
 */

export type InvestigationStatus = 'CREATED' | 'PROCESSING' | 'COMPLETED' | 'FAILED'

export type AssetType =
  | 'satellite_scene'
  | 'imagery_preview'
  | 'spill_geometry'
  | 'environment_wind'
  | 'environment_current'
  | 'drift_product'
  | 'ais_records'
  | 'vessel_track'
  | 'document'

export interface BoundingBox {
  west: number
  south: number
  east: number
  north: number
}

export interface BBoxAreaOfInterest {
  kind: 'bbox'
  bbox: BoundingBox
}

export interface PolygonAreaOfInterest {
  kind: 'polygon'
  polygon: {
    type: 'Polygon'
    coordinates: number[][][]
  }
}

export type AreaOfInterest = BBoxAreaOfInterest | PolygonAreaOfInterest

export interface TimeWindow {
  start: string
  end: string
}

export interface StageError {
  stage: string
  error: string
  message: string
}

export interface InvestigationCreateRequest {
  name: string
  area_of_interest: AreaOfInterest
  time_window: TimeWindow
  description?: string | null
  metadata?: Record<string, unknown>
}

export interface InvestigationResponse {
  id: string
  name: string
  status: InvestigationStatus
  area_of_interest: AreaOfInterest
  time_window: TimeWindow
  created_at: string
  description?: string | null
  metadata: Record<string, unknown>
  asset_ids: string[]
  evidence_ids: string[]
}

export interface InvestigationListItem {
  id: string
  name: string
  status: InvestigationStatus
  created_at: string
  description?: string | null
  asset_count: number
}

export interface InvestigationStatusResponse {
  investigation_id: string
  status: InvestigationStatus
  current_stage: string | null
  completed_stages: string[]
  available_artifacts: string[]
  errors: StageError[]
}

export interface InvestigationRunRequest {
  sentinel1_artifact_path?: string | null
  sar_asset_id?: string | null
  spill_id?: string | null
  scene_id?: string | null
  wind_asset_id?: string | null
  current_asset_id?: string | null
  ais_asset_id?: string | null
  polarization?: string | null
  drift_hours?: number | null
  lookback_hours?: number | null
  step_hours?: number | null
  leeway_fraction?: number | null
  temporal_window_hours?: number | null
  spatial_buffer_km?: number | null
}

export interface InvestigationRunResponse {
  investigation_id: string
  status: InvestigationStatus
  current_stage: string | null
  completed_stages: string[]
  artifacts: Record<string, string>
  errors: StageError[]
}

export interface Provenance {
  product_id?: string | null
  retrieved_at?: string | null
  processing_level?: string | null
  software_version?: string | null
  provider?: string | null
  uri?: string | null
  hash?: string | null
  notes?: string | null
  extra?: Record<string, unknown>
}

export interface ArtifactSummary {
  asset_id: string
  investigation_id: string
  asset_type: AssetType
  provider: string
  source: string
  location: string
  acquisition_time?: string | null
  processing_level?: string | null
  validation_status?: string | null
  provenance: Provenance
  upstream_asset_ids: string[]
  metadata: Record<string, unknown>
}

// Stage F1/F2 Models
export interface BehavioralContextSummary {
  total_anomalies_count: number
  anomalies_in_zone_count: number
  loitering_detected: boolean
  transmission_gaps_count: number
  gaps_spanning_zone_count: number
  speed_drop_in_zone: boolean
  speed_surge_near_zone: boolean
  nav_status_mismatch: boolean
  anchor_swing_observed: boolean
  notable_anomaly_types: string[]
}

export interface ForwardDriftCrossCheck {
  evaluated: boolean
  min_distance_to_forward_track_km?: number | null
  closest_forward_step_time?: string | null
  cross_check_note: string
}

export interface RankedCandidate {
  rank: number
  vessel_id: string
  candidate_id: string
  mmsi?: string | null
  imo?: string | null
  name?: string | null
  vessel_type?: string | null
  evidence_consistency_score: number | null
  spatial_score?: number | null
  temporal_score?: number | null
  trajectory_score?: number | null
  valid_primary_channels: number
  total_primary_channels: number
  evidence_availability_ratio: number
  contextual_evidence: BehavioralContextSummary
  drift_cross_check: ForwardDriftCrossCheck
  limitations: string[]
  provenance: Record<string, unknown>
}

export interface CandidateRanking {
  id: string
  investigation_id: string
  spill_id: string
  evidence_fusion_id: string
  generated_at: string
  methodology_version: string
  asset_id?: string | null
  nominal_weights: Record<string, number>
  candidate_count: number
  candidates: RankedCandidate[]
  metadata: Record<string, unknown>
}

// Stage F3 Explainability Models
export type EvidenceConsistencyLevel = 'HIGH' | 'MODERATE' | 'LOW' | 'INSUFFICIENT_DATA'

export type DriftCrossCheckStatus = 'CONSISTENT' | 'NOT_CONSISTENT' | 'UNAVAILABLE'

export interface BehavioralExplanation {
  label: string
  observations: string[]
  numerical_contribution_statement: string
  raw_summary: BehavioralContextSummary
}

export interface DriftCrossCheckExplanation {
  label: string
  status: DriftCrossCheckStatus
  min_distance_km?: number | null
  explanation: string
  non_additive_statement: string
  raw_cross_check: ForwardDriftCrossCheck
}

export interface CandidateExplanation {
  rank: number
  vessel_id: string
  candidate_id: string
  mmsi?: string | null
  imo?: string | null
  name?: string | null
  vessel_type?: string | null
  evidence_consistency_score: number | null
  evidence_consistency_level: EvidenceConsistencyLevel
  evidence_availability_ratio: number
  valid_primary_channels: number
  total_primary_channels: number
  evidence_availability_summary: string
  evidence_availability_band: string
  spatial_score?: number | null
  spatial_explanation: string
  temporal_score?: number | null
  temporal_explanation: string
  trajectory_score?: number | null
  trajectory_explanation: string
  behavioral_context: BehavioralExplanation
  drift_cross_check: DriftCrossCheckExplanation
  limitations: string[]
  scientific_disclaimer: string
  provenance: Record<string, unknown>
}

export interface ExplainabilityReport {
  id: string
  investigation_id: string
  spill_id: string
  candidate_ranking_id: string
  generated_at: string
  methodology_version: string
  asset_id?: string | null
  candidate_count: number
  candidates: CandidateExplanation[]
  scientific_disclaimer: string
  metadata: Record<string, unknown>
}
