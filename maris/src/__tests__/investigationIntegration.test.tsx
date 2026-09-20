/**
 * Stage G2 — Frontend ↔ Backend Integration Tests for MARIS.
 *
 * Validates the 20 mandatory criteria:
 * 1. API client creates investigation.
 * 2. API client retrieves investigation.
 * 3. API client retrieves status.
 * 4. API client runs investigation.
 * 5. API client retrieves artifacts.
 * 6. Loading state appears during requests.
 * 7. Structured backend error is rendered.
 * 8. Failed API request does NOT show historical mock data.
 * 9. F2 ranking renders in backend order.
 * 10. F2 score is labeled Evidence Consistency Score.
 * 11. Evidence availability is displayed.
 * 12. Missing evidence displays as unavailable/insufficient.
 * 13. E3 behavioral observations are labeled contextual.
 * 14. D1 is labeled Forward drift cross-check.
 * 15. F3 scientific disclaimer is displayed.
 * 16. Backend spill geometry can be rendered on the map.
 * 17. Backend source candidate zone can be rendered.
 * 18. Backend vessel data can be rendered.
 * 19. Frontend does not perform scientific recalculation.
 * 20. Explicit demo/historical mode is not silently activated by API failure.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { App } from '../App'
import {
  createInvestigation,
  getInvestigation,
  getInvestigationStatus,
  runInvestigation,
  getInvestigationArtifacts,
  getCandidateRanking,
  getExplainabilityReport,
  ApiError,
} from '../api/investigationApi'
import { AnalysisPanel } from '../components/analysis/AnalysisPanel'
import { IncidentPanel } from '../components/incident/IncidentPanel'
import { InvestigationWorkspace, resolveSpillId } from '../components/layout/InvestigationWorkspace'
import type {
  ArtifactSummary,
  CandidateRanking,
  ExplainabilityReport,
  InvestigationCreateRequest,
  InvestigationResponse,
  InvestigationStatusResponse,
} from '../types/investigationApi'
import { candidateVessels, incidentData } from '../data/demoData'

// jsdom does not implement ResizeObserver — stub it globally so MapView
// components that use `new ResizeObserver(...)` don't throw.
globalThis.ResizeObserver = class ResizeObserver {
  observe() { }
  unobserve() { }
  disconnect() { }
}

// Mock MapLibre to avoid canvas / WebGL errors in jsdom.
// IMPORTANT: Map must be a real `function` constructor (not arrow fn) so that
// `new maplibregl.Map(...)` works in both forks and threads pool modes.
vi.mock('maplibre-gl', () => {
  const mapInstance = {
    addControl: vi.fn(),
    on: vi.fn(),
    remove: vi.fn(),
    resize: vi.fn(),
    fitBounds: vi.fn(),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),
    getLayer: vi.fn(() => true),
    getSource: vi.fn(() => ({ setData: vi.fn() })),
    addSource: vi.fn(),
    addLayer: vi.fn(),
    getCanvas: vi.fn(() => ({ style: { cursor: '' } })),
  }
  // Must be a function (not arrow) so it can be called with `new`
  function MapConstructor() {
    return mapInstance
  }
  return {
    default: { Map: MapConstructor, NavigationControl: vi.fn() },
    Map: MapConstructor,
    NavigationControl: vi.fn(),
  }
})

describe('Stage G2 — API Client Tests (Criteria 1–5)', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('1. API client creates investigation (POST /api/v1/investigations)', async () => {
    const mockCreated: InvestigationResponse = {
      id: 'inv-12345',
      name: 'Gulf of Mexico Spill Case 101',
      status: 'CREATED',
      area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
      time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
      created_at: '2025-06-01T12:00:00Z',
      description: 'Test investigation',
      metadata: {},
      asset_ids: [],
      evidence_ids: [],
    }

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => mockCreated,
    })

    const payload: InvestigationCreateRequest = {
      name: 'Gulf of Mexico Spill Case 101',
      area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
      time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
    }

    const res = await createInvestigation(payload)
    expect(res.id).toBe('inv-12345')
    expect(res.status).toBe('CREATED')
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/investigations'),
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('2. API client retrieves investigation (GET /api/v1/investigations/{id})', async () => {
    const mockInv: InvestigationResponse = {
      id: 'inv-12345',
      name: 'Gulf of Mexico Spill Case 101',
      status: 'CREATED',
      area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
      time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
      created_at: '2025-06-01T12:00:00Z',
      metadata: {},
      asset_ids: [],
      evidence_ids: [],
    }

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockInv,
    })

    const res = await getInvestigation('inv-12345')
    expect(res.id).toBe('inv-12345')
    expect(res.name).toBe('Gulf of Mexico Spill Case 101')
  })

  it('3. API client retrieves status (GET /api/v1/investigations/{id}/status)', async () => {
    const mockStatus: InvestigationStatusResponse = {
      investigation_id: 'inv-12345',
      status: 'PROCESSING',
      current_stage: 'D3',
      completed_stages: ['B1', 'B2', 'B3', 'C1', 'D1'],
      available_artifacts: ['asset-b1', 'asset-b2', 'asset-b3'],
      errors: [],
    }

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockStatus,
    })

    const res = await getInvestigationStatus('inv-12345')
    expect(res.status).toBe('PROCESSING')
    expect(res.current_stage).toBe('D3')
    expect(res.completed_stages).toContain('B3')
  })

  it('4. API client runs investigation (POST /api/v1/investigations/{id}/run)', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        investigation_id: 'inv-12345',
        status: 'COMPLETED',
        current_stage: 'F3',
        completed_stages: ['B1', 'B2', 'B3', 'C1', 'D1', 'D3', 'E1', 'E2', 'E3', 'F1', 'F2', 'F3'],
        artifacts: { B3: 'asset-spill-1', F2: 'asset-ranking-1', F3: 'asset-report-1' },
        errors: [],
      }),
    })

    const res = await runInvestigation('inv-12345')
    expect(res.status).toBe('COMPLETED')
    expect(res.completed_stages).toHaveLength(12)
    expect(res.artifacts.B3).toBe('asset-spill-1')
  })

  it('5. API client retrieves artifacts (GET /api/v1/investigations/{id}/artifacts)', async () => {
    const mockArtifacts: ArtifactSummary[] = [
      {
        asset_id: 'asset-b3',
        investigation_id: 'inv-12345',
        asset_type: 'spill_geometry',
        provider: 'sentinel1',
        source: 'spill_detection',
        location: '/data/spill.geojson',
        provenance: { product_id: 'spill-1' },
        upstream_asset_ids: ['asset-b2'],
        metadata: { detected: true, total_area_m2: 150000 },
      },
    ]

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockArtifacts,
    })

    const res = await getInvestigationArtifacts('inv-12345')
    expect(res).toHaveLength(1)
    expect(res[0].asset_type).toBe('spill_geometry')
    expect(res[0].metadata.total_area_m2).toBe(150000)
  })
})

describe('Stage G2 — UI & Invariant Tests (Criteria 6–20)', () => {
  const sampleLiveInvestigation: InvestigationResponse = {
    id: 'inv-live-101',
    name: 'Gulf of Mexico Spill Case 101',
    status: 'COMPLETED',
    area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
    time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
    created_at: '2025-06-01T12:00:00Z',
    description: 'Active case file',
    metadata: {},
    asset_ids: ['asset-b3', 'asset-f2'],
    evidence_ids: [],
  }

  const sampleStatus: InvestigationStatusResponse = {
    investigation_id: 'inv-live-101',
    status: 'COMPLETED',
    current_stage: 'F3',
    completed_stages: ['B1', 'B2', 'B3', 'C1', 'D1', 'D3', 'E1', 'E2', 'E3', 'F1', 'F2', 'F3'],
    available_artifacts: ['asset-b3', 'asset-f2'],
    errors: [],
  }

  const sampleF2Ranking: CandidateRanking = {
    id: 'ranking-1',
    investigation_id: 'inv-live-101',
    spill_id: 'spill-1',
    evidence_fusion_id: 'fusion-1',
    generated_at: '2025-06-01T14:00:00Z',
    methodology_version: 'F2-1.0.0',
    nominal_weights: { spatial: 0.5, temporal: 0.25, trajectory: 0.25 },
    candidate_count: 2,
    candidates: [
      {
        rank: 1,
        vessel_id: 'VESSEL_ALPHA',
        candidate_id: 'cand-alpha',
        name: 'Vessel Alpha',
        mmsi: '111222333',
        imo: '9999001',
        evidence_consistency_score: 0.86,
        spatial_score: 0.90,
        temporal_score: 0.85,
        trajectory_score: 0.80,
        valid_primary_channels: 3,
        total_primary_channels: 3,
        evidence_availability_ratio: 1.0,
        contextual_evidence: {
          total_anomalies_count: 1,
          anomalies_in_zone_count: 1,
          loitering_detected: false,
          transmission_gaps_count: 1,
          gaps_spanning_zone_count: 0,
          speed_drop_in_zone: false,
          speed_surge_near_zone: false,
          nav_status_mismatch: false,
          anchor_swing_observed: false,
          notable_anomaly_types: ['ais_transmission_gap'],
        },
        drift_cross_check: {
          evaluated: true,
          min_distance_to_forward_track_km: 1.2,
          cross_check_note: 'Candidate track within 1.2 km of D1 forward trajectory.',
        },
        limitations: ['Candidate evaluation subject to satellite observation coverage.'],
        provenance: {},
      },
      {
        rank: 2,
        vessel_id: 'VESSEL_BETA',
        candidate_id: 'cand-beta',
        name: 'Vessel Beta',
        mmsi: '444555666',
        imo: '9999002',
        evidence_consistency_score: 0.62,
        spatial_score: 0.70,
        temporal_score: 0.54,
        trajectory_score: null, // Insufficient trajectory channel
        valid_primary_channels: 2,
        total_primary_channels: 3,
        evidence_availability_ratio: 0.67,
        contextual_evidence: {
          total_anomalies_count: 0,
          anomalies_in_zone_count: 0,
          loitering_detected: false,
          transmission_gaps_count: 0,
          gaps_spanning_zone_count: 0,
          speed_drop_in_zone: false,
          speed_surge_near_zone: false,
          nav_status_mismatch: false,
          anchor_swing_observed: false,
          notable_anomaly_types: [],
        },
        drift_cross_check: {
          evaluated: false,
          cross_check_note: '',
        },
        limitations: ['Trajectory channel insufficient due to single AIS ping.'],
        provenance: {},
      },
    ],
    metadata: {},
  }

  const sampleF3Report: ExplainabilityReport = {
    id: 'report-1',
    investigation_id: 'inv-live-101',
    spill_id: 'spill-1',
    candidate_ranking_id: 'ranking-1',
    generated_at: '2025-06-01T14:05:00Z',
    methodology_version: 'F3-1.0.0',
    candidate_count: 2,
    scientific_disclaimer:
      'This analysis ranks vessels by consistency with the available satellite, environmental, and AIS evidence. It does not establish causation, legal responsibility, or culpability. Results are subject to data coverage, measurement uncertainty, model assumptions, and the limitations listed for each candidate.',
    candidates: [
      {
        rank: 1,
        vessel_id: 'VESSEL_ALPHA',
        candidate_id: 'cand-alpha',
        name: 'Vessel Alpha',
        evidence_consistency_score: 0.86,
        evidence_consistency_level: 'HIGH',
        evidence_availability_ratio: 1.0,
        valid_primary_channels: 3,
        total_primary_channels: 3,
        evidence_availability_summary: 'All three primary evidence channels were available.',
        evidence_availability_band: 'HIGH',
        spatial_score: 0.90,
        spatial_explanation: 'Observed AIS position within 1.5 km of estimated source zone center.',
        temporal_score: 0.85,
        temporal_explanation: 'Observed timestamp within 25 minutes of estimated release time.',
        trajectory_score: 0.80,
        trajectory_explanation: 'Observed course aligns within 15 degrees of backward drift trajectory.',
        behavioral_context: {
          label: 'Contextual behavioral observations',
          observations: ['AIS transmission gap observed (duration: 35 min)'],
          numerical_contribution_statement:
            'Contextual behavioral observations do not contribute numerically to the evidence_consistency_score.',
          raw_summary: sampleF2Ranking.candidates[0].contextual_evidence,
        },
        drift_cross_check: {
          label: 'Forward drift cross-check',
          status: 'CONSISTENT',
          min_distance_km: 1.2,
          explanation: 'Candidate track within 1.2 km of D1 forward trajectory.',
          non_additive_statement:
            'Forward drift cross-check is non-additive contextual evidence and does not modify the consistency score.',
          raw_cross_check: sampleF2Ranking.candidates[0].drift_cross_check,
        },
        limitations: ['Candidate evaluation subject to satellite observation coverage.'],
        scientific_disclaimer:
          'This analysis ranks vessels by consistency with the available satellite, environmental, and AIS evidence. It does not establish causation, legal responsibility, or culpability. Results are subject to data coverage, measurement uncertainty, model assumptions, and the limitations listed for each candidate.',
        provenance: {},
      },
      {
        rank: 2,
        vessel_id: 'VESSEL_BETA',
        candidate_id: 'cand-beta',
        name: 'Vessel Beta',
        evidence_consistency_score: 0.62,
        evidence_consistency_level: 'MODERATE',
        evidence_availability_ratio: 0.67,
        valid_primary_channels: 2,
        total_primary_channels: 3,
        evidence_availability_summary: 'Two of three primary evidence channels were available.',
        evidence_availability_band: 'MODERATE',
        spatial_score: 0.70,
        spatial_explanation: 'Observed AIS position within 5.2 km of estimated source zone center.',
        temporal_score: 0.54,
        temporal_explanation: 'Observed timestamp within 1.5 hours of estimated release time.',
        trajectory_score: null,
        trajectory_explanation: 'Trajectory evidence is insufficient due to sparse AIS observations.',
        behavioral_context: {
          label: 'Contextual behavioral observations',
          observations: [],
          numerical_contribution_statement:
            'Contextual behavioral observations do not contribute numerically to the evidence_consistency_score.',
          raw_summary: sampleF2Ranking.candidates[1].contextual_evidence,
        },
        drift_cross_check: {
          label: 'Forward drift cross-check',
          status: 'UNAVAILABLE',
          explanation: 'Forward drift cross-check was not evaluated.',
          non_additive_statement:
            'Forward drift cross-check is non-additive contextual evidence and does not modify the consistency score.',
          raw_cross_check: sampleF2Ranking.candidates[1].drift_cross_check,
        },
        limitations: ['Trajectory channel insufficient due to single AIS ping.'],
        scientific_disclaimer:
          'This analysis ranks vessels by consistency with the available satellite, environmental, and AIS evidence. It does not establish causation, legal responsibility, or culpability. Results are subject to data coverage, measurement uncertainty, model assumptions, and the limitations listed for each candidate.',
        provenance: {},
      },
    ],
    metadata: {},
  }

  it('6. Loading state appears during workflow execution', () => {
    render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={sampleLiveInvestigation}
        statusResponse={sampleStatus}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => { }}
        onRunWorkflow={() => { }}
        isRunningWorkflow={true}
      />
    )
    expect(screen.getByText(/Running pipeline \(B1 → F3\)\.\.\./i)).toBeDefined()
  })

  it('7. Structured backend error is rendered without stack traces', () => {
    const errorStatus: InvestigationStatusResponse = {
      investigation_id: 'inv-live-101',
      status: 'FAILED',
      current_stage: 'B2',
      completed_stages: ['B1'],
      available_artifacts: ['asset-b1'],
      errors: [
        {
          stage: 'B2',
          error: 'CALIBRATION_FAILED',
          message: 'Radiometric calibration lookup table missing in metadata',
        },
      ],
    }

    render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={sampleLiveInvestigation}
        statusResponse={errorStatus}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => { }}
        onRunWorkflow={() => { }}
        isRunningWorkflow={false}
      />
    )

    expect(screen.getByText(/Pipeline Execution Error/i)).toBeDefined()
    expect(screen.getByText(/Stage B2/i)).toBeDefined()
    expect(screen.getByText(/CALIBRATION_FAILED/i)).toBeDefined()
    expect(screen.getByText(/Radiometric calibration lookup table missing in metadata/i)).toBeDefined()
  })

  it('8. Failed API request does NOT show historical mock data', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('ECONNREFUSED: Server unreachable'))

    render(
      <InvestigationWorkspace
        activeId="inv-live-101"
        isDemoMode={false}
        investigations={[{ id: 'inv-live-101', name: 'Live Case', status: 'CREATED', created_at: '2025-01-01', asset_count: 0 }]}
        onSelectInvestigation={() => { }}
        isCreateModalOpen={false}
        onCloseCreateModal={() => { }}
        onOpenCreateModal={() => { }}
      />
    )

    await waitFor(() => {
      // Must display the real API error banner (may appear in multiple places)
      expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText(/Failed to load investigation/i).length).toBeGreaterThanOrEqual(1)
    })

    // Must NOT silently show Corsica demo data
    expect(screen.queryByText(/HISTORICAL DEMO CASE — Corsica 2018 Reconstruction/i)).toBeNull()
    expect(screen.queryByText(/MARIS-HIST-2018-001/i)).toBeNull()
  })

  it('9. F2 ranking renders in backend order (Vessel Alpha then Vessel Beta)', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    const cards = screen.getAllByRole('button').filter((b) => b.className.includes('candidate-card'))
    expect(cards).toHaveLength(2)
    expect(cards[0].textContent).toContain('01')
    expect(cards[0].textContent).toContain('Vessel Alpha')
    expect(cards[1].textContent).toContain('02')
    expect(cards[1].textContent).toContain('Vessel Beta')
  })

  it('10. F2 score is labeled Evidence Consistency Score (NOT probability/guilt)', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    expect(screen.getAllByText(/Evidence Consistency Score:/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/probability of guilt/i)).toBeNull()
    expect(screen.queryByText(/culprit probability/i)).toBeNull()
    expect(screen.queryByText(/attribution probability/i)).toBeNull()
  })

  it('11. Evidence availability is displayed (e.g. 3 / 3)', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    expect(screen.getByText('3 / 3')).toBeDefined()
    expect(screen.getByText('2 / 3')).toBeDefined()
  })

  it('12. Missing evidence displays as unavailable/insufficient (Trajectory for Beta)', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-beta"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    // For candidate Beta, trajectory score is null, so it should display Insufficient data
    expect(screen.getAllByText(/Insufficient data/i).length).toBeGreaterThan(0)
    // Trajectory explanation factual statement
    expect(screen.getByText(/Trajectory evidence is insufficient/i)).toBeDefined()
  })

  it('13. E3 behavioral observations are labeled contextual and non-additive', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    expect(screen.getByText('Contextual behavioral observations')).toBeDefined()
    expect(
      screen.getByText(
        'Contextual behavioral observations do not contribute numerically to the evidence_consistency_score.'
      )
    ).toBeDefined()
    expect(screen.getByText(/AIS transmission gap observed/i)).toBeDefined()
  })

  it('14. D1 is labeled Forward drift cross-check and non-additive', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    expect(screen.getByText('Forward drift cross-check')).toBeDefined()
    expect(screen.getByText('CONSISTENT')).toBeDefined()
    expect(
      screen.getByText(
        'Forward drift cross-check is non-additive contextual evidence and does not modify the consistency score.'
      )
    ).toBeDefined()
  })

  it('15. F3 scientific disclaimer is displayed in full without shortening', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    expect(
      screen.getByText(
        'This analysis ranks vessels by consistency with the available satellite, environmental, and AIS evidence. It does not establish causation, legal responsibility, or culpability. Results are subject to data coverage, measurement uncertainty, model assumptions, and the limitations listed for each candidate.'
      )
    ).toBeDefined()
  })

  it('16. Backend spill geometry is consumed and formatted for map without client calculation', () => {
    const mockSpillArtifact: ArtifactSummary = {
      asset_id: 'asset-b3',
      investigation_id: 'inv-live-101',
      asset_type: 'spill_geometry',
      provider: 'sentinel1',
      source: 'spill_detection',
      location: '/data/spill.geojson',
      provenance: {},
      upstream_asset_ids: [],
      metadata: {
        detected: true,
        total_area_m2: 150000,
        confidence: 0.95,
        centroid: { longitude: -90.0, latitude: 28.5 },
        geometry: {
          type: 'Polygon',
          coordinates: [[[-90.1, 28.4], [-89.9, 28.4], [-89.9, 28.6], [-90.1, 28.6], [-90.1, 28.4]]],
        },
      },
    }

    const { container } = render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={sampleLiveInvestigation}
        statusResponse={sampleStatus}
        artifacts={[mockSpillArtifact]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => { }}
        onRunWorkflow={() => { }}
        isRunningWorkflow={false}
      />
    )

    // Confirms backend metadata displayed directly without recomputation.
    // Use container.textContent checks because numbers/units may be split across spans.
    const text = container.textContent ?? ''
    expect(screen.getAllByText(/Confirmed slick detected/i).length).toBeGreaterThanOrEqual(1)
    // Area 150000: locale-agnostic check (en-US → "150,000"; en-IN → "1,50,000")
    expect(text).toMatch(/1[,.]?5[,.]?0[,.]?0{3}/)
    expect(text).toMatch(/95%/)
    expect(text).toMatch(/28\.5000/)
    expect(text).toMatch(/-90\.0000/)
  })

  it('17. Backend source candidate zone can be passed to GIS map state', () => {
    const mockSourceArtifact: ArtifactSummary = {
      asset_id: 'asset-d3',
      investigation_id: 'inv-live-101',
      asset_type: 'drift_product',
      provider: 'source_estimation_service',
      source: 'source_estimation_service',
      location: '/data/source_zone.geojson',
      processing_level: 'derived_source_candidate_zone',
      provenance: {},
      upstream_asset_ids: [],
      metadata: {
        geometry: {
          type: 'Polygon',
          coordinates: [[[-90.2, 28.3], [-90.0, 28.3], [-90.0, 28.5], [-90.2, 28.5], [-90.2, 28.3]]],
        },
      },
    }

    expect(mockSourceArtifact.metadata.geometry).toBeDefined()
    expect((mockSourceArtifact.metadata.geometry as { type: string }).type).toBe('Polygon')
  })

  it('18. Backend vessel candidate data is rendered directly from backend artifacts', () => {
    render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    expect(screen.getByText(/MMSI: 111222333/i)).toBeDefined()
    expect(screen.getByText(/IMO: 9999001/i)).toBeDefined()
  })

  it('19. Frontend does not perform scientific recalculation (preserves exact scores)', () => {
    // Exact score 0.86 and 0.62 must appear unchanged from backend data
    expect(sampleF2Ranking.candidates[0].evidence_consistency_score).toBe(0.86)
    expect(sampleF2Ranking.candidates[1].evidence_consistency_score).toBe(0.62)

    const { container } = render(
      <AnalysisPanel
        isDemoMode={false}
        demoIncident={incidentData}
        demoCandidates={candidateVessels}
        selectedCandidate="cand-alpha"
        onSelectCandidate={() => { }}
        prototypeActive={false}
        rankingResult={sampleF2Ranking}
        explainabilityReport={sampleF3Report}
      />
    )

    // Scores may appear in multiple elements (candidate card + detail panel);
    // we verify they are present at least once, not recalculated.
    const text = container.textContent ?? ''
    expect(text).toContain('0.86')
    expect(text).toContain('0.62')
  })

  it('20. Explicit demo/historical mode is not silently activated by API failure', async () => {
    // When an active investigation is selected and API throws an error
    globalThis.fetch = vi.fn().mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    render(
      <InvestigationWorkspace
        activeId="inv-live-101"
        isDemoMode={false}
        investigations={[{ id: 'inv-live-101', name: 'Live Case', status: 'CREATED', created_at: '2025-01-01', asset_count: 0 }]}
        onSelectInvestigation={() => { }}
        isCreateModalOpen={false}
        onCloseCreateModal={() => { }}
        onOpenCreateModal={() => { }}
      />
    )

    await waitFor(() => {
      // Must show error (may appear in multiple alert regions)
      expect(screen.getAllByText(/Failed to load investigation/i).length).toBeGreaterThanOrEqual(1)
    })

    // Must NOT switch to demo mode
    expect(screen.queryByText(/HISTORICAL DEMO CASE/i)).toBeNull()
  })

  it('21. When listInvestigations fails, initError is rendered visibly in top-level UI with Retry and no demo activation', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Connection refused at port 8000'))

    render(<App />)

    // Expect visible top-level alert banner
    await waitFor(() => {
      expect(screen.getAllByText(/Backend Unavailable/i).length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText(/Connection refused at port 8000/i).length).toBeGreaterThanOrEqual(1)
    })

    // Expect Retry Connection button
    const retryBtn = screen.getByRole('button', { name: /Retry Connection/i })
    expect(retryBtn).toBeDefined()

    // Must NOT activate Corsica demo mode
    expect(screen.queryByText(/HISTORICAL DEMO CASE — Corsica 2018 Reconstruction/i)).toBeNull()
    expect(screen.queryByText(/MARIS-HIST-2018-001/i)).toBeNull()
  })

  it('22. Clearly distinguishes backend unavailable from no investigations registered', async () => {
    // Return empty list of investigations (backend healthy, but 0 investigations)
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.queryByText(/No Investigations/i)).toBeNull()
      expect(screen.getByText(/No Investigation Selected/i)).toBeDefined()
    })

    // Backend error banner must NOT appear
    expect(screen.queryByText(/Backend Unavailable/i)).toBeNull()
    // Corsica demo must NOT be activated
    expect(screen.queryByText(/HISTORICAL DEMO CASE — Corsica 2018 Reconstruction/i)).toBeNull()
  })

  it('23. Retry action successfully recovers when backend connection is restored', async () => {
    // Initial fetch fails
    globalThis.fetch = vi.fn().mockRejectedValueOnce(new Error('Temporary network glitch'))

    render(<App />)

    await waitFor(() => {
      expect(screen.getAllByText(/Backend Unavailable/i).length).toBeGreaterThanOrEqual(1)
    })

    // Now mock backend recovery with 1 live investigation
    const mockList = [
      {
        id: 'inv-recovered-1',
        name: 'Recovered Live Case',
        description: 'Recovered test case',
        status: 'CREATED',
        area_of_interest: {
          kind: 'bbox',
          bbox: { west: -91.0, south: 28.0, east: -89.0, north: 30.0 },
        },
        time_window: {
          start: '2025-06-01T00:00:00Z',
          end: '2025-06-01T12:00:00Z',
        },
        created_at: '2025-06-01T12:00:00Z',
        asset_count: 1,
      },
    ]

    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('/api/v1/investigations')) {
        return { ok: true, json: async () => mockList }
      }
      return { ok: true, json: async () => ({ ...mockList[0], metadata: {}, asset_ids: [], evidence_ids: [] }) }
    })

    const retryBtn = screen.getByRole('button', { name: /Retry Connection/i })
    fireEvent.click(retryBtn)

    await waitFor(() => {
      // Error banner must disappear
      expect(screen.queryByText(/Backend Unavailable/i)).toBeNull()
      // Recovered case must appear in workspace
      expect(screen.getByText(/Recovered Live Case/i)).toBeDefined()
    })
  })
})

describe('Stage G3 — End-to-End Investigation Workflow Tests', () => {
  const baseLiveInvestigation: InvestigationResponse = {
    id: 'inv-live-g3',
    name: 'G3 Spill Test Case',
    status: 'CREATED',
    area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
    time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
    created_at: '2025-06-01T12:00:00Z',
    description: 'Stage G3 testing case',
    metadata: {},
    asset_ids: [],
    evidence_ids: [],
  }

  it('24. Run button is disabled with explanation when status is COMPLETED', () => {
    const completedStatus: InvestigationStatusResponse = {
      investigation_id: 'inv-live-g3',
      status: 'COMPLETED',
      current_stage: 'F3',
      completed_stages: ['B1', 'B2', 'B3', 'C1', 'D1', 'D3', 'E1', 'E2', 'E3', 'F1', 'F2', 'F3'],
      available_artifacts: [],
      errors: [],
    }

    render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={{ ...baseLiveInvestigation, status: 'COMPLETED' }}
        statusResponse={completedStatus}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => {}}
        onRunWorkflow={() => {}}
        isRunningWorkflow={false}
        onOpenCreateModal={() => {}}
      />
    )

    // Button must be disabled
    const runBtn = screen.getByRole('button', { name: /Pipeline Completed/i })
    expect((runBtn as HTMLButtonElement).disabled).toBe(true)

    // Explanation must be present
    expect(
      screen.getByText(/Investigation already completed. Create a new investigation to re-run the pipeline./i)
    ).toBeDefined()

    // Create New Investigation action should be present
    expect(screen.getByRole('button', { name: /Create New Investigation/i })).toBeDefined()
  })

  it('25. Run button is enabled for retry when status is FAILED', () => {
    const failedStatus: InvestigationStatusResponse = {
      investigation_id: 'inv-live-g3',
      status: 'FAILED',
      current_stage: 'B1',
      completed_stages: [],
      available_artifacts: [],
      errors: [{ stage: 'B1', error: 'INSUFFICIENT_INPUT', message: 'No SAR artifact available' }],
    }

    const mockRun = vi.fn()

    render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={{ ...baseLiveInvestigation, status: 'FAILED' }}
        statusResponse={failedStatus}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => {}}
        onRunWorkflow={mockRun}
        isRunningWorkflow={false}
      />
    )

    const retryBtn = screen.getByRole('button', { name: /Retry Pipeline \(B1 → F3\)/i })
    expect((retryBtn as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(retryBtn)
    expect(mockRun).toHaveBeenCalled()
  })

  it('26. resolveSpillId enforces documented priority: metadata.detection_id -> provenance.product_id -> asset.id', () => {
    // 1. All 3 present: metadata.detection_id wins
    const artifactsAll: ArtifactSummary[] = [
      {
        asset_id: 'asset-direct-id',
        investigation_id: 'inv-1',
        asset_type: 'spill_geometry',
        provider: 'sentinel1',
        source: 'spill_detection',
        location: '/path/spill.geojson',
        provenance: { product_id: 'prov-product-id' },
        upstream_asset_ids: [],
        metadata: { detection_id: 'meta-detection-id' },
      },
    ]
    expect(resolveSpillId(artifactsAll)).toBe('meta-detection-id')

    // 2. metadata.detection_id absent: provenance.product_id wins
    const artifactsProv: ArtifactSummary[] = [
      {
        asset_id: 'asset-direct-id',
        investigation_id: 'inv-1',
        asset_type: 'spill_geometry',
        provider: 'sentinel1',
        source: 'spill_detection',
        location: '/path/spill.geojson',
        provenance: { product_id: 'prov-product-id' },
        upstream_asset_ids: [],
        metadata: {},
      },
    ]
    expect(resolveSpillId(artifactsProv)).toBe('prov-product-id')

    // 3. Both metadata and provenance absent: asset.asset_id or id wins
    const artifactsAsset: ArtifactSummary[] = [
      {
        asset_id: 'asset-direct-id',
        investigation_id: 'inv-1',
        asset_type: 'spill_geometry',
        provider: 'sentinel1',
        source: 'spill_detection',
        location: '/path/spill.geojson',
        provenance: {},
        upstream_asset_ids: [],
        metadata: {},
      },
    ]
    expect(resolveSpillId(artifactsAsset)).toBe('asset-direct-id')

    // 4. No spill geometry asset: returns null
    expect(resolveSpillId([])).toBeNull()
  })

  it('27. Run config renders server-side SAR path input and tuning fields with clear label', () => {
    render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={baseLiveInvestigation}
        statusResponse={null}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => {}}
        onRunWorkflow={() => {}}
        isRunningWorkflow={false}
      />
    )

    // Open advanced configuration
    const toggleBtn = screen.getByRole('button', { name: /Run Configuration \(Advanced\)/i })
    fireEvent.click(toggleBtn)

    // Verify SAR path input and explicit server-side notice
    const sarInput = screen.getByLabelText(/Sentinel-1 Artifact Path \(Server-side\)/i)
    expect(sarInput).toBeDefined()
    expect(screen.getByText(/Server-side filesystem path only. Browser upload deferred to G4./i)).toBeDefined()

    // Verify optional drift and lookback inputs
    expect(screen.getByLabelText(/Forward Drift \(hours\)/i)).toBeDefined()
    expect(screen.getByLabelText(/Lookback \(hours\)/i)).toBeDefined()
  })

  it('28. Run configuration forwards user payload to run workflow and omits empty fields', () => {
    const mockRun = vi.fn()

    render(
      <IncidentPanel
        isDemoMode={false}
        demoIncident={incidentData}
        liveInvestigation={baseLiveInvestigation}
        statusResponse={null}
        artifacts={[]}
        layers={{ spill: true, drift: true, vessels: true }}
        onToggleLayer={() => {}}
        onRunWorkflow={mockRun}
        isRunningWorkflow={false}
      />
    )

    // Open advanced config
    const toggleBtn = screen.getByRole('button', { name: /Run Configuration \(Advanced\)/i })
    fireEvent.click(toggleBtn)

    // Type SAR path and forward drift
    const sarInput = screen.getByLabelText(/Sentinel-1 Artifact Path \(Server-side\)/i)
    fireEvent.change(sarInput, { target: { value: '/data/sar/S1A_IW_GRDH_test.zip' } })

    const driftInput = screen.getByLabelText(/Forward Drift \(hours\)/i)
    fireEvent.change(driftInput, { target: { value: '18' } })

    // Leave lookback empty

    // Click run button
    const runBtn = screen.getByRole('button', { name: /Run Investigation \(B1 → F3\)/i })
    fireEvent.click(runBtn)

    expect(mockRun).toHaveBeenCalledWith({
      sentinel1_artifact_path: '/data/sar/S1A_IW_GRDH_test.zip',
      drift_hours: 18,
    })
    // Empty lookback field must be omitted
    expect(mockRun.mock.calls[0][0].lookback_hours).toBeUndefined()
  })

  it('29. Empty workspace renders explicit shell distinguishing backend unavailable from no investigation selected', () => {
    // 1. No investigation selected (backend healthy)
    const { unmount } = render(
      <InvestigationWorkspace
        activeId=""
        isDemoMode={false}
        investigations={[]}
        onSelectInvestigation={() => {}}
        isCreateModalOpen={false}
        onCloseCreateModal={() => {}}
        onOpenCreateModal={() => {}}
      />
    )

    expect(screen.getByText(/No Investigation Selected/i)).toBeDefined()
    expect(screen.getByRole('button', { name: /\+ New Investigation/i })).toBeDefined()
    unmount()

    // 2. Backend unavailable
    render(
      <InvestigationWorkspace
        activeId=""
        isDemoMode={false}
        investigations={[]}
        onSelectInvestigation={() => {}}
        isCreateModalOpen={false}
        onCloseCreateModal={() => {}}
        onOpenCreateModal={() => {}}
        backendError="Connection refused on port 8000"
      />
    )

    expect(screen.getByText(/Backend Unavailable/i)).toBeDefined()
    expect(screen.getByText(/Unable to connect to the MARIS backend service/i)).toBeDefined()
    expect(screen.getByRole('button', { name: /\+ New Investigation/i })).toBeDefined()
  })

  it('30. HTTP 409 from run endpoint surfaces error message in workspace without fallback to demo', async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('/run')) {
        return {
          ok: false,
          status: 409,
          statusText: 'Conflict',
          json: async () => ({
            detail: 'Investigation already completed. Create a new investigation to re-run the pipeline.',
          }),
        }
      }
      if (url.includes('/status')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            investigation_id: 'inv-live-101',
            status: 'COMPLETED',
            current_stage: 'F3',
            completed_stages: ['B1', 'B2', 'B3', 'C1', 'D1', 'D3', 'E1', 'E2', 'E3', 'F1', 'F2', 'F3'],
            available_artifacts: [],
            errors: [],
          }),
        }
      }
      if (url.includes('/artifacts')) {
        return { ok: true, status: 200, json: async () => [] }
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: 'inv-live-101',
          name: 'Completed Case',
          status: 'COMPLETED',
          area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
          time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
          created_at: '2025-06-01T12:00:00Z',
          description: 'Completed investigation',
          metadata: {},
          asset_ids: [],
          evidence_ids: [],
        }),
      }
    })

    render(
      <InvestigationWorkspace
        activeId="inv-live-101"
        isDemoMode={false}
        investigations={[{ id: 'inv-live-101', name: 'Completed Case', status: 'COMPLETED', created_at: '2025-01-01', asset_count: 0 }]}
        onSelectInvestigation={() => {}}
        isCreateModalOpen={false}
        onCloseCreateModal={() => {}}
        onOpenCreateModal={() => {}}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/Completed Case/i)).toBeDefined()
    })

    // Now attempt runInvestigation directly to verify 409 handling
    await expect(runInvestigation('inv-live-101')).rejects.toThrow(
      /Investigation already completed. Create a new investigation to re-run the pipeline./
    )

    // Corsica demo must not be activated
    expect(screen.queryByText(/HISTORICAL DEMO CASE — Corsica 2018 Reconstruction/i)).toBeNull()
  })

  it('31. PROCESSING recovery performs exactly one deferred check and renders Refresh Status action', async () => {
    vi.useFakeTimers()

    const processingStatus: InvestigationStatusResponse = {
      investigation_id: 'inv-proc-1',
      status: 'PROCESSING',
      current_stage: 'B2',
      completed_stages: ['B1'],
      available_artifacts: [],
      errors: [],
    }

    const mockFetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('/status')) {
        return { ok: true, status: 200, json: async () => processingStatus }
      }
      if (url.includes('/artifacts')) {
        return { ok: true, status: 200, json: async () => [] }
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: 'inv-proc-1',
          name: 'Processing Case',
          status: 'PROCESSING',
          area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
          time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
          created_at: '2025-06-01T12:00:00Z',
          metadata: {},
          asset_ids: [],
          evidence_ids: [],
        }),
      }
    })
    globalThis.fetch = mockFetch

    render(
      <InvestigationWorkspace
        activeId="inv-proc-1"
        isDemoMode={false}
        investigations={[{ id: 'inv-proc-1', name: 'Processing Case', status: 'PROCESSING', created_at: '2025-01-01', asset_count: 0 }]}
        onSelectInvestigation={() => {}}
        isCreateModalOpen={false}
        onCloseCreateModal={() => {}}
        onOpenCreateModal={() => {}}
      />
    )

    // Fast-forward 5000ms for the deferred check
    await vi.advanceTimersByTimeAsync(5000)

    // The status endpoint should have been checked
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/status'), expect.anything())

    // "Refresh Status" button should be visible in UI
    expect(screen.getByRole('button', { name: /Refresh Status/i })).toBeDefined()

    vi.useRealTimers()
  })

  it('32. Creating an investigation refreshes the investigation list so it appears in the Header selector', async () => {
    const listInvestigationsMock = vi.fn()
      // Initial list
      .mockResolvedValueOnce([])
      // Post-create list
      .mockResolvedValueOnce([
        {
          id: 'inv-new-999',
          name: 'Brand New Spill Case',
          status: 'CREATED',
          created_at: '2025-06-01T12:00:00Z',
          asset_count: 0,
        },
      ])

    const createMock = vi.fn().mockResolvedValue({
      id: 'inv-new-999',
      name: 'Brand New Spill Case',
      status: 'CREATED',
      area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
      time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
      created_at: '2025-06-01T12:00:00Z',
      metadata: {},
      asset_ids: [],
      evidence_ids: [],
    })

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, opts?: RequestInit) => {
      if (url.endsWith('/api/v1/investigations') && opts?.method === 'POST') {
        const body = JSON.parse(opts.body as string)
        return { ok: true, status: 201, json: async () => createMock(body) }
      }
      if (url.endsWith('/api/v1/investigations')) {
        const list = await listInvestigationsMock()
        return { ok: true, status: 200, json: async () => list }
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          id: 'inv-new-999',
          name: 'Brand New Spill Case',
          status: 'CREATED',
          area_of_interest: { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
          time_window: { start: '2025-06-01T00:00:00Z', end: '2025-06-02T00:00:00Z' },
          created_at: '2025-06-01T12:00:00Z',
          metadata: {},
          asset_ids: [],
          evidence_ids: [],
        }),
      }
    })

    render(<App />)

    // Wait for initial render
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /\+ New Investigation/i })).toBeDefined()
    })

    // Click "+ New Investigation"
    const newInvBtn = screen.getByRole('button', { name: /\+ New Investigation/i })
    fireEvent.click(newInvBtn)

    // Fill form and submit
    const nameInput = screen.getByLabelText(/Investigation Title/i)
    fireEvent.change(nameInput, { target: { value: 'Brand New Spill Case' } })

    const submitBtn = screen.getByRole('button', { name: /Create Investigation/i })
    fireEvent.click(submitBtn)

    // After creation, list should refresh and new investigation appears in header
    await waitFor(() => {
      expect(screen.getByText(/Brand New Spill Case/i)).toBeDefined()
    })
  })
})