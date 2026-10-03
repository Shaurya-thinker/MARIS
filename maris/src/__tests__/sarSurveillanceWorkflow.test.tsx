import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import SarSurveillanceSection from '../components/views/SarSurveillanceSection'
import AttributionMap from '../components/views/AttributionMap'
import ScientificReportView from '../components/views/ScientificReportView'
import type {
  SarSurveillanceResult,
  SarBrightTarget,
  SarAisAssociation,
  ExperimentRunResult,
} from '../real-experiment/experimentTypes'

globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.mock('../real-experiment/experimentApi', async () => {
  const actual = await vi.importActual<any>('../real-experiment/experimentApi')
  return {
    ...actual,
    fetchScientificReport: vi.fn().mockImplementation(async (runId: string) => ({
      run_id: runId,
      candidates: [],
    })),
    getExperimentRun: vi.fn(),
  }
})

const mockTargets: SarBrightTarget[] = [
  {
    target_id: 'SAR_TGT_001',
    pixel_x: 120,
    pixel_y: 240,
    lon: 9.485,
    lat: 43.255,
    peak_backscatter_db: -4.5,
    local_clutter_mean_db: -18.2,
    target_to_clutter_ratio_db: 13.7,
    apparent_major_extent_m: 145.0,
    apparent_minor_extent_m: 28.0,
    pixel_count: 32,
    detection_confidence: 0.94,
    provenance: 'SAR_CFAR_SENTINEL_1',
  },
  {
    target_id: 'SAR_TGT_002',
    pixel_x: 200,
    pixel_y: 310,
    lon: 9.512,
    lat: 43.280,
    peak_backscatter_db: -6.1,
    local_clutter_mean_db: -17.9,
    target_to_clutter_ratio_db: 11.8,
    apparent_major_extent_m: 90.0,
    apparent_minor_extent_m: 18.0,
    pixel_count: 18,
    detection_confidence: 0.88,
    provenance: 'SAR_CFAR_SENTINEL_1',
  },
  {
    target_id: 'SAR_TGT_003',
    pixel_x: 350,
    pixel_y: 420,
    lon: 9.540,
    lat: 43.300,
    peak_backscatter_db: -5.0,
    local_clutter_mean_db: -18.0,
    target_to_clutter_ratio_db: 13.0,
    apparent_major_extent_m: 110.0,
    apparent_minor_extent_m: 22.0,
    pixel_count: 24,
    detection_confidence: 0.91,
    provenance: 'SAR_CFAR_SENTINEL_1',
  },
]

