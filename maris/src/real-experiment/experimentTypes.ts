/**
 * TypeScript interfaces for the MARIS real-data attribution experiment.
 *
 * These types mirror the Pydantic schemas in:
 *   backend/app/api/experiment_schemas.py
 *
 * They are intentionally isolated from the simulation showcase types
 * in src/simulation/ and the historical demo types.
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

export interface ExperimentConfig {
  sentinel1_configured: boolean;
  era5_configured: boolean;
  cmems_configured: boolean;
  ais_configured: boolean;
  ais_adapter_id: string;
  all_configured: boolean;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Step 1 — Sentinel-1 Discovery
// ---------------------------------------------------------------------------

export interface SentinelDiscoverRequest {
  west: number;
  south: number;
  east: number;
  north: number;
  start: string;  // ISO 8601
  end: string;    // ISO 8601
  limit?: number;
}

export interface SentinelProduct {
  product_id: string;
  title: string;
  sensing_start: string;
  sensing_end: string | null;
  online: boolean;
  platform: string;
  mode: string | null;
  product_class: string | null;
  polarisation: string | null;
  centroid_lon: number | null;
  centroid_lat: number | null;
  footprint: Record<string, unknown> | null;
  content_length_bytes: number | null;
}

export interface SentinelDiscoverResponse {
  products: SentinelProduct[];
  count: number;
  configured: boolean;
}

// ---------------------------------------------------------------------------
// Step 1b — Slick Detection & Characterization (Step 10)
// ---------------------------------------------------------------------------

export interface SlickCharacterization {
  detected: boolean;
  status: string;
  centroid_lon: number | null;
  centroid_lat: number | null;
  area_km2: number | null;
  area_m2: number | null;
  bbox: { west: number; south: number; east: number; north: number } | null;
  slick_geometry: Record<string, unknown> | null;
  observation_time: string;
  satellite_product_id: string;
  platform: string;
  sensor: string;
  mode: string | null;
  polarisation: string | null;
  confidence: number | null;
  damping_contrast_db: number | null;
  estimated_age_hours: number | null;
  detection_method: string;
  provenance: string;
  data_fidelity: string;
  sar_raster_path?: string | null;
  has_physical_raster?: boolean;
  pixel_count?: number | null;
}

export interface SentinelCharacterizeRequest {
  product_id: string;
  title?: string | null;
  sensing_start?: string | null;
  centroid_lon?: number | null;
  centroid_lat?: number | null;
  mode?: string | null;
  polarisation?: string | null;
  footprint?: Record<string, unknown> | null;
  backtrack_hours?: number;
  sar_raster_path?: string | null;
}

export interface SentinelCharacterizeResponse {
  characterization: SlickCharacterization;
}

export interface SarAcquisitionRequest {
  product_id: string;
  bbox?: { west: number; south: number; east: number; north: number } | null;
  centroid_lon?: number | null;
  centroid_lat?: number | null;
  sensing_time?: string | null;
  width_px?: number;
  height_px?: number;
  aoi_radius_km?: number;
  force_refresh?: boolean;
}

export interface SarAcquisitionResponse {
  success: boolean;
  sar_raster_path: string;
  product_id: string;
  file_size_bytes: number;
  is_cached: boolean;
  source_provider: string;
  data_authenticity: string;
  is_test_fixture: boolean;
  crs: string;
  bands: string[];
  bbox: { west: number; south: number; east: number; north: number };
  message: string;
}

// ---------------------------------------------------------------------------
// Step 2 — Environment Selection
// ---------------------------------------------------------------------------

export interface EnvironmentSelectRequest {
  observation_time: string;
  west: number;
  south: number;
  east: number;
  north: number;
  backtrack_hours?: number;
  investigation_id?: string;
  era5_override_path?: string | null;
  cmems_override_path?: string | null;
}

export interface SelectedEnvironment {
  era5_netcdf_path: string;
  era5_timestamp: string;
  era5_u_sample: number | null;
  era5_v_sample: number | null;
  cmems_netcdf_path: string;
  cmems_timestamp: string;
  cmems_u_sample: number | null;
  cmems_v_sample: number | null;
  auto_selected: boolean;
  era5_configured: boolean;
  cmems_configured: boolean;
}

// ---------------------------------------------------------------------------
// Step 4 — AIS Vessel Search
// ---------------------------------------------------------------------------

export interface AisSearchRequest {
  west: number;
  south: number;
  east: number;
  north: number;
  start: string;
  end: string;
  investigation_id?: string;
  backward_steps?: BackwardStep[];
  observation_time?: string;
  backtrack_hours?: number;
  source_candidate_zone?: Record<string, unknown>;
  observation_lat?: number;
  observation_lon?: number;
  era5_netcdf_path?: string;
  cmems_netcdf_path?: string;
  step_hours?: number;
  spill_area_m2?: number;
  satellite_product_id?: string;
}

export interface VesselSummary {
  mmsi: string | null;
  vessel_name: string | null;
  imo: string | null;
  position_count: number;
  first_timestamp: string;
  last_timestamp: string;
  source_adapter: string;
  positions?: AisPosition[];
  min_trajectory_distance_km?: number | null;
  trajectory_time_delta_hours?: number | null;
  source_zone_intersection?: boolean;
  min_source_distance_km?: number | null;
  source_type?: string;
  provider_name?: string;
}

export interface AisSearchResponse {
  vessels: VesselSummary[];
  total_positions: number;
  search_bbox: { west: number; south: number; east: number; north: number };
  search_window_start: string;
  search_window_end: string;
  adapter_id: string;
  configured: boolean;
  backward_steps?: BackwardStep[];
}

export interface AisPositionsRequest {
  mmsis: string[];
  west?: number;
  south?: number;
  east?: number;
  north?: number;
  start: string;
  end: string;
}

export interface AisPositionsResponse {
  vessel_positions: Record<string, AisPosition[]>;
  total_positions: number;
}

// ---------------------------------------------------------------------------
// Step 5 — Run Experiment
// ---------------------------------------------------------------------------

export interface AisPosition {
  timestamp: string;
  lat: number;
  lon: number;
  speed?: number | null;
  heading?: number | null;
}

export interface VesselInput {
  mmsi?: string | null;
  vessel_name?: string | null;
  id?: string | null;
  positions: AisPosition[];
}

export interface ExperimentRunRequest {
  satellite_product_id: string;
  observation_lon: number;
  observation_lat: number;
  observation_time: string;
  era5_netcdf_path: string;
  cmems_netcdf_path: string;
  backtrack_hours?: number;
  step_hours?: number;
  spill_area_m2?: number | null;
  selected_vessels: VesselInput[];
  slick_characterization?: SlickCharacterization | null;
  forward_prediction_hours?: number;
  forward_step_hours?: number;
  monte_carlo?: MonteCarloConfig;
}

export interface MonteCarloConfig {
  enabled: boolean;
  ensemble_size: number;
  seed?: number | null;
  perturb_origin: boolean;
  origin_std_m: number;
  perturb_leeway: boolean;
  leeway_std: number;
  perturb_wind: boolean;
  wind_speed_std_ms: number;
  wind_dir_std_deg: number;
  perturb_current: boolean;
  current_std_ms: number;
}

// ---------------------------------------------------------------------------
// Step 12 — ML Attribution + AIS Behavioral Intelligence types
// ---------------------------------------------------------------------------

export interface BehavioralAnomalyItem {
  anomaly_type: string;
  severity: string;           // 'info' | 'notable' | 'anomalous'
  description: string;
  timestamp: string;
  location_lon: number;
  location_lat: number;
  inside_source_zone: boolean;
  observed_value: number | null;
  baseline_or_threshold_value: number | null;
  details: Record<string, unknown>;
}

export interface VesselBehavioralIntelligence {
  anomalies: BehavioralAnomalyItem[];
  transmission_gap_count: number;
  loitering_detected: boolean;
  observed_loitering_duration_seconds: number;
  nav_status_consistent: boolean;
  summary_flags: string[];
  /** Contextual-only note: findings do NOT modify evidence_consistency_score */
  analysis_note: string;
}

