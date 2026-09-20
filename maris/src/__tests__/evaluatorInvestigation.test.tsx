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

async function selectFirstObservation() {
  const selectBtns = await screen.findAllByRole('button', { name: /Select This Observation/i })
  fireEvent.click(selectBtns[0])
}

function getFlowStepButton(stepNum: number): HTMLButtonElement {
  const titles = [
    'SELECT SENTINEL-1 IMAGE',
    'WIND + OCEAN CURRENT',
    'ELIGIBLE AIS VESSELS',
    'RUN ATTRIBUTION',
    'RESULTS + MAP',
    'SAVED INVESTIGATION',
  ]
  const title = titles[stepNum - 1]
  const allBtns = screen.getAllByRole('button')
  const found = allBtns.find(
    b => b.classList.contains('eval-flow-step') && b.textContent?.includes(title)
  )
  if (!found) throw new Error(`Flow step button ${stepNum} (${title}) not found`)
  return found as HTMLButtonElement
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

  // -------------------------------------------------------------------------
  // 12 Mandatory Sequential Navigation & State Gating Scenarios
  // -------------------------------------------------------------------------

  it('Scenario 1: Initial state has Step 1 active and Steps 2–6 strictly locked', async () => {
    render(<EvaluatorInvestigationSection />)
    await screen.findByText(/Cap Corse \/ Northern Corsica/i)

    const step1 = getFlowStepButton(1)
    const step2 = getFlowStepButton(2)
    const step3 = getFlowStepButton(3)
    const step4 = getFlowStepButton(4)
    const step5 = getFlowStepButton(5)
    const step6 = getFlowStepButton(6)

    expect(step1.className).toContain('active')
    expect(step1.disabled).toBe(false)

    expect(step2.disabled).toBe(true)
    expect(step2.className).toContain('locked')

    expect(step3.disabled).toBe(true)
    expect(step3.className).toContain('locked')

    expect(step4.disabled).toBe(true)
    expect(step4.className).toContain('locked')

    expect(step5.disabled).toBe(true)
    expect(step5.className).toContain('locked')

    expect(step6.disabled).toBe(true)
    expect(step6.className).toContain('locked')

    expect(screen.getByText(/Step 1: Select Verified Sentinel-1 SAR Reference Observation/i)).toBeDefined()
  })

  it('Scenario 2: Attempt to click Step 6 from Step 1 is a strict NO-OP', async () => {
    render(<EvaluatorInvestigationSection />)
    await screen.findByText(/Cap Corse \/ Northern Corsica/i)

    const step6 = getFlowStepButton(6)
    fireEvent.click(step6)

    // Active step remains Step 1
    expect(screen.getByText(/Step 1: Select Verified Sentinel-1 SAR Reference Observation/i)).toBeDefined()
    expect(screen.queryByText(/Step 6: Saved Investigation/i)).toBeNull()
    expect(screen.queryByText(/Historical Saved Investigations/i)).toBeNull()

    // No workflow APIs called
    expect(experimentApi.previewEvaluatorDrift).not.toHaveBeenCalled()
    expect(experimentApi.filterEvaluatorVessels).not.toHaveBeenCalled()
    expect(experimentApi.runEvaluatorInvestigation).not.toHaveBeenCalled()
  })

  it('Scenario 3: Completing Step 1 unlocks Step 2 while Steps 3–6 remain locked', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    // Step 2 is now active
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()

    const step1 = getFlowStepButton(1)
    const step2 = getFlowStepButton(2)
    const step3 = getFlowStepButton(3)
    const step4 = getFlowStepButton(4)
    const step5 = getFlowStepButton(5)
    const step6 = getFlowStepButton(6)

    expect(step1.className).toContain('completed')
    expect(step1.disabled).toBe(false)

    expect(step2.className).toContain('active')
    expect(step2.disabled).toBe(false)

    expect(step3.disabled).toBe(true)
    expect(step3.className).toContain('locked')
    expect(step4.disabled).toBe(true)
    expect(step4.className).toContain('locked')
    expect(step5.disabled).toBe(true)
    expect(step5.className).toContain('locked')
    expect(step6.disabled).toBe(true)
    expect(step6.className).toContain('locked')
  })

  it('Scenario 4: Attempt to click Step 4 while on Step 2 does not navigate', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()

    const step4 = getFlowStepButton(4)
    fireEvent.click(step4)

    // Still on Step 2
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()
    expect(screen.queryByText(/Step 4: Execute Attribution/i)).toBeNull()
    expect(experimentApi.runEvaluatorInvestigation).not.toHaveBeenCalled()
  })

  it('Scenario 5: Completing Step 2 unlocks Step 3', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    await waitFor(() => {
      expect(screen.getByText(/Step 3: Filter Eligible AIS Vessels/i)).toBeDefined()
    })

    const step3 = getFlowStepButton(3)
    expect(step3.disabled).toBe(false)
    expect(step3.className).toContain('active')
  })

  it('Scenario 6: Completing Step 3 unlocks Step 4', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Proceed to ML Attribution/i })).toBeDefined()
    })

    const proceedBtn = screen.getByRole('button', { name: /Proceed to ML Attribution/i }) as HTMLButtonElement
    expect(proceedBtn.disabled).toBe(false)
    fireEvent.click(proceedBtn)

    expect(screen.getByText(/Step 4: Execute Attribution with Active Scaled ML Model/i)).toBeDefined()

    const step4 = getFlowStepButton(4)
    expect(step4.disabled).toBe(false)
    expect(step4.className).toContain('active')
  })

  it('Scenario 7: Completing Step 4 unlocks Step 5 and persistance unlocks Step 6', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Proceed to ML Attribution/i })).toBeDefined()
    })
    fireEvent.click(screen.getByRole('button', { name: /Proceed to ML Attribution/i }))

    const runBtn = screen.getByRole('button', { name: /RUN MARIS ATTRIBUTION NOW/i })
    fireEvent.click(runBtn)

    await waitFor(() => {
      expect(screen.getByText(/Step 5: Attribution Results & Interactive Investigation Map/i)).toBeDefined()
    })

    const step5 = getFlowStepButton(5)
    const step6 = getFlowStepButton(6)

    expect(step5.disabled).toBe(false)
    expect(step5.className).toContain('active')

    // Since attribution persists to SQLite, Step 6 is now unlocked
    expect(step6.disabled).toBe(false)
    expect(step6.className).not.toContain('locked')
  })

  it('Scenario 8: Completing Step 5 allows accessing Step 6 normally', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Proceed to ML Attribution/i })).toBeDefined()
    })
    fireEvent.click(screen.getByRole('button', { name: /Proceed to ML Attribution/i }))

    const runBtn = screen.getByRole('button', { name: /RUN MARIS ATTRIBUTION NOW/i })
    fireEvent.click(runBtn)

    await waitFor(() => {
      expect(screen.getByText(/Step 5: Attribution Results/i)).toBeDefined()
    })

    const viewSavedBtn = screen.getByRole('button', { name: /View Saved Investigation & History →/i })
    fireEvent.click(viewSavedBtn)

    expect(screen.getByText(/Step 6: Saved Investigations & Replay Records/i)).toBeDefined()
    expect(screen.getByText(/Historical Saved Investigations/i)).toBeDefined()
  })

  it('Scenario 9: Zero eligible AIS candidates legitimately completes Step 3 and unlocks Step 4', async () => {
    // Mock 0 eligible candidates
    vi.spyOn(experimentApi, 'filterEvaluatorVessels').mockResolvedValue({
      corridor_km: 25.0,
      temporal_window: mockFilterResponse.temporal_window,
      total_evaluated: 1,
      eligible_count: 0,
      ineligible_count: 1,
      eligible_candidates: [],
      ineligible_candidates: mockFilterResponse.ineligible_candidates,
      provider_status: 'NO_ELIGIBLE_VESSELS',
      provider_description: 'No candidates met spatial corridor or temporal intersection criteria.',
    })

    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    await waitFor(() => {
      expect(screen.getByText(/NO ELIGIBLE VESSELS/i)).toBeDefined()
    })

    const proceedBtn = screen.getByRole('button', { name: /Proceed to ML Attribution \(0 Eligible Vessels\) →/i }) as HTMLButtonElement
    expect(proceedBtn.disabled).toBe(false)
    fireEvent.click(proceedBtn)

    expect(screen.getByText(/Step 4: Execute Attribution with Active Scaled ML Model/i)).toBeDefined()
    expect(screen.getAllByText(/0 Vessels/i).length).toBeGreaterThan(0)
  })

  it('Scenario 10: Failed API operation does not mark step complete and keeps next step locked', async () => {
    vi.spyOn(experimentApi, 'previewEvaluatorDrift').mockRejectedValue(new Error('CMEMS service offline'))

    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    const calcDriftBtn = screen.getByRole('button', { name: /Calculate Backward Drift →/i })
    fireEvent.click(calcDriftBtn)

    await waitFor(() => {
      expect(screen.getByText(/CMEMS service offline/i)).toBeDefined()
    })

    // User remains on Step 2
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()

    // Step 3 remains locked
    const step3 = getFlowStepButton(3)
    expect(step3.disabled).toBe(true)
    expect(step3.className).toContain('locked')

    fireEvent.click(step3)
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()
    expect(screen.queryByText(/Step 3: Filter Eligible AIS Vessels/i)).toBeNull()
  })

  it('Scenario 11: Previously saved investigation restores completed state with Step 6 accessible', async () => {
    render(<EvaluatorInvestigationSection initialInvestigationId="inv_eval_test_001" />)

    await waitFor(() => {
      expect(screen.getByText(/Replaying saved investigation inv_eval_test_001/i)).toBeDefined()
    })

    const step1 = getFlowStepButton(1)
    const step2 = getFlowStepButton(2)
    const step3 = getFlowStepButton(3)
    const step4 = getFlowStepButton(4)
    const step5 = getFlowStepButton(5)
    const step6 = getFlowStepButton(6)

    // All steps unlocked for replayed investigation
    expect(step1.disabled).toBe(false)
    expect(step2.disabled).toBe(false)
    expect(step3.disabled).toBe(false)
    expect(step4.disabled).toBe(false)
    expect(step5.disabled).toBe(false)
    expect(step6.disabled).toBe(false)

    // Clicking Step 6 opens saved investigation view normally
    fireEvent.click(step6)
    expect(screen.getByText(/Historical Saved Investigations/i)).toBeDefined()
    expect(screen.getAllByText(/inv_eval_test_001/i).length).toBeGreaterThan(0)
  })

  it('Scenario 12: Back navigation works for completed steps while future incomplete steps remain locked', async () => {
    render(<EvaluatorInvestigationSection />)
    await selectFirstObservation()

    // Now on Step 2. Step 1 is complete.
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()

    // Navigate back to Step 1
    const step1 = getFlowStepButton(1)
    fireEvent.click(step1)
    expect(screen.getByText(/Step 1: Select Verified Sentinel-1 SAR Reference Observation/i)).toBeDefined()

    // Can navigate forward to unlocked Step 2
    const step2 = getFlowStepButton(2)
    fireEvent.click(step2)
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()

    // Cannot jump forward to locked Step 3 or 5
    const step3 = getFlowStepButton(3)
    const step5 = getFlowStepButton(5)
    fireEvent.click(step3)
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()
    fireEvent.click(step5)
    expect(screen.getByText(/Step 2: Set Environmental Forcing/i)).toBeDefined()
  })
})