const mockCorrelationsAllSix: SarAisAssociation[] = [
  // 1. COINCIDENT_AIS_MATCH
  {
    correlation_id: 'CORR_01',
    classification: 'COINCIDENT_AIS_MATCH',
    target: mockTargets[0],
    vessel_id: 'vessel-ulysse',
    mmsi: '228000001',
    vessel_name: 'MV ULYSSE',
    vessel_type: 'Cargo',
    ais_lon: 9.486,
    ais_lat: 43.256,
    ais_timestamp: '2018-10-08T05:28:00Z',
    ais_position_provenance: 'GENUINE_OBSERVATION',
    spatial_separation_m: 120.5,
    temporal_delta_seconds: 22,
    findings_summary: 'Tight coincident radar return and AIS transponder position.',
    investigation_flag: false,
  },
  // 2. SPATIAL_DISCREPANCY_EXCEEDANCE
  {
    correlation_id: 'CORR_02',
    classification: 'SPATIAL_DISCREPANCY_EXCEEDANCE',
    target: mockTargets[1],
    vessel_id: 'vessel-star',
    mmsi: '228000002',
    vessel_name: 'MEDITERRANEAN STAR',
    vessel_type: 'Tanker',
    ais_lon: 9.525,
    ais_lat: 43.292,
    ais_timestamp: '2018-10-08T05:26:30Z',
    ais_position_provenance: 'TEMPORALLY_ALIGNED_FIX',
    spatial_separation_m: 1650.0,
    temporal_delta_seconds: 112,
    findings_summary: 'Position discrepancy exceeds standard kinematic tolerance.',
    investigation_flag: true,
  },
  // 3. AIS_OBSERVATION_GAP
  {
    correlation_id: 'CORR_03',
    classification: 'AIS_OBSERVATION_GAP',
    target: mockTargets[2],
    vessel_id: 'vessel-gap',
    mmsi: '228000003',
    vessel_name: 'PACIFIC TRADER',
    vessel_type: 'Bulk Carrier',
    ais_lon: 9.542,
    ais_lat: 43.302,
    ais_timestamp: '2018-10-08T04:45:00Z',
    ais_position_provenance: 'TEMPORALLY_ALIGNED_FIX',
    spatial_separation_m: 280.0,
    temporal_delta_seconds: 2580,
    findings_summary: 'Surveillance fix temporal delta exceeds regular reporting interval.',
    investigation_flag: true,
  },
  // 4. RADAR_TARGET_UNCORRELATED (radar only)
  {
    correlation_id: 'CORR_04',
    classification: 'RADAR_TARGET_UNCORRELATED',
    target: {
      target_id: 'SAR_TGT_004',
      pixel_x: 500,
      pixel_y: 100,
      lon: 9.600,
      lat: 43.350,
      peak_backscatter_db: -3.8,
      local_clutter_mean_db: -18.5,
      target_to_clutter_ratio_db: 14.7,
      apparent_major_extent_m: 130.0,
      apparent_minor_extent_m: 25.0,
      pixel_count: 28,
      detection_confidence: 0.95,
      provenance: 'SAR_CFAR_SENTINEL_1',
    },
    vessel_id: null,
    mmsi: null,
    vessel_name: null,
    vessel_type: null,
    ais_lon: null,
    ais_lat: null,
    ais_timestamp: null,
    ais_position_provenance: 'UNAVAILABLE',
    spatial_separation_m: null,
    temporal_delta_seconds: null,
    findings_summary: 'Radar scattering detection with no corresponding AIS record in search gate.',
    investigation_flag: true,
  },
  // 5. AIS_VESSEL_NOT_DETECTED (AIS only)
  {
    correlation_id: 'CORR_05',
    classification: 'AIS_VESSEL_NOT_DETECTED',
    target: null,
    vessel_id: 'vessel-undetected',
    mmsi: '228000005',
    vessel_name: 'BLUE OCEAN',
    vessel_type: 'Tug',
    ais_lon: 9.420,
    ais_lat: 43.210,
    ais_timestamp: '2018-10-08T05:28:10Z',
    ais_position_provenance: 'GENUINE_OBSERVATION',
    spatial_separation_m: null,
    temporal_delta_seconds: null,
    findings_summary: 'AIS transponder record with no corresponding CFAR bright target in SAR raster.',
    investigation_flag: false,
  },
  // 6. AMBIGUOUS_MULTI_TARGET_PROXIMITY
  {
    correlation_id: 'CORR_06',
    classification: 'AMBIGUOUS_MULTI_TARGET_PROXIMITY',
    target: mockTargets[0],
    vessel_id: 'vessel-ambiguous',
    mmsi: '228000006',
    vessel_name: 'COASTAL PATROL',
    vessel_type: 'Law Enforcement',
    ais_lon: 9.488,
    ais_lat: 43.257,
    ais_timestamp: '2018-10-08T05:27:50Z',
    ais_position_provenance: 'GENUINE_OBSERVATION',
    spatial_separation_m: 210.0,
    temporal_delta_seconds: 32,
    findings_summary: 'Multiple radar detections in close spatiotemporal proximity to AIS broadcast.',
    investigation_flag: true,
  },
]

