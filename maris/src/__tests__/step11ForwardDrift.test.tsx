/**
 * Test suite for MARIS Step 11 — Forward Drift Prediction & Visualization.
 * Validates AttributionMap forward layer, toggle, popups, RealExperimentView Step 3 & 6,
 * and ScientificReportView section 4b.
 */

import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import AttributionMap from '../components/views/AttributionMap'
import RealExperimentView from '../components/views/RealExperimentView'
import ScientificReportView from '../components/views/ScientificReportView'
import type { ForwardDriftStep, ForwardPredictionResult, ExperimentRunResult } from '../real-experiment/experimentTypes'
import * as experimentApi from '../real-experiment/experimentApi'

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
    setLayoutProperty: vi.fn(),
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

const mockObservation = {
  lat: 43.2483,
  lon: 9.4783,
  timestamp: '2018-10-08T08:00:00Z',
  title: 'Sentinel-1A Scene',
}

const mockReconstructedSource = {
  lat: 43.15,
  lon: 9.35,
  radiusM: 6500,
}

const mockBackwardSteps = [
  { step: 1, lon: 9.45, lat: 43.22, timestamp: '2018-10-08T07:00:00Z', uncertainty_radius_m: 1000 },
  { step: 2, lon: 9.40, lat: 43.18, timestamp: '2018-10-08T06:00:00Z', uncertainty_radius_m: 1500 },
]

const mockForwardSteps: ForwardDriftStep[] = [
  { step: 1, lon: 9.50, lat: 43.27, timestamp: '2018-10-08T09:00:00Z', u_wind_ms: 5.2, v_wind_ms: 3.1, u_current_ms: 0.25, v_current_ms: 0.12 },
  { step: 2, lon: 9.53, lat: 43.30, timestamp: '2018-10-08T10:00:00Z', u_wind_ms: 5.5, v_wind_ms: 3.4, u_current_ms: 0.27, v_current_ms: 0.14 },
]

const mockForwardPrediction: ForwardPredictionResult = {
  observation_lon: 9.4783,
  observation_lat: 43.2483,
  observation_time: '2018-10-08T08:00:00Z',
  prediction_hours: 2,
  step_hours: 1.0,
  model_version: 'leeway_euler_forward_v1',
  status: 'COMPLETED',
  steps: mockForwardSteps,
  final_lon: 9.53,
  final_lat: 43.30,
  displacement_km: 7.2,
  scientific_disclaimer: 'Deterministic model projection under supplied forcing.',
}

