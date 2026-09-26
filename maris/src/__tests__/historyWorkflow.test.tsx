/**
 * Step 7 — History & Experiment Record / Run Audit Trail Test Suite.
 *
 * Verifies:
 * - History page rendering, completed runs table, and status badges.
 * - Empty state, loading state, and error handling with retry.
 * - Historical run detail view with metadata, observation, environment, drift config.
 * - Reusable historical attribution map rendering without rerunning.
 * - Authoritative backend ordering and scientific invariance.
 * - Full navigation: Step 6 → Step 7, History → Detail, Detail → History, Detail → Evaluator.
 * - Factual run comparison without evaluative bias.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import RealExperimentView from '../components/views/RealExperimentView'
import HistoryView, { ReproducibilityCard, RunComparisonSection } from '../components/views/HistoryView'
import * as experimentApi from '../real-experiment/experimentApi'
import type {
  ExperimentRunResult,
  ExperimentRunSummary,
  VesselFeatures,
} from '../real-experiment/experimentTypes'

// Mock maplibre-gl
vi.mock('maplibre-gl', () => {
  const mockMap = {
    on: vi.fn((event, cb) => {
      if (event === 'load') cb()
    }),
    remove: vi.fn(),
    addSource: vi.fn(),
    addLayer: vi.fn(),
    getSource: vi.fn().mockReturnValue(null),
    getLayer: vi.fn().mockReturnValue(null),
    flyTo: vi.fn(),
    fitBounds: vi.fn(),
    project: vi.fn(() => ({ x: 100, y: 100 })),
  }
  const mockMarker = {
    setLngLat: vi.fn().mockReturnThis(),
    setPopup: vi.fn().mockReturnThis(),
    addTo: vi.fn().mockReturnThis(),
    remove: vi.fn(),
  }
  return {
    Map: vi.fn(() => mockMap),
    Marker: vi.fn(() => mockMarker),
    Popup: vi.fn(() => ({ setHTML: vi.fn().mockReturnThis() })),
    NavigationControl: vi.fn(),
  }
})

vi.mock('../real-experiment/experimentApi', async () => {
  const actual = await vi.importActual<typeof experimentApi>('../real-experiment/experimentApi')
  return {
    ...actual,
    fetchExperimentConfig: vi.fn().mockResolvedValue({
      sentinel1_configured: true,
      era5_configured: true,
      cmems_configured: true,
      ais_configured: true,
      ais_adapter_id: 'sqlite_benchmark',
      all_configured: true,
      warnings: [],
    }),
    listExperimentRuns: vi.fn(),
    getExperimentRun: vi.fn(),
    runExperiment: vi.fn(),
    selectEnvironment: vi.fn(),
    searchAisVessels: vi.fn(),
    fetchAisPositions: vi.fn(),
    discoverSentinelProducts: vi.fn(),
  }
})

const mockRunA: ExperimentRunSummary = {
  run_id: '1ed7ac6a-d6ff-4720-b2e4-2eb62939dbb5',
  satellite_product_id: 'S1A_IW_GRDH_1SDV_20181008T052807_Corsica',
  observation_time: '2018-10-08T05:28:07Z',
  observation_lat: 43.2483,
  observation_lon: 9.4783,
  backtrack_hours: 12,
  model_version: 'experiment_runner_v1+leeway_euler_backward_v1',
  source_lon: 9.5751,
  source_lat: 43.2276,
  source_radius_m: 6500,
  candidate_count: 2,
  status: 'completed',
  created_at: '2026-09-25T16:56:00Z',
}

const mockRunB: ExperimentRunSummary = {
  run_id: '2fd8bc7b-e7aa-4831-c3f5-3fc73040ecc6',
  satellite_product_id: 'S1B_IW_GRDH_1SDV_20181009T060000_TestArea',
  observation_time: '2018-10-09T06:00:00Z',
  observation_lat: 43.5,
  observation_lon: 9.8,
  backtrack_hours: 6,
  model_version: 'experiment_runner_v1+leeway_euler_backward_v1',
  source_lon: 9.7,
  source_lat: 43.4,
  source_radius_m: 4000,
  candidate_count: 1,
  status: 'completed',
  created_at: '2026-09-25T17:30:00Z',
}

const mockVesselUlysse: VesselFeatures = {
  vessel_id: '228308800',
  vessel_name: 'MV ULYSSE',
  mmsi: '228308800',
  min_source_distance_km: 8.1,
  temporal_overlap_hours: 8.5,
  trajectory_overlap_fraction: 0.75,
  heading_consistency: 0.8,
  speed_consistency: 0.85,
  ais_position_count: 42,
  ais_coverage_fraction: 0.8,
  evidence_consistency_score: 0.538,
  rank: 1,
  has_meaningful_support: true,
  positions: [
    { lat: 43.2, lon: 9.5, timestamp: '2018-10-07T18:00:00Z', speed: 14.5, heading: 210 },
    { lat: 43.1, lon: 9.4, timestamp: '2018-10-07T22:00:00Z', speed: 14.2, heading: 212 },
  ],
}

const mockVesselMedStar: VesselFeatures = {
  vessel_id: '247112233',
  vessel_name: 'MEDITERRANEAN STAR',
  mmsi: '247112233',
  min_source_distance_km: 15.4,
  temporal_overlap_hours: 6.0,
  trajectory_overlap_fraction: 0.4,
  heading_consistency: 0.6,
  speed_consistency: 0.7,
  ais_position_count: 28,
  ais_coverage_fraction: 0.65,
  evidence_consistency_score: 0.312,
  rank: 2,
  has_meaningful_support: true,
  positions: [
    { lat: 43.3, lon: 9.6, timestamp: '2018-10-07T19:00:00Z', speed: 11.0, heading: 180 },
  ],
}

const mockFullRunA: ExperimentRunResult = {
  run_id: mockRunA.run_id,
  satellite_product_id: mockRunA.satellite_product_id,
  observation_time: mockRunA.observation_time,
  observation_lat: mockRunA.observation_lat,
  observation_lon: mockRunA.observation_lon,
  backtrack_hours: 12,
  step_hours: 1.0,
  model_version: mockRunA.model_version,
  source_lon: mockRunA.source_lon,
  source_lat: mockRunA.source_lat,
  source_radius_m: mockRunA.source_radius_m,
  source_zone_geojson: { type: 'Feature', geometry: { type: 'Point', coordinates: [9.5751, 43.2276] } },
  backward_steps: [
    { step_index: 0, timestamp: '2018-10-08T05:28:07Z', lon: 9.4783, lat: 43.2483, uncertainty_radius_m: 500, wind_u: 2.1, wind_v: -1.5, current_u: 0.1, current_v: -0.2 },
    { step_index: 1, timestamp: '2018-10-08T04:28:07Z', lon: 9.4850, lat: 43.2450, uncertainty_radius_m: 1000, wind_u: 2.3, wind_v: -1.7, current_u: 0.12, current_v: -0.22 },
  ],
  vessels: [mockVesselUlysse, mockVesselMedStar],
  era5_path: 'backend/data/era5/era5_corsica_20181008.nc',
  cmems_path: 'backend/data/cmems/cmems_corsica_20181008.nc',
  created_at: mockRunA.created_at,
  scientific_disclaimer: 'This analysis is an evidence-consistency assessment and is not a legal determination of responsibility or causation.',
  status: 'completed',
}

describe('Step 7 — History & Experiment Record / Run Audit Trail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(experimentApi.listExperimentRuns).mockResolvedValue({
      runs: [mockRunA, mockRunB],
      count: 2,
    })
    vi.mocked(experimentApi.getExperimentRun).mockResolvedValue(mockFullRunA)
  })

  // 1. History Page Renders Completed Runs
  it('1. History page renders completed runs in table with all required columns', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)

    const step7Dot = screen.getByText('History')
    fireEvent.click(step7Dot)

    expect(await screen.findByTestId('run-history-table')).toBeDefined()
    expect(screen.getByText('Experiment History & Audit Trail')).toBeDefined()

    // Table rows
    expect(screen.getByTestId(`history-row-${mockRunA.run_id}`)).toBeDefined()
    expect(screen.getByTestId(`history-row-${mockRunB.run_id}`)).toBeDefined()

    // Verify Run A columns
    const rowA = screen.getByTestId(`history-row-${mockRunA.run_id}`)
    expect(rowA.textContent).toContain('1ed7ac6a')
    expect(rowA.textContent).toContain('43.248°N, 9.478°E')
    expect(rowA.textContent).toContain('43.228°N, 9.575°E')
    expect(rowA.textContent).toContain('6.5 km')
    expect(rowA.textContent).toContain('12 h')
    expect(rowA.textContent).toContain('2') // 2 candidates
    expect(rowA.textContent).toContain('Completed')

    // Action buttons
    expect(screen.getByTestId(`view-btn-${mockRunA.run_id}`)).toBeDefined()
    expect(screen.getByTestId(`load-btn-${mockRunA.run_id}`)).toBeDefined()
  })

  // 2. Empty History State
  it('2. Empty state renders correctly when no runs exist', async () => {
    vi.mocked(experimentApi.listExperimentRuns).mockResolvedValue({ runs: [], count: 0 })

    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    expect(await screen.findByTestId('empty-history-state')).toBeDefined()
    expect(screen.getByText('No completed experiments yet.')).toBeDefined()
    expect(
      screen.getByText('Run a real-data experiment to create an auditable experiment record.')
    ).toBeDefined()
  })

  // 3. Error Handling and Retry
  it('3. Error state renders when API fails and retry action works', async () => {
    vi.mocked(experimentApi.listExperimentRuns)
      .mockResolvedValueOnce({ runs: [mockRunA], count: 1 })
      .mockRejectedValueOnce(new Error('Network offline'))

    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    const refreshBtn = await screen.findByRole('button', { name: /Refresh Archive/i })
    fireEvent.click(refreshBtn)

    expect(await screen.findByTestId('history-error-state')).toBeDefined()
    expect(screen.getByText(/Unable to load experiment history/i)).toBeDefined()

    // Successful retry
    vi.mocked(experimentApi.listExperimentRuns).mockResolvedValueOnce({
      runs: [mockRunA],
      count: 1,
    })
    const retryBtn = screen.getByRole('button', { name: 'Retry' })
    fireEvent.click(retryBtn)

    expect(await screen.findByTestId(`history-row-${mockRunA.run_id}`)).toBeDefined()
  })

  // 4. Run Detail View
  it('4. Clicking View opens detailed historical record with metadata, observation, environment, and drift config', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    const viewBtn = await screen.findByTestId(`view-btn-${mockRunA.run_id}`)
    fireEvent.click(viewBtn)

    // Historical run detail panel
    expect(await screen.findByTestId('historical-run-detail')).toBeDefined()
    expect(screen.getByText('Historical Experiment Audit Record')).toBeDefined()

    // Run Metadata
    expect(screen.getAllByText(mockRunA.run_id).length).toBeGreaterThan(0)
    expect(screen.getAllByText(mockRunA.model_version).length).toBeGreaterThan(0)

    // Observation & Environment
    expect(screen.getAllByText(/ECMWF ERA5 10m Wind/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Copernicus Marine CMEMS/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Curated Historical SQLite Database/i).length).toBeGreaterThan(0)

    // Drift Config
    expect(screen.getByText('12 h')).toBeDefined()
    expect(screen.getByText('2 steps')).toBeDefined()

    // Reconstructed Source
    expect(screen.getAllByText('43.2276°N, 9.5751°E').length).toBeGreaterThan(0)
    expect(screen.getAllByText('6.5 km').length).toBeGreaterThan(0)
  })

  // 5. Scientific Lineage & Invariance in Detail View
  it('5. Detail view preserves authoritative candidate ranking without client-side reordering', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    const viewBtn = await screen.findByTestId(`view-btn-${mockRunA.run_id}`)
    fireEvent.click(viewBtn)

    await screen.findByTestId('historical-run-detail')

    // Candidates table
    expect(screen.getByTestId('candidate-comparison-table')).toBeDefined()
    const rows = screen.getAllByRole('row')
    expect(rows[1].textContent).toContain('#1')
    expect(rows[1].textContent).toContain('MV ULYSSE')
    expect(rows[1].textContent).toContain('53.8%')
    expect(rows[1].textContent).toContain('8.1 km')

    expect(rows[2].textContent).toContain('#2')
    expect(rows[2].textContent).toContain('MEDITERRANEAN STAR')
    expect(rows[2].textContent).toContain('31.2%')
    expect(rows[2].textContent).toContain('15.4 km')

    // Detailed evidence panel
    expect(screen.getByTestId('evidence-breakdown-panel')).toBeDefined()
    expect(screen.getAllByText('Spatial Proximity').length).toBe(2)
    expect(screen.getAllByText('Temporal Overlap').length).toBe(2)
  })

  // 6. Experiment Reproducibility Audit Card
  it('6. Reproducibility audit summary renders concise and authentic provenance', () => {
    render(<ReproducibilityCard run={mockFullRunA} />)

    expect(screen.getByTestId('experiment-reproducibility-card')).toBeDefined()
    expect(screen.getByText('EXPERIMENT REPRODUCIBILITY')).toBeDefined()
    expect(screen.getByText(mockFullRunA.run_id)).toBeDefined()
    expect(screen.getByText(mockFullRunA.model_version)).toBeDefined()
    expect(screen.getByText('Sentinel-1 S1A')).toBeDefined()
    expect(screen.getByText('ECMWF ERA5 10m Wind')).toBeDefined()
    expect(screen.getByText('Copernicus Marine CMEMS')).toBeDefined()
    expect(screen.getByText('Curated Historical SQLite Database')).toBeDefined()
    expect(screen.getByText('12 h / 2 steps')).toBeDefined()
  })

  // 7. Navigation Flow: History -> Detail -> History -> Evaluator
  it('7. Full navigation flows: Detail back to History, and Load into Evaluator', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    // 1. Open Detail
    const viewBtn = await screen.findByTestId(`view-btn-${mockRunA.run_id}`)
    fireEvent.click(viewBtn)
    expect(await screen.findByTestId('historical-run-detail')).toBeDefined()

    // 2. Back to History
    const backBtn = screen.getByTestId('back-to-history-btn')
    fireEvent.click(backBtn)
    expect(await screen.findByTestId('run-history-table')).toBeDefined()

    // 3. Load into Evaluator
    const loadBtn = screen.getByTestId(`load-btn-${mockRunA.run_id}`)
    fireEvent.click(loadBtn)

    // Should navigate to Step 6
    await waitFor(() => {
      expect(screen.getByText('Attribution Results')).toBeDefined()
    })
    expect(screen.getByText('View History →')).toBeDefined()

    // 4. Step 6 "View History →" returns to Step 7
    fireEvent.click(screen.getByText('View History →'))
    expect(await screen.findByTestId('run-history-table')).toBeDefined()
  })

  // 8. Factual Run Comparison
  it('8. Selecting two runs displays side-by-side factual comparison without evaluative bias', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    await screen.findByTestId('run-history-table')

    const chk1 = screen.getByTestId(`compare-checkbox-${mockRunA.run_id}`)
    const chk2 = screen.getByTestId(`compare-checkbox-${mockRunB.run_id}`)

    fireEvent.click(chk1)
    fireEvent.click(chk2)

    // Comparison panel renders
    const panel = await screen.findByTestId('run-comparison-panel')
    expect(panel).toBeDefined()
    expect(within(panel).getByText('Compare Experiment Runs (Factual Audit)')).toBeDefined()
    expect(within(panel).getByText('6.5 km')).toBeDefined()
    expect(within(panel).getByText('4.0 km')).toBeDefined()
    expect(within(panel).getByText('12 h')).toBeDefined()
    expect(within(panel).getByText('6 h')).toBeDefined()

    // Ensure NO evaluative or biased labels exist
    expect(screen.queryByText(/better/i)).toBeNull()
    expect(screen.queryByText(/worse/i)).toBeNull()
    expect(screen.queryByText(/winner/i)).toBeNull()
    expect(screen.queryByText(/correct/i)).toBeNull()
  })

  // 9. Search and Filter
  it('9. Search filter narrows runs by Run ID or satellite product', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)
    fireEvent.click(screen.getByText('History'))

    await screen.findByTestId('run-history-table')

    const searchInput = screen.getByTestId('history-search-input')
    fireEvent.change(searchInput, { target: { value: '2fd8bc7b' } })

    expect(screen.queryByTestId(`history-row-${mockRunA.run_id}`)).toBeNull()
    expect(screen.getByTestId(`history-row-${mockRunB.run_id}`)).toBeDefined()
  })
})