const mockSurveillanceResult: SarSurveillanceResult = {
  product_id: 'S1A_IW_GRDH_1SDV_20181008T052822',
  observation_time: '2018-10-08T05:28:22Z',
  total_radar_targets_detected: 4,
  correlated_ais_matches: 1,
  spatial_discrepancies: 1,
  uncorrelated_radar_targets: 1,
  undetected_ais_vessels: 1,
  correlations: mockCorrelationsAllSix,
  model_version: 'sar_ais_surveillance_v1',
  provenance: 'SENTINEL_1_GRD_CFAR + AIS_BENCHMARK_CORRELATION',
  scientific_disclaimer:
    'Bright radar targets are SAR scattering detections and are not automatically classified as vessels. Association with an AIS record indicates spatial/temporal correspondence, not proof of causation, identity, intent, or illegal activity.',
}

const mockRunResultWithSurveillance: ExperimentRunResult = {
  run_id: 'run-sar-surv-test',
  model_version: 'leeway_euler_backward_v1',
  observation_source: 'SAR_DERIVED',
  satellite_product_id: 'S1A_IW_GRDH_1SDV_20181008T052822',
  observation_time: '2018-10-08T05:28:22Z',
  observation_lon: 9.4783,
  observation_lat: 43.2483,
  source_lon: 9.6757,
  source_lat: 41.1538,
  source_radius_m: 6500,
  backtrack_hours: 12,
  backward_steps: [
    { step: 0, lon: 9.4783, lat: 43.2483, timestamp: '2018-10-08T05:28:07Z', uncertainty_radius_m: 500 },
    { step: 1, lon: 9.6757, lat: 41.1538, timestamp: '2018-10-07T17:28:07Z', uncertainty_radius_m: 6500 },
  ],
  vessels: [],
  era5_path: 'backend/acquisitions/era5_wind.nc',
  cmems_path: 'backend/acquisitions/cmems_current.nc',
  created_at: '2026-10-03T12:00:00Z',
  scientific_disclaimer: 'Scientific assessment only.',
  sar_surveillance: mockSurveillanceResult,
}

