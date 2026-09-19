import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SyntheticExperimentSection from '../components/views/SyntheticExperimentSection'
import RealExperimentView from '../components/views/RealExperimentView'
import * as experimentApi from '../real-experiment/experimentApi'

vi.mock('../real-experiment/experimentApi', () => ({
  fetchExperimentConfig: vi.fn().mockResolvedValue({
    sentinel1_configured: false,
    era5_configured: false,
    cmems_configured: false,
    ais_configured: false,
    ais_adapter_id: 'mock',
    all_configured: false,
    warnings: [],
  }),
  discoverSentinelProducts: vi.fn(),
  selectEnvironment: vi.fn(),
  searchAisVessels: vi.fn(),
  runExperiment: vi.fn(),
  listExperimentRuns: vi.fn().mockResolvedValue({ runs: [], count: 0 }),
  getExperimentRun: vi.fn(),
  fetchActiveModelInfo: vi.fn().mockResolvedValue({
    model_id: 'attr_lr_test_001',
    model_type: 'logistic_regression',
    trained_at: '2024-06-15T12:00:00Z',
    feature_coefficients: {
      min_source_distance_km: -0.85,
      reconstructed_source_proximity: 1.24,
      heading_consistency: 0.42,
    },
    val_metrics: {},
    test_metrics: {
      scenario_top1_accuracy: 0.95,
      candidate_roc_auc: 0.98,
      candidate_f1: 0.91,
    },
    training_sample_count: 140,
    training_scenario_count: 35,
  }),
  generateSyntheticScenario: vi.fn().mockResolvedValue({
    scenario_id: 'syn_test_123',
    seed: 123456,
    observation_time: '2024-06-15T10:00:00Z',
    backtrack_hours: 6,
    step_hours: 0.5,
    origin_lon: 9.45,
    origin_lat: 43.25,
    spill_area_m2: 120000,
    wind_speed_ms: 7.2,
    wind_direction_deg: 210,
    current_speed_ms: 0.25,
    current_direction_deg: 230,
    ground_truth_vessel_id: '228001000',
    estimated_source_lon: 9.2,
    estimated_source_lat: 43.1,
    candidate_count: 4,
    vessels: [
      {
        id: '228001000',
        mmsi: '228001000',
        vessel_name: 'TEST-GROUND-TRUTH',
        vessel_type: 'Tanker',
        is_ground_truth: true,
      },
      {
        id: '333002000',
        mmsi: '333002000',
        vessel_name: 'TEST-DISTRACTOR-1',
        vessel_type: 'Cargo',
        is_ground_truth: false,
      },
    ],
    is_synthetic: true,
  }),
  runSyntheticExperiment: vi.fn().mockResolvedValue({
    run_id: 'syn_run_test_999',
    is_synthetic: true,
    scenario_id: 'syn_test_123',
    seed: 123456,
    observation_time: '2024-06-15T10:00:00Z',
    backtrack_hours: 6,
    step_hours: 0.5,
    origin_lon: 9.45,
    origin_lat: 43.25,
    spill_area_m2: 120000,
    wind_speed_ms: 7.2,
    wind_direction_deg: 210,
    current_speed_ms: 0.25,
    current_direction_deg: 230,
    u10: -3.6,
    v10: -6.2,
    uo: -0.15,
    vo: -0.2,
    model_id: 'attr_lr_test_001',
    model_type: 'logistic_regression',
    source_lon: 9.2,
    source_lat: 43.1,
    source_radius_m: 850,
    source_zone_geojson: { type: 'Polygon', coordinates: [] },
    backward_steps: [
      { step: 0, lon: 9.45, lat: 43.25, uncertainty_radius_m: 500 },
      { step: 1, lon: 9.2, lat: 43.1, uncertainty_radius_m: 850 },
    ],
    vessels: [
      {
        vessel_id: '228001000',
        vessel_name: 'TEST-GROUND-TRUTH',
        mmsi: '228001000',
        vessel_type: 'Tanker',
        rank: 1,
        model_probability: 0.892,
        scenario_normalized_attribution_score: 0.745,
        has_meaningful_support: true,
        features: {
          min_source_distance_km: 0.45,
          temporal_overlap_hours: 5.5,
          trajectory_overlap_fraction: 0.65,
          heading_consistency: 0.82,
          speed_consistency: 0.9,
          ais_position_count: 36,
          ais_coverage_fraction: 1.0,
          reconstructed_source_proximity: 0.88,
          drift_trajectory_min_distance_km: 0.45,
          time_difference_hours: 0.2,
        },
        positions: [],
        is_ground_truth: true,
      },
      {
        vessel_id: '333002000',
        vessel_name: 'TEST-DISTRACTOR-1',
        mmsi: '333002000',
        vessel_type: 'Cargo',
        rank: 2,
        model_probability: 0.125,
        scenario_normalized_attribution_score: 0.255,
        has_meaningful_support: false,
        features: {
          min_source_distance_km: 18.2,
          temporal_overlap_hours: 4.0,
          trajectory_overlap_fraction: 0.0,
          heading_consistency: 0.35,
          speed_consistency: 0.85,
          ais_position_count: 24,
          ais_coverage_fraction: 0.8,
          reconstructed_source_proximity: 0.05,
          drift_trajectory_min_distance_km: 15.1,
          time_difference_hours: 3.5,
        },
        positions: [],
        is_ground_truth: false,
      },
    ],
    ground_truth_vessel_id: '228001000',
    top_candidate_id: '228001000',
    attribution_match: true,
    model_coefficients: {
      reconstructed_source_proximity: 1.24,
    },
    model_test_metrics: {
      scenario_top1_accuracy: 0.95,
    },
  }),
  trainAttributionModel: vi.fn().mockResolvedValue({
    model_id: 'attr_lr_retrained',
  }),
}))

