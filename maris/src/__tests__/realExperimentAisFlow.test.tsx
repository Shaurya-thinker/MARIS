import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import RealExperimentView from '../components/views/RealExperimentView'
import * as experimentApi from '../real-experiment/experimentApi'
import type { AisPosition, ExperimentRunResult, SentinelProduct } from '../real-experiment/experimentTypes'

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
  characterizeSentinelObservation: vi.fn().mockResolvedValue(null),
  acquireSarSubscene: vi.fn().mockResolvedValue({ success: false }),
}))

describe('Real-Data Observation Wizard — AIS Integration Flow (Step 4 -> Step 5 -> Step 6)', () => {
  const mockProduct: SentinelProduct = {
    product_id: '27fc21a0-57a1-5d0a-9909-b4239e6f080b',
    title: 'S1A_IW_GRDH_1SDV_20181008T052807_20181008T052832_024039_02A0BB_04F2',
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

  const mockUlyssePositions: AisPosition[] = Array.from({ length: 12 }, (_, i) => ({
    timestamp: `2018-10-07T${21 + Math.floor(i / 3)}:00:00Z`,
    lat: 43.15 + i * 0.01,
    lon: 9.38 + i * 0.008,
    speed: 16.5,
    heading: 215.0,
  }))

  const mockMedStarPositions: AisPosition[] = Array.from({ length: 9 }, (_, i) => ({
    timestamp: `2018-10-07T${21 + Math.floor(i / 2)}:00:00Z`,
    lat: 42.7 + i * 0.04,
    lon: 9.15 + i * 0.05,
    speed: 14.0,
    heading: 45.0,
  }))

  beforeEach(() => {
    vi.clearAllMocks()

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
          position_count: 12,
          first_timestamp: '2018-10-07T21:00:00Z',
          last_timestamp: '2018-10-08T05:28:00Z',
          source_adapter: 'sqlite',
          positions: mockUlyssePositions,
        },
        {
          mmsi: '247112233',
          vessel_name: 'MEDITERRANEAN STAR',
          imo: '9123456',
          position_count: 9,
          first_timestamp: '2018-10-07T21:00:00Z',
          last_timestamp: '2018-10-08T05:28:00Z',
          source_adapter: 'sqlite',
          positions: mockMedStarPositions,
        },
      ],
      total_positions: 21,
      search_bbox: { west: 9.0, south: 41.5, east: 9.68, north: 43.25 },
      search_window_start: '2018-10-07T17:28:07Z',
      search_window_end: '2018-10-08T05:28:07Z',
      adapter_id: 'sqlite',
      configured: true,
    })

    vi.mocked(experimentApi.runExperiment).mockImplementation(async (req) => {
      const ulysse = req.selected_vessels.find((v) => v.mmsi === '228308800')
      const posCount = ulysse?.positions?.length ?? 0
      return {
        run_id: 'test-run-ais-fix-001',
        satellite_product_id: req.satellite_product_id,
        observation_time: req.observation_time,
        backtrack_hours: req.backtrack_hours ?? 12,
        step_hours: req.step_hours ?? 1,
        model_version: 'experiment_runner_v1+leeway_euler_backward_v1',
        source_lon: 9.4589,
        source_lat: 42.9812,
        source_radius_m: 3500,
        source_zone_geojson: {},
        backward_steps: [],
        vessels: [
          {
            vessel_id: '228308800',
            vessel_name: 'MV ULYSSE',
            mmsi: '228308800',
            min_source_distance_km: 1.2,
            temporal_overlap_hours: 8.5,
            trajectory_overlap_fraction: 0.75,
            heading_consistency: 0.88,
            speed_consistency: 0.92,
            ais_position_count: posCount,
            ais_coverage_fraction: 0.8,
            evidence_consistency_score: posCount > 0 ? 0.78 : 0.0,
            rank: 1,
            has_meaningful_support: posCount > 0,
          },
        ],
        era5_path: req.era5_netcdf_path,
        cmems_path: req.cmems_netcdf_path,
        created_at: new Date().toISOString(),
        scientific_disclaimer: 'Scientific disclaimer text',
      } as ExperimentRunResult
    })
  })

  it('Step 4 provides real positions to selected vessels and Step 5 forwards them to ExperimentRunner', async () => {
    render(<RealExperimentView />)

    // Step 1: Search and Select Product
    const searchButton = await screen.findByRole('button', { name: /Search CDSE Catalogue/i })
    fireEvent.click(searchButton)

    const selectButton = await screen.findByRole('button', { name: /Select →/i })
    fireEvent.click(selectButton)

    // Step 2: Acquire Environment (auto-advances to Step 3)
    const acquireButton = await screen.findByRole('button', { name: /Acquire ERA5 \+ CMEMS/i })
    fireEvent.click(acquireButton)

    // Step 3: Drift Config -> Next: Vessel Search
    const nextVesselBtn = await screen.findByRole('button', { name: /Next: Vessel Search →/i })
    fireEvent.click(nextVesselBtn)

    // Step 4: Search AIS Tracks
    const searchAisBtn = await screen.findByRole('button', { name: /Search AIS Tracks/i })
    fireEvent.click(searchAisBtn)

    // Verify MV ULYSSE and MEDITERRANEAN STAR are displayed
    expect(await screen.findByText('MV ULYSSE')).toBeDefined()
    expect(await screen.findByText('MEDITERRANEAN STAR')).toBeDefined()
    expect(screen.getByText(/2 vessel\(s\) found — 21 total positions/i)).toBeDefined()

    // Select MV ULYSSE
    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes.length).toBe(2)
    fireEvent.click(checkboxes[0])

    // Proceed to Step 5
    const nextAttributionBtn = screen.getByRole('button', { name: /Next: Run Attribution →/i })
    fireEvent.click(nextAttributionBtn)

    // Step 5: Execute Attribution Experiment
    const runBtn = await screen.findByRole('button', { name: /Execute Attribution Experiment/i })
    fireEvent.click(runBtn)

    // Verify runExperiment was called with actual positions, NOT positions: []
    await waitFor(() => {
      expect(experimentApi.runExperiment).toHaveBeenCalledTimes(1)
    })

    const runCall = vi.mocked(experimentApi.runExperiment).mock.calls[0][0]
    expect(runCall.selected_vessels.length).toBe(1)
    const ulysseSent = runCall.selected_vessels[0]
    expect(ulysseSent.mmsi).toBe('228308800')
    expect(ulysseSent.positions.length).toBe(12)
    expect(ulysseSent.positions[0].lat).toBeCloseTo(43.15, 2)
    expect(ulysseSent.positions[0].lon).toBeCloseTo(9.38, 2)

    // Step 6: Verify results view does NOT report AIS positions = 0
    await waitFor(() => {
      expect(screen.getByText('Attribution Results')).toBeDefined()
    })

    // Look for the AIS positions metric
    const featureBoxes = screen.getAllByText('AIS positions')
    expect(featureBoxes.length).toBeGreaterThan(0)
    // The rendered count must be 12, NOT 0
    expect(screen.getByText('12')).toBeDefined()
    expect(screen.queryByText('No meaningful spatial/temporal overlap with source zone')).toBeNull()
  })
})
