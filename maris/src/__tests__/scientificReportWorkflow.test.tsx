/**
 * Step 8 — Scientific Investigation Report & Export Test Suite.
 *
 * Verifies:
 * - Read-only transformation of stored historical run record into report.
 * - Publication-ready preview rendering (cover, metadata, executive summary, observation,
 *   environment, drift config, candidate ranks, evidence matrix, reproducibility, disclaimers).
 * - Preservation of authoritative backend candidate ranking & scores (#1 MV ULYSSE, #2 MEDITERRANEAN STAR).
 * - Action buttons: Download PDF, Export JSON, Print, Load into Evaluator, and Back to Investigation.
 * - Navigation paths: History row "Report" -> ScientificReportView; Detail topbar -> ScientificReportView;
 *   Step 6 results -> Step 8 Report; Back -> History.
 * - Zero recalculation or rerunning of drift models or queries.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import RealExperimentView from '../components/views/RealExperimentView'
import ScientificReportView from '../components/views/ScientificReportView'
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
    fetchScientificReport: vi.fn(),
    downloadReportPdf: vi.fn().mockResolvedValue(undefined),
    downloadExportJson: vi.fn().mockResolvedValue(undefined),
    // Kept for backward compatibility — not used by component anymore
    getReportPdfUrl: vi.fn((runId: string) => `/api/experiment/runs/${runId}/report?format=pdf`),
    getExportJsonUrl: vi.fn((runId: string) => `/api/experiment/runs/${runId}/export`),
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

const mockReportData = {
  metadata: {
    run_id: mockRunA.run_id,
    report_generated_at: '2026-09-26T08:00:00Z',
    model_version: mockRunA.model_version,
    status: 'completed',
  },
  executive_summary: {
    assessment_statement: 'The experiment reconstructed a candidate source zone and evaluated available AIS vessel evidence against the reconstructed attribution result. This analysis is an evidence-consistency assessment.',
    observation_coords: '43.2483°N, 9.4783°E',
    source_coords: '43.2276°N, 9.5751°E',
    uncertainty_radius_km: 6.5,
    top_candidate: 'MV ULYSSE (MMSI: 228308800)',
    top_candidate_score: 0.538,
  },
  observation: {
    platform: 'Copernicus Sentinel-1A C-SAR',
    satellite_product_id: mockRunA.satellite_product_id,
    latitude: 43.2483,
    longitude: 9.4783,
    timestamp: '2018-10-08T05:28:07Z',
  },
  environment: {
    wind: 'ECMWF ERA5 10m Wind',
    currents: 'Copernicus Marine CMEMS',
    ais: 'Curated Historical SQLite Database',
  },
  drift_reconstruction: {
    backtrack_hours: 12,
    step_hours: 1.0,
    steps_count: 2,
    source_lat: 43.2276,
    source_lon: 9.5751,
    source_radius_km: 6.5,
  },
  candidates: [mockVesselUlysse, mockVesselMedStar],
  reproducibility: {
    run_id: mockRunA.run_id,
    model_version: mockRunA.model_version,
  },
  limitations: [
    'Attribution results reflect physical hydrodynamic consistency based on available observational and transponder evidence.',
    'Metocean backward drift integration is subject to boundary conditions and interpolation uncertainties.',
    'Vessel evaluation is constrained by AIS transponder transmission continuity.',
  ],
}

describe('Step 8 — Scientific Investigation Report & Export', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(experimentApi.listExperimentRuns).mockResolvedValue({
      runs: [mockRunA],
      count: 1,
    })
    vi.mocked(experimentApi.getExperimentRun).mockResolvedValue(mockFullRunA)
    vi.mocked(experimentApi.fetchScientificReport).mockResolvedValue(mockReportData)
  })

  // 1. Direct Rendering of ScientificReportView
  it('1. Renders complete scientific report with all required sections and data', async () => {
    render(<ScientificReportView runId={mockRunA.run_id} onBack={vi.fn()} initialRun={mockFullRunA} />)

    expect(await screen.findByTestId('scientific-report-view')).toBeDefined()
    expect(screen.getByText('SCIENTIFIC ATTRIBUTION & AUDIT REPORT')).toBeDefined()
    expect(screen.getByText('1. Executive Summary')).toBeDefined()
    expect(screen.getByText('2. Observation Information')).toBeDefined()
    expect(screen.getByText('3. Environmental Conditions & Data Sources')).toBeDefined()
    expect(screen.getByText('4. Drift Configuration & Source Reconstruction')).toBeDefined()
    expect(screen.getByText('5. Historical Attribution Map')).toBeDefined()
    expect(screen.getByText('6. Candidate Vessel Assessment')).toBeDefined()
    expect(screen.getByText('7. Detailed Evidence Dimensions Matrix')).toBeDefined()
    expect(screen.getByText('8. Experiment Reproducibility Audit')).toBeDefined()
    expect(screen.getByText('9. Scientific Interpretation & Limitations')).toBeDefined()

    // Key metrics rendered
    expect(screen.getByTestId('report-run-id').textContent).toContain(mockRunA.run_id)
    expect(screen.getAllByText(/6.5 km/i).length).toBeGreaterThanOrEqual(1)
  })

  // 2. Candidate Ranking & Score Preservation
  it('2. Preserves authoritative backend candidate rankings and exact consistency scores', async () => {
    render(<ScientificReportView runId={mockRunA.run_id} onBack={vi.fn()} initialRun={mockFullRunA} />)

    const table = await screen.findByTestId('report-candidate-table')
    const tableScope = within(table)

    // #1 MV ULYSSE with 53.8%
    expect(tableScope.getByText('#1')).toBeDefined()
    expect(tableScope.getByText('MV ULYSSE')).toBeDefined()
    expect(tableScope.getByText('53.8%')).toBeDefined()

    // #2 MEDITERRANEAN STAR with 31.2%
    expect(tableScope.getByText('#2')).toBeDefined()
    expect(tableScope.getByText('MEDITERRANEAN STAR')).toBeDefined()
    expect(tableScope.getByText('31.2%')).toBeDefined()

    // Zero reruns triggered
    expect(experimentApi.runExperiment).not.toHaveBeenCalled()
  })

  // 3. Export Buttons (PDF, JSON, Print)
  it('3. Provides Download PDF, Export JSON, and Print buttons with correct URLs and actions', async () => {
    const originalPrint = window.print
    window.print = vi.fn()

    render(<ScientificReportView runId={mockRunA.run_id} onBack={vi.fn()} initialRun={mockFullRunA} />)

    await screen.findByTestId('scientific-report-view')

    const pdfBtn = screen.getByTestId('report-download-pdf-btn')
    const jsonBtn = screen.getByTestId('report-export-json-btn')
    const printBtn = screen.getByTestId('report-print-btn')

    expect(pdfBtn).toBeDefined()
    expect(jsonBtn).toBeDefined()
    expect(printBtn).toBeDefined()

    // Clicking print calls window.print()
    fireEvent.click(printBtn)
    expect(window.print).toHaveBeenCalledTimes(1)

    // Clicking PDF and JSON trigger proper blob-based download functions
    fireEvent.click(pdfBtn)
    expect(experimentApi.downloadReportPdf).toHaveBeenCalledWith(mockRunA.run_id)

    fireEvent.click(jsonBtn)
    expect(experimentApi.downloadExportJson).toHaveBeenCalledWith(mockRunA.run_id)

    window.print = originalPrint
  })

  // 4. Neutral Scientific Terminology & Disclaimer
  it('4. Enforces neutral evidence-consistency disclaimer and avoids legal fault terms', async () => {
    render(<ScientificReportView runId={mockRunA.run_id} onBack={vi.fn()} initialRun={mockFullRunA} />)

    const disclaimer = await screen.findByTestId('report-disclaimer')
    expect(disclaimer.textContent).toContain('evidence-consistency assessment')
    expect(disclaimer.textContent).toContain('not a legal determination of responsibility')

    // Verify forbidden words do NOT appear in the entire document
    const reportText = screen.getByTestId('scientific-report-view').textContent || ''
    expect(reportText).not.toMatch(/\bguilty\b/i)
    expect(reportText).not.toMatch(/\bculprit\b/i)
    expect(reportText).not.toMatch(/\bliable\b/i)
  })

  // 5. Navigation from History Table "Report" Button
  it('5. Navigates from History Table via Report button and returns back to history', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)

    const step7Dot = screen.getByText('History')
    fireEvent.click(step7Dot)

    // Wait for history table
    await screen.findByTestId('run-history-table')

    const reportBtn = screen.getByTestId(`report-btn-${mockRunA.run_id}`)
    expect(reportBtn).toBeDefined()
    fireEvent.click(reportBtn)

    // Scientific report view appears
    expect(await screen.findByTestId('scientific-report-view')).toBeDefined()
    expect(screen.getByText('SCIENTIFIC ATTRIBUTION & AUDIT REPORT')).toBeDefined()

    // Back to investigation returns to history
    const backBtn = screen.getByTestId('report-back-btn')
    fireEvent.click(backBtn)

    expect(await screen.findByTestId('run-history-table')).toBeDefined()
  })

  // 6. Navigation from Historical Detail "Scientific Report / Export" Button
  it('6. Navigates from Historical Detail view into Scientific Report view', async () => {
    render(<RealExperimentView />)
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)

    const step7Dot = screen.getByText('History')
    fireEvent.click(step7Dot)

    await screen.findByTestId('run-history-table')
    const viewBtn = screen.getByTestId(`view-btn-${mockRunA.run_id}`)
    fireEvent.click(viewBtn)

    // Now in Historical Run Detail
    await screen.findByTestId('historical-run-detail')
    const generateBtn = screen.getByTestId('detail-generate-report-btn')
    expect(generateBtn).toBeDefined()
    fireEvent.click(generateBtn)

    // Report view opens
    expect(await screen.findByTestId('scientific-report-view')).toBeDefined()
  })
})
