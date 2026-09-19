import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import EvaluatorInvestigationSection from '../components/views/EvaluatorInvestigationSection'
import * as experimentApi from '../real-experiment/experimentApi'
import type {
  EvaluatorReferenceObservation,
  EvaluatorDriftPreviewResponse,
  EvaluatorFilterVesselsResponse,
  EvaluatorInvestigationRecord,
} from '../real-experiment/experimentTypes'

const mockObservations: EvaluatorReferenceObservation[] = [
  {
    id: 'ref_corsica_2018',
    title: 'Cap Corse / Northern Corsica (Historical Demo Benchmark)',
    image_file: 'corsica_2018_s1.jpg',
    image_path: '/satellite/corsica_2018_s1.jpg',
    region: 'Cap Corse, Mediterranean Sea',
    observation_lon: 9.47833,
    observation_lat: 43.24833,
    observation_time: '2018-10-08T05:28:00Z',
    sensor: 'Sentinel-1A C-SAR (Historical Scene)',
    mode: 'IW / GRDH',
    slick_area_km2: 45.2,
    is_historical_demo: true,
    historical_context: 'Historical Corsica benchmark scene (October 2018 collision between Ulysse and CSL Virginia).',
    default_wind_speed_ms: 7.2,
    default_wind_direction_deg: 235.0,
    default_current_speed_ms: 0.22,
    default_current_direction_deg: 35.0,
    backtrack_hours: 8.0,
    benchmark_candidates: [
      {
        id: 'vessel_ulysse',
        vessel_name: 'MV ULYSSE',
        mmsi: '228308800',
        vessel_type: 'Ro-Ro Cargo',
        positions: [
          { timestamp: '2018-10-08T00:00:00Z', lat: 43.26, lon: 9.45, speed: 17.8, heading: 216.0 },
        ],
      },
    ],
  },
  {
    id: 'ref_arabian_sea_alpha',
    title: 'Central Arabian Sea Transit Corridor',
    image_file: 'sentinel1_arabian_sea_alpha.png',
    image_path: '/satellite/sentinel1_arabian_sea_alpha.png',
    region: 'Central Arabian Sea',
    observation_lon: 66.198,
    observation_lat: 15.642,
    observation_time: '2025-03-15T05:42:00Z',
    sensor: 'Sentinel-1 C-SAR IW',
    mode: 'IW / GRDH',
    slick_area_km2: 28.4,
    is_historical_demo: false,
    historical_context: 'Reference Sentinel-1 SAR acquisition capturing slick signature.',
    default_wind_speed_ms: 5.8,
    default_wind_direction_deg: 310.0,
    default_current_speed_ms: 0.18,
    default_current_direction_deg: 120.0,
    backtrack_hours: 6.0,
  },
]

const mockDriftPreview: EvaluatorDriftPreviewResponse = {
  observation_point: { lon: 9.47833, lat: 43.24833 },
  observation_time: '2018-10-08T05:28:00Z',
  backtrack_hours: 8.0,
  step_hours: 0.5,
  reconstructed_source: {
    source_lon: 9.45,
    source_lat: 43.20,
    source_radius_m: 4500.0,
    source_radius_km: 4.5,
    estimated_release_time: '2018-10-07T21:28:00Z',
    source_zone_geojson: { type: 'Polygon', coordinates: [] },
  },
  backward_steps: [
    {
      step_index: 0,
      timestamp: '2018-10-08T05:28:00Z',
      lon: 9.47833,
      lat: 43.24833,
      uncertainty_radius_m: 500,
      wind_u: -4.5,
      wind_v: -3.2,
      current_u: 0.15,
      current_v: 0.1,
    },
  ],
  wind_vector: { speed_ms: 7.2, direction_deg: 235.0, u: -4.5, v: -3.2 },
  current_vector: { speed_ms: 0.22, direction_deg: 35.0, u: 0.15, v: 0.1 },
}

