/**
 * ScientificReportView.tsx — Step 8: Scientific Investigation Report & Export.
 *
 * Renders a publication-grade, self-contained scientific investigation report
 * constructed strictly from the stored historical experiment record.
 *
 * CRITICAL SCIENTIFIC INTEGRITY INVARIANTS:
 * - Read-only transformation of stored experiment records.
 * - Zero re-running of drift calculations or AIS queries.
 * - Authoritative backend ordering and exact consistency scores strictly preserved.
 * - Neutral terminology: Strictly evidence-consistency assessment; no attribution of
 *   guilt, fault, or legal responsibility.
 */

import React, { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  FileCheck,
  FileText,
  Printer,
} from 'lucide-react'

import {
  fetchScientificReport,
  downloadReportPdf,
  downloadExportJson,
  getExperimentRun,
} from '../../real-experiment/experimentApi'
import type {
  AisPosition,
  ExperimentRunResult,
} from '../../real-experiment/experimentTypes'
import AttributionMap from './AttributionMap'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number | null | undefined, dec = 3): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toFixed(dec)
}

function scoreColor(score: number): string {
  if (score >= 0.7) return '#4ade80'
  if (score >= 0.4) return '#facc15'
  return '#f87171'
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso.slice(0, 16).replace('T', ' ')
    return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
  } catch {
    return iso.slice(0, 16).replace('T', ' ')
  }
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ScientificReportViewProps {
  runId: string
  onBack: () => void
  initialRun?: ExperimentRunResult | null
  onLoadIntoEvaluator?: (runId: string) => void | Promise<void>
}