// ---------------------------------------------------------------------------
// Phase #4 — Attribution & Explainability
// ---------------------------------------------------------------------------

export interface EvidenceComponent {
  score: number;
  weight: number;
}

export interface EvidenceBreakdown {
  spatial: EvidenceComponent;
  temporal: EvidenceComponent;
  trajectory: EvidenceComponent;
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export interface VesselFeatures {
  vessel_id: string;
  vessel_name: string | null;
  mmsi: string | null;
  min_source_distance_km: number | null;
  temporal_overlap_hours: number;
  trajectory_overlap_fraction: number;
  heading_consistency: number | null;
  speed_consistency: number | null;
  ais_position_count: number;
  ais_coverage_fraction: number;
  evidence_consistency_score: number;
  rank: number;
  has_meaningful_support: boolean;
  positions?: AisPosition[];
  min_trajectory_distance_km?: number | null;
  trajectory_time_delta_hours?: number | null;
  source_zone_intersection?: boolean;
  source_type?: string;
  provider_name?: string;
  /** Step 12 — Independent ML Model Probability in [0,1].
   *  Not a probability of legal responsibility or causation.
   *  NOT forced to sum to 1 across candidates; NOT used to re-rank. */
  model_probability?: number | null;
  /** Step 12 — 10-dimensional ML feature vector.
   *  ML feature-vector values are model inputs produced by the feature extractor
   *  and may use definitions or normalization different from the physical evidence presentation metrics. */
  ml_feature_vector?: Record<string, number> | null;
  /** Step 12 — Contextual AIS Rule-Based Detector Findings. Does NOT alter evidence_consistency_score. */
  behavioral_intelligence?: VesselBehavioralIntelligence | null;
  /** Phase #4 — Evidence Consistency Breakdown (Spatial 50%, Temporal 25%, Trajectory 25%) */
  evidence_breakdown?: EvidenceBreakdown | null;
  /** Phase #4 — 2–3 factual statements explaining spatiotemporal evidence consistency */
  explanation?: string[];
  /** Phase #4 — Evidence Consistency Level ('HIGH' | 'MODERATE' | 'LOW') */
  consistency_level?: "HIGH" | "MODERATE" | "LOW" | string;
  /** Phase #4 — Scientific neutral disclaimer */
  scientific_disclaimer?: string;
  /** Phase #5 — Monte Carlo Ensemble Evidence & Sensitivity */
  ensemble_evidence?: EnsembleEvidence | null;
}

// ---------------------------------------------------------------------------
// Phase #5 — Monte Carlo Ensemble Result Schemas
// ---------------------------------------------------------------------------

export interface EnsembleTrajectoryStep {
  step: number;
  lat: number;
  lon: number;
  timestamp?: string | null;
  uncertainty_radius_m?: number | null;
  drift_u_ms?: number | null;
  drift_v_ms?: number | null;
}

export interface EnsembleRealization {
  realization_id: number;
  steps: EnsembleTrajectoryStep[];
  final_source_lon: number;
  final_source_lat: number;
  final_uncertainty_radius_m: number;
  perturbation_parameters: Record<string, number>;
  termination_status: string;
  provenance: string;
}

export interface EnsembleEvidence {
  ensemble_support_fraction: number;
  trajectory_consistency_across_ensemble?: number | null;
  source_intersection_fraction?: number | null;
  score_mean?: number | null;
  score_std?: number | null;
  score_p05?: number | null;
  score_p95?: number | null;
  ensemble_disclaimer: string;
}

export interface MonteCarloEnsembleResult {
  ensemble_size: number;
  realizations: EnsembleRealization[];
  mean_trajectory: Array<Record<string, unknown>>;
  final_source_centroid: { lat: number; lon: number };
  dispersion_radius_km: number;
  p05_source_lon: number;
  p95_source_lon: number;
  p05_source_lat: number;
  p95_source_lat: number;
  effective_seed?: number | null;
  execution_time_ms: number;
  provenance: string;
  scientific_disclaimer: string;
}

export interface BackwardStep {
  step: number;
  lon: number;
  lat: number;
  timestamp: string | null;
  uncertainty_radius_m: number;
  u_wind_ms?: number | null;
  v_wind_ms?: number | null;
  u_current_ms?: number | null;
  v_current_ms?: number | null;
}

export interface ForwardDriftStep {
  step: number;
  lon: number;
  lat: number;
  timestamp: string | null;
  u_wind_ms?: number | null;
  v_wind_ms?: number | null;
  u_current_ms?: number | null;
  v_current_ms?: number | null;
}

export interface ForwardPredictionResult {
  observation_lon: number;
  observation_lat: number;
  origin_lon?: number;
  origin_lat?: number;
  observation_time: string;
  prediction_hours: number;
  step_hours: number;
  model_version: string;
  status: string;
  termination_status?: string;
  steps: ForwardDriftStep[];
  final_lon: number;
  final_lat: number;
  displacement_km: number;
  total_distance_km?: number;
  provenance?: Record<string, string>;
  scientific_disclaimer?: string;
}

export interface ForwardPredictionRequest {
  satellite_product_id?: string;
  observation_lon?: number;
  observation_lat?: number;
  origin_lon?: number;
  origin_lat?: number;
  observation_time: string;
  era5_netcdf_path: string;
  cmems_netcdf_path: string;
  prediction_hours?: number;
  step_hours?: number;
}

export interface ForwardPredictionResponse {
  forward_prediction?: ForwardPredictionResult;
  prediction?: ForwardPredictionResult;
}

export interface ExperimentRunResult {
  run_id: string;
  satellite_product_id: string;
  observation_time: string;
  observation_lon?: number | null;
  observation_lat?: number | null;
  observation_source?: string;
  candidate_count?: number;
  status?: string;
  backtrack_hours: number;
  step_hours: number;
  model_version: string;
  source_lon: number;
  source_lat: number;
  source_radius_m: number;
  source_zone_geojson: Record<string, unknown>;
  backward_steps: BackwardStep[];
  vessels: VesselFeatures[];
  era5_path: string;
  cmems_path: string;
  created_at: string;
  scientific_disclaimer: string;
  slick_characterization?: SlickCharacterization | null;
  forward_prediction?: ForwardPredictionResult | null;
  monte_carlo_ensemble?: MonteCarloEnsembleResult | null;
}

export interface ExperimentRunSummary {
  run_id: string;
  satellite_product_id: string;
  observation_time: string;
  observation_lon?: number | null;
  observation_lat?: number | null;
  observation_source?: string;
  candidate_count?: number;
  status?: string;
  backtrack_hours: number;
  model_version: string;
  source_lon: number;
  source_lat: number;
  source_radius_m: number;
  created_at: string;
  slick_characterization?: SlickCharacterization | null;
  forward_prediction?: ForwardPredictionResult | null;
  monte_carlo_ensemble?: MonteCarloEnsembleResult | null;
}

export interface ExperimentListResponse {
  runs: ExperimentRunSummary[];
  count: number;
}

// ---------------------------------------------------------------------------
// Wizard state
// ---------------------------------------------------------------------------

export type WizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

export interface ExperimentWizardState {
  step: WizardStep;
  config: ExperimentConfig | null;
  // Step 1 — Observation selection
  searchBbox: { west: number; south: number; east: number; north: number } | null;
  searchStart: string;
  searchEnd: string;
  discoveredProducts: SentinelProduct[];
  selectedProduct: SentinelProduct | null;
  // Step 2 — Environment
  environment: SelectedEnvironment | null;
  backtrackHours: number;
  // Step 3 — Drift configuration
  stepHours: number;
  spillAreaM2: number | null;
  observationLat: number | null;
  observationLon: number | null;
  // Step 11 — Forward Prediction configuration & preview
  forwardPredictionHours: number;
  forwardStepHours: number;
  enableForwardPrediction: boolean;
  forwardPrediction: ForwardPredictionResult | null;
  forwardPredicting: boolean;
  forwardPredictError: string | null;
  // Step 4 — Vessel selection
  aisSearchResult: AisSearchResponse | null;
  selectedVessels: VesselInput[];
  // Phase #5 — Monte Carlo Ensemble Configuration
  enableMonteCarlo: boolean;
  monteCarloEnsembleSize: number;
  monteCarloSeed: number | null;
  monteCarloPerturbOrigin: boolean;
  monteCarloOriginStdM: number;
  monteCarloPerturbLeeway: boolean;
  monteCarloLeewayStd: number;
  monteCarloPerturbWind: boolean;
  monteCarloWindSpeedStdMs: number;
  monteCarloWindDirStdDeg: number;
  monteCarloPerturbCurrent: boolean;
  monteCarloCurrentStdMs: number;
  // Step 5 — Run status
  runStatus: 'idle' | 'running' | 'success' | 'error';
  runError: string | null;
  // Step 6 — Results
  runResult: ExperimentRunResult | null;
  // Step 7 — History
  runHistory: ExperimentRunSummary[];
  // Step 10 — Detected Slick Characterization
  slickCharacterization: SlickCharacterization | null;
  // Step 1b.5 — SAR Subscene Acquisition
  acquiringSubscene: boolean;
  acquiredRasterPath: string | null;
  acquisitionResponse: SarAcquisitionResponse | null;
  acquisitionError: string | null;
}

// ---------------------------------------------------------------------------
// Synthetic ML Experiment Types
// ---------------------------------------------------------------------------

export interface SyntheticScenario {
  scenario_id: string;
  seed: number;
  observation_time: string;
  backtrack_hours: number;
  step_hours: number;
  origin_lon: number;
  origin_lat: number;
  spill_area_m2: number;
  wind_speed_ms: number;
  wind_direction_deg: number;
  current_speed_ms: number;
  current_direction_deg: number;
  ground_truth_vessel_id: string;
  estimated_source_lon: number;
  estimated_source_lat: number;
  candidate_count: number;
  vessels: Array<Record<string, unknown>>;
  is_synthetic: boolean;
}

export interface SyntheticCandidateScored {
  vessel_id: string;
  vessel_name: string;
  mmsi: string | null;
  vessel_type: string;
  rank: number;
  model_probability: number;
  scenario_normalized_attribution_score: number;
  has_meaningful_support: boolean;
  features: Record<string, number>;
  positions: Array<{ lat: number; lon: number; timestamp: string; speed?: number; heading?: number }>;
  is_ground_truth: boolean;
}

export interface SyntheticRunResult {
  run_id: string;
  is_synthetic: boolean;
  scenario_id: string;
  seed: number;
  observation_time: string;
  backtrack_hours: number;
  step_hours: number;
  origin_lon: number;
  origin_lat: number;
  spill_area_m2: number;
  wind_speed_ms: number;
  wind_direction_deg: number;
  current_speed_ms: number;
  current_direction_deg: number;
  u10: number;
  v10: number;
  uo: number;
  vo: number;
  model_id: string;
  model_type: string;
  source_lon: number;
  source_lat: number;
  source_radius_m: number;
  source_zone_geojson: Record<string, unknown>;
  backward_steps: BackwardStep[];
  vessels: SyntheticCandidateScored[];
  ground_truth_vessel_id: string;
  top_candidate_id: string | null;
  attribution_match: boolean;
  model_coefficients: Record<string, number>;
  model_test_metrics: Record<string, unknown>;
}

export interface ActiveModelInfo {
  model_id: string;
  model_type: string;
  trained_at: string;
  feature_coefficients: Record<string, number>;
  val_metrics: Record<string, unknown>;
  test_metrics: {
    candidate_accuracy?: number;
    candidate_precision?: number;
    candidate_recall?: number;
    candidate_f1?: number;
    candidate_roc_auc?: number;
    scenario_top1_accuracy?: number;
    total_candidates?: number;
    total_scenarios?: number;
    [key: string]: unknown;
  };
  training_sample_count: number;
  training_scenario_count: number;
}

// ---------------------------------------------------------------------------
// Evaluator Investigation Workflow
// ---------------------------------------------------------------------------

export type AisProviderBoundaryStatus = 'LIVE_AIS' | 'NO_PROVIDER' | 'NO_ELIGIBLE_VESSELS';

export interface EvaluatorReferenceObservation {
  id: string;
  title: string;
  image_file: string;
  image_path: string;
  region: string;
  observation_lon: number;
  observation_lat: number;
  observation_time: string;
  sensor: string;
  mode: string;
  slick_area_km2: number;
  is_historical_demo: boolean;
  historical_context: string;
  default_wind_speed_ms: number;
  default_wind_direction_deg: number;
  default_current_speed_ms: number;
  default_current_direction_deg: number;
  backtrack_hours: number;
  sar_subscene_path?: string | null;
  has_physical_raster?: boolean;
  raster_provenance?: string | null;
  detected_slick_metrics?: {
    detected: boolean;
    status: string;
    centroid_lon: number;
    centroid_lat: number;
    area_km2: number;
    area_m2?: number;
    damping_contrast_db?: number;
    confidence?: number;
    pixel_count?: number;
    detection_method?: string;
    provenance?: string;
    sar_raster_path?: string;
    slick_geometry?: Record<string, unknown>;
  } | null;
  benchmark_candidates?: Array<{
    id: string;
    vessel_name: string;
    mmsi: string | null;
    vessel_type: string;
    positions: Array<{ lat: number; lon: number; timestamp: string; speed?: number; heading?: number }>;
  }>;
}

export interface EvaluatorDriftPreviewRequest {
  origin_lon: number;
  origin_lat: number;
  observation_time: string;
  wind_speed_ms: number;
  wind_direction_deg: number;
  current_speed_ms: number;
  current_direction_deg: number;
  backtrack_hours?: number;
  step_hours?: number;
  spill_area_m2?: number;
  selected_image_id?: string;
  observation_source?: string;
}

export interface EvaluatorDriftPreviewResponse {
  observation_point: { lon: number; lat: number };
  observation_time: string;
  observation_source?: string;
  backtrack_hours: number;
  step_hours: number;
  reconstructed_source: {
    source_lon: number;
    source_lat: number;
    source_radius_m: number;
    source_radius_km: number;
    estimated_release_time: string;
    source_zone_geojson: Record<string, unknown>;
  };
  backward_steps: Array<{
    step_index: number;
    timestamp: string;
    lon: number;
    lat: number;
    uncertainty_radius_m: number;
    wind_u: number;
    wind_v: number;
    current_u: number;
    current_v: number;
    drift_u?: number;
    drift_v?: number;
    cumulative_distance_m?: number;
  }>;
  wind_vector: {
    speed_ms: number;
    direction_deg: number;
    u: number;
    v: number;
  };
  current_vector: {
    speed_ms: number;
    direction_deg: number;
    u: number;
    v: number;
  };
}

export interface EvaluatorCandidateVessel {
  vessel_id: string;
  vessel_name: string;
  mmsi: string | null;
  vessel_type: string;
  min_source_dist_km: number;
  min_trajectory_dist_km: number;
  min_corridor_dist_km: number;
  has_temporal_overlap: boolean;
  has_spatial_corridor_overlap: boolean;
  position_count: number;
  window_position_count: number;
  positions: Array<{ lat: number; lon: number; timestamp: string; speed?: number; heading?: number }>;
  rejection_reason?: string;
  rejection_detail?: string;
}

export interface EvaluatorFilterVesselsRequest {
  vessels: Array<Record<string, unknown>>;
  backward_steps: Array<Record<string, unknown>>;
  source_lon: number;
  source_lat: number;
  source_radius_m: number;
  observation_time: string;
  backtrack_hours?: number;
  corridor_km?: number;
}

export interface EvaluatorFilterVesselsResponse {
  corridor_km: number;
  temporal_window: {
    window_start: string;
    window_end: string;
    release_time: string;
    observation_time: string;
  };
  total_evaluated: number;
  eligible_count: number;
  ineligible_count: number;
  eligible_candidates: EvaluatorCandidateVessel[];
  ineligible_candidates: EvaluatorCandidateVessel[];
  provider_status: AisProviderBoundaryStatus;
  provider_description: string;
}

export interface EvaluatorRunRequest {
  selected_image_id: string;
  observation_lon: number;
  observation_lat: number;
  observation_time: string;
  wind_speed_ms: number;
  wind_direction_deg: number;
  current_speed_ms: number;
  current_direction_deg: number;
  corridor_km?: number;
  backtrack_hours?: number;
  step_hours?: number;
  spill_area_m2?: number;
  custom_vessels?: Array<Record<string, unknown>>;
  model_id?: string;
}

export interface EvaluatorCandidateScored {
  vessel_id: string;
  vessel_name: string;
  mmsi: string | null;
  vessel_type: string;
  rank: number;
  model_probability: number;
  scenario_normalized_score: number;
  features: Record<string, number>;
  corridor_dist_km: number;
  min_source_dist_km: number;
  time_difference_hours: number;
  heading_consistency: number;
  speed_consistency: number;
  positions: Array<{ lat: number; lon: number; timestamp: string; speed?: number; heading?: number }>;
}

export interface EvaluatorInvestigationRecord {
  investigation_id: string;
  selected_image_id: string;
  image_path: string;
  image_title: string;
  is_historical_demo: boolean;
  coordinates: {
    observation_lon: number;
    observation_lat: number;
  };
  acquisition_timestamp: string;
  wind_inputs: {
    speed_ms: number;
    direction_deg: number;
    u: number;
    v: number;
  };
  current_inputs: {
    speed_ms: number;
    direction_deg: number;
    u: number;
    v: number;
  };
  drift_parameters: {
    backtrack_hours: number;
    step_hours: number;
    corridor_km: number;
    spill_area_m2: number;
  };
  reconstructed_source: {
    source_lon: number;
    source_lat: number;
    source_radius_m: number;
    source_radius_km: number;
    estimated_release_time: string;
    source_zone_geojson: Record<string, unknown>;
  };
  backward_steps: Array<{
    step_index: number;
    timestamp: string;
    lon: number;
    lat: number;
    uncertainty_radius_m: number;
    wind_u: number;
    wind_v: number;
    current_u: number;
    current_v: number;
    drift_u?: number;
    drift_v?: number;
    cumulative_distance_m?: number;
  }>;
  provider_status: AisProviderBoundaryStatus;
  provider_description: string;
  filtering_summary: {
    corridor_km: number;
    total_candidates_checked: number;
    eligible_candidates_count: number;
    ineligible_candidates_count: number;
  };
  eligible_vessel_ids: string[];
  relevant_vessels_data: EvaluatorCandidateScored[];
  ineligible_vessels_data: EvaluatorCandidateVessel[];
  model_version: string;
  candidate_probabilities: EvaluatorCandidateScored[];
  final_attribution: {
    top_candidate: EvaluatorCandidateScored | null;
    eligible_candidate_count: number;
    ineligible_candidate_count: number;
    model_id: string;
    model_type: string;
    confidence_assessment: string;
  };
  observation_source?: string;
  created_at: string;
}

export interface EvaluatorInvestigationSummary {
  investigation_id: string;
  selected_image_id: string;
  image_title: string;
  image_path: string;
  acquisition_timestamp: string;
  model_version: string;
  observation_source?: string;
  created_at: string;
  top_candidate?: EvaluatorCandidateScored | null;
}