describe('SyntheticExperimentSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders parameter controls and active model info', async () => {
    render(<SyntheticExperimentSection />)

    expect(screen.getByText(/1\. Scenario & Environmental Controls/i)).toBeDefined()
    expect(screen.getByText(/2\. Trained ML Attribution Model/i)).toBeDefined()
    expect(screen.getByLabelText(/Scenario Seed/i)).toBeDefined()
    expect(screen.getByLabelText(/Wind Speed/i)).toBeDefined()
    expect(screen.getByLabelText(/Candidate Count/i)).toBeDefined()

    // Verifies model metrics loaded from API
    await waitFor(() => {
      expect(screen.getByText('95.0%')).toBeDefined() // scenario_top1_accuracy
      expect(screen.getByText(/attr_lr_test_001/i)).toBeDefined()
    })
  })

  it('generates a synthetic scenario preview upon clicking Generate Scenario', async () => {
    render(<SyntheticExperimentSection />)

    const genBtn = screen.getByRole('button', { name: /Generate Scenario/i })
    fireEvent.click(genBtn)

    await waitFor(() => {
      expect(experimentApi.generateSyntheticScenario).toHaveBeenCalled()
      expect(screen.getByText(/Generated Unseen Scenario Preview/i)).toBeDefined()
      expect(screen.getByText(/TEST-GROUND-TRUTH/i)).toBeDefined()
    })
  })

  it('executes drift physics and ML attribution upon clicking Run Drift & ML Attribution', async () => {
    render(<SyntheticExperimentSection />)

    const runBtn = screen.getByRole('button', { name: /Run Drift & ML Attribution/i })
    fireEvent.click(runBtn)

    await waitFor(() => {
      expect(experimentApi.runSyntheticExperiment).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(screen.getByText(/Attribution Results & ML Evaluation/i)).toBeDefined()
    })
    expect(screen.getByText(/Correct Ground-Truth Match/i)).toBeDefined()
    expect(screen.getByText('89.2%')).toBeDefined() // independent model probability
    expect(screen.getByText('74.5%')).toBeDefined() // scenario-normalized score
    expect(screen.getAllByText(/scenario-normalized/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/YES \(Actual Spiller\)/i)).toBeDefined()
  })
})

describe('RealExperimentView Mode Switcher', () => {
  it('switches between Real-Data wizard and Synthetic ML Experiment pipeline', async () => {
    render(<RealExperimentView />)

    // Initially in real data mode
    expect(screen.getByText(/REAL DATA/i)).toBeDefined()
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    expect(realTab.className).toContain('active')

    // Switch to synthetic mode
    const synTab = screen.getByRole('button', { name: /Synthetic ML Experiment Pipeline/i })
    fireEvent.click(synTab)

    expect(screen.getByText('SYNTHETIC ML')).toBeDefined()
    expect(synTab.className).toContain('active')
    expect(screen.getByText(/1\. Scenario & Environmental Controls/i)).toBeDefined()
  })
})
