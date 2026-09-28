import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import RealExperimentView from '../components/views/RealExperimentView'
import AttributionMap from '../components/views/AttributionMap'
import * as experimentApi from '../real-experiment/experimentApi'
import type { SlickCharacterization, SentinelProduct } from '../real-experiment/experimentTypes'

globalThis.ResizeObserver = class ResizeObserver {
  observe() { }
  unobserve() { }
  disconnect() { }
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
  characterizeSentinelObservation: vi.fn(),
  selectEnvironment: vi.fn(),
  searchAisVessels: vi.fn(),
  fetchAisPositions: vi.fn(),
  runExperiment: vi.fn(),
  listExperimentRuns: vi.fn().mockResolvedValue({ runs: [], count: 0 }),
  getExperimentRun: vi.fn(),
}))

describe('MARIS Step 10 — Automated Oil-Spill Detection & Characterization', () => {
  const mockCorsicaProduct: SentinelProduct = {
    product_id: 'S1A_IW_GRDH_1SDV_20181008T052822_20181008T052847_024039_02A039_E2B6',
    title: 'S1A_IW_GRDH_1SDV_20181008T052822_20181008T052847_024039_02A039_E2B6',
    sensing_start: '2018-10-08T05:28:22Z',
    sensing_end: '2018-10-08T05:28:47Z',
    online: true,
    platform: 'Sentinel-1A',
    mode: 'IW',
    product_class: 'S',
    polarisation: 'VV+VH',
    centroid_lon: 9.47833,
    centroid_lat: 43.24833,
    footprint: { type: 'Polygon', coordinates: [[[9.0, 42.5], [10.0, 42.5], [10.0, 43.5], [9.0, 42.5]]] },
    content_length_bytes: 1048576,
  }

  const mockCorsicaCharacterization: SlickCharacterization = {
    detected: true,
    status: 'DETECTED (Verified Benchmark)',
    centroid_lon: 9.47833,
    centroid_lat: 43.24833,
    area_km2: 45.2,
    area_m2: 45200000.0,
    bbox: { west: 9.418, south: 43.190, east: 9.538, north: 43.310 },
    slick_geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [9.418, 43.195],
          [9.450, 43.230],
          [9.535, 43.310],
          [9.502, 43.258],
          [9.418, 43.195],
        ],
      ],
    },
    observation_time: '2018-10-08T05:28:22Z',
    satellite_product_id: 'S1A_IW_GRDH_1SDV_20181008T052822',
    platform: 'Sentinel-1A',
    sensor: 'Sentinel-1A C-SAR',
    mode: 'IW GRDH (Interferometric Wide Swath)',
    polarisation: 'VV+VH',
    confidence: 0.95,
    damping_contrast_db: 5.4,
    estimated_age_hours: 12.0,
    detection_method: 'Adaptive Thresholding (Bragg damping Δσ⁰ ≥ 3.5 dB) & 8-connectivity geometry extraction',
    provenance: 'ESA Copernicus Sentinel-1A Ground Truth (Cap Corse Collision Benchmark)',
    data_fidelity: 'Verified physical slick observation with validated ground-truth coordinates and measured damping contrast.',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(experimentApi.discoverSentinelProducts).mockResolvedValue({
      products: [mockCorsicaProduct],
      count: 1,
      configured: true,
    })
    vi.mocked(experimentApi.characterizeSentinelObservation).mockResolvedValue({
      characterization: mockCorsicaCharacterization,
    })
  })

  it('renders Slick Characterization Panel when Sentinel-1 observation is selected', async () => {
    render(<RealExperimentView />)

    // Trigger CDSE search
    const searchBtn = await screen.findByRole('button', { name: /search cdse catalogue/i })
    fireEvent.click(searchBtn)

    expect(await screen.findByText(/1 product\(s\) found/i)).toBeTruthy()

    // Click "Select →"
    const selectBtn = await screen.findByRole('button', { name: /select →/i })
    fireEvent.click(selectBtn)

    // Verify characterization API was called
    await waitFor(() => {
      expect(experimentApi.characterizeSentinelObservation).toHaveBeenCalledWith(
        expect.objectContaining({
          product_id: mockCorsicaProduct.product_id,
        })
      )
    })

    // Verify characterization panel is rendered in Step 2 with authentic metrics
    await waitFor(() => {
      expect(screen.getByTestId('step2-slick-characterization-panel')).toBeTruthy()
    })

    expect(screen.getByText(/Automated Oil-Spill Characterization/i)).toBeTruthy()
    expect(screen.getByText('DETECTED (Verified Benchmark)')).toBeTruthy()
    expect(screen.getByText(/43\.2483°N, 9\.4783°E/)).toBeTruthy()
    expect(screen.getByText(/45\.2 km²/)).toBeTruthy()
    expect(screen.getByText(/5\.4 dB/)).toBeTruthy()
    expect(screen.getByText(/95%/)).toBeTruthy()
    expect(screen.getByText(/Verified physical slick observation/i)).toBeTruthy()
  })

  it('handles unsegmented catalogue observations honestly without fabricating area or damping', async () => {
    const mockUnsegmentedProduct: SentinelProduct = {
      product_id: 'S1B_IW_CATALOGUE_SCENE_001',
      title: 'S1B_IW_CATALOGUE_SCENE_001',
      sensing_start: '2024-03-15T12:00:00Z',
      sensing_end: null,
      online: true,
      platform: 'Sentinel-1B',
      mode: 'IW',
      product_class: 'S',
      polarisation: 'VV',
      centroid_lon: 15.0,
      centroid_lat: 38.0,
      footprint: null,
      content_length_bytes: null,
    }

    const mockUnsegmentedChar: SlickCharacterization = {
      detected: false,
      status: 'CATALOGUE_SELECTION',
      centroid_lon: 15.0,
      centroid_lat: 38.0,
      area_km2: null,
      area_m2: null,
      bbox: null,
      slick_geometry: null,
      observation_time: '2024-03-15T12:00:00Z',
      satellite_product_id: 'S1B_IW_CATALOGUE_SCENE_001',
      platform: 'Sentinel-1B',
      sensor: 'Sentinel-1B C-SAR',
      mode: 'IW GRDH',
      polarisation: 'VV',
      confidence: null,
      damping_contrast_db: null,
      estimated_age_hours: 12.0,
      detection_method: 'CDSE Sentinel-1 Product Catalogue Spatial Anchor',
      provenance: 'European Space Agency (ESA) Copernicus Data Space Ecosystem',
      data_fidelity: 'Catalogue metadata only. Physical raster segment not locally materialized.',
    }

    vi.mocked(experimentApi.discoverSentinelProducts).mockResolvedValue({
      products: [mockUnsegmentedProduct],
      count: 1,
      configured: true,
    })
    vi.mocked(experimentApi.characterizeSentinelObservation).mockResolvedValue({
      characterization: mockUnsegmentedChar,
    })

    render(<RealExperimentView />)

    fireEvent.click(await screen.findByRole('button', { name: /search cdse catalogue/i }))

    const selectBtn = await screen.findByRole('button', { name: /select →/i })
    fireEvent.click(selectBtn)

    await waitFor(() => {
      expect(screen.getByTestId('step2-slick-characterization-panel')).toBeTruthy()
    })

    expect(screen.getByText('CATALOGUE_SELECTION')).toBeTruthy()

    // Confirm that missing values are displayed as Unavailable rather than fabricated numbers
    expect(screen.getByText(/Unavailable \(unsegmented\)/i)).toBeTruthy()
    expect(screen.getByText('Unavailable')).toBeTruthy()
    expect(screen.getByText(/Catalogue geometric anchor/i)).toBeTruthy()
  })

  it('renders detected slick polygon layer in AttributionMap', () => {
    const { container } = render(
      <AttributionMap
        observation={{
          lat: 43.24833,
          lon: 9.47833,
          timestamp: '2018-10-08T05:28:22Z',
          areaKm2: 45.2,
          confidence: 0.95,
          dampingContrastDb: 5.4,
          detectionStatus: 'DETECTED (Verified Benchmark)',
          slickGeometry: {
            type: 'Polygon',
            coordinates: [
              [
                [9.418, 43.195],
                [9.450, 43.230],
                [9.535, 43.310],
                [9.502, 43.258],
                [9.418, 43.195],
              ],
            ],
          },
        }}
        reconstructedSource={{
          lat: 43.19,
          lon: 9.42,
          radiusM: 6500,
          geojson: null,
        }}
        backwardSteps={[
          { step: 0, lat: 43.24833, lon: 9.47833, uncertainty_radius_m: 1000, timestamp: '2018-10-08T05:28:22Z' },
          { step: 1, lat: 43.19, lon: 9.42, uncertainty_radius_m: 6500, timestamp: '2018-10-07T17:28:22Z' },
        ]}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => { }}
      />
    )

    // Check SVG fallback slick polygon exists in DOM
    const slickPolygonLayer = container.querySelector('[data-testid="slick-polygon-layer"]')
    expect(slickPolygonLayer).toBeTruthy()
    expect(slickPolygonLayer?.querySelector('polygon')).toBeTruthy()
  })
})
