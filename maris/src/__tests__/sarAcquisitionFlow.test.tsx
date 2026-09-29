import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import RealExperimentView from '../components/views/RealExperimentView'
import * as experimentApi from '../real-experiment/experimentApi'
import type { SentinelProduct, SlickCharacterization, SarAcquisitionResponse } from '../real-experiment/experimentTypes'

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
  acquireSarSubscene: vi.fn(),
  selectEnvironment: vi.fn(),
  searchAisVessels: vi.fn(),
  fetchAisPositions: vi.fn(),
  runExperiment: vi.fn(),
  listExperimentRuns: vi.fn().mockResolvedValue({ runs: [], count: 0 }),
  getExperimentRun: vi.fn(),
}))

describe('MARIS SAR Subscene Acquisition Flow', () => {
  const mockProduct: SentinelProduct = {
    product_id: 'S1A_IW_GRDH_1SDV_20181008T052822_TEST',
    title: 'S1A_IW_GRDH_1SDV_20181008T052822_TEST',
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

  const mockAcquisitionResponse: SarAcquisitionResponse = {
    success: true,
    sar_raster_path: 'backend/data/sar_subscenes/corsica_2018_sar_subscene.tif',
    product_id: 'S1A_IW_GRDH_1SDV_20181008T052822_TEST',
    file_size_bytes: 4024974,
    is_cached: true,
    source_provider: 'Copernicus Data Space Ecosystem (Sentinel Hub Process API)',
    data_authenticity: 'GENUINE_SENTINEL1_SAR_SATELLITE_PIXELS',
    is_test_fixture: false,
    crs: 'EPSG:4326',
    bands: ['sigma0_db_VV', 'sigma0_db_VH'],
    bbox: { west: 9.38, south: 43.12, east: 9.58, north: 43.35 },
    message: 'Subscene successfully acquired.',
  }

  const mockDetectedCharacterization: SlickCharacterization = {
    detected: true,
    status: 'DETECTED (Adaptive Thresholding on SAR Raster)',
    centroid_lon: 9.47833,
    centroid_lat: 43.24833,
    area_km2: 9.52,
    area_m2: 9520000.0,
    bbox: { west: 9.418, south: 43.190, east: 9.538, north: 43.310 },
    slick_geometry: {
      type: 'Polygon',
      coordinates: [[[9.418, 43.19], [9.538, 43.19], [9.538, 43.31], [9.418, 43.31], [9.418, 43.19]]],
    },
    confidence: 0.94,
    damping_contrast_db: 5.4,
    estimated_age_hours: 12,
    sensor: 'Sentinel-1 C-SAR',
    mode: 'IW GRDH',
    polarisation: 'VV',
    data_fidelity: 'Physical observation metrics derived from satellite SAR radar backscatter damping.',
    detection_method: 'Stage B3 Adaptive Thresholding (Physical SAR Raster)',
    has_physical_raster: true,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(experimentApi.discoverSentinelProducts).mockResolvedValue({
      products: [mockProduct],
      count: 1,
      configured: true,
    })
    vi.mocked(experimentApi.characterizeSentinelObservation).mockResolvedValue({
      characterization: {
        detected: false,
        status: 'CATALOGUE_SELECTION',
        centroid_lon: 9.47833,
        centroid_lat: 43.24833,
        area_km2: null,
        area_m2: null,
        bbox: null,
        slick_geometry: null,
        confidence: 0.5,
        damping_contrast_db: null,
        estimated_age_hours: null,
        sensor: 'Sentinel-1',
        mode: 'IW',
        polarisation: 'VV+VH',
        data_fidelity: 'Catalogue unsegmented observation',
        detection_method: 'Metadata anchor',
        has_physical_raster: false,
      },
    })
    vi.mocked(experimentApi.acquireSarSubscene).mockResolvedValue(mockAcquisitionResponse)
  })

  it('renders SAR Subscene card in Step 2 and acquires authentic SAR subscene on user click', async () => {
    render(<RealExperimentView />)

    // Search and select product
    const searchBtn = await screen.findByRole('button', { name: /search cdse catalogue/i })
    fireEvent.click(searchBtn)

    expect(await screen.findByText(/1 product\(s\) found/i)).toBeTruthy()
    const selectBtn = await screen.findByRole('button', { name: /select →/i })
    fireEvent.click(selectBtn)

    // Now in Step 2: verify SarSubsceneCard is displayed in unacquired state
    await waitFor(() => {
      expect(screen.getByTestId('sar-subscene-card')).toBeTruthy()
    })
    expect(screen.getByText('NOT ACQUIRED (CATALOGUE ONLY)')).toBeTruthy()

    const acquireBtn = screen.getByRole('button', { name: /fetch live from copernicus/i })
    expect(acquireBtn).toBeTruthy()

    // Mock next characterization to return detected physical raster
    vi.mocked(experimentApi.characterizeSentinelObservation).mockResolvedValue({
      characterization: mockDetectedCharacterization,
    })

    // Click Acquire SAR Subscene
    fireEvent.click(acquireBtn)

    // Verify acquireSarSubscene API was called with target product_id
    await waitFor(() => {
      expect(experimentApi.acquireSarSubscene).toHaveBeenCalledWith(
        expect.objectContaining({
          product_id: mockProduct.product_id,
        })
      )
    })

    // Verify characterization was re-triggered with the acquired raster path
    await waitFor(() => {
      expect(experimentApi.characterizeSentinelObservation).toHaveBeenCalledWith(
        expect.objectContaining({
          sar_raster_path: mockAcquisitionResponse.sar_raster_path,
        })
      )
    })

    // Verify card updates to LIVE COPERNICUS SAR LOADED state
    await waitFor(() => {
      expect(screen.getByText('LIVE COPERNICUS SAR LOADED')).toBeTruthy()
    })
    expect(screen.getByText(/3\.84 MB/)).toBeTruthy()
    expect(screen.getByText(/EPSG:4326/)).toBeTruthy()
    expect(screen.getByText('DETECTED (Adaptive Thresholding on SAR Raster)')).toBeTruthy()
    expect(screen.getByText(/9\.52 km²/)).toBeTruthy()
  })
})