const mockFilterResponse: EvaluatorFilterVesselsResponse = {
  corridor_km: 25.0,
  temporal_window: {
    window_start: '2018-10-07T20:28:00Z',
    window_end: '2018-10-08T06:28:00Z',
    release_time: '2018-10-07T21:28:00Z',
    observation_time: '2018-10-08T05:28:00Z',
  },
  total_evaluated: 2,
  eligible_count: 1,
  ineligible_count: 1,
  eligible_candidates: [
    {
      vessel_id: 'vessel_ulysse',
      vessel_name: 'MV ULYSSE',
      mmsi: '228308800',
      vessel_type: 'Ro-Ro Cargo',
      min_source_dist_km: 3.2,
      min_trajectory_dist_km: 1.5,
      min_corridor_dist_km: 1.5,
      has_temporal_overlap: true,
      has_spatial_corridor_overlap: true,
      position_count: 5,
      window_position_count: 5,
      positions: [],
    },
  ],
  ineligible_candidates: [
    {
      vessel_id: 'vessel_far',
      vessel_name: 'DISTANT SHIP',
      mmsi: '999888777',
      vessel_type: 'Tanker',
      min_source_dist_km: 75.0,
      min_trajectory_dist_km: 68.0,
      min_corridor_dist_km: 68.0,
      has_temporal_overlap: true,
      has_spatial_corridor_overlap: false,
      position_count: 4,
      window_position_count: 4,
      positions: [],
      rejection_reason: 'OUTSIDE_CORRIDOR_25.0KM',
      rejection_detail: 'Min corridor distance was 68.0 km (limit: 25.0 km)',
    },
  ],
  provider_status: 'LIVE_AIS',
  provider_description: 'Active AIS source available with 1 eligible candidates within investigation corridor.',
}

const mockInvestigationResult: EvaluatorInvestigationRecord = {
  investigation_id: 'inv_eval_test_001',
  selected_image_id: 'ref_corsica_2018',
  image_path: '/satellite/corsica_2018_s1.jpg',
  image_title: 'Cap Corse / Northern Corsica (Historical Demo Benchmark)',
  is_historical_demo: true,
  coordinates: { observation_lon: 9.47833, observation_lat: 43.24833 },
  acquisition_timestamp: '2018-10-08T05:28:00Z',
  wind_inputs: { speed_ms: 7.2, direction_deg: 235.0, u: -4.5, v: -3.2 },
  current_inputs: { speed_ms: 0.22, direction_deg: 35.0, u: 0.15, v: 0.1 },
  drift_parameters: { backtrack_hours: 8.0, step_hours: 0.5, corridor_km: 25.0, spill_area_m2: 100000.0 },
  reconstructed_source: mockDriftPreview.reconstructed_source,
  backward_steps: mockDriftPreview.backward_steps,
  provider_status: 'LIVE_AIS',
  provider_description: 'Active AIS source available with 1 eligible candidate within investigation corridor.',
  filtering_summary: {
    corridor_km: 25.0,
    total_candidates_checked: 2,
    eligible_candidates_count: 1,
    ineligible_candidates_count: 1,
  },
  eligible_vessel_ids: ['vessel_ulysse'],
  relevant_vessels_data: [
    {
      vessel_id: 'vessel_ulysse',
      vessel_name: 'MV ULYSSE',
      mmsi: '228308800',
      vessel_type: 'Ro-Ro Cargo',
      rank: 1,
      model_probability: 0.942,
      scenario_normalized_score: 1.0,
      features: { min_source_distance_km: 3.2, time_difference_hours: 0.5, heading_consistency: 0.85, speed_consistency: 0.9 },
      corridor_dist_km: 1.5,
      min_source_dist_km: 3.2,
      time_difference_hours: 0.5,
      heading_consistency: 0.85,
      speed_consistency: 0.9,
      positions: [],
    },
  ],
  ineligible_vessels_data: mockFilterResponse.ineligible_candidates,
  model_version: 'attr_lr_scaled_10k_20260919_183349',
  candidate_probabilities: [
    {
      vessel_id: 'vessel_ulysse',
      vessel_name: 'MV ULYSSE',
      mmsi: '228308800',
      vessel_type: 'Ro-Ro Cargo',
      rank: 1,
      model_probability: 0.942,
      scenario_normalized_score: 1.0,
      features: { min_source_distance_km: 3.2, time_difference_hours: 0.5, heading_consistency: 0.85, speed_consistency: 0.9 },
      corridor_dist_km: 1.5,
      min_source_dist_km: 3.2,
      time_difference_hours: 0.5,
      heading_consistency: 0.85,
      speed_consistency: 0.9,
      positions: [],
    },
  ],
  final_attribution: {
    top_candidate: {
      vessel_id: 'vessel_ulysse',
      vessel_name: 'MV ULYSSE',
      mmsi: '228308800',
      vessel_type: 'Ro-Ro Cargo',
      rank: 1,
      model_probability: 0.942,
      scenario_normalized_score: 1.0,
      features: { min_source_distance_km: 3.2, time_difference_hours: 0.5, heading_consistency: 0.85, speed_consistency: 0.9 },
      corridor_dist_km: 1.5,
      min_source_dist_km: 3.2,
      time_difference_hours: 0.5,
      heading_consistency: 0.85,
      speed_consistency: 0.9,
      positions: [],
    },
    eligible_candidate_count: 1,
    ineligible_candidate_count: 1,
    model_id: 'attr_lr_scaled_10k_20260919_183349',
    model_type: 'logistic_regression',
    confidence_assessment: 'Top attributed vessel is MV ULYSSE with independent model probability of 94.2%.',
  },
  created_at: '2026-09-19T20:00:00Z',
}