describe('MARIS Phase 6.4 — Frontend Surveillance UI & Scientific Report Integration', () => {
  // 1. sar_surveillance renders when present
  it('1. sar_surveillance renders when present in SarSurveillanceSection', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getByText('SAR ↔ AIS Dual-Sensor Maritime Surveillance')).toBeDefined()
    expect(screen.getByTestId('sar-surveillance-table')).toBeDefined()
  })

  // 2. null surveillance state renders disabled/not-run message
  it('2. null surveillance state renders disabled/not-run message', () => {
    render(<SarSurveillanceSection surveillance={null} />)
    expect(
      screen.getByText('Dual-sensor surveillance was not enabled for this experiment.')
    ).toBeDefined()
    expect(screen.queryByTestId('sar-surveillance-table')).toBeNull()
  })

  // 3. summary counts render correctly
  it('3. summary counts render correctly', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getByTestId('count-targets-detected').textContent?.trim()).toBe('4')
    expect(screen.getByTestId('count-ais-associations').textContent?.trim()).toBe('1')
    expect(screen.getByTestId('count-spatial-discrepancies').textContent?.trim()).toBe('1')
    expect(screen.getByTestId('count-uncorrelated-targets').textContent?.trim()).toBe('1')
    expect(screen.getByTestId('count-undetected-vessels').textContent?.trim()).toBe('1')
  })

  // 4. all six classifications render correctly
  it('4. all six classifications render correctly with human-readable labels', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getByText('Coincident AIS Match')).toBeDefined()
    expect(screen.getByText('Spatial Discrepancy Exceedance')).toBeDefined()
    expect(screen.getByText('AIS Observation Gap')).toBeDefined()
    expect(screen.getByText('Radar Target Uncorrelated')).toBeDefined()
    expect(screen.getByText('AIS Vessel Not Detected')).toBeDefined()
    expect(screen.getAllByText('Ambiguous Multi-Target Proximity').length).toBeGreaterThan(0)
  })

  // 5. radar-only target renders without AIS vessel
  it('5. radar-only target renders without AIS vessel ("No AIS association")', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getByText('Target SAR_TGT_004')).toBeDefined()
    expect(screen.getByText('No AIS association')).toBeDefined()
  })

  // 6. AIS-only vessel renders without radar target
  it('6. AIS-only vessel renders without radar target ("No SAR bright target")', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getByText(/BLUE OCEAN/)).toBeDefined()
    expect(screen.getAllByText('No SAR bright target').length).toBeGreaterThan(0)
  })

  // 7. ambiguous association remains visible
  it('7. ambiguous association remains visible without collapsing', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getAllByText('Ambiguous Multi-Target Proximity').length).toBeGreaterThan(0)
    expect(screen.getByText(/COASTAL PATROL/)).toBeDefined()
  })

  // 8. AIS position provenance is displayed
  it('8. AIS position provenance is displayed', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    expect(screen.getAllByText('GENUINE_OBSERVATION').length).toBeGreaterThan(0)
    expect(screen.getAllByText('TEMPORALLY_ALIGNED_FIX').length).toBeGreaterThan(0)
    expect(screen.getAllByText('UNAVAILABLE').length).toBeGreaterThan(0)
  })

  // 9. scientific disclaimer is displayed
  it('9. scientific disclaimer is displayed verbatim from backend', () => {
    render(<SarSurveillanceSection surveillance={mockSurveillanceResult} />)
    const disclaimerCard = screen.getByTestId('sar-surveillance-disclaimer')
    expect(disclaimerCard).toBeDefined()
    expect(disclaimerCard.textContent).toContain('Bright radar targets are SAR scattering detections')
  })

  // 10. map receives radar targets and AIS positions
  it('10. AttributionMap renders bright radar targets and AIS positions in SVG fallback', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockRunResultWithSurveillance.backward_steps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        sarSurveillance={mockSurveillanceResult}
      />
    )

    const radarTargets = screen.getAllByTestId('bright-radar-target-marker')
    expect(radarTargets.length).toBeGreaterThan(0)

    const aisObs = screen.getAllByTestId('ais-observation-marker')
    expect(aisObs.length).toBeGreaterThan(0)
  })

  // 11. association vector is rendered from existing coordinates
  it('11. AttributionMap renders SAR ↔ AIS association lines from existing coordinates', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockRunResultWithSurveillance.backward_steps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        sarSurveillance={mockSurveillanceResult}
      />
    )

    const assocLines = screen.getAllByTestId('sar-ais-association-line')
    expect(assocLines.length).toBeGreaterThan(0)

    // Check legend items
    expect(screen.getByTestId('legend-sar-target')).toBeDefined()
    expect(screen.getByTestId('legend-ais-obs')).toBeDefined()
    expect(screen.getByTestId('legend-sar-ais-assoc')).toBeDefined()
  })

  // 12. no "dark vessel" terminology appears
  it('12. Zero-Accusation Invariant: no "dark vessel" or "dark ship" terminology appears anywhere', () => {
    const { container } = render(
      <div>
        <SarSurveillanceSection surveillance={mockSurveillanceResult} />
        <AttributionMap
          observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
          reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
          backwardSteps={mockRunResultWithSurveillance.backward_steps}
          vessels={[]}
          selectedVesselId={null}
          onSelectVessel={() => {}}
          sarSurveillance={mockSurveillanceResult}
        />
      </div>
    )

    const html = container.innerHTML.toLowerCase()
    expect(html).not.toContain('dark vessel')
    expect(html).not.toContain('dark ship')
    expect(html).not.toContain('illegal vessel')
    expect(html).not.toContain('suspect vessel')
    expect(html).not.toContain('proof of ais tampering')
  })

  // 13. no "responsible vessel" / "culprit" terminology appears
  it('13. Zero-Accusation Invariant: no "responsible vessel" or "culprit" terminology appears', () => {
    const { container } = render(
      <SarSurveillanceSection surveillance={mockSurveillanceResult} />
    )
    const html = container.innerHTML.toLowerCase()
    expect(html).not.toContain('culprit vessel')
    expect(html).not.toContain('culprit')
    expect(html).not.toContain('responsible vessel')
    expect(html).not.toContain('probability of vessel identity')
  })

  // 14. empty-target state renders correctly
  it('14. empty-target state renders "No bright radar targets detected in the configured SAR scene."', () => {
    const zeroTargetResult: SarSurveillanceResult = {
      ...mockSurveillanceResult,
      total_radar_targets_detected: 0,
      correlations: [],
    }
    render(<SarSurveillanceSection surveillance={zeroTargetResult} />)
    expect(
      screen.getByText('No bright radar targets detected in the configured SAR scene.')
    ).toBeDefined()
  })

  // 15. zero-AIS state renders correctly
  it('15. zero-AIS state renders "No AIS observations were available within the configured search window."', () => {
    const zeroAisResult: SarSurveillanceResult = {
      ...mockSurveillanceResult,
      total_radar_targets_detected: 2,
      correlations: [],
    }
    render(<SarSurveillanceSection surveillance={zeroAisResult} />)
    expect(
      screen.getByText('No AIS observations were available within the configured search window.')
    ).toBeDefined()
  })

  // 16. ScientificReportView Section 8.5 integration
  it('16. ScientificReportView renders Section 8.5 with metadata, summary, and disclaimer', async () => {
    render(
      <ScientificReportView
        runId="run-sar-surv-test"
        onBack={() => {}}
        initialRun={mockRunResultWithSurveillance}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('report-sar-surveillance-section')).toBeDefined()
    })

    expect(screen.getByText('8.5 SAR ↔ AIS Dual-Sensor Maritime Surveillance')).toBeDefined()
    expect(screen.getByTestId('report-surveillance-table')).toBeDefined()
    expect(screen.getByTestId('report-surveillance-disclaimer')).toBeDefined()
  })

  // 17. ScientificReportView disabled state when surveillance is null
  it('17. ScientificReportView renders disabled state when surveillance is null', async () => {
    const runWithoutSurv: ExperimentRunResult = {
      ...mockRunResultWithSurveillance,
      sar_surveillance: null,
    }
    render(
      <ScientificReportView
        runId="run-sar-surv-test"
        onBack={() => {}}
        initialRun={runWithoutSurv}
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId('report-sar-surveillance-section')).toBeDefined()
    })

    expect(
      screen.getByText('Dual-sensor surveillance was not enabled for this experiment.')
    ).toBeDefined()
  })

  // 18. Radar target popups in AttributionMap show technical metrics and neutral wording
  it('18. AttributionMap popups display target metrics without claiming target is a vessel', () => {
    render(
      <AttributionMap
        observation={{ lat: 43.2483, lon: 9.4783, timestamp: '2018-10-08T05:28:07Z' }}
        reconstructedSource={{ lat: 41.1538, lon: 9.6757, radiusM: 6500 }}
        backwardSteps={mockRunResultWithSurveillance.backward_steps}
        vessels={[]}
        selectedVesselId={null}
        onSelectVessel={() => {}}
        sarSurveillance={mockSurveillanceResult}
      />
    )

    const targetMarker = screen.getAllByTestId('bright-radar-target-marker')[0]
    fireEvent.click(targetMarker)

    expect(screen.getByTestId('radar-target-popup')).toBeDefined()
    expect(screen.getByText(/Target-to-Clutter Ratio/)).toBeDefined()
    expect(screen.getByText(/Peak Backscatter:/)).toBeDefined()
    expect(
      screen.getByText(
        'Bright radar targets are SAR scattering detections and are not automatically classified as vessels.'
      )
    ).toBeDefined()
  })

  // 19. SarSurveillanceSection calls onConfigureStep when disabled and action button is clicked
  it('19. SarSurveillanceSection triggers onConfigureStep callback from disabled empty state', () => {
    const handleConfigure = vi.fn()
    render(
      <SarSurveillanceSection
        surveillance={null}
        onConfigureStep={handleConfigure}
      />
    )

    expect(screen.getByTestId('sar-surveillance-disabled-card')).toBeDefined()
    const btn = screen.getByTestId('btn-enable-surveillance-goto-step5')
    expect(btn).toBeDefined()
    fireEvent.click(btn)
    expect(handleConfigure).toHaveBeenCalledTimes(1)
  })
})