describe('Step 11 — Forward Drift Visualization on AttributionMap', () => {
  it('renders forward prediction polyline and destination marker in SVG fallback', () => {
    render(
      <AttributionMap
        observation={mockObservation}
        reconstructedSource={mockReconstructedSource}
        backwardSteps={mockBackwardSteps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        forwardSteps={mockForwardSteps}
        forwardPrediction={mockForwardPrediction}
      />
    )

    // Should find the forward drift SVG polyline
    const forwardPolyline = screen.getByTestId('forward-drift-polyline')
    expect(forwardPolyline).toBeDefined()
    expect(forwardPolyline.getAttribute('stroke')).toBe('#06b6d4')

    // Should find the forward predicted destination marker
    const destMarker = screen.getByTestId('forward-predicted-destination')
    expect(destMarker).toBeDefined()
    expect(destMarker.textContent).toContain('PREDICTED FUTURE')
  })

  it('toggles forward drift layer visibility with the checkbox', () => {
    render(
      <AttributionMap
        observation={mockObservation}
        reconstructedSource={mockReconstructedSource}
        backwardSteps={mockBackwardSteps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        forwardSteps={mockForwardSteps}
        forwardPrediction={mockForwardPrediction}
      />
    )

    const toggle = screen.getByTestId('forward-drift-toggle').querySelector('input') as HTMLInputElement
    expect(toggle).toBeDefined()
    expect(toggle.checked).toBe(true)
    expect(screen.getByTestId('forward-drift-polyline')).toBeDefined()

    // Uncheck toggle
    fireEvent.click(toggle)
    expect(toggle.checked).toBe(false)
    expect(screen.queryByTestId('forward-drift-polyline')).toBeNull()

    // Recheck toggle
    fireEvent.click(toggle)
    expect(toggle.checked).toBe(true)
    expect(screen.getByTestId('forward-drift-polyline')).toBeDefined()
  })

  it('opens forward step popup when step marker is clicked', () => {
    render(
      <AttributionMap
        observation={mockObservation}
        reconstructedSource={mockReconstructedSource}
        backwardSteps={mockBackwardSteps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        forwardSteps={mockForwardSteps}
        forwardPrediction={mockForwardPrediction}
      />
    )

    // Click step marker 1 (corresponding to +1h)
    const marker1 = screen.getByTestId('forward-step-marker-1')
    fireEvent.click(marker1)

    // Popup card should appear
    const popup = screen.getByTestId('forward-step-popup')
    expect(popup).toBeDefined()
    expect(popup.textContent).toContain('Forward Drift Prediction (+1h)')
    expect(popup.textContent).toContain('leeway_euler_forward_v1')
    expect(popup.textContent).toContain('Deterministic model trajectory')
  })

  it('displays forward drift entry in the legend', () => {
    render(
      <AttributionMap
        observation={mockObservation}
        reconstructedSource={mockReconstructedSource}
        backwardSteps={mockBackwardSteps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        forwardSteps={mockForwardSteps}
        forwardPrediction={mockForwardPrediction}
      />
    )

    const legendEntry = screen.getByTestId('legend-forward-drift')
    expect(legendEntry).toBeDefined()
    expect(legendEntry.textContent).toContain('Forward Drift Prediction')
  })
})

describe('Step 11 — RealExperimentView Forward Drift Configuration & Results', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(experimentApi, 'fetchExperimentConfig').mockResolvedValue({
      era5_configured: true,
      cmems_configured: true,
      ais_configured: true,
      default_backtrack_hours: 12,
      warnings: [],
      sentinel1_configured: true,
      ais_adapter_id: 'sqlite_benchmark',
      all_configured: true,
    })
    vi.spyOn(experimentApi, 'listExperimentRuns').mockResolvedValue({
      runs: [],
      count: 0,
    })
  })

  it('renders RealExperimentView and displays forward drift controls on Step 3', async () => {
    render(<RealExperimentView />)

    await waitFor(() => {
      expect(screen.getByText(/Select Sentinel-1 Observation/i)).toBeDefined()
    })

    // Click on Step 3 in the wizard progress bar
    const step3Btn = screen.getByText('Drift Config')
    fireEvent.click(step3Btn)

    await waitFor(() => {
      expect(screen.getByTestId('forward-prediction-config-section')).toBeDefined()
    })

    const configSection = screen.getByTestId('forward-prediction-config-section')
    expect(configSection.textContent).toContain('Forward Drift Prediction (Step 11)')

    const checkbox = screen.getByTestId('enable-forward-prediction-checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(true)

    const previewBtn = screen.getByTestId('preview-forward-drift-btn')
    expect(previewBtn).toBeDefined()
  })
})

describe('Step 11 — ScientificReportView with Forward Drift', () => {
  const mockRunResultWithForward: ExperimentRunResult = {
    run_id: 'test-run-step11',
    satellite_product_id: 'S1A_IW_GRDH_1SDV_20181008T053424',
    observation_time: '2018-10-08T05:34:24Z',
    observation_lon: 9.4783,
    observation_lat: 43.2483,
    backtrack_hours: 12,
    step_hours: 1.0,
    model_version: 'leeway_euler_backward_v1 + leeway_euler_forward_v1',
    source_lon: 9.35,
    source_lat: 43.15,
    source_radius_m: 6500,
    source_zone_geojson: { type: 'Polygon', coordinates: [] },
    backward_steps: mockBackwardSteps,
    vessels: [],
    era5_path: 'mock_era5.nc',
    cmems_path: 'mock_cmems.nc',
    created_at: '2026-09-26T12:00:00Z',
    scientific_disclaimer: 'Evidence consistency only.',
    forward_prediction: mockForwardPrediction,
  }

  const mockRunResultHistorical: ExperimentRunResult = {
    ...mockRunResultWithForward,
    run_id: 'historical-run-without-forward',
    forward_prediction: null,
  }

  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(experimentApi, 'downloadReportPdf').mockResolvedValue(undefined)
    vi.spyOn(experimentApi, 'downloadExportJson').mockResolvedValue(undefined)
  })

  it('renders section 4b Forward Drift Prediction when forward prediction is present', async () => {
    vi.spyOn(experimentApi, 'getExperimentRun').mockResolvedValue(mockRunResultWithForward)
    vi.spyOn(experimentApi, 'fetchScientificReport').mockResolvedValue({
      metadata: { experiment_run_id: 'test-run-step11' },
      executive_summary: { assessment_statement: 'Assessment' },
      forward_drift_prediction: mockForwardPrediction,
    })

    render(
      <ScientificReportView
        runId="test-run-step11"
        onBack={() => {}}
        initialRun={mockRunResultWithForward}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('report-forward-prediction-section')).toBeDefined()
    })

    const section = screen.getByTestId('report-forward-prediction-section')
    expect(section.textContent).toContain('4b. Forward Drift Prediction (Model Projection)')
    expect(section.textContent).toContain('+2 h')
    expect(section.textContent).toContain('7.2 km')
    expect(section.textContent).toContain('leeway_euler_forward_v1')
    expect(section.textContent).toContain('Scientific Uncertainty Notice')
  })

  it('does not crash and omits section 4b for historical experiments lacking forward prediction', async () => {
    vi.spyOn(experimentApi, 'getExperimentRun').mockResolvedValue(mockRunResultHistorical)
    vi.spyOn(experimentApi, 'fetchScientificReport').mockResolvedValue({
      metadata: { experiment_run_id: 'historical-run-without-forward' },
      executive_summary: { assessment_statement: 'Assessment' },
      forward_drift_prediction: null,
    })

    render(
      <ScientificReportView
        runId="historical-run-without-forward"
        onBack={() => {}}
        initialRun={mockRunResultHistorical}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('report-paper')).toBeDefined()
    })

    expect(screen.queryByTestId('report-forward-prediction-section')).toBeNull()
  })

  it('renders Step 6 Results without crashing when backend returns total_distance_km and termination_status', async () => {
    // Backend payload without displacement_km or status
    const rawBackendForwardPrediction: any = {
      origin_lon: 9.4783,
      origin_lat: 43.2483,
      observation_time: '2018-10-08T08:00:00Z',
      prediction_hours: 12,
      step_hours: 1.0,
      model_version: 'leeway_euler_v1',
      termination_status: 'completed',
      steps: mockForwardSteps,
      final_lon: 9.53,
      final_lat: 43.30,
      total_distance_km: 8.29,
    }

    const mockRunResultFromBackend: ExperimentRunResult = {
      ...mockRunResultWithForward,
      run_id: 'real-run-backend-schema',
      forward_prediction: rawBackendForwardPrediction,
    }

    vi.spyOn(experimentApi, 'fetchExperimentConfig').mockResolvedValue({
      era5_configured: true,
      cmems_configured: true,
      copernicus_configured: true,
      ais_configured: true,
      warnings: [],
    })
    vi.spyOn(experimentApi, 'listExperimentRuns').mockResolvedValue({ runs: [mockRunResultFromBackend], total: 1 })
    vi.spyOn(experimentApi, 'getExperimentRun').mockResolvedValue(mockRunResultFromBackend)

    render(<RealExperimentView initialMode="real" />)

    // Navigate to History step
    await waitFor(() => {
      expect(screen.getByText('History')).toBeDefined()
    })
    fireEvent.click(screen.getByText('History'))

    // Load the run into active wizard (Step 6 Results)
    await waitFor(() => {
      expect(screen.getByTestId('load-btn-real-run-backend-schema')).toBeDefined()
    })

    fireEvent.click(screen.getByTestId('load-btn-real-run-backend-schema'))

    await waitFor(() => {
      expect(screen.getByTestId('forward-prediction-results-card')).toBeDefined()
    })

    const card = screen.getByTestId('forward-prediction-results-card')
    expect(card.textContent).toContain('8.3 km')
    expect(card.textContent).toContain('completed')
  })
})
