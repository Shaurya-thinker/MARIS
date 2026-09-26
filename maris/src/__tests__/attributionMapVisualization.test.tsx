import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import RealExperimentView from '../components/views/RealExperimentView'
import AttributionMap from '../components/views/AttributionMap'
import * as experimentApi from '../real-experiment/experimentApi'
import type { AisPosition, ExperimentRunResult, SentinelProduct, VesselFeatures } from '../real-experiment/experimentTypes'

globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.mock('../real-experiment/experimentApi', () => ({
  fetchExperimentConfig: vi.fn().mockResolvedValue({
    sentinel1_configured: true,
    era5_configured: true,
    cmems_configured: true,
    ais_configured: true,
    ais_adapter_id: 'sqlite',
    all_configured: true,
    warnings: [],
  }),
  discoverSentinelProducts: vi.fn(),
  selectEnvironment: vi.fn(),
  searchAisVessels: vi.fn(),
  fetchAisPositions: vi.fn(),
  runExperiment: vi.fn(),
  listExperimentRuns: vi.fn().mockResolvedValue({ runs: [], count: 0 }),
  getExperimentRun: vi.fn(),
}))

describe('Attribution Map & Scientific Explainability Visualization (Points 1–12)', () => {
  const mockUlyssePositions: AisPosition[] = [
    { timestamp: '2018-10-07T21:00:00Z', lat: 43.15, lon: 9.38, speed: 16.5, heading: 215.0 },
    { timestamp: '2018-10-07T23:00:00Z', lat: 43.10, lon: 9.42, speed: 16.2, heading: 212.0 },
    { timestamp: '2018-10-08T01:00:00Z', lat: 43.05, lon: 9.45, speed: 15.9, heading: 210.0 },
  ]

  const mockMedStarPositions: AisPosition[] = [
    { timestamp: '2018-10-07T22:00:00Z', lat: 42.80, lon: 9.20, speed: 14.0, heading: 45.0 },
    { timestamp: '2018-10-08T00:00:00Z', lat: 42.90, lon: 9.30, speed: 14.1, heading: 48.0 },
  ]

  const mockVessels: VesselFeatures[] = [
    {
      vessel_id: '228308800',
      vessel_name: 'MV ULYSSE',
      mmsi: '228308800',
      min_source_distance_km: 8.1,
      temporal_overlap_hours: 8.5,
      trajectory_overlap_fraction: 0.75,
      heading_consistency: 0.42,
      speed_consistency: 0.80,
      ais_position_count: 12,
      ais_coverage_fraction: 0.80,
      evidence_consistency_score: 0.538,
      rank: 1,
      has_meaningful_support: true,
      positions: mockUlyssePositions,
    },
    {
      vessel_id: '247112233',
      vessel_name: 'MEDITERRANEAN STAR',
      mmsi: '247112233',
      min_source_distance_km: 15.4,
      temporal_overlap_hours: 6.0,
      trajectory_overlap_fraction: 0.20,
      heading_consistency: 0.35,
      speed_consistency: 0.70,
      ais_position_count: 9,
      ais_coverage_fraction: 0.60,
      evidence_consistency_score: 0.312,
      rank: 2,
      has_meaningful_support: true,
      positions: mockMedStarPositions,
    },
  ]

  const mockResult: ExperimentRunResult = {
    run_id: 'test-attribution-map-run-001',
    satellite_product_id: 'S1A_IW_GRDH_1SDV_20181008T052807_024039_02A0BB',
    observation_time: '2018-10-08T05:28:07Z',
    backtrack_hours: 12,
    step_hours: 1,
    model_version: 'experiment_runner_v1+leeway_euler_backward_v1',
    source_lon: 9.6757,
    source_lat: 41.1538,
    source_radius_m: 6500,
    source_zone_geojson: {
      type: 'Polygon',
      coordinates: [
        [
          [9.65, 41.13],
          [9.70, 41.13],
          [9.70, 41.18],
          [9.65, 41.18],
          [9.65, 41.13],
        ],
      ],
    },
    backward_steps: [
      { step: 0, lon: 9.4783, lat: 43.2483, timestamp: '2018-10-08T05:28:07Z', uncertainty_radius_m: 500, u_wind_ms: 3.2, v_wind_ms: 1.1 },
      { step: 1, lon: 9.5000, lat: 42.9000, timestamp: '2018-10-08T04:28:07Z', uncertainty_radius_m: 1000, u_wind_ms: 3.0, v_wind_ms: 1.2 },
      { step: 2, lon: 9.6757, lat: 41.1538, timestamp: '2018-10-07T17:28:07Z', uncertainty_radius_m: 6500, u_wind_ms: 2.8, v_wind_ms: 0.9 },
    ],
    vessels: mockVessels,
    era5_path: 'backend/acquisitions/era5_wind.nc',
    cmems_path: 'backend/acquisitions/cmems_current.nc',
    created_at: '2026-09-25T17:00:00Z',
    scientific_disclaimer: 'This analysis is an evidence-consistency assessment and is not a legal determination of responsibility or causation.',
  }

  it('1. Map renders with valid experiment data', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500, geojson: mockResult.source_zone_geojson }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(screen.getByTestId('attribution-map')).toBeDefined()
    expect(screen.getByText('Interactive Attribution Map & Evidence Reconstruction')).toBeDefined()
  })

  it('2. Observation marker renders with clear label and coordinates', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(screen.getByTestId('observation-marker')).toBeDefined()
    expect(screen.getAllByText('Observed Spill / Slick').length).toBeGreaterThan(0)
    expect(screen.getByText(/43\.2483°N, 9\.4783°E/)).toBeDefined()
  })

  it('3. Reconstructed source renders with center coordinates', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(screen.getByTestId('reconstructed-source-marker')).toBeDefined()
    expect(screen.getAllByText('Reconstructed Source Zone').length).toBeGreaterThan(0)
    expect(screen.getByText(/41\.1538°N, 9\.6757°E/)).toBeDefined()
  })

  it('4. Uncertainty zone renders when available with exact backend radius', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(screen.getByTestId('source-uncertainty-zone')).toBeDefined()
    expect(screen.getByText('6.5 km')).toBeDefined()
  })

  it('5. AIS tracks render when positions exist', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(screen.getByTestId('vessel-track-228308800')).toBeDefined()
    expect(screen.getAllByText('MV ULYSSE').length).toBeGreaterThan(0)
  })

  it('6. Multiple vessel tracks render with distinct identities', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(screen.getByTestId('vessel-track-228308800')).toBeDefined()
    expect(screen.getByTestId('vessel-track-247112233')).toBeDefined()
    expect(screen.getAllByText('MEDITERRANEAN STAR').length).toBeGreaterThan(0)
  })

  it('7. Empty AIS positions do not create fake tracks and display unavailable notice', () => {
    const vesselWithoutPositions: VesselFeatures = {
      vessel_id: '999999999',
      vessel_name: 'GHOST VESSEL',
      mmsi: '999999999',
      min_source_distance_km: null,
      temporal_overlap_hours: 0,
      trajectory_overlap_fraction: 0,
      heading_consistency: null,
      speed_consistency: null,
      ais_position_count: 0,
      ais_coverage_fraction: 0,
      evidence_consistency_score: 0,
      rank: 3,
      has_meaningful_support: false,
      positions: [],
    }

    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockResult.backward_steps}
        vessels={[...mockVessels, vesselWithoutPositions]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    // Ensure no SVG track was created for 999999999
    expect(screen.queryByTestId('vessel-track-999999999')).toBeNull()
    // Ensure legend marks it as (No AIS)
    expect(screen.getByText('(No AIS)')).toBeDefined()
  })

  it('8. Missing source geometry falls back safely without crashing', () => {
    const { container } = render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 3000, geojson: null }}
        backwardSteps={mockResult.backward_steps}
        vessels={mockVessels}
        selectedVesselId={null}
        onSelectVessel={() => {}}
      />
    )

    expect(container).toBeDefined()
    expect(screen.getByTestId('reconstructed-source-marker')).toBeDefined()
    expect(screen.getByTestId('source-uncertainty-zone')).toBeDefined()
  })

  it('9. Evidence values displayed match backend response exactly', async () => {
    vi.mocked(experimentApi.getExperimentRun).mockResolvedValue(mockResult)
    vi.mocked(experimentApi.listExperimentRuns).mockResolvedValue({
      runs: [
        {
          run_id: mockResult.run_id,
          satellite_product_id: mockResult.satellite_product_id,
          observation_time: mockResult.observation_time,
          backtrack_hours: 12,
          model_version: mockResult.model_version,
          source_lon: mockResult.source_lon,
          source_lat: mockResult.source_lat,
          source_radius_m: mockResult.source_radius_m,
          created_at: mockResult.created_at,
        },
      ],
      count: 1,
    })

    render(<RealExperimentView />)

    // Click on Real Data tab
    const realTab = screen.getByRole('button', { name: /Real-Data Observation Wizard/i })
    fireEvent.click(realTab)

    // Go to Step 7 (History) to load this exact result
    const step7Dot = screen.getByText('History')
    fireEvent.click(step7Dot)

    const loadBtn = await screen.findByRole('button', { name: 'Load' })
    fireEvent.click(loadBtn)

    // Wait for Step 6 Results view
    await waitFor(() => {
      expect(screen.getByTestId('candidate-comparison-table')).toBeDefined()
    })

    // Verify MV Ulysse exact values
    expect(screen.getAllByText('53.8%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('8.1 km').length).toBeGreaterThan(0)
    expect(screen.getAllByText('8.5 h').length).toBeGreaterThan(0)
    expect(screen.getAllByText('75%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('42%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('80%').length).toBeGreaterThan(0)

    // Verify Mediterranean Star exact values
    expect(screen.getAllByText('31.2%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('15.4 km').length).toBeGreaterThan(0)
    expect(screen.getAllByText('6.0 h').length).toBeGreaterThan(0)
  })

  it('10. Candidate ranking/order produced by backend is strictly preserved', async () => {
    vi.mocked(experimentApi.getExperimentRun).mockResolvedValue(mockResult)
    render(<RealExperimentView />)

    const step7Dot = screen.getByText('History')
    fireEvent.click(step7Dot)

    const loadBtn = await screen.findByRole('button', { name: 'Load' })
    fireEvent.click(loadBtn)

    await waitFor(() => {
      expect(screen.getByTestId('candidate-comparison-table')).toBeDefined()
    })

    const rows = screen.getAllByRole('row')
    // Header row is index 0. Row 1 must be MV ULYSSE (#1), Row 2 must be MEDITERRANEAN STAR (#2)
    expect(rows[1].textContent).toContain('#1')
    expect(rows[1].textContent).toContain('MV ULYSSE')
    expect(rows[2].textContent).toContain('#2')
    expect(rows[2].textContent).toContain('MEDITERRANEAN STAR')
  })

  it('11. Scientific/legal disclaimer is prominently displayed without bias', async () => {
    vi.mocked(experimentApi.getExperimentRun).mockResolvedValue(mockResult)
    render(<RealExperimentView />)

    const step7Dot = screen.getByText('History')
    fireEvent.click(step7Dot)

    const loadBtn = await screen.findByRole('button', { name: 'Load' })
    fireEvent.click(loadBtn)

    await waitFor(() => {
      expect(screen.getByText(/This analysis is an evidence-consistency assessment and is not a legal determination of responsibility or causation/i)).toBeDefined()
    })

    // Strict neutrality check: ensure forbidden biased terms are NOT in the document
    expect(screen.queryByText(/probability of guilt/i)).toBeNull()
    expect(screen.queryByText(/probability of responsibility/i)).toBeNull()
    expect(screen.queryByText(/probability of causation/i)).toBeNull()
  })

  it('12. Existing wizard workflow remains functional through all 6 steps', async () => {
    const mockProduct: SentinelProduct = {
      product_id: 'prod-001',
      title: 'S1A_IW_GRDH_1SDV_20181008T052807_024039_02A0BB',
      sensing_start: '2018-10-08T05:28:07Z',
      sensing_stop: '2018-10-08T05:28:32Z',
      platform: 'Sentinel-1A',
      mode: 'IW',
      product_class: 'GRD',
      online: true,
      centroid_lon: 9.7874,
      centroid_lat: 41.9907,
      footprint: null,
      content_length_bytes: 1000000,
    }

    vi.mocked(experimentApi.discoverSentinelProducts).mockResolvedValue({
      products: [mockProduct],
      count: 1,
      configured: true,
    })

    vi.mocked(experimentApi.selectEnvironment).mockResolvedValue({
      era5_netcdf_path: 'backend/acquisitions/era5_wind.nc',
      era5_timestamp: '2018-10-08T05:00:00Z',
      era5_u_sample: 3.2,
      era5_v_sample: 1.1,
      cmems_netcdf_path: 'backend/acquisitions/cmems_current.nc',
      cmems_timestamp: '2018-10-08T05:00:00Z',
      cmems_u_sample: 0.15,
      cmems_v_sample: 0.08,
      auto_selected: true,
      era5_configured: true,
      cmems_configured: true,
    })

    vi.mocked(experimentApi.searchAisVessels).mockResolvedValue({
      vessels: [
        {
          mmsi: '228308800',
          vessel_name: 'MV ULYSSE',
          imo: '9138408',
          position_count: 3,
          first_timestamp: '2018-10-07T21:00:00Z',
          last_timestamp: '2018-10-08T05:28:00Z',
          source_adapter: 'sqlite',
          positions: mockUlyssePositions,
        },
      ],
      total_positions: 3,
      search_bbox: { west: 9.0, south: 41.5, east: 9.68, north: 43.25 },
      search_window_start: '2018-10-07T17:28:07Z',
      search_window_end: '2018-10-08T05:28:07Z',
      adapter_id: 'sqlite',
      configured: true,
    })

    vi.mocked(experimentApi.runExperiment).mockResolvedValue(mockResult)

    render(<RealExperimentView />)

    // Step 1: Discover & Select Product
    const searchBtn = await screen.findByRole('button', { name: /Search CDSE Catalogue/i })
    fireEvent.click(searchBtn)
    const selectBtn = await screen.findByRole('button', { name: /Select →/i })
    fireEvent.click(selectBtn)

    // Step 2: Environment
    const acquireBtn = await screen.findByRole('button', { name: /Acquire ERA5 \+ CMEMS/i })
    fireEvent.click(acquireBtn)

    // Step 3: Drift Config
    const nextVesselBtn = await screen.findByRole('button', { name: /Next: Vessel Search →/i })
    fireEvent.click(nextVesselBtn)

    // Step 4: AIS Search
    const searchAisBtn = await screen.findByRole('button', { name: /Search AIS Tracks/i })
    fireEvent.click(searchAisBtn)

    const checkbox = (await screen.findAllByRole('checkbox'))[0]
    fireEvent.click(checkbox)

    const nextRunBtn = screen.getByRole('button', { name: /Next: Run Attribution →/i })
    fireEvent.click(nextRunBtn)

    // Step 5: Run Attribution
    const runBtn = await screen.findByRole('button', { name: /Execute Attribution Experiment/i })
    fireEvent.click(runBtn)

    // Step 6: Verify Attribution Map and Results are displayed
    await waitFor(() => {
      expect(screen.getByTestId('attribution-map')).toBeDefined()
      expect(screen.getByTestId('candidate-comparison-table')).toBeDefined()
    })
  })
})