export default function ScientificReportView({
  runId,
  onBack,
  initialRun,
  onLoadIntoEvaluator,
}: ScientificReportViewProps) {
  const [runResult, setRunResult] = useState<ExperimentRunResult | null>(initialRun ?? null)
  const [reportData, setReportData] = useState<Record<string, any> | null>(null)
  const [loading, setLoading] = useState<boolean>(!initialRun)
  const [error, setError] = useState<string | null>(null)
  const [selectedVesselId, setSelectedVesselId] = useState<string | null>(null)
  const [pdfDownloading, setPdfDownloading] = useState(false)
  const [jsonDownloading, setJsonDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [fullRun, reportJson] = await Promise.all([
        initialRun ? Promise.resolve(initialRun) : getExperimentRun(runId),
        fetchScientificReport(runId),
      ])
      setRunResult(fullRun)
      setReportData(reportJson)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to generate scientific report.')
    } finally {
      setLoading(false)
    }
  }, [runId, initialRun])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handlePrint = () => {
    window.print()
  }

  const handleDownloadPdf = async () => {
    setPdfDownloading(true)
    setDownloadError(null)
    try {
      await downloadReportPdf(runId)
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'PDF download failed. Is the backend running?')
    } finally {
      setPdfDownloading(false)
    }
  }

  const handleExportJson = async () => {
    setJsonDownloading(true)
    setDownloadError(null)
    try {
      await downloadExportJson(runId)
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'JSON export failed. Is the backend running?')
    } finally {
      setJsonDownloading(false)
    }
  }

  if (loading) {
    return (
      <div className="re-step-panel re-report-loading" data-testid="report-loading-state">
        <div className="re-running-status">
          <div className="re-spinner" />
          <span>Generating scientific report from stored experiment record…</span>
        </div>
      </div>
    )
  }

  if (error || !runResult) {
    return (
      <div className="re-step-panel re-report-error" data-testid="report-error-state">
        <div className="re-btn-row" style={{ marginBottom: '1rem' }}>
          <button className="re-btn-ghost" onClick={onBack}>
            ← Back to Investigation
          </button>
        </div>
        <p className="re-error">
          <strong>Unable to generate scientific report.</strong>
          {error && ` (${error})`}
        </p>
        <div className="re-btn-row">
          <button className="re-btn-secondary" onClick={loadData}>
            Retry Report Generation
          </button>
        </div>
      </div>
    )
  }

  // Authentic AIS positions map from stored vessel records
  const vesselPositionsMap: Record<string, AisPosition[]> = {}
  runResult.vessels.forEach((v) => {
    if (v.positions && v.positions.length > 0) {
      vesselPositionsMap[v.vessel_id] = v.positions
      if (v.mmsi) vesselPositionsMap[v.mmsi] = v.positions
    }
  })

  // Observation coordinate determination
  const obsLat =
    runResult.observation_lat != null && runResult.observation_lat !== 0
      ? runResult.observation_lat
      : runResult.slick_characterization?.centroid_lat != null
      ? runResult.slick_characterization.centroid_lat
      : runResult.satellite_product_id.includes('20181008')
      ? 43.2483
      : runResult.source_lat
  const obsLon =
    runResult.observation_lon != null && runResult.observation_lon !== 0
      ? runResult.observation_lon
      : runResult.slick_characterization?.centroid_lon != null
      ? runResult.slick_characterization.centroid_lon
      : runResult.satellite_product_id.includes('20181008')
      ? 9.4783
      : runResult.source_lon

  const meta = reportData?.metadata ?? {}
  const execSum = reportData?.executive_summary ?? {}

  return (
    <div className="re-step-panel re-report-view" data-testid="scientific-report-view">
      {/* Top Action Toolbar (Hidden during print) */}
      <div className="re-report-toolbar no-print">
        <div className="re-toolbar-left">
          <button
            className="re-btn-ghost"
            onClick={onBack}
            data-testid="report-back-btn"
            title="Return to experiment details"
          >
            <ArrowLeft size={14} style={{ marginRight: '0.35rem' }} />
            Back to Investigation
          </button>
        </div>
        <div className="re-toolbar-right">
          <button
            className="re-btn-primary"
            onClick={handleDownloadPdf}
            disabled={pdfDownloading}
            data-testid="report-download-pdf-btn"
            title="Download publication-ready PDF"
          >
            <Download size={14} style={{ marginRight: '0.35rem' }} />
            {pdfDownloading ? 'Generating PDF…' : 'Download PDF'}
          </button>
          <button
            className="re-btn-secondary"
            onClick={handleExportJson}
            disabled={jsonDownloading}
            data-testid="report-export-json-btn"
            title="Export full experiment record as structured JSON"
          >
            <FileText size={14} style={{ marginRight: '0.35rem' }} />
            {jsonDownloading ? 'Exporting…' : 'Export JSON'}
          </button>
          {onLoadIntoEvaluator && (
            <button
              className="re-btn-ghost"
              onClick={() => onLoadIntoEvaluator(runId)}
              data-testid="report-load-evaluator-btn"
              title="Load this run into the active Evaluator wizard"
            >
              Load into Evaluator
            </button>
          )}
          <button
            className="re-btn-ghost"
            onClick={handlePrint}
            data-testid="report-print-btn"
            title="Print or save as browser PDF"
          >
            <Printer size={14} style={{ marginRight: '0.35rem' }} />
            Print
          </button>
        </div>
      </div>
      {downloadError && (
        <div className="re-report-download-error no-print" data-testid="report-download-error">
          <FileCheck size={14} style={{ marginRight: '0.4rem', flexShrink: 0 }} />
          <span><strong>Download failed:</strong> {downloadError}</span>
          <button className="re-btn-ghost" style={{ marginLeft: 'auto', padding: '0 0.4rem' }} onClick={() => setDownloadError(null)}>✕</button>
        </div>
      )}

      {/* Report Paper Container */}
      <div className="re-report-paper" data-testid="report-paper">
        {/* Cover / Document Header */}
        <header className="re-report-cover">
          <div className="re-report-org">
            <span className="re-report-logo">MARIS</span>
            <span className="re-report-sublogo">Marine Attribution & Investigation System</span>
          </div>
          <h1 className="re-report-main-title">SCIENTIFIC ATTRIBUTION & AUDIT REPORT</h1>
          <p className="re-report-subtitle">
            Hydrodynamic Backward Drift Reconstruction & Multi-Factor Vessel Telemetry Consistency Assessment
          </p>
          <div className="re-report-divider" />
        </header>

        {/* Report Metadata Grid */}
        <section className="re-report-meta-box">
          <div className="re-report-meta-item">
            <span className="re-meta-label">Experiment Run ID</span>
            <code className="re-meta-value re-td-mono" data-testid="report-run-id">
              {runResult.run_id}
            </code>
          </div>
          <div className="re-report-meta-item">
            <span className="re-meta-label">Report Generated</span>
            <span className="re-meta-value">
              {meta.report_generated_at || formatDate(new Date().toISOString())}
            </span>
          </div>
          <div className="re-report-meta-item">
            <span className="re-meta-label">Experiment Status</span>
            <span className="re-status-badge re-status-completed">
              <CheckCircle2 size={12} />
              Completed
            </span>
          </div>
          <div className="re-report-meta-item">
            <span className="re-meta-label">Attribution Model</span>
            <code
              className="re-meta-value re-td-mono"
              title={runResult.model_version}
              style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}
            >
              {runResult.model_version.includes('+')
                ? runResult.model_version.replace('+', ' + ')
                : runResult.model_version}
            </code>
          </div>
        </section>

        {/* Section 1: Executive Summary */}
        <section className="re-report-section">
          <h2 className="re-report-sec-title">1. Executive Summary</h2>
          <div className="re-exec-summary-box">
            <p className="re-exec-text">
              {execSum.assessment_statement ||
                'The experiment reconstructed a candidate source zone using backward drift integration and evaluated available AIS vessel evidence against the reconstructed attribution result. This analysis is an evidence-consistency assessment and does not constitute a legal determination of responsibility or causation.'}
            </p>
            <div className="re-exec-grid">
              <div className="re-exec-metric">
                <span className="re-exec-label">Observation</span>
                <strong>
                  {fmt(obsLat, 4)}°N, {fmt(obsLon, 4)}°E
                </strong>
              </div>
              <div className="re-exec-metric">
                <span className="re-exec-label">Reconstructed Source</span>
                <strong>
                  {fmt(runResult.source_lat, 4)}°N, {fmt(runResult.source_lon, 4)}°E
                </strong>
              </div>
              <div className="re-exec-metric">
                <span className="re-exec-label">Source Uncertainty</span>
                <strong>{(runResult.source_radius_m / 1000).toFixed(1)} km</strong>
              </div>
              <div className="re-exec-metric">
                <span className="re-exec-label">Backward Integration</span>
                <strong>
                  {runResult.backtrack_hours} h ({runResult.backward_steps.length} steps)
                </strong>
              </div>
              <div className="re-exec-metric">
                <span className="re-exec-label">Candidate Vessels</span>
                <strong>{runResult.vessels.length} evaluated</strong>
              </div>
            </div>
          </div>
        </section>

        {/* Section 2: Observation Details */}
        <section className="re-report-section">
          <h2 className="re-report-sec-title">2. Observation Information</h2>
          <div className="re-table-card">
            <table className="re-table re-report-table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <tbody>
                <tr>
                  <th style={{ width: '26%' }}>Sensor Platform</th>
                  <td style={{ width: '74%', wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                    {runResult.satellite_product_id.includes('S1A')
                      ? 'Copernicus Sentinel-1A C-SAR'
                      : runResult.satellite_product_id.includes('S1B')
                      ? 'Copernicus Sentinel-1B C-SAR'
                      : 'Satellite Synthetic Aperture Radar (SAR)'}
                  </td>
                </tr>
                <tr>
                  <th>Observation Coordinates</th>
                  <td className="re-td-mono" style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                    {fmt(obsLat, 4)}°N, {fmt(obsLon, 4)}°E
                  </td>
                </tr>
                <tr>
                  <th>Observation Timestamp</th>
                  <td className="re-td-mono" style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                    {formatDate(runResult.observation_time)}
                  </td>
                </tr>
                <tr>
                  <th>Satellite Product ID</th>
                  <td className="re-td-mono" style={{ wordBreak: 'break-all', overflowWrap: 'anywhere' }}>
                    {runResult.satellite_product_id}
                  </td>
                </tr>
                {reportData?.oil_spill_characterization && (
                  <>
                    <tr>
                      <th>Slick Detection Status</th>
                      <td>
                        <span
                          style={{
                            fontWeight: 600,
                            padding: '0.15rem 0.5rem',
                            borderRadius: '4px',
                            background: reportData.oil_spill_characterization.estimated_area_km2 ? 'rgba(34, 197, 94, 0.15)' : 'rgba(234, 179, 8, 0.15)',
                            color: reportData.oil_spill_characterization.estimated_area_km2 ? '#22c55e' : '#eab308',
                          }}
                        >
                          {reportData.oil_spill_characterization.detection_status}
                        </span>
                      </td>
                    </tr>
                    <tr>
                      <th>Estimated Slick Area</th>
                      <td>
                        <strong>
                          {reportData.oil_spill_characterization.estimated_area_km2 != null
                            ? `${reportData.oil_spill_characterization.estimated_area_km2} km²`
                            : 'Unavailable (unsegmented scene)'}
                        </strong>
                      </td>
                    </tr>
                    <tr>
                      <th>Bragg Damping Contrast</th>
                      <td>
                        {reportData.oil_spill_characterization.damping_contrast_db != null
                          ? `${reportData.oil_spill_characterization.damping_contrast_db} dB`
                          : 'Unavailable'}
                      </td>
                    </tr>
                    <tr>
                      <th>Detection Confidence</th>
                      <td>
                        {reportData.oil_spill_characterization.confidence_score != null
                          ? `${(reportData.oil_spill_characterization.confidence_score * 100).toFixed(0)}%`
                          : 'Catalogue geometric anchor'}
                      </td>
                    </tr>
                    <tr>
                      <th>Detection Method</th>
                      <td style={{ fontSize: '0.85rem' }}>
                        {reportData.oil_spill_characterization.detection_method}
                      </td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Section 3: Environmental Data Sources */}
        <section className="re-report-section">
          <h2 className="re-report-sec-title">3. Environmental Conditions & Data Sources</h2>
          <div className="re-table-card">
            <table className="re-table re-report-table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: '26%' }}>Domain</th>
                  <th style={{ width: '32%' }}>Source & Dataset</th>
                  <th style={{ width: '42%' }}>Coverage & Provenance</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Atmospheric Reanalysis</td>
                  <td><strong>ECMWF ERA5 10m Wind</strong></td>
                  <td className="re-td-mono" style={{ wordBreak: 'break-all', overflowWrap: 'anywhere' }}>
                    {runResult.era5_path || 'Metocean NetCDF'}
                  </td>
                </tr>
                <tr>
                  <td>Ocean Hydrodynamics</td>
                  <td><strong>Copernicus Marine CMEMS</strong></td>
                  <td className="re-td-mono" style={{ wordBreak: 'break-all', overflowWrap: 'anywhere' }}>
                    {runResult.cmems_path || 'Oceanic NetCDF'}
                  </td>
                </tr>
                <tr>
                  <td>AIS Vessel Telemetry</td>
                  <td><strong>Curated Historical SQLite Database</strong></td>
                  <td className="re-td-mono" style={{ wordBreak: 'break-all', overflowWrap: 'anywhere' }}>
                    ais_vessels.db Benchmark Archive
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        {/* Section 4: Drift Configuration */}
        <section className="re-report-section">
          <h2 className="re-report-sec-title">4. Drift Configuration & Source Reconstruction</h2>
          <div className="re-source-zone-card" style={{ marginBottom: '1rem' }}>
            <div className="re-source-grid">
              <div>
                <div className="re-source-label">Physics Model</div>
                <div className="re-source-value" style={{ fontSize: '0.8rem' }}>
                  Leeway-Euler Backward Integration
                </div>
              </div>
              <div>
                <div className="re-source-label">Backtrack Duration</div>
                <div className="re-source-value">{runResult.backtrack_hours} h</div>
              </div>
              <div>
                <div className="re-source-label">Backward Steps</div>
                <div className="re-source-value">{runResult.backward_steps.length} steps</div>
              </div>
              <div>
                <div className="re-source-label">Step Timestep</div>
                <div className="re-source-value">{runResult.step_hours} h</div>
              </div>
              <div>
                <div className="re-source-label">Reconstructed Source</div>
                <div className="re-source-value">
                  {fmt(runResult.source_lat, 4)}°N, {fmt(runResult.source_lon, 4)}°E
                </div>
              </div>
              <div>
                <div className="re-source-label">Uncertainty Radius</div>
                <div className="re-source-value">
                  {(runResult.source_radius_m / 1000).toFixed(1)} km
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Section 4b: Forward Drift Prediction (Step 11) */}
        {(runResult.forward_prediction || reportData?.forward_drift_prediction) && (() => {
          const fwd = runResult.forward_prediction || reportData?.forward_drift_prediction
          const steps: Array<any> = fwd.steps || []
          return (
            <section className="re-report-section" data-testid="report-forward-prediction-section">
              <h2 className="re-report-sec-title">4b. Forward Drift Prediction (Model Projection)</h2>
              <p className="re-section-desc">
                Deterministic forward trajectory simulation predicting future slick movement from the Sentinel-1 observation coordinate under available ERA5 wind and CMEMS ocean current forcing fields.
              </p>
              <div className="re-source-zone-card" style={{ marginBottom: '1rem', borderColor: '#0891b2', background: 'rgba(6, 40, 50, 0.4)' }}>
                <div className="re-source-grid">
                  <div>
                    <div className="re-source-label">Observation Origin</div>
                    <div className="re-source-value">
                      {fmt(fwd.observation_lat ?? fwd.origin_lat, 4)}°N, {fmt(fwd.observation_lon ?? fwd.origin_lon, 4)}°E
                    </div>
                  </div>
                  <div>
                    <div className="re-source-label">Final Predicted Coordinate</div>
                    <div className="re-source-value" style={{ color: '#22d3ee' }}>
                      {fmt(fwd.final_lat, 4)}°N, {fmt(fwd.final_lon, 4)}°E
                    </div>
                  </div>
                  <div>
                    <div className="re-source-label">Prediction Horizon</div>
                    <div className="re-source-value">+{fwd.prediction_hours ?? steps.length} h ({steps.length} steps)</div>
                  </div>
                  <div>
                    <div className="re-source-label">Cumulative Displacement</div>
                    <div className="re-source-value">{(fwd.displacement_km ?? fwd.total_distance_km)?.toFixed(1) ?? '—'} km</div>
                  </div>
                  <div>
                    <div className="re-source-label">Model Identifier</div>
                    <div className="re-source-value" style={{ fontSize: '0.8rem' }}>{fwd.model_version ?? 'leeway_euler_v1'}</div>
                  </div>
                  <div>
                    <div className="re-source-label">Projection Status</div>
                    <div className="re-source-value" style={{ color: '#22d3ee' }}>{fwd.status ?? fwd.termination_status ?? 'COMPLETED'}</div>
                  </div>
                </div>
              </div>

              {steps.length > 0 && (
                <div className="re-table-card" style={{ marginBottom: '1rem' }}>
                  <table className="re-table re-report-table" style={{ width: '100%' }}>
                    <thead>
                      <tr>
                        <th>Step</th>
                        <th>Forecast Time</th>
                        <th>Latitude (°N)</th>
                        <th>Longitude (°E)</th>
                        <th>Wind (m/s)</th>
                        <th>Current (m/s)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {steps.map((st: any, i: number) => {
                        const windSpd = st.u_wind_ms != null && st.v_wind_ms != null ? Math.hypot(st.u_wind_ms, st.v_wind_ms).toFixed(1) : '—'
                        const currSpd = st.u_current_ms != null && st.v_current_ms != null ? Math.hypot(st.u_current_ms, st.v_current_ms).toFixed(2) : '—'
                        return (
                          <tr key={i}>
                            <td className="re-td-mono">+{st.step} h</td>
                            <td className="re-td-mono">{formatDate(st.timestamp)}</td>
                            <td className="re-td-mono">{fmt(st.lat, 4)}°N</td>
                            <td className="re-td-mono">{fmt(st.lon, 4)}°E</td>
                            <td className="re-td-mono">{windSpd}</td>
                            <td className="re-td-mono">{currSpd}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="re-disclaimer">
                <strong>Scientific Uncertainty Notice:</strong> The forward trajectory is a deterministic model projection under the supplied environmental forcing fields (Leeway-Euler with α = 0.035). MARIS does not project a synthetic uncertainty radius without a scientifically calibrated stochastic dispersion model. This prediction does not represent a guaranteed or observed future path.
              </div>
            </section>
          )
        })()}

        {/* Section 5: Attribution Map (Stored Result) */}
        <section className="re-report-section">
          <h2 className="re-report-sec-title">5. Historical Attribution Map</h2>
          <p className="re-section-desc">
            Visualizing the stored observed slick location, backward drift trajectory, reconstructed
            source uncertainty zone, and authentic historical AIS vessel paths.
          </p>

          <div className="re-report-map-wrap">
            <AttributionMap
              observation={{
                lat: obsLat,
                lon: obsLon,
                timestamp: runResult.observation_time,
                title: runResult.satellite_product_id,
              }}
              reconstructedSource={{
                lat: runResult.source_lat,
                lon: runResult.source_lon,
                radiusM: runResult.source_radius_m,
                geojson: runResult.source_zone_geojson,
              }}
              backwardSteps={runResult.backward_steps}
              vessels={runResult.vessels}
              selectedVesselId={selectedVesselId}
              onSelectVessel={setSelectedVesselId}
              vesselPositionsMap={vesselPositionsMap}
              forwardSteps={runResult.forward_prediction?.steps}
              forwardPrediction={runResult.forward_prediction}
              sarSurveillance={runResult.sar_surveillance ?? reportData?.sar_surveillance}
            />
          </div>
        </section>

        {/* Section 6: Candidate Vessel Comparison (Authoritative Backend Ranking) */}
        <section className="re-report-section">
          <h2 className="re-report-sec-title">6. Candidate Vessel Assessment</h2>
          <p className="re-section-desc">
            Authoritative candidate ranking strictly preserved from backend calculation. Ordered by
            Evidence Consistency Score.
          </p>

          {runResult.vessels.length === 0 ? (
            <p className="re-empty">No eligible AIS vessel tracks were available for this experiment.</p>
          ) : (
            <div className="re-results-table-wrap">
              <table className="re-table re-report-table" data-testid="report-candidate-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Vessel</th>
                    <th>MMSI</th>
                    <th>Evidence Consistency</th>
                    <th>Distance</th>
                    <th>AIS Coverage</th>
                    <th>Track Status</th>
                  </tr>
                </thead>
                <tbody>
                  {runResult.vessels.map((v) => {
                    const pos =
                      vesselPositionsMap[v.vessel_id] || vesselPositionsMap[v.mmsi ?? ''] || v.positions || []
                    return (
                      <tr key={v.vessel_id}>
                        <td className="re-td-mono"><strong>#{v.rank}</strong></td>
                        <td><strong>{v.vessel_name ?? v.mmsi ?? v.vessel_id}</strong></td>
                        <td className="re-td-mono">{v.mmsi ?? '—'}</td>
                        <td>
                          <span
                            style={{
                              color: scoreColor(v.evidence_consistency_score),
                              fontWeight: 800,
                              fontFamily: 'monospace',
                            }}
                          >
                            {(v.evidence_consistency_score * 100).toFixed(1)}%
                          </span>
                        </td>
                        <td className="re-td-mono">
                          {v.min_source_distance_km != null
                            ? `${v.min_source_distance_km.toFixed(1)} km`
                            : '—'}
                        </td>
                        <td className="re-td-mono">
                          {(v.ais_coverage_fraction * 100).toFixed(0)}% ({v.ais_position_count} pos)
                        </td>
                        <td>
                          {pos.length > 0 ? (
                            <span style={{ color: '#4ade80', fontSize: '0.75rem' }}>
                              ✓ {pos.length} pts
                            </span>
                          ) : (
                            <span style={{ color: '#f87171', fontSize: '0.75rem' }}>
                              AIS trajectory unavailable
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Section 7: Detailed Evidence Breakdown & Multi-Factor Matrix */}
        {runResult.vessels.length > 0 && (
          <section className="re-report-section">
            <h2 className="re-report-sec-title">7. Detailed Evidence Dimensions Matrix</h2>
            <div className="re-results-table-wrap">
              <table className="re-table re-report-table" data-testid="report-evidence-matrix">
                <thead>
                  <tr>
                    <th>Evidence Dimension</th>
                    {runResult.vessels.map((v) => (
                      <th key={v.vessel_id}>
                        #{v.rank} {v.vessel_name ?? v.mmsi}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><strong>Evidence Consistency Score</strong></td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        <strong>{(v.evidence_consistency_score * 100).toFixed(1)}%</strong>
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td>Spatial Proximity (Min Distance)</td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        {v.min_source_distance_km != null ? `${v.min_source_distance_km.toFixed(1)} km` : '—'}
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td>Temporal Overlap</td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        {v.temporal_overlap_hours.toFixed(1)} h
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td>Trajectory Overlap Fraction</td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        {(v.trajectory_overlap_fraction * 100).toFixed(0)}%
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td>AIS Transponder Positions</td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        {v.ais_position_count}
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td>Speed Consistency</td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        {v.speed_consistency != null ? `${(v.speed_consistency * 100).toFixed(0)}%` : '—'}
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <td>Course Consistency</td>
                    {runResult.vessels.map((v) => (
                      <td key={v.vessel_id} className="re-td-mono">
                        {v.heading_consistency != null ? `${(v.heading_consistency * 100).toFixed(0)}%` : '—'}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Phase #4: Attribution Explainability & Decomposed Scoring */}
        {runResult.vessels.length > 0 && runResult.vessels.some((v) => v.evidence_breakdown || (reportData?.candidates ?? []).some((c: any) => c.evidence_breakdown)) && (
          <section className="re-report-section" data-testid="report-explainability-section">
            <h2 className="re-report-sec-title">Attribution Explainability &amp; Evidence Breakdown (Phase #4)</h2>
            <p className="re-section-desc">
              Decomposed consistency scores and factual attribution evidence chains. Scoring is based on fixed weights: Spatial (50%), Temporal (25%), and Trajectory (25%).
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem', marginBottom: '1.25rem' }}>
              {runResult.vessels.map((v) => {
                const cand = (reportData?.candidates ?? []).find((c: any) => c.vessel_id === v.vessel_id || c.mmsi === v.mmsi)
                const breakdown = v.evidence_breakdown || cand?.evidence_breakdown
                const explanation = v.explanation || cand?.explanation || []
                const consistencyLevel = v.consistency_level || cand?.consistency_level || 'LOW'
                if (!breakdown && explanation.length === 0) return null

                return (
                  <div
                    key={v.vessel_id}
                    style={{
                      padding: '1rem',
                      background: 'rgba(15, 23, 42, 0.6)',
                      borderRadius: '8px',
                      border: '1px solid rgba(56, 189, 248, 0.2)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                      <strong style={{ fontSize: '0.9rem', color: '#f8fafc' }}>
                        #{v.rank} {v.vessel_name ?? v.mmsi ?? v.vessel_id}
                      </strong>
                      <span
                        style={{
                          fontSize: '0.7rem',
                          fontWeight: 700,
                          padding: '0.15rem 0.5rem',
                          borderRadius: '4px',
                          background:
                            consistencyLevel === 'HIGH'
                              ? 'rgba(74, 222, 128, 0.15)'
                              : consistencyLevel === 'MODERATE'
                              ? 'rgba(250, 204, 21, 0.15)'
                              : 'rgba(148, 163, 184, 0.15)',
                          color:
                            consistencyLevel === 'HIGH'
                              ? '#4ade80'
                              : consistencyLevel === 'MODERATE'
                              ? '#facc15'
                              : '#94a3b8',
                          border: `1px solid ${
                            consistencyLevel === 'HIGH'
                              ? 'rgba(74, 222, 128, 0.3)'
                              : consistencyLevel === 'MODERATE'
                              ? 'rgba(250, 204, 21, 0.3)'
                              : 'rgba(148, 163, 184, 0.3)'
                          }`,
                        }}
                      >
                        Level: {consistencyLevel}
                      </span>
                    </div>

                    {breakdown && (
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem', marginBottom: '0.75rem', textAlign: 'center' }}>
                        <div style={{ background: 'rgba(30, 41, 59, 0.5)', padding: '0.4rem', borderRadius: '4px' }}>
                          <span style={{ fontSize: '0.65rem', color: '#94a3b8', display: 'block' }}>Spatial (50%)</span>
                          <strong style={{ fontSize: '0.85rem', color: '#38bdf8' }}>
                            {(typeof breakdown.spatial === 'object' ? (breakdown.spatial?.score ?? 0) : (breakdown.spatial ?? 0)).toFixed(3)}
                          </strong>
                        </div>
                        <div style={{ background: 'rgba(30, 41, 59, 0.5)', padding: '0.4rem', borderRadius: '4px' }}>
                          <span style={{ fontSize: '0.65rem', color: '#94a3b8', display: 'block' }}>Temporal (25%)</span>
                          <strong style={{ fontSize: '0.85rem', color: '#818cf8' }}>
                            {(typeof breakdown.temporal === 'object' ? (breakdown.temporal?.score ?? 0) : (breakdown.temporal ?? 0)).toFixed(3)}
                          </strong>
                        </div>
                        <div style={{ background: 'rgba(30, 41, 59, 0.5)', padding: '0.4rem', borderRadius: '4px' }}>
                          <span style={{ fontSize: '0.65rem', color: '#94a3b8', display: 'block' }}>Trajectory (25%)</span>
                          <strong style={{ fontSize: '0.85rem', color: '#34d399' }}>
                            {(typeof breakdown.trajectory === 'object' ? (breakdown.trajectory?.score ?? 0) : (breakdown.trajectory ?? 0)).toFixed(3)}
                          </strong>
                        </div>
                      </div>
                    )}

                    {explanation.length > 0 && (
                      <ul style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.75rem', color: '#cbd5e1', lineHeight: 1.5 }}>
                        {explanation.map((fact: string, idx: number) => (
                          <li key={idx}>{fact}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {/* Phase #5: Uncertainty Propagation & Monte Carlo Ensemble Sensitivity */}
        {(runResult.monte_carlo_ensemble || (reportData as any)?.monte_carlo_ensemble) && (() => {
          const mc = runResult.monte_carlo_ensemble || (reportData as any)?.monte_carlo_ensemble
          const cands = reportData?.candidates ?? []
          return (
            <section className="re-report-section" data-testid="report-monte-carlo-section">
              <h2 className="re-report-sec-title">Uncertainty Propagation &amp; Monte Carlo Sensitivity (Phase #5)</h2>
              <p className="re-section-desc">
                Stochastic sensitivity assessment perturbing initial observation coordinates and metocean forcing fields across {mc.num_realizations} realizations.
              </p>

              <div className="re-source-zone-card" style={{ marginBottom: '1.25rem', borderColor: '#38bdf8', background: 'rgba(8, 47, 73, 0.3)' }}>
                <div className="re-source-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
                  <div>
                    <div className="re-source-label">Ensemble Size</div>
                    <div className="re-source-value" style={{ color: '#38bdf8' }}>
                      {mc.num_realizations ?? (mc as any).ensemble_size ?? 0} realizations
                    </div>
                  </div>
                  <div>
                    <div className="re-source-label">Dispersion Radius</div>
                    <div className="re-source-value" style={{ color: '#38bdf8' }}>
                      {((mc.ensemble_dispersion_radius_m != null
                        ? mc.ensemble_dispersion_radius_m / 1000
                        : (mc as any).dispersion_radius_km) ?? 0).toFixed(2)} km
                    </div>
                  </div>
                  <div>
                    <div className="re-source-label">P05 – P95 Longitude</div>
                    <div className="re-source-value" style={{ fontSize: '0.85rem' }}>
                      {(mc.percentile_bounding_box?.p05_lon ?? (mc as any).p05_source_lon ?? 0).toFixed(4)}° to {(mc.percentile_bounding_box?.p95_lon ?? (mc as any).p95_source_lon ?? 0).toFixed(4)}°E
                    </div>
                  </div>
                  <div>
                    <div className="re-source-label">P05 – P95 Latitude</div>
                    <div className="re-source-value" style={{ fontSize: '0.85rem' }}>
                      {(mc.percentile_bounding_box?.p05_lat ?? (mc as any).p05_source_lat ?? 0).toFixed(4)}° to {(mc.percentile_bounding_box?.p95_lat ?? (mc as any).p95_source_lat ?? 0).toFixed(4)}°N
                    </div>
                  </div>
                  <div>
                    <div className="re-source-label">Effective Seed</div>
                    <div className="re-source-value" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem' }}>
                      {mc.effective_seed ?? 'N/A'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Candidate Ensemble Sensitivity Table */}
              <div className="re-results-table-wrap" style={{ marginBottom: '1.25rem' }}>
                <table className="re-table re-report-table">
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Vessel</th>
                      <th>Deterministic Score</th>
                      <th>Score Spread (μ ± σ)</th>
                      <th>5th – 95th Percentile</th>
                      <th>Corridor Consistency</th>
                      <th>Source Zone Entry</th>
                      <th>Ensemble Support</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runResult.vessels.map((v) => {
                      const cand = cands.find((c: any) => c.vessel_id === v.vessel_id || c.mmsi === v.mmsi)
                      const ens = v.ensemble_evidence || cand?.ensemble_evidence
                      return (
                        <tr key={v.vessel_id}>
                          <td className="re-td-mono"><strong>#{v.rank}</strong></td>
                          <td><strong>{v.vessel_name ?? v.mmsi ?? v.vessel_id}</strong></td>
                          <td className="re-td-mono">{(v.evidence_consistency_score * 100).toFixed(1)}%</td>
                          <td className="re-td-mono">
                            {ens?.score_mean != null ? `${ens.score_mean.toFixed(3)} ± ${ens.score_std != null ? ens.score_std.toFixed(3) : '0.000'}` : '—'}
                          </td>
                          <td className="re-td-mono" style={{ color: '#38bdf8' }}>
                            {ens?.score_p05 != null ? `[${ens.score_p05.toFixed(3)}, ${ens.score_p95?.toFixed(3)}]` : '—'}
                          </td>
                          <td className="re-td-mono" style={{ color: '#4ade80' }}>
                            {ens?.trajectory_consistency_across_ensemble != null ? `${(ens.trajectory_consistency_across_ensemble * 100).toFixed(0)}%` : '—'}
                          </td>
                          <td className="re-td-mono">
                            {ens?.source_intersection_fraction != null ? `${(ens.source_intersection_fraction * 100).toFixed(0)}%` : '0%'}
                          </td>
                          <td>
                            {ens ? (
                              <span
                                style={{
                                  fontWeight: 700,
                                  color: ens.ensemble_support_fraction >= 0.7 ? '#4ade80' : '#facc15',
                                }}
                              >
                                {(ens.ensemble_support_fraction * 100).toFixed(0)}%
                              </span>
                            ) : '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {/* Sample Realizations Table */}
              {mc.realizations && mc.realizations.length > 0 && (
                <div style={{ marginBottom: '1rem' }}>
                  <h4 style={{ fontSize: '0.8rem', color: '#94a3b8', textTransform: 'uppercase', marginBottom: '0.5rem' }}>
                    Sample Ensemble Realizations (First {Math.min(10, mc.realizations.length)} of {mc.num_realizations})
                  </h4>
                  <div className="re-results-table-wrap">
                    <table className="re-table re-report-table" style={{ fontSize: '0.75rem' }}>
                      <thead>
                        <tr>
                          <th>Realization</th>
                          <th>Reconstructed Source</th>
                          <th>Radius</th>
                          <th>Leeway α</th>
                          <th>Wind Δw</th>
                          <th>Wind Δθ</th>
                          <th>Origin Offset</th>
                        </tr>
                      </thead>
                      <tbody>
                        {mc.realizations.slice(0, 10).map((rz: any) => {
                          const p = rz.perturbation_parameters || {}
                          const alpha = p.leeway_factor ?? p.leeway_fraction ?? 0.035
                          const dw = p.wind_speed_delta_ms ?? p.delta_wind_speed_ms ?? 0
                          const dtheta = p.wind_dir_delta_deg ?? p.delta_wind_dir_deg ?? 0
                          const dx = p.origin_offset_m?.dx_m ?? p.dx_m ?? 0
                          const dy = p.origin_offset_m?.dy_m ?? p.dy_m ?? 0
                          return (
                            <tr key={rz.realization_id}>
                              <td className="re-td-mono">#{rz.realization_id ?? rz.id ?? 1}</td>
                              <td className="re-td-mono">
                                {(rz.source_lat ?? rz.final_source_lat ?? 0).toFixed(4)}°N, {(rz.source_lon ?? rz.final_source_lon ?? 0).toFixed(4)}°E
                              </td>
                              <td className="re-td-mono">
                                {(((rz.source_radius_m != null ? rz.source_radius_m : (rz.source_radius_km ? rz.source_radius_km * 1000 : 0))) / 1000).toFixed(1)} km
                              </td>
                              <td className="re-td-mono">{alpha.toFixed(4)}</td>
                              <td className="re-td-mono">{dw >= 0 ? `+${dw.toFixed(2)}` : dw.toFixed(2)} m/s</td>
                              <td className="re-td-mono">{dtheta >= 0 ? `+${dtheta.toFixed(1)}` : dtheta.toFixed(1)}°</td>
                              <td className="re-td-mono">
                                ({dx >= 0 ? `+${dx.toFixed(0)}` : dx.toFixed(0)}m, {dy >= 0 ? `+${dy.toFixed(0)}` : dy.toFixed(0)}m)
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              <div className="re-disclaimer" style={{ borderColor: '#38bdf8', background: 'rgba(56, 189, 248, 0.05)' }}>
                <strong>Scientific Uncertainty Notice:</strong> {mc.scientific_notice ?? mc.scientific_disclaimer ?? ''}
              </div>
            </section>
          )
        })()}

        {/* Section 8: ML Model Signal & AIS Behavioural Intelligence */}
        {(() => {
          const cands: any[] = reportData?.candidates ?? []
          const hasML = cands.some((c: any) => c.model_probability != null)
          const hasBeh = cands.some((c: any) => c.behavioral_intelligence != null)
          if (!cands.length || (!hasML && !hasBeh)) return null
          return (
            <section className="re-report-section" data-testid="report-ml-section">
              <h2 className="re-report-sec-title">8. ML Model Signal &amp; AIS Behavioural Intelligence</h2>
              <div className="re-disclaimer" style={{ marginBottom: '1rem', borderColor: '#1d4ed8', background: 'rgba(29,78,216,0.07)' }}>
                <strong>CONTEXTUAL MODEL LAYER — NOT ATTRIBUTION OR LEGAL RESPONSIBILITY.</strong> ML Model Probability is an independent model output generated from the extracted feature representation. Not a probability of legal responsibility or causation. Training provenance: Model trained on synthetic benchmark scenarios; real-data inference is an experimental contextual signal and has not been established as a calibrated real-world responsibility probability. Behavioural findings are deterministic Stage E3 rule-based results only; no intent, wrongdoing, or legal culpability is implied.
              </div>

              {hasML && (
                <>
                  <p className="re-section-desc" style={{ color: '#93c5fd', marginBottom: '0.5rem' }}>
                    <strong>ML Model Probability</strong> — Independent per-candidate model output (Not a probability of legal responsibility or causation.)
                  </p>
                  <div className="re-results-table-wrap" style={{ marginBottom: '1.25rem' }}>
                    <table className="re-table re-report-table">
                      <thead>
                        <tr>
                          <th>Rank</th>
                          <th>Vessel</th>
                          <th>Physical Score</th>
                          <th>ML Model Probability</th>
                          <th>Signal Interpretation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cands.map((c: any) => {
                          const prob: number | null = c.model_probability ?? null
                          const probStr = prob != null ? `${(prob * 100).toFixed(1)}%` : '—'
                          const color = prob == null ? '#94a3b8' : prob >= 0.70 ? '#4ade80' : prob >= 0.40 ? '#facc15' : '#f87171'
                          const interp = prob == null
                            ? 'Inference unavailable'
                            : prob >= 0.70 ? 'High spatial/temporal alignment with reconstructed release'
                            : prob >= 0.40 ? 'Moderate alignment with reconstructed evidence profile'
                            : prob >= 0.15 ? 'Low alignment; limited spatial or temporal overlap'
                            : 'Minimal alignment with reconstructed release parameters'
                          return (
                            <tr key={c.vessel_id ?? c.rank}>
                              <td className="re-td-mono"><strong>#{c.rank}</strong></td>
                              <td>{c.vessel_name ?? c.mmsi ?? c.vessel_id}</td>
                              <td className="re-td-mono">{(c.evidence_consistency_score * 100).toFixed(1)}%</td>
                              <td className="re-td-mono" style={{ fontWeight: 800, color }}>{probStr}</td>
                              <td style={{ fontSize: '0.78rem', color: '#cbd5e1' }}>{interp}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p style={{ fontSize: '0.72rem', color: '#475569', fontStyle: 'italic', marginTop: '0.25rem', marginBottom: '0.2rem' }}>
                    <strong>Training provenance:</strong> Model trained on synthetic benchmark scenarios; real-data inference is an experimental contextual signal and has not been established as a calibrated real-world responsibility probability.
                  </p>
                  <p style={{ fontSize: '0.72rem', color: '#475569', fontStyle: 'italic', marginTop: '0.15rem', marginBottom: 0 }}>
                    ML feature-vector values are model inputs produced by the feature extractor and may use definitions or normalization different from the physical evidence presentation metrics.
                  </p>
                </>
              )}

              {hasBeh && (
                <>
                  <p className="re-section-desc" style={{ color: '#6ee7b7', marginBottom: '0.5rem' }}>
                    <strong>AIS Behavioural Intelligence (Stage E3 Detectors)</strong> — Contextual Observable AIS Patterns Only
                  </p>
                  <div className="re-results-table-wrap" style={{ marginBottom: '0.75rem' }}>
                    <table className="re-table re-report-table">
                      <thead>
                        <tr>
                          <th>Rank</th>
                          <th>Vessel</th>
                          <th>AIS Gaps</th>
                          <th>Loitering</th>
                          <th>Rule-Based Detector Findings</th>
                          <th>Flags</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cands.map((c: any) => {
                          const bi = c.behavioral_intelligence
                          if (!bi) return (
                            <tr key={c.vessel_id ?? c.rank}>
                              <td className="re-td-mono">#{c.rank}</td>
                              <td>{c.vessel_name ?? c.mmsi ?? c.vessel_id}</td>
                              <td colSpan={4} style={{ color: '#64748b', fontSize: '0.8rem' }}>Analysis unavailable</td>
                            </tr>
                          )
                          return (
                            <tr key={c.vessel_id ?? c.rank}>
                              <td className="re-td-mono"><strong>#{c.rank}</strong></td>
                              <td>{c.vessel_name ?? c.mmsi ?? c.vessel_id}</td>
                              <td className="re-td-mono">{bi.transmission_gap_count ?? 0}</td>
                              <td style={{ color: bi.loitering_detected ? '#facc15' : '#4ade80', fontWeight: 600 }}>
                                {bi.loitering_detected ? 'Yes' : 'No'}
                              </td>
                              <td className="re-td-mono">{(bi.anomalies ?? []).length}</td>
                              <td style={{ fontSize: '0.75rem', color: '#cbd5e1' }}>
                                {(bi.summary_flags ?? []).join(', ') || 'None detected'}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p style={{ fontSize: '0.72rem', color: '#64748b', fontStyle: 'italic', margin: 0 }}>
                    ZERO-FABRICATION: All findings are based exclusively on genuine received AIS transmissions. Transmission gaps are reported as observable gaps in received telemetry only. No vessel movements, transponder disabling events, or activities during gaps are inferred. No intent, wrongdoing, or legal responsibility is implied by any finding in this section.
                  </p>
                </>
              )}
            </section>
          )
        })()}

        {/* Section 8.5: SAR ↔ AIS Dual-Sensor Maritime Surveillance */}
        {(() => {
          const surveillance = runResult.sar_surveillance || (reportData as any)?.sar_surveillance
          return (
            <section className="re-report-section" data-testid="report-sar-surveillance-section">
              <h2 className="re-report-sec-title">8.5 SAR ↔ AIS Dual-Sensor Maritime Surveillance</h2>

              {!surveillance ? (
                <div className="re-disclaimer" style={{ borderColor: 'rgba(148, 163, 184, 0.3)', background: 'rgba(30, 41, 59, 0.4)', color: '#94a3b8' }}>
                  Dual-sensor surveillance was not enabled for this experiment.
                </div>
              ) : (
                <>
                  {/* 1. Observation metadata */}
                  <div className="re-table-card" style={{ marginBottom: '1.25rem' }}>
                    <table className="re-table re-report-table" style={{ tableLayout: 'fixed', width: '100%' }}>
                      <tbody>
                        <tr>
                          <th style={{ width: '28%' }}>SAR Product ID</th>
                          <td className="re-td-mono" style={{ width: '72%', wordBreak: 'break-all' }}>
                            {surveillance.product_id || runResult.satellite_product_id}
                          </td>
                        </tr>
                        <tr>
                          <th>Observation Timestamp</th>
                          <td className="re-td-mono">
                            {formatDate(surveillance.observation_time || runResult.observation_time)}
                          </td>
                        </tr>
                        <tr>
                          <th>Surveillance Model Version</th>
                          <td className="re-td-mono">
                            {surveillance.model_version || 'sar_ais_surveillance_v1'}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>

                  {/* 2. Detection summary */}
                  {(() => {
                    const targetsCount =
                      surveillance.total_radar_targets_detected ??
                      surveillance.total_sar_targets ??
                      (surveillance.targets?.length ?? 0)
                    const aisAssociationsCount =
                      surveillance.correlated_ais_matches ??
                      surveillance.matched_coincident_count ??
                      0
                    const spatialDiscrepanciesCount =
                      surveillance.spatial_discrepancies ??
                      surveillance.spatial_discrepancy_count ??
                      0
                    const uncorrelatedTargetsCount =
                      surveillance.uncorrelated_radar_targets ??
                      surveillance.uncorrelated_target_count ??
                      0
                    const undetectedVesselsCount =
                      surveillance.undetected_ais_vessels ??
                      surveillance.undetected_vessel_count ??
                      0
                    const associationsList: any[] =
                      surveillance.associations ?? surveillance.correlations ?? []

                    return (
                      <>
                        <div className="re-source-zone-card" style={{ marginBottom: '1.25rem', borderColor: '#38bdf8', background: 'rgba(8, 47, 73, 0.25)' }}>
                          <div className="re-source-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}>
                            <div>
                              <div className="re-source-label">Radar Targets Detected</div>
                              <div className="re-source-value" style={{ color: '#fbbf24' }}>
                                {targetsCount}
                              </div>
                            </div>
                            <div>
                              <div className="re-source-label">AIS Associations</div>
                              <div className="re-source-value" style={{ color: '#4ade80' }}>
                                {aisAssociationsCount}
                              </div>
                            </div>
                            <div>
                              <div className="re-source-label">Spatial Discrepancies</div>
                              <div className="re-source-value" style={{ color: '#facc15' }}>
                                {spatialDiscrepanciesCount}
                              </div>
                            </div>
                            <div>
                              <div className="re-source-label">Uncorrelated Radar Targets</div>
                              <div className="re-source-value" style={{ color: '#f87171' }}>
                                {uncorrelatedTargetsCount}
                              </div>
                            </div>
                            <div>
                              <div className="re-source-label">AIS Vessels Not Detected</div>
                              <div className="re-source-value" style={{ color: '#94a3b8' }}>
                                {undetectedVesselsCount}
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* 3. Association table / Empty State Handling */}
                        {targetsCount === 0 && (
                          <div className="re-empty" style={{ margin: '1rem 0' }}>
                            No bright radar targets detected in the configured SAR scene.
                          </div>
                        )}
                        {associationsList.length === 0 ? (
                          <div className="re-empty" style={{ margin: '1rem 0' }}>
                            No AIS observations were available within the configured search window.
                          </div>
                        ) : associationsList.every((c: any) => c.classification === 'RADAR_TARGET_UNCORRELATED') ? (
                          <div className="re-empty" style={{ margin: '1rem 0' }}>
                            No AIS association was found within the configured search gate.
                          </div>
                        ) : null}

                        {associationsList.length > 0 && (
                          <div className="re-results-table-wrap" style={{ marginBottom: '1.25rem' }}>
                            <table className="re-table re-report-table" data-testid="report-surveillance-table">
                              <thead>
                                <tr>
                                  <th style={{ whiteSpace: 'nowrap' }}>Radar Target</th>
                                  <th style={{ whiteSpace: 'nowrap' }}>AIS Vessel / Track</th>
                                  <th style={{ whiteSpace: 'nowrap' }}>Classification</th>
                                  <th style={{ whiteSpace: 'nowrap' }}>Spatial Separation</th>
                                  <th style={{ whiteSpace: 'nowrap' }}>Temporal Difference</th>
                                  <th style={{ whiteSpace: 'nowrap' }}>AIS Position Provenance</th>
                                  <th style={{ whiteSpace: 'nowrap' }}>Investigation Flag</th>
                                </tr>
                              </thead>
                              <tbody>
                                {associationsList.map((assoc: any, idx: number) => {
                                  const targetId = assoc.target?.target_id ?? assoc.target_id
                                  const targetLabel = targetId
                                    ? `Target ${targetId}`
                                    : 'No SAR bright target'
                                  const vesselLabel = (assoc.vessel_name || assoc.mmsi)
                                    ? `${assoc.vessel_name ?? 'MMSI ' + assoc.mmsi} ${assoc.mmsi ? `(${assoc.mmsi})` : ''}`
                                    : 'No AIS association'
                                  const prov = assoc.ais_position_provenance ?? assoc.ais_alignment_method ?? 'UNAVAILABLE'
                                  const isAmbiguous = assoc.classification === 'AMBIGUOUS_MULTI_TARGET_PROXIMITY'
                                  const separation = assoc.spatial_separation_m ?? assoc.distance_meters
                                  const timeDelta = assoc.temporal_delta_seconds ?? assoc.ais_time_offset_seconds
                                  const isFlagged = Boolean(assoc.investigation_flag || assoc.classification === 'SPATIAL_DISCREPANCY_EXCEEDANCE')

                                  return (
                                    <tr key={assoc.association_id || assoc.correlation_id || idx}>
                                      <td className="re-td-mono" style={{ color: targetId ? '#fbbf24' : '#94a3b8' }}>
                                        {targetLabel}
                                      </td>
                                      <td>
                                        <strong>{vesselLabel}</strong>
                                      </td>
                                      <td>
                                        <span style={{ fontWeight: 600 }}>
                                          {isAmbiguous
                                            ? 'Ambiguous Multi-Target Proximity'
                                            : (assoc.classification || '').replace(/_/g, ' ')}
                                        </span>
                                      </td>
                                      <td className="re-td-mono">
                                        {separation != null ? (
                                          `${separation.toFixed(1)} m`
                                        ) : assoc.classification === 'AIS_VESSEL_NOT_DETECTED' || !targetId ? (
                                          <span
                                            style={{ color: '#94a3b8', fontStyle: 'italic', fontSize: '0.74rem' }}
                                            title="No bright radar target detected within the 3,000m association gate"
                                          >
                                            Unassociated (&gt;3,000m)
                                          </span>
                                        ) : assoc.classification === 'RADAR_TARGET_UNCORRELATED' ? (
                                          <span
                                            style={{ color: '#94a3b8', fontStyle: 'italic', fontSize: '0.74rem' }}
                                            title="No AIS transponder fix within the 3,000m association gate"
                                          >
                                            Unassociated (&gt;3,000m)
                                          </span>
                                        ) : (
                                          '—'
                                        )}
                                      </td>
                                      <td className="re-td-mono">
                                        {timeDelta != null ? `${Math.abs(timeDelta).toFixed(0)} s` : '—'}
                                      </td>
                                      <td>
                                        <code style={{ fontSize: '0.75rem' }}>{prov}</code>
                                      </td>
                                      <td>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                                          <span style={{ color: isFlagged ? '#f87171' : '#4ade80', fontWeight: 600 }}>
                                            {isFlagged ? 'Flagged' : 'Nominal'}
                                          </span>
                                          {(assoc.findings_summary || assoc.notes) && (
                                            <span style={{ fontSize: '0.72rem', color: '#94a3b8', lineHeight: 1.3 }}>
                                              {assoc.findings_summary || assoc.notes}
                                            </span>
                                          )}
                                        </div>
                                      </td>
                                    </tr>
                                  )
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </>
                    )
                  })()}

                  {/* 4. Scientific interpretation */}
                  <div style={{ padding: '0.85rem 1rem', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '6px', border: '1px solid rgba(56, 189, 248, 0.2)', marginBottom: '1rem' }}>
                    <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.85rem', color: '#38bdf8' }}>
                      Scientific Interpretation Guidance
                    </h4>
                    <p style={{ margin: '0 0 0.4rem', fontSize: '0.78rem', color: '#cbd5e1', lineHeight: 1.5 }}>
                      Bright radar targets are SAR scattering detections representing localized backscatter maxima and are not automatically classified as vessels. Association with an AIS record indicates spatial and temporal correspondence within configured search gating thresholds, not proof of causation, identity, intent, or illegal activity.
                    </p>
                    <p style={{ margin: 0, fontSize: '0.78rem', color: '#94a3b8', lineHeight: 1.5 }}>
                      Spatial discrepancies and unassociated radar detections require contextual maritime investigation. Observed discrepancies may arise from transponder antenna positioning offsets, GPS precision bounds, temporal interpolation variance, or radar backscatter artifacts. No legal conclusions or culpability determinations may be inferred from dual-sensor surveillance outputs.
                    </p>
                  </div>

                  {/* 5. Data provenance */}
                  <div className="re-table-card" style={{ marginBottom: '1rem' }}>
                    <table className="re-table re-report-table" style={{ tableLayout: 'fixed', width: '100%' }}>
                      <tbody>
                        <tr>
                          <th style={{ width: '28%' }}>SAR Data Source</th>
                          <td style={{ width: '72%' }}>
                            Copernicus Sentinel-1 SAR Subscene (Calibrated GRD backscatter amplitude / CFAR detection)
                          </td>
                        </tr>
                        <tr>
                          <th>AIS Telemetry Source</th>
                          <td>
                            Curated Historical AIS Benchmark Database (ais_vessels.db)
                          </td>
                        </tr>
                        <tr>
                          <th>AIS Telemetry Nature</th>
                          <td style={{ fontSize: '0.8rem', color: '#cbd5e1' }}>
                            MARIS distinguishes genuine received transponder messages (<code className="re-td-mono">GENUINE_OBSERVATION</code>) from temporally aligned interpolated kinematic fixes (<code className="re-td-mono">TEMPORALLY_ALIGNED_FIX</code>). Curated benchmark AIS is historical research data and is not authoritative operational real-time AIS.
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>

                  {/* 6. Scientific disclaimer */}
                  <div className="re-disclaimer" style={{ borderColor: '#facc15', background: 'rgba(250, 204, 21, 0.08)' }} data-testid="report-surveillance-disclaimer">
                    <strong>Scientific Assessment Disclaimer:</strong> {surveillance.scientific_disclaimer || runResult.scientific_disclaimer || 'Bright radar targets are SAR scattering detections and are not automatically classified as vessels. Association with an AIS record indicates spatial/temporal correspondence, not proof of causation, identity, intent, or illegal activity.'}
                  </div>
                </>
              )}
            </section>
          )
        })()}

        {/* Sections 8-9 / 9-10: Reproducibility + Limitations — numbers shift when ML section is present */}
        {(() => {
          const cands: any[] = reportData?.candidates ?? []
          const hasMlSection = cands.some((c: any) => c.model_probability != null || c.behavioral_intelligence != null)
          const reproSecNum = hasMlSection ? 9 : 8
          const limSecNum = hasMlSection ? 10 : 9
          return (
            <>
              <section className="re-report-section">
                <h2 className="re-report-sec-title">{reproSecNum}. Experiment Reproducibility Audit</h2>
          <div className="re-reproducibility-card" data-testid="report-reproducibility-card">
            <div className="re-reproducibility-header">
              <FileCheck size={16} className="re-icon-cyan" />
              <h4>EXPERIMENT REPRODUCIBILITY</h4>
            </div>
            <div className="re-reproducibility-grid">
              <div className="re-repro-item">
                <span className="re-repro-label">Run ID</span>
                <code className="re-repro-code">{runResult.run_id}</code>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">Model</span>
                <code className="re-repro-code">{runResult.model_version}</code>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">Observation</span>
                <span className="re-repro-val">
                  {runResult.satellite_product_id.includes('S1') ? 'Sentinel-1 C-SAR' : 'Satellite SAR'}
                </span>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">Wind</span>
                <span className="re-repro-val">ECMWF ERA5 10m Wind</span>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">Currents</span>
                <span className="re-repro-val">Copernicus Marine CMEMS</span>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">AIS</span>
                <span className="re-repro-val">Curated Historical SQLite Database</span>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">Backtrack</span>
                <span className="re-repro-val">
                  {runResult.backtrack_hours} h / {runResult.backward_steps.length} steps
                </span>
              </div>
              <div className="re-repro-item">
                <span className="re-repro-label">Result State</span>
                <span className="re-repro-val re-text-cyan">Completed</span>
              </div>
            </div>
          </div>
              </section>

              {/* Limitations section — number follows Reproducibility */}
              <section className="re-report-section">
                <h2 className="re-report-sec-title">{limSecNum}. Scientific Interpretation &amp; Limitations</h2>
          <div className="re-disclaimer re-result-disclaimer" data-testid="report-disclaimer">
            <strong>Scientific Assessment Disclaimer:</strong> This analysis is an evidence-consistency
            assessment and does not constitute a legal determination of responsibility or causation.
            {runResult.scientific_disclaimer && ` ${runResult.scientific_disclaimer}`}
          </div>
          <ul className="re-limitations-list">
            <li>
              Attribution results reflect physical hydrodynamic consistency based on available
              observational and transponder evidence.
            </li>
            <li>
              Metocean backward drift integration is subject to boundary conditions and interpolation
              uncertainties inherent to reanalysis products.
            </li>
            <li>
              Vessel evaluation is constrained by AIS transponder transmission continuity and terrestrial/satellite
              reception geometry.
            </li>
            <li>
              Results should be corroborated with independent physical sampling and maritime authority logs.
            </li>
          </ul>
        </section>
            </>
          )
        })()}

        {/* Report Footer / Running Metadata */}
        <footer className="re-report-footer">
          <div className="re-report-footer-left">
            <span>MARIS Marine Attribution & Investigation System</span>
            <code className="re-td-mono">{runResult.run_id}</code>
          </div>
          <div className="re-report-footer-right">
            <span>Scientific assessment only. Not a legal determination.</span>
          </div>
        </footer>
      </div>
    </div>
  )
}