describe('EvaluatorInvestigationSection Component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(experimentApi, 'fetchEvaluatorReferenceObservations').mockResolvedValue(mockObservations)
    vi.spyOn(experimentApi, 'previewEvaluatorDrift').mockResolvedValue(mockDriftPreview)
    vi.spyOn(experimentApi, 'filterEvaluatorVessels').mockResolvedValue(mockFilterResponse)
    vi.spyOn(experimentApi, 'runEvaluatorInvestigation').mockResolvedValue(mockInvestigationResult)
    vi.spyOn(experimentApi, 'listEvaluatorInvestigations').mockResolvedValue([
      {
        investigation_id: 'inv_eval_test_001',
        selected_image_id: 'ref_corsica_2018',
        image_title: 'Cap Corse / Northern Corsica (Historical Demo Benchmark)',
        image_path: '/satellite/corsica_2018_s1.jpg',
        acquisition_timestamp: '2018-10-08T05:28:00Z',
        model_version: 'attr_lr_scaled_10k_20260919_183349',
        created_at: '2026-09-19T20:00:00Z',
      },
    ])
    vi.spyOn(experimentApi, 'getEvaluatorInvestigation').mockResolvedValue(mockInvestigationResult)
  })

  it('renders the 6-step visual architecture diagram in the header', async () => {
    render(<EvaluatorInvestigationSection />)

    expect(await screen.findByText('NEW MARIS INVESTIGATION WORKFLOW')).toBeDefined()
    expect(screen.getByText('SELECT SENTINEL-1 IMAGE')).toBeDefined()
    expect(screen.getByText('WIND + OCEAN CURRENT')).toBeDefined()
    expect(screen.getByText('ELIGIBLE AIS VESSELS')).toBeDefined()
    expect(screen.getByText('RUN ATTRIBUTION')).toBeDefined()
    expect(screen.getByText('RESULTS + MAP')).toBeDefined()
    expect(screen.getByText('SAVED INVESTIGATION')).toBeDefined()
  })

  it('displays reference observations with truthful Corsica historical demo semantics', async () => {
    render(<EvaluatorInvestigationSection />)

    expect(await screen.findByText(/Cap Corse \/ Northern Corsica/i)).toBeDefined()
    expect(screen.getByText('HISTORICAL DEMO')).toBeDefined()
    expect(screen.getByText(/October 2018 collision between Ulysse and CSL Virginia/i)).toBeDefined()

    expect(screen.getByText('VERIFIED S-1 SAR')).toBeDefined()
    expect(screen.getByText(/Central Arabian Sea Transit Corridor/i)).toBeDefined()
  })

  it('navigates through drift preview and executes strict candidate filtering', async () => {
    render(<EvaluatorInvestigationSection />)

    // Step 1: Select observation
    const selectBtn = await screen.findByRole('button', { name: /✓ Selected Observation/i })
    fireEvent.click(selectBtn)

    // Step 2: Set environmental parameters & click calculate drift
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()
    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    // Step 3: Candidate filtering displayed with explicit provider boundary
    await waitFor(() => {
      expect(screen.getByText(/Step 3: Filter Eligible AIS Vessels/i)).toBeDefined()
    })
    expect(screen.getByText(/LIVE AIS PROVIDER AVAILABLE/i)).toBeDefined()
    expect(screen.getByText('MV ULYSSE')).toBeDefined()
    expect(screen.getByText('ELIGIBLE FOR ML')).toBeDefined()
    expect(screen.getByText('DISTANT SHIP')).toBeDefined()
    expect(screen.getByText(/EXCLUDED: OUTSIDE_CORRIDOR_25.0KM/i)).toBeDefined()
  })

  it('executes attribution with active scaled ML model and shows results with map', async () => {
    render(<EvaluatorInvestigationSection />)

    // Step 1 -> Step 2
    const selectBtn = await screen.findByRole('button', { name: /✓ Selected Observation/i })
    fireEvent.click(selectBtn)

    // Step 2 -> Step 3
    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    // Step 3 -> Step 4
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Proceed to ML Attribution/i })).toBeDefined()
    })
    fireEvent.click(screen.getByRole('button', { name: /Proceed to ML Attribution/i }))

    // Step 4: Active scaled model confirmation
    expect(screen.getByText(/Active Model:/i)).toBeDefined()
    expect(screen.getByText(/10,000 Scenarios/i)).toBeDefined()

    // Step 4 -> Step 5: Run Attribution
    const runBtn = screen.getByRole('button', { name: /RUN MARIS ATTRIBUTION NOW/i })
    fireEvent.click(runBtn)

    // Step 5: Results & Map
    await waitFor(() => {
      expect(screen.getByText(/MOST PROBABLE SPILLER \(RANK #1\)/i)).toBeDefined()
    })
    expect(screen.getAllByText('94.2%').length).toBeGreaterThan(0) // model_probability in hero card & table
    expect(screen.getByText(/Geographic Reconstruction & Attribution Overlay/i)).toBeDefined()

    // "Why this vessel?" Evidence Panel assertions
    expect(screen.getByText(/“Why this vessel\?” Evidence Panel/i)).toBeDefined()
    expect(screen.getAllByText(/Spatial Distance/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Temporal Offset \/ Overlap/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Heading Consistency/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Speed Consistency/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Drift-Track Proximity/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Actual ML Features Used by Classifier/i)).toBeDefined()
    expect(screen.getByText(/Excluded Vessels — Pre-ML Quarantine/i)).toBeDefined()
    expect(screen.getByText(/OUTSIDE_CORRIDOR_25.0KM/i)).toBeDefined()
  })

  it('replays a saved investigation from history without re-running analysis', async () => {
    render(<EvaluatorInvestigationSection />)

    // Click step 6 in flow header to view saved investigations
    const step6Btn = await screen.findByRole('button', { name: /SAVED INVESTIGATION/i })
    fireEvent.click(step6Btn)

    expect(screen.getByText(/Historical Saved Investigations/i)).toBeDefined()
    const replayBtn = await screen.findByRole('button', { name: /Replay \/ View/i })
    fireEvent.click(replayBtn)

    await waitFor(() => {
      expect(screen.getByText(/Replaying saved investigation inv_eval_test_001/i)).toBeDefined()
    })
    expect(screen.getByText(/Analysis was NOT rerun/i)).toBeDefined()
  })
})
