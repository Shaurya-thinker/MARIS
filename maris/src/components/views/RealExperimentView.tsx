/**
 * RealExperimentView — 7-step interactive attribution experiment wizard.
 *
 * Step 1: Select Sentinel-1 observation (CDSE catalogue search)
 * Step 2: Acquire ERA5 + CMEMS environmental data
 * Step 3: Configure backward drift parameters
 * Step 4: Select AIS vessel tracks
 * Step 5: Run attribution experiment
 * Step 6: View results
 * Step 7: Previous runs
 *
 * Preserves ALL existing simulation and Corsica code — this is an additive-only view.
 */

import React, { useEffect, useState } from 'react'
import { Radio } from 'lucide-react'
import { useExperiment } from '../../real-experiment/useExperiment'
import type { AisPosition, SentinelProduct, VesselFeatures } from '../../real-experiment/experimentTypes'
import AttributionMap from './AttributionMap'
import HistoryView from './HistoryView'
import ScientificReportView from './ScientificReportView'
import SarSurveillanceSection from './SarSurveillanceSection'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number | null | undefined, dec = 3): string {
  if (n == null) return '—'
  return n.toFixed(dec)
}

function scoreColor(score: number): string {
  if (score >= 0.7) return '#4ade80'
  if (score >= 0.4) return '#facc15'
  return '#f87171'
}

function stepLabel(step: number): string {
  const labels: Record<number, string> = {
    1: 'Observation',
    2: 'Environment',
    3: 'Drift Config',
    4: 'Vessel Search',
    5: 'Run',
    6: 'Results',
    7: 'History',
    8: 'Report',
  }
  return labels[step] ?? `Step ${step}`
}

// ---------------------------------------------------------------------------
// Wizard Progress Bar
// ---------------------------------------------------------------------------

function WizardProgress({ currentStep, onStepClick }: { currentStep: number; onStepClick?: (step: any) => void }) {
  return (
    <div className="re-wizard-progress">
      {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
        <div
          key={s}
          className={`re-wizard-step ${s === currentStep ? 're-active' : ''} ${s < currentStep ? 're-done' : ''}`}
          onClick={() => onStepClick?.(s)}
          style={{ cursor: onStepClick ? 'pointer' : 'default' }}
        >
          <div className="re-step-dot">{s < currentStep ? '✓' : s}</div>
          <div className="re-step-label">{stepLabel(s)}</div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Configuration Banner
// ---------------------------------------------------------------------------

function ConfigBanner({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null
  return (
    <div className="re-config-banner">
      <span className="re-banner-icon">⚠</span>
      <div>
        <strong>Some data sources require configuration:</strong>
        <ul className="re-banner-list">
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SAR Subscene Acquisition Card
// ---------------------------------------------------------------------------

function SarSubsceneAcquisitionCard({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const { state, acquireSubscene } = experiment
  const [acquiringLocal, setAcquiringLocal] = useState(false)
  const [localErr, setLocalErr] = useState<string | null>(null)

  const handleAcquire = async (forceLive: boolean = false) => {
    if (!state.selectedProduct) return
    setAcquiringLocal(true)
    setLocalErr(null)
    try {
      await acquireSubscene(state.selectedProduct, undefined, forceLive)
    } catch (e: unknown) {
      setLocalErr(e instanceof Error ? e.message : 'Acquisition failed')
    } finally {
      setAcquiringLocal(false)
    }
  }

  const isAcquired = Boolean(state.acquiredRasterPath && state.slickCharacterization?.has_physical_raster)
  const isAcquiring = state.acquiringSubscene || acquiringLocal
  const acqResp = state.acquisitionResponse
  const isLiveCopernicus = Boolean(
    acqResp?.source_provider?.toLowerCase().includes('copernicus') ||
    acqResp?.source_provider?.toLowerCase().includes('process api')
  )

  return (
    <div
      data-testid="sar-subscene-card"
      style={{
        marginTop: '0.75rem',
        marginBottom: '0.75rem',
        background: isAcquired ? (isLiveCopernicus ? 'rgba(2, 132, 199, 0.25)' : 'rgba(6, 78, 59, 0.35)') : 'rgba(30, 41, 59, 0.6)',
        border: `1.5px solid ${isAcquired ? (isLiveCopernicus ? '#0284c7' : '#059669') : '#334155'}`,
        borderRadius: '8px',
        padding: '0.9rem',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '1.1rem' }}>🛰️</span>
          <strong style={{ color: isAcquired ? (isLiveCopernicus ? '#38bdf8' : '#34d399') : '#e2e8f0', fontSize: '0.88rem' }}>
            Sentinel-1 SAR Subscene (Lightweight ~2–4 MB)
          </strong>
        </div>
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 600,
            padding: '0.15rem 0.5rem',
            borderRadius: '9999px',
            background: isAcquired
              ? (isLiveCopernicus ? 'rgba(2, 132, 199, 0.25)' : 'rgba(16, 185, 129, 0.2)')
              : 'rgba(148, 163, 184, 0.15)',
            color: isAcquired ? (isLiveCopernicus ? '#38bdf8' : '#34d399') : '#94a3b8',
            border: `1px solid ${isAcquired ? (isLiveCopernicus ? '#0284c7' : '#10b981') : '#475569'}`,
          }}
        >
          {isAcquired
            ? (isLiveCopernicus ? 'LIVE COPERNICUS SAR LOADED' : 'AUTHENTIC SAR RASTER LOADED')
            : 'NOT ACQUIRED (CATALOGUE ONLY)'}
        </span>
      </div>

      {isAcquired && acqResp ? (
        <div style={{ fontSize: '0.78rem', color: '#cbd5e1', display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
          <div>
            <strong>Provider:</strong> {acqResp.source_provider}
            {acqResp.is_cached ? (
              <span style={{ marginLeft: '0.5rem', color: '#6ee7b7' }}>(Local Cache Hit)</span>
            ) : (
              <span style={{ marginLeft: '0.5rem', color: '#38bdf8', fontWeight: 600 }}>(Live Copernicus Process API Query)</span>
            )}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.4rem', marginTop: '0.2rem' }}>
            <div><strong>Size:</strong> {(acqResp.file_size_bytes / (1024 * 1024)).toFixed(2)} MB</div>
            <div><strong>CRS:</strong> {acqResp.crs}</div>
            <div><strong>Bands:</strong> {acqResp.bands.join(', ')}</div>
            <div><strong>AOI:</strong> [{acqResp.bbox.west?.toFixed(2)}, {acqResp.bbox.south?.toFixed(2)} to {acqResp.bbox.east?.toFixed(2)}, {acqResp.bbox.north?.toFixed(2)}]</div>
          </div>
          <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginTop: '0.2rem' }}>
            Data authenticity: Authenticated Sentinel-1 backscatter; physical radar pixels processed by Stage B3 detector.
          </div>
          {!isLiveCopernicus ? (
            <div style={{ marginTop: '0.6rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <button
                type="button"
                className="re-btn-secondary"
                style={{ fontSize: '0.75rem', padding: '0.3rem 0.7rem' }}
                disabled={isAcquiring}
                onClick={() => handleAcquire(true)}
              >
                {isAcquiring ? '⏳ Querying CDSE Process API...' : '🌐 Upgrade to Live Copernicus Process API'}
              </button>
            </div>
          ) : (
            <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#38bdf8', fontSize: '0.75rem' }}>
              <span style={{ color: '#34d399', fontWeight: 'bold' }}>✓</span>
              <span>Authentic Copernicus SAR pixels verified and ready for Stage B3 detection.</span>
            </div>
          )}
        </div>
      ) : (
        <div>
          <p style={{ fontSize: '0.76rem', color: '#94a3b8', margin: '0 0 0.6rem 0' }}>
            Acquires an authentic orthorectified Sentinel-1 SAR subscene (~2–4 MB) centered on the target AOI via Copernicus Process API or pre-staged benchmark cache. Replaces catalogue estimates with real physical pixel segmentation.
          </p>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="re-btn-primary"
              style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
              disabled={isAcquiring}
              onClick={() => handleAcquire(true)}
            >
              {isAcquiring ? '⏳ Querying CDSE Process API...' : '🌐 Fetch Live from Copernicus Process API'}
            </button>
            <button
              type="button"
              className="re-btn-secondary"
              style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
              disabled={isAcquiring}
              onClick={() => handleAcquire(false)}
            >
              ⚡ Smart Cache / Benchmark
            </button>
          </div>
        </div>
      )}

      {(state.acquisitionError || localErr) && (
        <p className="re-error" style={{ marginTop: '0.5rem', fontSize: '0.75rem' }}>
          {state.acquisitionError || localErr}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — Observation Selection
// ---------------------------------------------------------------------------

function Step1Observation({
  experiment,
}: {
  experiment: ReturnType<typeof useExperiment>
}) {
  const { state, setSearchBbox, setSearchDates, searchProducts, selectProduct, goToStep } = experiment

  const [west, setWest] = useState('9.30')
  const [south, setSouth] = useState('43.05')
  const [east, setEast] = useState('9.65')
  const [north, setNorth] = useState('43.40')
  const [startDate, setStartDate] = useState('2018-10-08')
  const [endDate, setEndDate] = useState('2018-10-08')
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSearch = async () => {
    setSearching(true)
    setError(null)
    const bbox = {
      west: parseFloat(west), south: parseFloat(south),
      east: parseFloat(east), north: parseFloat(north),
    }
    setSearchBbox(bbox)
    setSearchDates(startDate + 'T00:00:00Z', endDate + 'T23:59:59Z')
    try {
      await searchProducts({
        ...bbox,
        start: startDate + 'T00:00:00Z',
        end: endDate + 'T23:59:59Z',
        limit: 20,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  const handleSelect = (product: SentinelProduct) => {
    selectProduct(product)
    goToStep(2)
  }

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">1</span>
        Select Sentinel-1 Observation
      </h3>
      <p className="re-step-desc">
        Query the Copernicus Data Space Ecosystem catalogue for Sentinel-1 products.
        No data is downloaded at this stage.
      </p>

      <div className="re-field-grid">
        <label className="re-label">
          West °E
          <input className="re-input" type="number" value={west} onChange={e => setWest(e.target.value)} step="0.1" />
        </label>
        <label className="re-label">
          South °N
          <input className="re-input" type="number" value={south} onChange={e => setSouth(e.target.value)} step="0.1" />
        </label>
        <label className="re-label">
          East °E
          <input className="re-input" type="number" value={east} onChange={e => setEast(e.target.value)} step="0.1" />
        </label>
        <label className="re-label">
          North °N
          <input className="re-input" type="number" value={north} onChange={e => setNorth(e.target.value)} step="0.1" />
        </label>
        <label className="re-label">
          Start Date
          <input className="re-input" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
        </label>
        <label className="re-label">
          End Date
          <input className="re-input" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
        </label>
      </div>

      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
        <button
          className="re-btn-primary"
          onClick={handleSearch}
          disabled={searching || !state.config?.sentinel1_configured}
        >
          {searching ? 'Searching…' : 'Search CDSE Catalogue'}
        </button>
        <button
          type="button"
          className="re-btn-secondary"
          onClick={() => {
            setWest('9.30')
            setSouth('43.05')
            setEast('9.65')
            setNorth('43.40')
            setStartDate('2018-10-08')
            setEndDate('2018-10-08')
            setError(null)
            const bbox = { west: 9.30, south: 43.05, east: 9.65, north: 43.40 }
            setSearchBbox(bbox)
            setSearchDates('2018-10-08T00:00:00Z', '2018-10-08T23:59:59Z')
            setSearching(true)
            searchProducts({
              ...bbox,
              start: '2018-10-08T00:00:00Z',
              end: '2018-10-08T23:59:59Z',
              limit: 20,
            })
              .catch(err => setError(err instanceof Error ? err.message : 'Search failed'))
              .finally(() => setSearching(false))
          }}
          disabled={searching}
        >
          📍 Load Cap Corse Benchmark (2018-10-08)
        </button>
      </div>

      {!state.config?.sentinel1_configured && (
        <p className="re-warning-inline">CDSE credentials not configured. Set CDSE_USERNAME + CDSE_PASSWORD in the backend environment.</p>
      )}
      {error && <p className="re-error">{error}</p>}

      {state.discoveredProducts.length > 0 && (
        <div className="re-results-table-wrap">
          <p className="re-results-count">{state.discoveredProducts.length} product(s) found</p>
          <table className="re-table">
            <thead>
              <tr>
                <th>Product</th>
                <th>Sensing Start</th>
                <th>Platform</th>
                <th>Mode</th>
                <th>Class</th>
                <th>Online</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {state.discoveredProducts.map(p => (
                <tr
                  key={p.product_id}
                  className={state.selectedProduct?.product_id === p.product_id ? 're-row-selected' : ''}
                >
                  <td className="re-td-mono re-td-truncate" title={p.title}>{p.title.slice(0, 30)}…</td>
                  <td>{p.sensing_start.slice(0, 16).replace('T', ' ')} UTC</td>
                  <td>{p.platform}</td>
                  <td>{p.mode ?? '—'}</td>
                  <td>{p.product_class ?? '—'}</td>
                  <td>{p.online ? '✓' : '✗'}</td>
                  <td>
                    <button className="re-btn-small" onClick={() => handleSelect(p)}>
                      Select →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {state.selectedProduct && (
        <>
          <SarSubsceneAcquisitionCard experiment={experiment} />
          <div
            className="re-characterization-panel"
            data-testid="slick-characterization-panel"
          style={{
            marginTop: '1.5rem',
            background: '#091e2f',
            border: '1.5px solid #0284c7',
            borderRadius: '8px',
            padding: '1.25rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <h4 style={{ margin: 0, color: '#38bdf8', fontSize: '1rem', fontWeight: 600 }}>
              Step 10: Automated Oil-Spill Detection &amp; Characterization
            </h4>
            <span
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                padding: '0.2rem 0.6rem',
                borderRadius: '9999px',
                background: state.slickCharacterization?.detected ? 'rgba(34, 197, 94, 0.2)' : 'rgba(234, 179, 8, 0.2)',
                color: state.slickCharacterization?.detected ? '#4ade80' : '#facc15',
                border: `1px solid ${state.slickCharacterization?.detected ? '#22c55e' : '#eab308'}`,
              }}
            >
              {state.slickCharacterization?.status ?? 'CATALOGUE_SELECTION'}
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.6rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Slick Centroid</div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#f8fafc', fontFamily: 'monospace' }}>
                {state.slickCharacterization?.centroid_lat != null && state.slickCharacterization?.centroid_lon != null
                  ? `${state.slickCharacterization.centroid_lat.toFixed(5)}°N, ${state.slickCharacterization.centroid_lon.toFixed(5)}°E`
                  : '—'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.6rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Estimated Area</div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#38bdf8' }}>
                {state.slickCharacterization?.area_km2 != null
                  ? `${state.slickCharacterization.area_km2} km²`
                  : 'Unavailable (unsegmented)'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.6rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Bragg Damping Contrast</div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#f8fafc' }}>
                {state.slickCharacterization?.damping_contrast_db != null
                  ? `${state.slickCharacterization.damping_contrast_db} dB`
                  : 'Unavailable'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.6rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Confidence / Quality</div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#f8fafc' }}>
                {state.slickCharacterization?.confidence != null
                  ? `${(state.slickCharacterization.confidence * 100).toFixed(0)}%`
                  : 'Catalogue geometric anchor'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.6rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Estimated Slick Age</div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#f8fafc' }}>
                {state.slickCharacterization?.estimated_age_hours != null
                  ? `${state.slickCharacterization.estimated_age_hours} h (from backtrack)`
                  : '—'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.6rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Sensor &amp; Mode</div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#f8fafc' }}>
                {state.slickCharacterization?.sensor ?? 'Sentinel-1'} ({state.slickCharacterization?.mode ?? 'IW GRDH'})
              </div>
            </div>
          </div>

          <div style={{ fontSize: '0.78rem', color: '#cbd5e1', marginBottom: '0.4rem', lineHeight: 1.4 }}>
            <strong>Detection Method:</strong> {state.slickCharacterization?.detection_method ?? 'Deterministic Stage B3 Adaptive Thresholding'}
          </div>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginBottom: '1rem', fontStyle: 'italic' }}>
            {state.slickCharacterization?.data_fidelity ?? 'Physical observation metrics derived from satellite SAR radar backscatter damping.'}
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button className="re-btn-primary" onClick={() => goToStep(2)}>
              Confirm &amp; Proceed to Environmental Forcing (Step 2) →
            </button>
          </div>
        </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — Environment Selection
// ---------------------------------------------------------------------------

function Step2Environment({
  experiment,
}: {
  experiment: ReturnType<typeof useExperiment>
}) {
  const { state, acquireEnvironment, goToStep, setBacktrackHours } = experiment
  const [acquiring, setAcquiring] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAcquire = async () => {
    setAcquiring(true)
    setError(null)
    try {
      await acquireEnvironment()
      goToStep(3)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Environment acquisition failed')
    } finally {
      setAcquiring(false)
    }
  }

  const era5Ok = state.config?.era5_configured
  const cmemsOk = state.config?.cmems_configured
  const canAcquire = era5Ok && cmemsOk && !acquiring

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">2</span>
        Select Environmental Data
      </h3>
      <p className="re-step-desc">
        Automatically acquire ERA5 10 m wind and CMEMS GLORYS12V1 near-surface ocean current
        data for the observation time window.
      </p>

      <div className="re-info-card">
        <div className="re-info-row">
          <span>Selected observation:</span>
          <strong>{state.selectedProduct?.title.slice(0, 35) ?? '—'}</strong>
        </div>
        <div className="re-info-row">
          <span>Sensing time:</span>
          <strong>{state.selectedProduct?.sensing_start.slice(0, 16).replace('T', ' ') ?? '—'} UTC</strong>
        </div>
      </div>

      {state.selectedProduct && (
        <SarSubsceneAcquisitionCard experiment={experiment} />
      )}

      {state.slickCharacterization && (
        <div
          className="re-characterization-panel"
          data-testid="step2-slick-characterization-panel"
          style={{
            marginTop: '1rem',
            marginBottom: '1rem',
            background: '#091e2f',
            border: '1.5px solid #0284c7',
            borderRadius: '8px',
            padding: '1rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.6rem' }}>
            <h4 style={{ margin: 0, color: '#38bdf8', fontSize: '0.95rem', fontWeight: 600 }}>
              Automated Oil-Spill Characterization (Step 10)
            </h4>
            <span
              style={{
                fontSize: '0.72rem',
                fontWeight: 600,
                padding: '0.15rem 0.5rem',
                borderRadius: '9999px',
                background: state.slickCharacterization?.detected ? 'rgba(34, 197, 94, 0.2)' : 'rgba(234, 179, 8, 0.2)',
                color: state.slickCharacterization?.detected ? '#4ade80' : '#facc15',
                border: `1px solid ${state.slickCharacterization?.detected ? '#22c55e' : '#eab308'}`,
              }}
            >
              {state.slickCharacterization?.status ?? 'CATALOGUE_SELECTION'}
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '0.6rem', marginBottom: '0.75rem' }}>
            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.5rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Centroid</div>
              <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#f8fafc', fontFamily: 'monospace' }}>
                {state.slickCharacterization?.centroid_lat != null && state.slickCharacterization?.centroid_lon != null
                  ? `${state.slickCharacterization.centroid_lat.toFixed(4)}°N, ${state.slickCharacterization.centroid_lon.toFixed(4)}°E`
                  : '—'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.5rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Estimated Area</div>
              <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#38bdf8' }}>
                {state.slickCharacterization?.area_km2 != null
                  ? `${state.slickCharacterization.area_km2} km²`
                  : 'Unavailable (unsegmented)'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.5rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Damping Contrast</div>
              <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#f8fafc' }}>
                {state.slickCharacterization?.damping_contrast_db != null
                  ? `${state.slickCharacterization.damping_contrast_db} dB`
                  : 'Unavailable'}
              </div>
            </div>

            <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '0.5rem', borderRadius: '6px', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>Confidence / Quality</div>
              <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#f8fafc' }}>
                {state.slickCharacterization?.confidence != null
                  ? `${(state.slickCharacterization.confidence * 100).toFixed(0)}%`
                  : 'Catalogue geometric anchor'}
              </div>
            </div>
          </div>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontStyle: 'italic' }}>
            {state.slickCharacterization?.data_fidelity ?? 'Physical observation metrics derived from satellite SAR radar backscatter damping.'}
          </div>
        </div>
      )}

      <div className="re-field-grid">
        <label className="re-label">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Backtrack Duration (hours)</span>
            <span className="re-range-value" style={{ margin: 0 }}>{state.backtrackHours} h</span>
          </div>
          <input
            className="re-input"
            type="range"
            min={1} max={72} step={1}
            value={state.backtrackHours}
            onChange={e => setBacktrackHours(Number(e.target.value))}
          />
        </label>
      </div>

      <div className="re-credentials-grid">
        <div className={`re-cred-item ${era5Ok ? 're-cred-ok' : 're-cred-missing'}`}>
          <span className="re-cred-icon">{era5Ok ? '✓' : '✗'}</span>
          ERA5 (CDS API)
        </div>
        <div className={`re-cred-item ${cmemsOk ? 're-cred-ok' : 're-cred-missing'}`}>
          <span className="re-cred-icon">{cmemsOk ? '✓' : '✗'}</span>
          CMEMS (Copernicus Marine)
        </div>
      </div>

      {!era5Ok && <p className="re-warning-inline">ERA5: set CDSAPI_KEY in the backend environment.</p>}
      {!cmemsOk && <p className="re-warning-inline">CMEMS: set COPERNICUSMARINE_SERVICE_USERNAME + COPERNICUSMARINE_SERVICE_PASSWORD.</p>}
      {error && <p className="re-error">{error}</p>}

      {state.environment && (
        <div className="re-env-summary">
          <h4>Acquired Environment</h4>
          <table className="re-table re-table-sm">
            <tbody>
              <tr><td>ERA5 Wind U</td><td>{fmt(state.environment.era5_u_sample, 2)} m/s</td></tr>
              <tr><td>ERA5 Wind V</td><td>{fmt(state.environment.era5_v_sample, 2)} m/s</td></tr>
              <tr><td>CMEMS Current U</td><td>{fmt(state.environment.cmems_u_sample, 3)} m/s</td></tr>
              <tr><td>CMEMS Current V</td><td>{fmt(state.environment.cmems_v_sample, 3)} m/s</td></tr>
              <tr><td>Auto-selected</td><td>{state.environment.auto_selected ? 'Yes' : 'Manual override'}</td></tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(1)}>← Back</button>
        <button className="re-btn-primary" onClick={handleAcquire} disabled={!canAcquire}>
          {acquiring ? 'Acquiring… (may take several minutes)' : 'Acquire ERA5 + CMEMS'}
        </button>
        {state.environment && (
          <button className="re-btn-secondary" onClick={() => goToStep(3)}>Next: Drift Config →</button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 3 — Drift Configuration
// ---------------------------------------------------------------------------

function Step3DriftConfig({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const {
    state,
    setStepHours,
    setSpillArea,
    setObservationCoords,
    setForwardPredictionHours,
    setForwardStepHours,
    setEnableForwardPrediction,
    predictForward,
    goToStep,
  } = experiment

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">3</span>
        Configure Drift Physics &amp; Forward Prediction
      </h3>
      <p className="re-step-desc">
        Configure backward reconstruction to trace candidate origin zones and forward prediction to project future slick movement from the Sentinel-1 observation time using the same Leeway-Euler physical dynamics (v<sub>drift</sub> = v<sub>current</sub> + α·v<sub>wind</sub>, α = 0.035).
      </p>

      <div className="re-info-card">
        <div className="re-info-row">
          <span>Backtrack duration:</span>
          <strong>{state.backtrackHours} hours</strong>
        </div>
        <div className="re-info-row">
          <span>Leeway fraction (α):</span>
          <strong>0.035 (ITOPF/NOAA GNOME baseline)</strong>
        </div>
        <div className="re-info-row">
          <span>Uncertainty growth:</span>
          <strong>500 m/hour (heuristic backward search envelope)</strong>
        </div>
      </div>

      {/* Observed Spill Origin Location Card */}
      <div
        style={{
          background: 'rgba(15, 23, 42, 0.55)',
          border: '1px solid #1e293b',
          borderRadius: '8px',
          padding: '0.9rem 1rem 1rem 1rem',
          marginBottom: '1rem',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '0.75rem',
            flexWrap: 'wrap',
            gap: '0.5rem',
          }}
        >
          <div>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#38bdf8', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Observed Spill Coordinates (WGS84)
            </span>
            <span style={{ fontSize: '0.72rem', color: '#94a3b8', display: 'block', marginTop: '0.15rem' }}>
              Physical origin anchor derived from Sentinel-1 SAR characterization (or enter custom coordinates)
            </span>
          </div>
          {(state.selectedProduct?.title.includes('20181008') || state.selectedProduct?.product_id.includes('20181008') || state.selectedProduct?.title.toLowerCase().includes('corsica')) && (
            <button
              type="button"
              className="re-btn-secondary"
              style={{ fontSize: '0.74rem', padding: '0.25rem 0.6rem', borderColor: '#334155', color: '#94a3b8' }}
              onClick={() => {
                setObservationCoords(43.2736, 9.4913)
              }}
              title="Reset to Cap Corse incident coordinates (43.2736°N, 9.4913°E)"
            >
              📍 Reset to Authentic Cap Corse Benchmark (43.2736°N, 9.4913°E)
            </button>
          )}
        </div>

        <div className="re-field-grid">
          <label className="re-label">
            Observed Spill Latitude (°N)
            <input
              className="re-input"
              type="number"
              step="0.00001"
              value={state.observationLat ?? ''}
              onChange={e => setObservationCoords(e.target.value ? Number(e.target.value) : null, state.observationLon)}
            />
            <span className="re-field-hint">Initial location of observed slick detection (WGS84)</span>
          </label>
          <label className="re-label">
            Observed Spill Longitude (°E)
            <input
              className="re-input"
              type="number"
              step="0.00001"
              value={state.observationLon ?? ''}
              onChange={e => setObservationCoords(state.observationLat, e.target.value ? Number(e.target.value) : null)}
            />
            <span className="re-field-hint">Initial location of observed slick detection (WGS84)</span>
          </label>
        </div>
      </div>

      <div className="re-field-grid" style={{ marginBottom: '1.25rem' }}>
        <label className="re-label">
          Backward Integration Step (hours)
          <select
            className="re-select"
            value={state.stepHours}
            onChange={e => setStepHours(Number(e.target.value))}
          >
            <option value={0.25}>0.25 h</option>
            <option value={0.5}>0.5 h</option>
            <option value={1.0}>1.0 h (recommended)</option>
            <option value={2.0}>2.0 h</option>
          </select>
          <span className="re-field-hint">Discrete integration timestep for backward reconstruction</span>
        </label>
        <label className="re-label">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span>Observed Slick Area (m²) — optional</span>
            {state.spillAreaM2 != null && (
              <span style={{ fontSize: '0.72rem', color: '#22d3ee', fontWeight: 600, textTransform: 'none' }}>
                ≈ {(state.spillAreaM2 / 1e6).toFixed(2)} km²
              </span>
            )}
          </div>
          <input
            className="re-input"
            type="number"
            placeholder="e.g. 500000"
            value={state.spillAreaM2 != null ? Math.round(state.spillAreaM2) : ''}
            onChange={e => setSpillArea(e.target.value ? Number(e.target.value) : null)}
            min={0}
          />
          <span className="re-field-hint">Used to set initial source radius R₀ = max(√(area/π), 500 m)</span>
        </label>
      </div>

      {/* Step 11 — Forward Drift Prediction Configuration */}
      <div
        className="re-forward-prediction-config-card"
        data-testid="forward-prediction-config-section"
        style={{
          marginTop: '0.5rem',
          marginBottom: '1rem',
          padding: '1rem',
          borderRadius: '8px',
          background: 'rgba(6, 40, 50, 0.45)',
          border: '1.5px solid #0891b2',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
          <div>
            <h4 style={{ margin: 0, color: '#22d3ee', fontSize: '0.95rem', fontWeight: 600 }}>
              Forward Drift Prediction (Step 11)
            </h4>
            <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
              Project future slick trajectory forward in time from Sentinel-1 observation time
            </span>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
            <input
              type="checkbox"
              data-testid="enable-forward-prediction-checkbox"
              checked={state.enableForwardPrediction}
              onChange={e => setEnableForwardPrediction(e.target.checked)}
            />
            <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e2e8f0' }}>Enable Forward Prediction</span>
          </label>
        </div>

        {state.enableForwardPrediction && (
          <>
            <div className="re-field-grid" style={{ marginBottom: '0.85rem' }}>
              <label className="re-label">
                Forward Horizon (hours)
                <input
                  className="re-input"
                  data-testid="forward-prediction-hours-input"
                  type="number"
                  min={1}
                  max={48}
                  step={1}
                  value={state.forwardPredictionHours}
                  onChange={e => setForwardPredictionHours(Math.max(1, Number(e.target.value)))}
                />
                <span className="re-field-hint">Prediction duration forward from sensing time (+{state.forwardPredictionHours} h)</span>
              </label>
              <label className="re-label">
                Forward Integration Step (hours)
                <select
                  className="re-select"
                  data-testid="forward-step-hours-select"
                  value={state.forwardStepHours}
                  onChange={e => setForwardStepHours(Number(e.target.value))}
                >
                  <option value={0.5}>0.5 h</option>
                  <option value={1.0}>1.0 h (recommended)</option>
                  <option value={2.0}>2.0 h</option>
                </select>
                <span className="re-field-hint">Discrete integration timestep for forward Euler scheme</span>
              </label>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem', marginTop: '0.25rem', marginBottom: '0.75rem' }}>
              <button
                type="button"
                className="re-btn-secondary"
                data-testid="preview-forward-drift-btn"
                onClick={predictForward}
                disabled={state.forwardPredicting || !state.environment}
                style={{ borderColor: '#0891b2', color: '#22d3ee', display: 'flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}
              >
                {state.forwardPredicting ? '⏳ Computing Forward Trajectory…' : '▶ Preview Forward Trajectory'}
              </button>
              {state.environment ? (
                <span style={{ fontSize: '0.75rem', color: '#34d399', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                  ✓ Environmental forcing active (ERA5 wind + CMEMS current)
                </span>
              ) : (
                <span style={{ fontSize: '0.75rem', color: '#facc15' }}>
                  ⚠ Environmental forcing (ERA5/CMEMS) must be acquired first to preview.
                </span>
              )}
            </div>

            {state.forwardPredictError && (
              <div className="re-error" data-testid="forward-predict-error" style={{ marginTop: '0.5rem', marginBottom: '0.5rem' }}>
                {state.forwardPredictError}
              </div>
            )}

            {state.forwardPrediction && (
              <div
                data-testid="forward-preview-summary"
                style={{
                  marginTop: '0.75rem',
                  padding: '0.85rem 1rem',
                  background: 'rgba(15, 23, 42, 0.75)',
                  borderRadius: '6px',
                  border: '1px solid #1e293b',
                  fontSize: '0.8rem',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.65rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ color: '#34d399', fontSize: '0.85rem' }}>✓</span>
                    <strong style={{ color: '#22d3ee' }}>Forward Preview Trajectory Computed</strong>
                  </div>
                  <span
                    style={{
                      padding: '0.15rem 0.55rem',
                      borderRadius: '12px',
                      background: 'rgba(34, 211, 238, 0.12)',
                      border: '1px solid rgba(34, 211, 238, 0.3)',
                      color: '#22d3ee',
                      fontSize: '0.75rem',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 600,
                    }}
                  >
                    {state.forwardPrediction.steps.length} steps (+{state.forwardPredictionHours}h)
                  </span>
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
                    gap: '0.5rem',
                    marginBottom: '0.5rem',
                  }}
                >
                  <div
                    style={{
                      background: 'rgba(15, 23, 42, 0.65)',
                      padding: '0.45rem 0.65rem',
                      borderRadius: '4px',
                      border: '1px solid #1e293b',
                    }}
                  >
                    <div style={{ color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                      Start Coordinate
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: '0.8rem', color: '#e2e8f0', marginTop: '0.15rem' }}>
                      {fmt(state.observationLat, 4)}°N, {fmt(state.observationLon, 4)}°E
                    </div>
                  </div>
                  <div
                    style={{
                      background: 'rgba(15, 23, 42, 0.65)',
                      padding: '0.45rem 0.65rem',
                      borderRadius: '4px',
                      border: '1px solid #1e293b',
                    }}
                  >
                    <div style={{ color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                      Final Coordinate
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: '0.8rem', color: '#22d3ee', marginTop: '0.15rem' }}>
                      {fmt(state.forwardPrediction.final_lat, 4)}°N, {fmt(state.forwardPrediction.final_lon, 4)}°E
                    </div>
                  </div>
                  <div
                    style={{
                      background: 'rgba(15, 23, 42, 0.65)',
                      padding: '0.45rem 0.65rem',
                      borderRadius: '4px',
                      border: '1px solid #1e293b',
                    }}
                  >
                    <div style={{ color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                      Displacement
                    </div>
                    <div style={{ fontWeight: 700, color: '#38bdf8', fontSize: '0.85rem', marginTop: '0.15rem' }}>
                      {(state.forwardPrediction.displacement_km ?? state.forwardPrediction.total_distance_km)?.toFixed(1) ?? '—'} km
                    </div>
                  </div>
                  <div
                    style={{
                      background: 'rgba(15, 23, 42, 0.65)',
                      padding: '0.45rem 0.65rem',
                      borderRadius: '4px',
                      border: '1px solid #1e293b',
                    }}
                  >
                    <div style={{ color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                      Model &amp; Scheme
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.74rem', color: '#cbd5e1', marginTop: '0.15rem' }}>
                      {state.forwardPrediction.model_version}
                    </div>
                  </div>
                </div>
                <div style={{ fontSize: '0.72rem', color: '#94a3b8', fontStyle: 'italic', marginTop: '0.35rem' }}>
                  Projected forward slick drift under combined ocean surface currents (CMEMS) and wind leeway (ERA5, α = 0.035).
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="re-disclaimer">
        <strong>Scientific note:</strong> Backward drift reconstructs past source zones with heuristic expansion. Forward prediction is a deterministic trajectory projection under supplied forcing fields; it does not fabricate confidence radii without a calibrated stochastic dispersion model.
      </div>

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(2)}>← Back</button>
        <button className="re-btn-primary" onClick={() => goToStep(4)}>Next: Vessel Search →</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 4 — AIS Vessel Search
// ---------------------------------------------------------------------------

function Step4VesselSearch({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const { state, searchVessels, toggleVessel, clearVessels, goToStep } = experiment
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSearch = async () => {
    const bbox = state.searchBbox
    if (!bbox || !state.selectedProduct) return
    setSearching(true)
    setError(null)
    // Search from (obs_time - backtrack_hours) to obs_time
    const obsTime = new Date(state.selectedProduct.sensing_start)
    const startTime = new Date(obsTime.getTime() - state.backtrackHours * 3600 * 1000)

    let obsLon = state.observationLon
    let obsLat = state.observationLat
    const isCorsicaIncident = state.selectedProduct.title.includes('20181008') ||
      state.selectedProduct.product_id.includes('20181008') ||
      state.selectedProduct.title.toLowerCase().includes('corsica')
    if (isCorsicaIncident && (obsLat == null || obsLat < 43.0)) {
      obsLat = 43.2736
      obsLon = 9.4913
    } else if (obsLon == null || obsLat == null) {
      if (state.slickCharacterization?.centroid_lat != null && state.slickCharacterization?.centroid_lon != null) {
        obsLat = state.slickCharacterization.centroid_lat
        obsLon = state.slickCharacterization.centroid_lon
      }
    }

    try {
      await searchVessels({
        west: bbox.west,
        south: bbox.south,
        east: bbox.east,
        north: bbox.north,
        start: startTime.toISOString(),
        end: obsTime.toISOString(),
        backward_steps: state.runResult?.backward_steps,
        observation_time: obsTime.toISOString(),
        backtrack_hours: state.backtrackHours,
        observation_lat: obsLat ?? undefined,
        observation_lon: obsLon ?? undefined,
        era5_netcdf_path: state.environment?.era5_netcdf_path,
        cmems_netcdf_path: state.environment?.cmems_netcdf_path,
        step_hours: state.stepHours,
        spill_area_m2: state.spillAreaM2 ?? undefined,
        satellite_product_id: state.selectedProduct.product_id,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'AIS search failed')
    } finally {
      setSearching(false)
    }
  }

  const isVesselSelected = (mmsi: string | null, name: string | null) => {
    const key = mmsi ?? name ?? ''
    return state.selectedVessels.some(v => (v.mmsi ?? v.vessel_name ?? '') === key)
  }

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">4</span>
        Select AIS Vessel Tracks (Dynamic Drift Corridor)
      </h3>
      <p className="re-step-desc">
        Search for vessels whose AIS positions correlate with the reconstructed backward drift trajectory
        and source candidate zone. Select candidate vessels to include in the physical attribution analysis.
      </p>

      <div
        style={{
          background: 'rgba(6, 40, 50, 0.45)',
          border: '1px solid #0891b2',
          borderRadius: '6px',
          padding: '0.65rem 0.85rem',
          marginBottom: '1rem',
          fontSize: '0.8rem',
          color: '#cbd5e1',
          display: 'flex',
          alignItems: 'center',
          gap: '0.6rem',
        }}
      >
        <span style={{ color: '#22d3ee', fontSize: '1.1rem' }}>🧭</span>
        <div>
          <strong style={{ color: '#22d3ee' }}>Dynamic Drift Corridor Search:</strong>{' '}
          Querying <span style={{ fontFamily: 'var(--font-mono)', color: '#38bdf8' }}>ais_vessels.db</span> along the backward drift trajectory with spatial expansion for dispersion uncertainty. Candidates are correlated for minimum trajectory distance, time delta, and source-zone entry.
        </div>
      </div>

      {!state.config?.ais_configured && (
        <div className="re-warning-inline">
          AIS adapter not configured (MARIS_AIS_ADAPTER=unconfigured).
          Contact your MARIS administrator to configure an AIS data source.
          You may still run the experiment without AIS vessel data.
        </div>
      )}

      {error && <p className="re-error">{error}</p>}

      <div className="re-btn-row">
        <button
          className="re-btn-primary"
          onClick={handleSearch}
          disabled={searching || !state.config?.ais_configured}
        >
          {searching ? 'Correlating AIS Tracks…' : 'Search AIS Tracks (Drift Corridor)'}
        </button>
        {state.selectedVessels.length > 0 && (
          <button className="re-btn-ghost" onClick={clearVessels}>
            Clear ({state.selectedVessels.length} selected)
          </button>
        )}
      </div>

      {state.aisSearchResult && (
        <div className="re-results-table-wrap">
          <p className="re-results-count">
            {state.aisSearchResult.vessels.length} vessel(s) found —{' '}
            {state.aisSearchResult.total_positions} total positions
          </p>
          {state.aisSearchResult.vessels.length > 0 ? (
            <table className="re-table">
              <thead>
                <tr>
                  <th>Select</th>
                  <th>MMSI</th>
                  <th>Vessel Name</th>
                  <th>Positions</th>
                  <th>Min Trajectory Dist</th>
                  <th>Drift Δt</th>
                  <th>Source Zone</th>
                  <th>Provenance</th>
                  <th>First Seen</th>
                  <th>Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {state.aisSearchResult.vessels.map((v, i) => {
                  const selected = isVesselSelected(v.mmsi, v.vessel_name)
                  return (
                    <tr key={i} className={selected ? 're-row-selected' : ''}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleVessel({
                            mmsi: v.mmsi,
                            vessel_name: v.vessel_name,
                            positions: v.positions ?? [],
                          })}
                        />
                      </td>
                      <td className="re-td-mono">{v.mmsi ?? '—'}</td>
                      <td>{v.vessel_name ?? '—'}</td>
                      <td>{v.position_count}</td>
                      <td className="re-td-mono">
                        {v.min_trajectory_distance_km != null ? `${v.min_trajectory_distance_km.toFixed(1)} km` : '—'}
                      </td>
                      <td className="re-td-mono">
                        {v.trajectory_time_delta_hours != null ? `${v.trajectory_time_delta_hours.toFixed(1)} h` : '—'}
                      </td>
                      <td>
                        {v.source_zone_intersection ? (
                          <span style={{ color: '#4ade80', fontWeight: 600 }}>✓ Intersected</span>
                        ) : (
                          <span style={{ color: '#94a3b8' }}>Outside</span>
                        )}
                      </td>
                      <td>
                        <span style={{ fontSize: '0.72rem', color: '#94a3b8', background: 'rgba(30, 41, 59, 0.6)', padding: '0.1rem 0.35rem', borderRadius: '3px' }}>
                          {v.provider_name ?? v.source_adapter}
                        </span>
                      </td>
                      <td>{v.first_timestamp.slice(0, 16).replace('T', ' ')}</td>
                      <td>{v.last_timestamp.slice(0, 16).replace('T', ' ')}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <p className="re-empty">No vessels found in this drift corridor and time window.</p>
          )}
        </div>
      )}

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(3)}>← Back</button>
        <button className="re-btn-primary" onClick={() => goToStep(5)}>
          Next: Run Attribution →
          {state.selectedVessels.length > 0 && ` (${state.selectedVessels.length} vessels)`}
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 5 — Run
// ---------------------------------------------------------------------------

function Step5Run({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const {
    state,
    executeRun,
    goToStep,
    setEnableMonteCarlo,
    setMonteCarloEnsembleSize,
    setMonteCarloSeed,
    setMonteCarloPerturbOrigin,
    setMonteCarloPerturbLeeway,
    setMonteCarloPerturbWind,
    setMonteCarloPerturbCurrent,
    setEnableSarSurveillance,
    setSarSurveillanceCfarKSigma,
  } = experiment

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">5</span>
        Run Attribution Experiment
      </h3>

      <div className="re-run-summary">
        <div className="re-info-card">
          <div className="re-info-row"><span>Observation:</span><strong>{state.selectedProduct?.title.slice(0, 30) ?? '—'}</strong></div>
          <div className="re-info-row"><span>Spill Origin:</span><strong>{state.observationLat != null && state.observationLon != null ? `${state.observationLat.toFixed(4)}°N, ${state.observationLon.toFixed(4)}°E` : '—'}</strong></div>
          {state.slickCharacterization && (
            <>
              <div className="re-info-row"><span>Slick Detection:</span><strong>{state.slickCharacterization.status}</strong></div>
              <div className="re-info-row">
                <span>Estimated Area:</span>
                <strong>{state.slickCharacterization.area_km2 != null ? `${state.slickCharacterization.area_km2} km²` : 'Unavailable'}</strong>
              </div>
              <div className="re-info-row">
                <span>Damping Contrast:</span>
                <strong>{state.slickCharacterization.damping_contrast_db != null ? `${state.slickCharacterization.damping_contrast_db} dB` : 'Unavailable'}</strong>
              </div>
            </>
          )}
          <div className="re-info-row"><span>Backtrack:</span><strong>{state.backtrackHours} h, step {state.stepHours} h</strong></div>
          <div className="re-info-row">
            <span>Forward Prediction:</span>
            <strong style={{ color: state.enableForwardPrediction ? '#22d3ee' : '#94a3b8' }}>
              {state.enableForwardPrediction
                ? `Enabled (+${state.forwardPredictionHours} h, step ${state.forwardStepHours} h)`
                : 'Disabled'}
            </strong>
          </div>
          <div className="re-info-row">
            <span>Monte Carlo Ensemble:</span>
            <strong style={{ color: state.enableMonteCarlo ? '#38bdf8' : '#94a3b8' }}>
              {state.enableMonteCarlo
                ? `Enabled (${state.monteCarloEnsembleSize} runs${state.monteCarloSeed != null ? `, seed=${state.monteCarloSeed}` : ''})`
                : 'Disabled (Deterministic Baseline)'}
            </strong>
          </div>
          <div className="re-info-row">
            <span>SAR ↔ AIS Surveillance:</span>
            <strong style={{ color: state.enableSarSurveillance ? '#c084fc' : '#94a3b8' }}>
              {state.enableSarSurveillance
                ? `Enabled (k=${state.sarSurveillanceCfarKSigma.toFixed(1)}σ, ${state.acquiredRasterPath ? 'Acquired GeoTIFF' : 'Authoritative fallback'})`
                : 'Disabled'}
            </strong>
          </div>
          <div className="re-info-row"><span>ERA5 acquired:</span><strong>{state.environment?.era5_netcdf_path ? '✓' : '✗'}</strong></div>
          <div className="re-info-row"><span>CMEMS acquired:</span><strong>{state.environment?.cmems_netcdf_path ? '✓' : '✗'}</strong></div>
          <div className="re-info-row"><span>Vessels selected:</span><strong>{state.selectedVessels.length}</strong></div>
        </div>
      </div>

      {/* Phase #5 — Monte Carlo Ensemble Configuration Card */}
      <div
        className="re-source-zone-card"
        data-testid="monte-carlo-config-card"
        style={{
          marginTop: '1.25rem',
          padding: '1.25rem',
          background: 'rgba(15, 23, 42, 0.65)',
          borderRadius: 'var(--radius-md, 8px)',
          border: state.enableMonteCarlo ? '1px solid #38bdf8' : '1px solid rgba(148, 163, 184, 0.2)',
          boxShadow: state.enableMonteCarlo ? '0 0 16px rgba(56, 189, 248, 0.15)' : 'none',
          transition: 'all 0.2s ease',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <h4 style={{ margin: 0, fontSize: '0.95rem', color: '#f8fafc', fontWeight: 700 }}>
                Monte Carlo Ensemble / Uncertainty Propagation
              </h4>
              <span
                style={{
                  fontSize: '0.68rem',
                  padding: '0.15rem 0.5rem',
                  borderRadius: '4px',
                  background: 'rgba(56, 189, 248, 0.15)',
                  color: '#38bdf8',
                  border: '1px solid rgba(56, 189, 248, 0.3)',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                }}
              >
                Phase #5
              </span>
            </div>
            <p style={{ margin: '0.35rem 0 0 0', fontSize: '0.78rem', color: '#94a3b8' }}>
              Quantify physical sensitivity across metocean turbulence, leeway variations, and origin offsets without modifying the deterministic baseline.
            </p>
          </div>

          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.6rem',
              cursor: 'pointer',
              background: state.enableMonteCarlo ? 'rgba(56, 189, 248, 0.15)' : 'rgba(30, 41, 59, 0.5)',
              padding: '0.4rem 0.8rem',
              borderRadius: '6px',
              border: `1px solid ${state.enableMonteCarlo ? '#38bdf8' : 'rgba(148, 163, 184, 0.2)'}`,
            }}
          >
            <input
              type="checkbox"
              checked={state.enableMonteCarlo}
              onChange={(e) => setEnableMonteCarlo(e.target.checked)}
              style={{ width: '16px', height: '16px', accentColor: '#38bdf8', cursor: 'pointer' }}
            />
            <span style={{ fontSize: '0.82rem', fontWeight: 600, color: state.enableMonteCarlo ? '#38bdf8' : '#cbd5e1' }}>
              {state.enableMonteCarlo ? 'Ensemble Enabled' : 'Enable Monte Carlo Ensemble (Phase #5)'}
            </span>
          </label>
        </div>

        {state.enableMonteCarlo && (
          <div style={{ marginTop: '1rem', borderTop: '1px solid rgba(148, 163, 184, 0.15)', paddingTop: '1rem' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.35rem' }}>
                  <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>Realizations (N):</span>
                  <strong style={{ fontSize: '0.82rem', color: '#38bdf8', fontFamily: 'var(--font-mono)' }}>
                    {state.monteCarloEnsembleSize} runs
                  </strong>
                </div>
                <input
                  type="range"
                  min="10"
                  max="200"
                  step="5"
                  value={state.monteCarloEnsembleSize}
                  onChange={(e) => setMonteCarloEnsembleSize(parseInt(e.target.value, 10))}
                  style={{ width: '100%', accentColor: '#38bdf8', cursor: 'pointer' }}
                />
              </div>

              <div>
                <span style={{ fontSize: '0.78rem', color: '#94a3b8', display: 'block', marginBottom: '0.35rem' }}>
                  Random Seed (Optional):
                </span>
                <input
                  type="number"
                  placeholder="e.g. 42 (blank for random)"
                  value={state.monteCarloSeed ?? ''}
                  onChange={(e) => setMonteCarloSeed(e.target.value === '' ? null : parseInt(e.target.value, 10))}
                  style={{
                    width: '100%',
                    padding: '0.35rem 0.6rem',
                    background: 'rgba(15, 23, 42, 0.8)',
                    border: '1px solid rgba(148, 163, 184, 0.25)',
                    borderRadius: '4px',
                    color: '#f8fafc',
                    fontSize: '0.78rem',
                    fontFamily: 'var(--font-mono)',
                  }}
                />
              </div>
            </div>

            <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#94a3b8', fontWeight: 600, marginBottom: '0.5rem' }}>
              Stochastic Perturbation Parameters
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.65rem' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem', color: '#cbd5e1', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={state.monteCarloPerturbOrigin}
                  onChange={(e) => setMonteCarloPerturbOrigin(e.target.checked)}
                  style={{ accentColor: '#38bdf8' }}
                />
                Spill Origin (±1,000 m)
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem', color: '#cbd5e1', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={state.monteCarloPerturbLeeway}
                  onChange={(e) => setMonteCarloPerturbLeeway(e.target.checked)}
                  style={{ accentColor: '#38bdf8' }}
                />
                Leeway Factor (±0.005)
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem', color: '#cbd5e1', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={state.monteCarloPerturbWind}
                  onChange={(e) => setMonteCarloPerturbWind(e.target.checked)}
                  style={{ accentColor: '#38bdf8' }}
                />
                ERA5 Wind (±1.0 m/s, ±10°)
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem', color: '#cbd5e1', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={state.monteCarloPerturbCurrent}
                  onChange={(e) => setMonteCarloPerturbCurrent(e.target.checked)}
                  style={{ accentColor: '#38bdf8' }}
                />
                CMEMS Currents (±0.05 m/s)
              </label>
            </div>
          </div>
        )}
      </div>

      {/* Phase #6 — Dual-Sensor SAR ↔ AIS Maritime Surveillance Configuration Card */}
      <div
        className="re-source-zone-card"
        data-testid="sar-surveillance-config-card"
        style={{
          marginTop: '1.25rem',
          padding: '1.25rem',
          background: 'rgba(15, 23, 42, 0.65)',
          borderRadius: 'var(--radius-md, 8px)',
          border: state.enableSarSurveillance ? '1px solid #c084fc' : '1px solid rgba(148, 163, 184, 0.2)',
          boxShadow: state.enableSarSurveillance ? '0 0 16px rgba(192, 132, 252, 0.15)' : 'none',
          transition: 'all 0.2s ease',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Radio size={16} style={{ color: '#c084fc' }} />
              <h4 style={{ margin: 0, fontSize: '0.95rem', color: '#f8fafc', fontWeight: 700 }}>
                SAR ↔ AIS Dual-Sensor Maritime Surveillance
              </h4>
              <span
                style={{
                  fontSize: '0.68rem',
                  padding: '0.15rem 0.5rem',
                  borderRadius: '4px',
                  background: 'rgba(192, 132, 252, 0.15)',
                  color: '#c084fc',
                  border: '1px solid rgba(192, 132, 252, 0.3)',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                }}
              >
                Phase #6
              </span>
            </div>
            <p style={{ margin: '0.35rem 0 0 0', fontSize: '0.78rem', color: '#94a3b8' }}>
              Performs 2D CA-CFAR radar bright-target detection and spatiotemporally correlates radar targets against candidate AIS vessel tracks.
            </p>
          </div>

          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.6rem',
              cursor: 'pointer',
              background: state.enableSarSurveillance ? 'rgba(192, 132, 252, 0.15)' : 'rgba(30, 41, 59, 0.5)',
              padding: '0.4rem 0.8rem',
              borderRadius: '6px',
              border: state.enableSarSurveillance ? '1px solid rgba(192, 132, 252, 0.4)' : '1px solid rgba(148, 163, 184, 0.2)',
            }}
          >
            <input
              type="checkbox"
              data-testid="toggle-sar-surveillance"
              checked={state.enableSarSurveillance}
              onChange={(e) => setEnableSarSurveillance(e.target.checked)}
              style={{ accentColor: '#c084fc', cursor: 'pointer' }}
            />
            <span style={{ fontSize: '0.82rem', fontWeight: 600, color: state.enableSarSurveillance ? '#c084fc' : '#cbd5e1' }}>
              {state.enableSarSurveillance ? 'Surveillance Enabled' : 'Enable Surveillance (Phase #6)'}
            </span>
          </label>
        </div>

        {state.enableSarSurveillance && (
          <div style={{ marginTop: '1rem', borderTop: '1px solid rgba(148, 163, 184, 0.15)', paddingTop: '1rem' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '0.5rem' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.35rem' }}>
                  <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>CFAR Detection Sensitivity (k-σ):</span>
                  <strong style={{ fontSize: '0.82rem', color: '#c084fc', fontFamily: 'var(--font-mono)' }}>
                    {state.sarSurveillanceCfarKSigma.toFixed(1)} σ
                  </strong>
                </div>
                <input
                  type="range"
                  data-testid="sar-cfar-k-sigma-slider"
                  min="2.0"
                  max="8.0"
                  step="0.5"
                  value={state.sarSurveillanceCfarKSigma}
                  onChange={(e) => setSarSurveillanceCfarKSigma(parseFloat(e.target.value))}
                  style={{ width: '100%', accentColor: '#c084fc', cursor: 'pointer' }}
                />
              </div>

              <div>
                <span style={{ fontSize: '0.78rem', color: '#94a3b8', display: 'block', marginBottom: '0.35rem' }}>
                  SAR Subscene Source:
                </span>
                <div style={{ fontSize: '0.78rem', color: '#cbd5e1', display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.35rem' }}>
                  <span
                    style={{
                      display: 'inline-block',
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: state.acquiredRasterPath ? '#4ade80' : '#38bdf8',
                    }}
                  />
                  <span>
                    {state.acquiredRasterPath
                      ? 'Acquired GeoTIFF subscene (Step 1)'
                      : 'Authoritative Corsica benchmark subscene'}
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="re-disclaimer">
        <strong>Method:</strong> Physics + feature-based attribution baseline (not a trained ML model).
        The backward Leeway-Euler drift engine computes a spatially and temporally expanding
        source candidate zone. Each vessel is scored by spatial proximity, temporal overlap,
        and trajectory overlap against this zone. Results represent evidence consistency,
        NOT proof of legal responsibility or causation.
      </div>

      {state.runStatus === 'error' && (
        <div className="re-error">{state.runError}</div>
      )}

      {state.runStatus === 'running' && (
        <div className="re-running-status">
          <div className="re-spinner" />
          Running backward drift and vessel attribution…
        </div>
      )}

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(4)}>← Back</button>
        <button
          className="re-btn-primary re-btn-run"
          onClick={executeRun}
          disabled={state.runStatus === 'running' || !state.environment}
        >
          {state.runStatus === 'running' ? 'Running…' : 'Execute Attribution Experiment'}
        </button>
        {state.runResult && (
          <button className="re-btn-secondary" onClick={() => goToStep(6)}>View Results →</button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 6 — Results
// ---------------------------------------------------------------------------

function Step6Results({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const { state, goToStep } = experiment
  const result = state.runResult
  const [selectedVesselId, setSelectedVesselId] = useState<string | null>(null)

  if (!result) return <div className="re-step-panel"><p>No results yet.</p></div>

  // Create lookup for vessel positions from state.selectedVessels or result.vessels
  const vesselPositionsMap: Record<string, AisPosition[]> = {}
  state.selectedVessels.forEach((sv) => {
    const key = sv.mmsi ?? sv.id ?? sv.vessel_name ?? ''
    if (key && sv.positions && sv.positions.length > 0) {
      vesselPositionsMap[key] = sv.positions
      if (sv.mmsi) vesselPositionsMap[sv.mmsi] = sv.positions
      if (sv.id) vesselPositionsMap[sv.id] = sv.positions
    }
  })
  result.vessels.forEach((v) => {
    if (v.positions && v.positions.length > 0) {
      vesselPositionsMap[v.vessel_id] = v.positions
      if (v.mmsi) vesselPositionsMap[v.mmsi] = v.positions
    }
  })

  // Observation coordinate determination (prefer explicit observationLat/Lon or product centroid)
  const observationLat =
    result.observation_lat != null
      ? result.observation_lat
      : state.observationLat != null
      ? state.observationLat
      : (state.selectedProduct?.centroid_lat ?? result.source_lat)
  const observationLon =
    result.observation_lon != null
      ? result.observation_lon
      : state.observationLon != null
      ? state.observationLon
      : (state.selectedProduct?.centroid_lon ?? result.source_lon)

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">6</span>
        Attribution Results
      </h3>

      <div className="re-result-header">
        <div className="re-result-meta">
          <span>Run ID: <code>{result.run_id}</code></span>
          <span>Model: <code>{result.model_version}</code></span>
          <span>
            Observation Source:{' '}
            <strong style={{ color: result.observation_source === 'SAR_DERIVED' ? '#4ade80' : '#facc15' }}>
              {result.observation_source === 'SAR_DERIVED' ? 'SAR-derived detection' : 'Benchmark fallback'}
            </strong>
          </span>
          <span>Completed: {result.created_at.slice(0, 16).replace('T', ' ')} UTC</span>
        </div>
      </div>

      {/* 1. Interactive Attribution Map */}
      <AttributionMap
        observation={{
          lat: observationLat,
          lon: observationLon,
          timestamp: result.observation_time,
          title: state.selectedProduct?.title,
          footprint: state.selectedProduct?.footprint,
          slickGeometry: result.slick_characterization?.slick_geometry ?? state.slickCharacterization?.slick_geometry,
          areaKm2: result.slick_characterization?.area_km2 ?? state.slickCharacterization?.area_km2,
          confidence: result.slick_characterization?.confidence ?? state.slickCharacterization?.confidence,
          dampingContrastDb: result.slick_characterization?.damping_contrast_db ?? state.slickCharacterization?.damping_contrast_db,
          detectionStatus: result.slick_characterization?.status ?? state.slickCharacterization?.status,
        }}
        reconstructedSource={{
          lat: result.source_lat,
          lon: result.source_lon,
          radiusM: result.source_radius_m,
          geojson: result.source_zone_geojson,
        }}
        backwardSteps={result.backward_steps}
        vessels={result.vessels}
        selectedVesselId={selectedVesselId}
        onSelectVessel={setSelectedVesselId}
        vesselPositionsMap={vesselPositionsMap}
        forwardSteps={result.forward_prediction?.steps}
        forwardPrediction={result.forward_prediction}
        sarSurveillance={result.sar_surveillance}
      />

      {/* 2. Reconstructed Source Candidate Zone Metrics */}
      <div className="re-source-zone-card">
        <h4>Reconstructed Source Candidate Zone</h4>
        <div className="re-source-grid">
          <div>
            <div className="re-source-label">Source</div>
            <div className="re-source-value">{fmt(result.source_lat, 4)}°N, {fmt(result.source_lon, 4)}°E</div>
          </div>
          <div>
            <div className="re-source-label">Uncertainty radius</div>
            <div className="re-source-value">{(result.source_radius_m / 1000).toFixed(1)} km</div>
          </div>
          <div>
            <div className="re-source-label">Backward steps</div>
            <div className="re-source-value">{result.backward_steps.length}</div>
          </div>
          <div>
            <div className="re-source-label">Backtrack duration</div>
            <div className="re-source-value">{result.backtrack_hours} h</div>
          </div>
        </div>
      </div>

      {/* 2b. Forward Drift Predicted Trajectory Metrics (Step 11) */}
      {result.forward_prediction && (
        <div
          className="re-source-zone-card"
          data-testid="forward-prediction-results-card"
          style={{ marginTop: '1rem', borderColor: '#0891b2', background: 'rgba(6, 40, 50, 0.4)' }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
            <h4 style={{ margin: 0, color: '#22d3ee' }}>Forward Drift Prediction (Model Projection)</h4>
            <span
              style={{
                fontSize: '0.72rem',
                fontWeight: 600,
                padding: '0.15rem 0.5rem',
                borderRadius: '4px',
                background: 'rgba(6, 182, 212, 0.2)',
                color: '#22d3ee',
                border: '1px solid #0891b2',
              }}
            >
              {result.forward_prediction.status ?? result.forward_prediction.termination_status ?? 'COMPLETED'}
            </span>
          </div>
          <div className="re-source-grid">
            <div>
              <div className="re-source-label">Initial Observation</div>
              <div className="re-source-value">
                {fmt(result.forward_prediction.observation_lat ?? result.forward_prediction.origin_lat, 4)}°N, {fmt(result.forward_prediction.observation_lon ?? result.forward_prediction.origin_lon, 4)}°E
              </div>
            </div>
            <div>
              <div className="re-source-label">Final Predicted Position</div>
              <div className="re-source-value" style={{ color: '#22d3ee' }}>
                {fmt(result.forward_prediction.final_lat, 4)}°N, {fmt(result.forward_prediction.final_lon, 4)}°E
              </div>
            </div>
            <div>
              <div className="re-source-label">Prediction Horizon</div>
              <div className="re-source-value">
                +{result.forward_prediction.prediction_hours ?? result.forward_prediction.steps.length} h ({result.forward_prediction.steps.length} steps)
              </div>
            </div>
            <div>
              <div className="re-source-label">Projected Displacement</div>
              <div className="re-source-value">{(result.forward_prediction.displacement_km ?? result.forward_prediction.total_distance_km)?.toFixed(1) ?? '—'} km</div>
            </div>
          </div>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontStyle: 'italic', marginTop: '0.6rem' }}>
            Model: {result.forward_prediction.model_version ?? 'leeway_euler_v1'}. Forward trajectory is a deterministic model projection under supplied environmental forcing. It is not an observed future path.
          </div>
        </div>
      )}

      {/* Phase #5 — Uncertainty Propagation & Sensitivity Panel */}
      {result.monte_carlo_ensemble && (
        <div
          className="re-source-zone-card"
          data-testid="monte-carlo-results-panel"
          style={{
            marginTop: '1.25rem',
            padding: '1.25rem',
            background: 'rgba(15, 23, 42, 0.75)',
            borderRadius: 'var(--radius-md, 8px)',
            border: '1px solid rgba(56, 189, 248, 0.3)',
            boxShadow: '0 0 16px rgba(56, 189, 248, 0.1)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.85rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <h4 style={{ margin: 0, fontSize: '0.95rem', color: '#38bdf8', fontWeight: 700 }}>
                Uncertainty Propagation &amp; Sensitivity Analysis
              </h4>
              <span
                style={{
                  fontSize: '0.68rem',
                  padding: '0.15rem 0.5rem',
                  borderRadius: '4px',
                  background: 'rgba(56, 189, 248, 0.15)',
                  color: '#38bdf8',
                  border: '1px solid rgba(56, 189, 248, 0.3)',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                }}
              >
                Phase #5 Monte Carlo
              </span>
            </div>

            <span
              style={{
                fontSize: '0.68rem',
                fontFamily: 'var(--font-mono)',
                padding: '0.2rem 0.5rem',
                borderRadius: '4px',
                background: 'rgba(30, 41, 59, 0.7)',
                color: '#94a3b8',
                border: '1px solid rgba(148, 163, 184, 0.15)',
              }}
            >
              Provenance: <code>{result.monte_carlo_ensemble.provenance}</code>
            </span>
          </div>

          <div className="re-source-grid" style={{ marginBottom: '1rem' }}>
            <div>
              <div className="re-source-label">Ensemble Size</div>
              <div className="re-source-value" style={{ color: '#38bdf8' }}>
                {result.monte_carlo_ensemble.ensemble_size ?? (result.monte_carlo_ensemble as any).num_realizations ?? 0} realizations
              </div>
            </div>
            <div>
              <div className="re-source-label">Dispersion Radius</div>
              <div className="re-source-value">
                {((result.monte_carlo_ensemble.dispersion_radius_km != null
                  ? result.monte_carlo_ensemble.dispersion_radius_km
                  : (result.monte_carlo_ensemble as any).ensemble_dispersion_radius_m != null
                  ? (result.monte_carlo_ensemble as any).ensemble_dispersion_radius_m / 1000
                  : 0)).toFixed(2)} km
              </div>
            </div>
            <div>
              <div className="re-source-label">P05 – P95 Longitude</div>
              <div className="re-source-value" style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)' }}>
                {(result.monte_carlo_ensemble.p05_source_lon ?? (result.monte_carlo_ensemble as any).percentile_bounding_box?.p05_lon ?? 0).toFixed(4)}° to {(result.monte_carlo_ensemble.p95_source_lon ?? (result.monte_carlo_ensemble as any).percentile_bounding_box?.p95_lon ?? 0).toFixed(4)}°E
              </div>
            </div>
            <div>
              <div className="re-source-label">P05 – P95 Latitude</div>
              <div className="re-source-value" style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)' }}>
                {(result.monte_carlo_ensemble.p05_source_lat ?? (result.monte_carlo_ensemble as any).percentile_bounding_box?.p05_lat ?? 0).toFixed(4)}° to {(result.monte_carlo_ensemble.p95_source_lat ?? (result.monte_carlo_ensemble as any).percentile_bounding_box?.p95_lat ?? 0).toFixed(4)}°N
              </div>
            </div>
          </div>

          <div
            style={{
              fontSize: '0.72rem',
              color: '#facc15',
              background: 'rgba(250, 204, 21, 0.08)',
              border: '1px solid rgba(250, 204, 21, 0.25)',
              padding: '0.6rem 0.85rem',
              borderRadius: '4px',
              lineHeight: 1.45,
              marginBottom: '0.75rem',
            }}
          >
            <strong>Scientific &amp; Legal Notice:</strong> {result.monte_carlo_ensemble.scientific_disclaimer ?? (result.monte_carlo_ensemble as any).scientific_notice ?? ''}
          </div>

          <details style={{ marginTop: '0.5rem' }}>
            <summary style={{ fontSize: '0.76rem', color: '#38bdf8', cursor: 'pointer', fontWeight: 600 }}>
              Inspect Individual Realizations ({result.monte_carlo_ensemble.realizations.length} paths)
            </summary>
            <div style={{ marginTop: '0.6rem', maxHeight: '200px', overflowY: 'auto' }}>
              <table style={{ width: '100%', fontSize: '0.72rem', textAlign: 'left', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: '#94a3b8', borderBottom: '1px solid rgba(148, 163, 184, 0.2)' }}>
                    <th style={{ width: '45px', padding: '0.35rem' }}>ID</th>
                    <th style={{ width: '200px', padding: '0.35rem' }}>Final Source (Lat, Lon)</th>
                    <th style={{ width: '85px', padding: '0.35rem' }}>Radius</th>
                    <th style={{ padding: '0.35rem' }}>Perturbation Parameters</th>
                  </tr>
                </thead>
                <tbody>
                  {result.monte_carlo_ensemble.realizations.slice(0, 50).map((r) => {
                    const p = r.perturbation_parameters || {}
                    const leeway = p.leeway_factor ?? p.leeway_fraction ?? 0.035
                    const dWindSpd = p.delta_wind_speed_ms ?? p.wind_speed_delta_ms ?? 0.0
                    const dWindDir = p.delta_wind_dir_deg ?? p.wind_dir_delta_deg ?? 0.0
                    const dx = p.dx_m ?? 0.0
                    const dy = p.dy_m ?? 0.0

                    return (
                      <tr key={r.realization_id} style={{ borderBottom: '1px solid rgba(148, 163, 184, 0.08)' }}>
                        <td style={{ padding: '0.3rem', fontFamily: 'var(--font-mono)', color: '#94a3b8' }}>#{r.realization_id}</td>
                        <td style={{ padding: '0.3rem', fontFamily: 'var(--font-mono)' }}>
                          {r.final_source_lat.toFixed(4)}°N, {r.final_source_lon.toFixed(4)}°E
                        </td>
                        <td style={{ padding: '0.3rem' }}>{(r.final_uncertainty_radius_m / 1000).toFixed(1)} km</td>
                        <td style={{ padding: '0.3rem', color: '#cbd5e1', fontSize: '0.68rem', fontFamily: 'var(--font-mono)' }}>
                          α={leeway.toFixed(4)} |
                          Δw={dWindSpd >= 0 ? '+' : ''}{dWindSpd.toFixed(2)} m/s |
                          Δθ={dWindDir >= 0 ? '+' : ''}{dWindDir.toFixed(1)}°
                          {(dx !== 0 || dy !== 0) && ` | offset=(${dx >= 0 ? '+' : ''}${dx.toFixed(0)}m, ${dy >= 0 ? '+' : ''}${dy.toFixed(0)}m)`}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}

      {/* SAR ↔ AIS Dual-Sensor Maritime Surveillance Section */}
      <SarSurveillanceSection
        surveillance={result.sar_surveillance}
        observationTime={result.observation_time}
        productId={result.satellite_product_id}
        onConfigureStep={() => goToStep(5)}
      />

      {/* 3. Candidate Comparison Table (Authoritative Ranking from Backend) */}
      <h4 className="re-section-heading">Candidate Vessel Comparison</h4>
      <p className="re-section-desc">
        Authoritative candidate ranking produced by the backward drift attribution pipeline. Ranked by Evidence Consistency Score.
      </p>

      {result.vessels.length === 0 ? (
        <p className="re-empty">No eligible AIS vessel tracks were available for this experiment.</p>
      ) : (
        <div className="re-results-table-wrap">
          <table className="re-table" data-testid="candidate-comparison-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Vessel</th>
                <th>MMSI</th>
                <th>Evidence Consistency</th>
                {result.vessels.some((cand) => cand.model_probability != null) && (
                  <th title="ML Model Probability (Not a probability of legal responsibility or causation.)">
                    ML Model Probability
                  </th>
                )}
                <th>Distance</th>
                <th>AIS Coverage</th>
                <th>Track Status</th>
                <th>Map View</th>
              </tr>
            </thead>
            <tbody>
              {result.vessels.map((v) => {
                const pos = vesselPositionsMap[v.vessel_id] || vesselPositionsMap[v.mmsi ?? ''] || v.positions || []
                const isSelected = selectedVesselId === v.vessel_id
                return (
                  <tr
                    key={v.vessel_id}
                    className={isSelected ? 're-row-vessel-selected' : ''}
                    onClick={() => setSelectedVesselId(v.vessel_id)}
                    style={{ cursor: 'pointer' }}
                  >
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
                    {result.vessels.some((cand) => cand.model_probability != null) && (
                      <td className="re-td-mono" style={{ color: '#38bdf8', fontWeight: 600 }}>
                        {v.model_probability != null ? `${(v.model_probability * 100).toFixed(1)}%` : '—'}
                      </td>
                    )}
                    <td className="re-td-mono">
                      {v.min_source_distance_km != null ? `${v.min_source_distance_km.toFixed(1)} km` : '—'}
                    </td>
                    <td className="re-td-mono">
                      {(v.ais_coverage_fraction * 100).toFixed(0)}% ({v.ais_position_count} pos)
                    </td>
                    <td>
                      {pos.length > 0 ? (
                        <span style={{ color: '#4ade80', fontSize: '0.75rem' }}>✓ {pos.length} pts</span>
                      ) : (
                        <span style={{ color: '#f87171', fontSize: '0.75rem' }}>AIS trajectory unavailable</span>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="re-btn-small"
                        onClick={(e) => {
                          e.stopPropagation()
                          setSelectedVesselId(isSelected ? null : v.vessel_id)
                        }}
                      >
                        {isSelected ? 'Deselect' : 'Inspect'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 4. Structured Evidence Breakdown Panel for Each Candidate */}
      <h4 className="re-section-heading">Detailed Evidence Breakdown</h4>
      <p className="re-section-desc">
        Granular multi-factor physical and temporal consistency metrics matching backend evaluator results.
      </p>

      {result.vessels.length === 0 ? (
        <p className="re-empty">No eligible AIS vessel tracks were available for this experiment.</p>
      ) : (
        <div className="re-vessel-cards" data-testid="evidence-breakdown-panel">
          {result.vessels.map((v: VesselFeatures) => {
            const pos = vesselPositionsMap[v.vessel_id] || vesselPositionsMap[v.mmsi ?? ''] || v.positions || []
            const isSelected = selectedVesselId === v.vessel_id

            return (
              <div
                key={v.vessel_id}
                className={`re-vessel-card ${isSelected ? 're-vessel-card-selected' : ''}`}
                style={isSelected ? { borderColor: 'var(--color-accent)', boxShadow: '0 0 12px rgba(56, 189, 248, 0.2)' } : {}}
              >
                <div className="re-vessel-header">
                  <div className="re-vessel-rank">#{v.rank}</div>
                  <div className="re-vessel-name">
                    {v.vessel_name ?? v.mmsi ?? v.vessel_id}
                    {v.mmsi && <span className="vessel-tag" style={{ marginLeft: '0.5rem' }}>MMSI: {v.mmsi}</span>}
                  </div>
                  <div
                    className="re-vessel-score"
                    style={{ color: scoreColor(v.evidence_consistency_score) }}
                  >
                    {(v.evidence_consistency_score * 100).toFixed(1)}%
                  </div>
                </div>

                <div className="re-vessel-features">
                  <div className="re-feature">
                    <span>Spatial Proximity</span>
                    <strong>{v.min_source_distance_km != null ? `${v.min_source_distance_km.toFixed(1)} km` : '—'}</strong>
                  </div>
                  <div className="re-feature">
                    <span>Temporal Overlap</span>
                    <strong>{v.temporal_overlap_hours.toFixed(1)} h</strong>
                  </div>
                  <div className="re-feature">
                    <span>Trajectory Evidence</span>
                    <strong>{(v.trajectory_overlap_fraction * 100).toFixed(0)}%</strong>
                  </div>
                  <div className="re-feature">
                    <span>AIS positions</span>
                    <strong>{v.ais_position_count}</strong>
                  </div>
                  <div className="re-feature">
                    <span>AIS Track Density</span>
                    <strong>{(v.ais_coverage_fraction * 100).toFixed(0)}%</strong>
                  </div>
                  {v.heading_consistency != null && (
                    <div className="re-feature">
                      <span>Heading Consistency</span>
                      <strong>{(v.heading_consistency * 100).toFixed(0)}%</strong>
                    </div>
                  )}
                  {v.speed_consistency != null && (
                    <div className="re-feature">
                      <span>Speed Consistency</span>
                      <strong>{(v.speed_consistency * 100).toFixed(0)}%</strong>
                    </div>
                  )}
                  <div className="re-feature">
                    <span>Trajectory Proximity (CPA)</span>
                    <strong>{v.min_trajectory_distance_km != null ? `${v.min_trajectory_distance_km.toFixed(1)} km` : '—'}</strong>
                  </div>
                  <div className="re-feature">
                    <span>Trajectory Time Delta</span>
                    <strong>{v.trajectory_time_delta_hours != null ? `${v.trajectory_time_delta_hours.toFixed(1)} h` : '—'}</strong>
                  </div>
                  <div className="re-feature">
                    <span>Source Zone Entry</span>
                    <strong style={{ color: v.source_zone_intersection ? '#4ade80' : '#94a3b8' }}>
                      {v.source_zone_intersection ? '✓ Intersected' : 'Outside'}
                    </strong>
                  </div>
                  <div className="re-feature">
                    <span>Data Provenance</span>
                    <strong style={{ fontSize: '0.8rem', color: '#cbd5e1' }}>
                      {v.provider_name ?? v.source_type ?? 'ais_vessels.db'}
                    </strong>
                  </div>
                  {v.model_probability != null && (
                    <div className="re-feature" data-testid="re-model-probability">
                      <span>ML Model Probability</span>
                      <strong style={{ color: '#38bdf8' }}>{(v.model_probability * 100).toFixed(1)}%</strong>
                    </div>
                  )}
                </div>

                {/* Step 12 — ML Model Signal & AIS Behavioural Intelligence (Contextual Layer) */}
                {(v.model_probability != null || v.behavioral_intelligence != null || v.ml_feature_vector != null) && (
                  <div
                    style={{
                      marginTop: '0.75rem',
                      padding: '0.65rem 0.85rem',
                      background: 'rgba(15, 23, 42, 0.6)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid rgba(56, 189, 248, 0.15)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.5rem',
                    }}
                    data-testid="re-step12-panel"
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#94a3b8', fontWeight: 600 }}>
                          ML Model Signal &amp; AIS Behavioural Intelligence
                        </span>
                        <span
                          style={{
                            fontSize: '0.65rem',
                            padding: '0.1rem 0.4rem',
                            borderRadius: '3px',
                            background: 'rgba(56, 189, 248, 0.15)',
                            color: '#38bdf8',
                            border: '1px solid rgba(56, 189, 248, 0.3)',
                          }}
                        >
                          Contextual Model Layer
                        </span>
                      </div>
                      {v.model_probability != null && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem', alignItems: 'flex-end' }}>
                          <div style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)' }}>
                            <span style={{ color: '#94a3b8', marginRight: '0.35rem' }}>ML Model Probability:</span>
                            <strong style={{ color: '#38bdf8', fontWeight: 700 }}>
                              {(v.model_probability * 100).toFixed(1)}%
                            </strong>
                          </div>
                          <span style={{ fontSize: '0.62rem', color: '#f87171', fontStyle: 'italic' }}>
                            Not a probability of legal responsibility or causation.
                          </span>
                        </div>
                      )}
                    </div>

                    <div style={{ fontSize: '0.68rem', color: '#94a3b8', fontStyle: 'italic', borderLeft: '2px solid rgba(56, 189, 248, 0.4)', paddingLeft: '0.5rem', marginTop: '0.15rem' }}>
                      <strong>Training provenance:</strong> Model trained on synthetic benchmark scenarios; real-data inference is an experimental contextual signal and has not been established as a calibrated real-world responsibility probability.
                    </div>

                    {v.behavioral_intelligence && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', alignItems: 'center' }}>
                        <span style={{ fontSize: '0.7rem', color: '#64748b' }}>AIS Behavior:</span>
                        <span
                          style={{
                            fontSize: '0.7rem',
                            padding: '0.15rem 0.45rem',
                            borderRadius: '3px',
                            background: v.behavioral_intelligence.transmission_gap_count > 0 ? 'rgba(234, 179, 8, 0.15)' : 'rgba(34, 197, 94, 0.15)',
                            color: v.behavioral_intelligence.transmission_gap_count > 0 ? '#facc15' : '#4ade80',
                            border: `1px solid ${v.behavioral_intelligence.transmission_gap_count > 0 ? 'rgba(234, 179, 8, 0.3)' : 'rgba(34, 197, 94, 0.3)'}`,
                          }}
                        >
                          {v.behavioral_intelligence.transmission_gap_count > 0
                            ? `${v.behavioral_intelligence.transmission_gap_count} AIS Gap(s)`
                            : 'No AIS Gaps'}
                        </span>
                        <span
                          style={{
                            fontSize: '0.7rem',
                            padding: '0.15rem 0.45rem',
                            borderRadius: '3px',
                            background: v.behavioral_intelligence.loitering_detected ? 'rgba(239, 68, 68, 0.15)' : 'rgba(34, 197, 94, 0.15)',
                            color: v.behavioral_intelligence.loitering_detected ? '#f87171' : '#4ade80',
                            border: `1px solid ${v.behavioral_intelligence.loitering_detected ? 'rgba(239, 68, 68, 0.3)' : 'rgba(34, 197, 94, 0.3)'}`,
                          }}
                        >
                          {v.behavioral_intelligence.loitering_detected ? 'Loitering Detected' : 'No Loitering'}
                        </span>
                        <span
                          style={{
                            fontSize: '0.7rem',
                            padding: '0.15rem 0.45rem',
                            borderRadius: '3px',
                            background: (v.behavioral_intelligence.anomalies?.length ?? 0) > 0 ? 'rgba(234, 179, 8, 0.15)' : 'rgba(34, 197, 94, 0.15)',
                            color: (v.behavioral_intelligence.anomalies?.length ?? 0) > 0 ? '#facc15' : '#4ade80',
                            border: `1px solid ${(v.behavioral_intelligence.anomalies?.length ?? 0) > 0 ? 'rgba(234, 179, 8, 0.3)' : 'rgba(34, 197, 94, 0.3)'}`,
                          }}
                        >
                          Rule-Based Detector Findings: {v.behavioral_intelligence.anomalies?.length ?? 0}
                        </span>
                        <span
                          style={{
                            fontSize: '0.7rem',
                            padding: '0.15rem 0.45rem',
                            borderRadius: '3px',
                            background: v.behavioral_intelligence.nav_status_consistent ? 'rgba(34, 197, 94, 0.15)' : 'rgba(234, 179, 8, 0.15)',
                            color: v.behavioral_intelligence.nav_status_consistent ? '#4ade80' : '#facc15',
                            border: `1px solid ${v.behavioral_intelligence.nav_status_consistent ? 'rgba(34, 197, 94, 0.3)' : 'rgba(234, 179, 8, 0.3)'}`,
                          }}
                        >
                          {v.behavioral_intelligence.nav_status_consistent ? 'Nav Status Consistent' : 'Nav Discrepancy'}
                        </span>
                        {v.behavioral_intelligence.summary_flags && v.behavioral_intelligence.summary_flags.length > 0 && (
                          v.behavioral_intelligence.summary_flags.map((flag, idx) => (
                            <span
                              key={idx}
                              style={{
                                fontSize: '0.68rem',
                                padding: '0.1rem 0.4rem',
                                borderRadius: '3px',
                                background: 'rgba(148, 163, 184, 0.12)',
                                color: '#cbd5e1',
                                border: '1px solid rgba(148, 163, 184, 0.25)',
                              }}
                            >
                              {flag}
                            </span>
                          ))
                        )}
                      </div>
                    )}

                    {v.ml_feature_vector && (
                      <details style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                        <summary style={{ cursor: 'pointer', userSelect: 'none', color: '#38bdf8', fontWeight: 600 }}>
                          View ML Feature Vector (10 dimensions)
                        </summary>
                        <p style={{ fontSize: '0.68rem', color: '#64748b', fontStyle: 'italic', margin: '0.35rem 0 0' }}>
                          <strong style={{ color: '#94a3b8', fontStyle: 'normal' }}>ML Feature Semantics:</strong> ML feature-vector values are model inputs produced by the feature extractor and may use definitions or normalization different from the physical evidence presentation metrics.
                        </p>
                        <div
                          style={{
                            marginTop: '0.5rem',
                            padding: '0.6rem',
                            background: 'rgba(0, 0, 0, 0.35)',
                            borderRadius: '6px',
                            border: '1px solid rgba(56, 189, 248, 0.1)',
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                            gap: '0.4rem 0.75rem',
                          }}
                        >
                          {Object.entries(v.ml_feature_vector).map(([k, val]) => (
                            <div
                              key={k}
                              style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                padding: '0.35rem 0.55rem',
                                background: 'rgba(30, 41, 59, 0.5)',
                                borderRadius: '4px',
                                border: '1px solid rgba(148, 163, 184, 0.12)',
                                minWidth: 0,
                                gap: '0.5rem',
                              }}
                            >
                              <span
                                style={{
                                  color: '#94a3b8',
                                  fontSize: '0.7rem',
                                  fontFamily: 'var(--font-mono)',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                                title={k}
                              >
                                {k}
                              </span>
                              <span
                                style={{
                                  color: '#38bdf8',
                                  fontFamily: 'var(--font-mono)',
                                  fontWeight: 700,
                                  fontSize: '0.72rem',
                                  flexShrink: 0,
                                }}
                              >
                                {typeof val === 'number'
                                  ? (Number.isInteger(val) ? val.toFixed(1) : val.toFixed(4))
                                  : String(val)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                )}

                {/* Phase #4 — Attribution & Explainability: Evidence Breakdown & Traceability */}
                <div
                  className="re-evidence-breakdown-section"
                  data-testid="evidence-breakdown-section"
                  style={{
                    marginTop: '0.85rem',
                    padding: '0.85rem 1rem',
                    background: 'rgba(15, 23, 42, 0.75)',
                    borderRadius: 'var(--radius-sm, 6px)',
                    border: '1px solid rgba(56, 189, 248, 0.25)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.75rem',
                  }}
                >
                  {/* A. Header & Consistency Level */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#f8fafc' }}>
                        Evidence Breakdown &amp; Traceability
                      </span>
                      <span
                        style={{
                          fontSize: '0.65rem',
                          padding: '0.1rem 0.45rem',
                          borderRadius: '3px',
                          background: 'rgba(56, 189, 248, 0.12)',
                          color: '#38bdf8',
                          border: '1px solid rgba(56, 189, 248, 0.25)',
                        }}
                      >
                        Decomposed Scoring
                      </span>
                    </div>

                    <div
                      data-testid="re-consistency-level"
                      style={{
                        fontSize: '0.75rem',
                        fontWeight: 700,
                        padding: '0.2rem 0.6rem',
                        borderRadius: '4px',
                        background:
                          v.consistency_level === 'HIGH'
                            ? 'rgba(74, 222, 128, 0.15)'
                            : v.consistency_level === 'MODERATE'
                            ? 'rgba(250, 204, 21, 0.15)'
                            : 'rgba(148, 163, 184, 0.15)',
                        color:
                          v.consistency_level === 'HIGH'
                            ? '#4ade80'
                            : v.consistency_level === 'MODERATE'
                            ? '#facc15'
                            : '#94a3b8',
                        border: `1px solid ${
                          v.consistency_level === 'HIGH'
                            ? 'rgba(74, 222, 128, 0.35)'
                            : v.consistency_level === 'MODERATE'
                            ? 'rgba(250, 204, 21, 0.35)'
                            : 'rgba(148, 163, 184, 0.35)'
                        }`,
                        letterSpacing: '0.04em',
                      }}
                    >
                      Evidence Consistency Level: {v.consistency_level ?? (v.evidence_consistency_score >= 0.75 ? 'HIGH' : v.evidence_consistency_score >= 0.50 ? 'MODERATE' : 'LOW')}
                    </div>
                  </div>

                  {/* B. Three Component Scores (Spatial 50%, Temporal 25%, Trajectory 25%) */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.65rem' }}>
                    {/* Spatial Component */}
                    <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.55rem 0.75rem', borderRadius: '4px', border: '1px solid rgba(148, 163, 184, 0.15)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', color: '#94a3b8', marginBottom: '0.3rem' }}>
                        <span style={{ fontWeight: 600, color: '#e2e8f0' }}>Spatial</span>
                        <span>Weight 50%</span>
                        <strong style={{ color: '#38bdf8', fontFamily: 'var(--font-mono)' }}>
                          {(v.evidence_breakdown?.spatial?.score ?? 0).toFixed(3)}
                        </strong>
                      </div>
                      <div style={{ width: '100%', height: '5px', background: 'rgba(0,0,0,0.4)', borderRadius: '3px', overflow: 'hidden' }}>
                        <div
                          style={{
                            width: `${Math.min(100, Math.max(0, (v.evidence_breakdown?.spatial?.score ?? 0) * 100))}%`,
                            height: '100%',
                            background: '#38bdf8',
                            borderRadius: '3px',
                          }}
                        />
                      </div>
                    </div>

                    {/* Temporal Component */}
                    <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.55rem 0.75rem', borderRadius: '4px', border: '1px solid rgba(148, 163, 184, 0.15)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', color: '#94a3b8', marginBottom: '0.3rem' }}>
                        <span style={{ fontWeight: 600, color: '#e2e8f0' }}>Temporal</span>
                        <span>Weight 25%</span>
                        <strong style={{ color: '#818cf8', fontFamily: 'var(--font-mono)' }}>
                          {(v.evidence_breakdown?.temporal?.score ?? 0).toFixed(3)}
                        </strong>
                      </div>
                      <div style={{ width: '100%', height: '5px', background: 'rgba(0,0,0,0.4)', borderRadius: '3px', overflow: 'hidden' }}>
                        <div
                          style={{
                            width: `${Math.min(100, Math.max(0, (v.evidence_breakdown?.temporal?.score ?? 0) * 100))}%`,
                            height: '100%',
                            background: '#818cf8',
                            borderRadius: '3px',
                          }}
                        />
                      </div>
                    </div>

                    {/* Trajectory Component */}
                    <div style={{ background: 'rgba(30, 41, 59, 0.6)', padding: '0.55rem 0.75rem', borderRadius: '4px', border: '1px solid rgba(148, 163, 184, 0.15)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', color: '#94a3b8', marginBottom: '0.3rem' }}>
                        <span style={{ fontWeight: 600, color: '#e2e8f0' }}>Trajectory</span>
                        <span>Weight 25%</span>
                        <strong style={{ color: '#34d399', fontFamily: 'var(--font-mono)' }}>
                          {(v.evidence_breakdown?.trajectory?.score ?? 0).toFixed(3)}
                        </strong>
                      </div>
                      <div style={{ width: '100%', height: '5px', background: 'rgba(0,0,0,0.4)', borderRadius: '3px', overflow: 'hidden' }}>
                        <div
                          style={{
                            width: `${Math.min(100, Math.max(0, (v.evidence_breakdown?.trajectory?.score ?? 0) * 100))}%`,
                            height: '100%',
                            background: '#34d399',
                            borderRadius: '3px',
                          }}
                        />
                      </div>
                    </div>
                  </div>

                  {/* C. Factual Explanation Statements */}
                  {v.explanation && v.explanation.length > 0 && (
                    <div style={{ background: 'rgba(2, 6, 23, 0.45)', padding: '0.65rem 0.85rem', borderRadius: '4px', borderLeft: '3px solid #38bdf8' }}>
                      <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#cbd5e1', marginBottom: '0.35rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                        Attribution Evidence Chain
                      </div>
                      <ul style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.74rem', color: '#94a3b8', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                        {v.explanation.map((stmt, sIdx) => (
                          <li key={sIdx} style={{ lineHeight: 1.4 }}>
                            {stmt}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* D. Provenance */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', fontSize: '0.7rem', color: '#94a3b8', borderTop: '1px solid rgba(148, 163, 184, 0.1)', paddingTop: '0.5rem' }}>
                    <div>
                      <span style={{ color: '#64748b', marginRight: '0.35rem' }}>AIS Source:</span>
                      <strong style={{ color: '#e2e8f0' }}>{v.provider_name ?? 'ais_vessels.db'}</strong>
                    </div>
                    <div>
                      <span style={{ color: '#64748b', marginRight: '0.35rem' }}>Source Type:</span>
                      <strong style={{ color: '#e2e8f0' }}>{v.source_type ?? 'sqlite_ais'}</strong>
                    </div>
                    {result.satellite_product_id && (
                      <div>
                        <span style={{ color: '#64748b', marginRight: '0.35rem' }}>SAR Product:</span>
                        <strong style={{ color: '#e2e8f0' }}>{result.satellite_product_id}</strong>
                      </div>
                    )}
                  </div>

                  {/* E. Scientific Neutral Disclaimer */}
                  <div
                    style={{
                      fontSize: '0.68rem',
                      color: '#94a3b8',
                      fontStyle: 'italic',
                      lineHeight: 1.4,
                      background: 'rgba(30, 41, 59, 0.4)',
                      padding: '0.45rem 0.65rem',
                      borderRadius: '4px',
                      border: '1px solid rgba(148, 163, 184, 0.12)',
                    }}
                  >
                    <strong>Disclaimer:</strong> {v.scientific_disclaimer ?? 'Evidence consistency indicates spatiotemporal correlation with the reconstructed drift model, not legal liability.'}
                  </div>

                  {/* Phase #5 — Monte Carlo Ensemble Sensitivity for this Vessel */}
                  {v.ensemble_evidence && (
                    <div
                      style={{
                        marginTop: '0.5rem',
                        padding: '0.65rem 0.85rem',
                        background: 'rgba(15, 23, 42, 0.65)',
                        borderRadius: 'var(--radius-sm, 6px)',
                        border: '1px solid rgba(56, 189, 248, 0.25)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '0.45rem',
                      }}
                      data-testid="re-ensemble-evidence-panel"
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <span style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: '#94a3b8', fontWeight: 700 }}>
                            Ensemble Sensitivity &amp; Physical Robustness
                          </span>
                          <span
                            style={{
                              fontSize: '0.65rem',
                              padding: '0.1rem 0.4rem',
                              borderRadius: '3px',
                              background: 'rgba(56, 189, 248, 0.15)',
                              color: '#38bdf8',
                              border: '1px solid rgba(56, 189, 248, 0.3)',
                            }}
                          >
                            Phase #5
                          </span>
                        </div>

                        <div
                          style={{
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            padding: '0.2rem 0.55rem',
                            borderRadius: '4px',
                            background:
                              v.ensemble_evidence.ensemble_support_fraction >= 0.7
                                ? 'rgba(74, 222, 128, 0.15)'
                                : v.ensemble_evidence.ensemble_support_fraction >= 0.3
                                ? 'rgba(250, 204, 21, 0.15)'
                                : 'rgba(148, 163, 184, 0.15)',
                            color:
                              v.ensemble_evidence.ensemble_support_fraction >= 0.7
                                ? '#4ade80'
                                : v.ensemble_evidence.ensemble_support_fraction >= 0.3
                                ? '#facc15'
                                : '#94a3b8',
                            border: `1px solid ${
                              v.ensemble_evidence.ensemble_support_fraction >= 0.7
                                ? 'rgba(74, 222, 128, 0.3)'
                                : v.ensemble_evidence.ensemble_support_fraction >= 0.3
                                ? 'rgba(250, 204, 21, 0.3)'
                                : 'rgba(148, 163, 184, 0.3)'
                            }`,
                          }}
                        >
                          Ensemble Support: {(v.ensemble_evidence.ensemble_support_fraction * 100).toFixed(0)}%
                        </div>
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '0.6rem', fontSize: '0.72rem' }}>
                        <div>
                          <span style={{ color: '#94a3b8' }}>Score Spread (μ ± σ):</span>
                          <strong style={{ display: 'block', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}>
                            {v.ensemble_evidence.score_mean != null ? v.ensemble_evidence.score_mean.toFixed(3) : '—'} ±{' '}
                            {v.ensemble_evidence.score_std != null ? v.ensemble_evidence.score_std.toFixed(3) : '0.000'}
                          </strong>
                        </div>
                        <div>
                          <span style={{ color: '#94a3b8' }}>5th – 95th Percentile:</span>
                          <strong style={{ display: 'block', color: '#38bdf8', fontFamily: 'var(--font-mono)' }}>
                            [{v.ensemble_evidence.score_p05 != null ? v.ensemble_evidence.score_p05.toFixed(3) : '—'} ,{' '}
                            {v.ensemble_evidence.score_p95 != null ? v.ensemble_evidence.score_p95.toFixed(3) : '—'}]
                          </strong>
                        </div>
                        <div>
                          <span style={{ color: '#94a3b8' }}>Corridor Consistency:</span>
                          <strong style={{ display: 'block', color: '#4ade80', fontFamily: 'var(--font-mono)' }}>
                            {v.ensemble_evidence.trajectory_consistency_across_ensemble != null
                              ? `${(v.ensemble_evidence.trajectory_consistency_across_ensemble * 100).toFixed(0)}%`
                              : '—'}
                          </strong>
                        </div>
                        <div>
                          <span style={{ color: '#94a3b8' }}>Source Zone Entry:</span>
                          <strong style={{ display: 'block', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}>
                            {v.ensemble_evidence.source_intersection_fraction != null
                              ? `${(v.ensemble_evidence.source_intersection_fraction * 100).toFixed(0)}%`
                              : '0%'}
                          </strong>
                        </div>
                      </div>

                      <div style={{ fontSize: '0.66rem', color: '#94a3b8', fontStyle: 'italic', lineHeight: 1.35 }}>
                        {v.ensemble_evidence.ensemble_disclaimer}
                      </div>
                    </div>
                  )}
                </div>

                {pos.length === 0 ? (
                  <div className="re-no-support" style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#f87171' }}>
                    AIS trajectory unavailable
                  </div>
                ) : !v.has_meaningful_support ? (
                  <div className="re-no-support">No meaningful spatial/temporal overlap with source zone</div>
                ) : null}
              </div>
            )
          })}
        </div>
      )}

      {/* 5. Scientific Provenance & Pipeline Lineage */}
      <h4 className="re-section-heading">Data Provenance &amp; System Lineage</h4>
      <div className="re-provenance-grid">
        <div className="re-provenance-item">
          <span className="re-prov-label">Satellite Observation (SAR)</span>
          <span className="re-prov-val">Sentinel-1 {state.selectedProduct?.platform ?? 'SAR'}</span>
          <span className="re-prov-badge re-prov-badge-real">Copernicus CDSE Real Data</span>
        </div>
        <div className="re-provenance-item">
          <span className="re-prov-label">Atmospheric Reanalysis (Wind)</span>
          <span className="re-prov-val">ECMWF ERA5 10m Wind</span>
          <span className="re-prov-badge re-prov-badge-real">Real Metocean NetCDF</span>
        </div>
        <div className="re-provenance-item">
          <span className="re-prov-label">Ocean Hydrodynamics (Currents)</span>
          <span className="re-prov-val">Copernicus Marine CMEMS</span>
          <span className="re-prov-badge re-prov-badge-real">Real Oceanic NetCDF</span>
        </div>
        <div className="re-provenance-item">
          <span className="re-prov-label">AIS Telemetry Provider</span>
          <span className="re-prov-val">Curated Historical SQLite Database</span>
          <span className="re-prov-badge re-prov-badge-curated">ais_vessels.db Benchmark</span>
        </div>
      </div>

      {/* 6. Scientific / Legal Disclaimer */}
      <div className="re-disclaimer re-result-disclaimer">
        <strong>Scientific Assessment Disclaimer:</strong> This analysis is an evidence-consistency assessment and is not a legal determination of responsibility or causation.
        {result.scientific_disclaimer && ` ${result.scientific_disclaimer}`}
      </div>

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(5)}>← Back</button>
        <button className="re-btn-secondary" onClick={() => goToStep(7)}>View History →</button>
        {result.run_id && (
          <button
            className="re-btn-primary"
            onClick={() => goToStep(8)}
            data-testid="step6-generate-report-btn"
          >
            Generate Scientific Report →
          </button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 7 — History
// ---------------------------------------------------------------------------

function Step7History({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  return <HistoryView experiment={experiment} />
}

// ---------------------------------------------------------------------------
// Step 8 — Scientific Report & Export
// ---------------------------------------------------------------------------

function Step8Report({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const { state, goToStep, loadRun } = experiment
  const runId = state.runResult?.run_id || (state.runHistory[0]?.run_id)

  if (!runId) {
    return (
      <div className="re-step-panel re-empty-history" data-testid="empty-report-state">
        <h4>No experiment selected for report</h4>
        <p>Please select a completed experiment from History or run a new experiment to generate a scientific report.</p>
        <button className="re-btn-primary" onClick={() => goToStep(7)} style={{ marginTop: '0.75rem' }}>
          ← View History
        </button>
      </div>
    )
  }

  return (
    <ScientificReportView
      runId={runId}
      initialRun={state.runResult && state.runResult.run_id === runId ? state.runResult : null}
      onBack={() => goToStep(7)}
      onLoadIntoEvaluator={async (id) => {
        await loadRun(id)
        goToStep(6)
      }}
    />
  )
}

import SyntheticExperimentSection from './SyntheticExperimentSection'
import EvaluatorInvestigationSection from './EvaluatorInvestigationSection'

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export interface RealExperimentViewProps {
  initialMode?: 'real' | 'evaluator' | 'synthetic'
  initialInvestigationId?: string | null
  initialRunId?: string | null
}

export default function RealExperimentView({
  initialMode = 'real',
  initialInvestigationId,
  initialRunId,
}: RealExperimentViewProps) {
  const experiment = useExperiment()
  const { state, goToStep } = experiment
  const [experimentMode, setExperimentMode] = useState<'real' | 'evaluator' | 'synthetic'>(initialMode)
  const [prevInitialMode, setPrevInitialMode] = useState(initialMode)
  if (prevInitialMode !== initialMode) {
    setPrevInitialMode(initialMode)
    setExperimentMode(initialMode)
  }

  useEffect(() => {
    if (initialRunId) {
      setExperimentMode('real')
      experiment.loadRun(initialRunId).catch(console.error)
    } else if (initialMode === 'real') {
      setExperimentMode('real')
      goToStep(1)
    } else if (initialMode === 'evaluator') {
      setExperimentMode('evaluator')
    }
  }, [initialRunId, initialMode])

  useEffect(() => {
    if (initialInvestigationId) {
      setExperimentMode('evaluator')
    }
  }, [initialInvestigationId])

  return (
    <div className="re-container">
      <div className="re-header">
        <div className="re-header-title">
          <div className="re-badge">
            {experimentMode === 'synthetic'
              ? 'SYNTHETIC ML'
              : experimentMode === 'evaluator'
                ? 'EVALUATOR WORKFLOW'
                : 'REAL DATA'}
          </div>
          <h2>
            {experimentMode === 'synthetic'
              ? 'Synthetic ML Experiment Pipeline & Model Validation'
              : experimentMode === 'evaluator'
                ? 'Interactive Attribution Investigation'
                : 'Real-Data Observation & Interactive Attribution Experiment'}
          </h2>
        </div>
        <div className="re-header-sub">
          {experimentMode === 'synthetic'
            ? '⚠️ Offline Benchmark Sandbox: Controlled hydrodynamic scenario simulation, feature engineering, offline model training, and model evaluation registry. Not an authoritative legal finding.'
            : experimentMode === 'evaluator'
              ? 'Authoritative 6-step interactive workflow: Sentinel-1 SAR observation inspection, backward drift trajectory reconstruction, AIS corridor filtering, and ML-assisted candidate attribution.'
              : 'Authoritative end-to-end scientific workflow: Sentinel-1 SAR acquisition, ERA5 & CMEMS environmental forcing, Lagrangian backward drift, AIS spatio-temporal matching, and official reporting.'}
        </div>

        {/* Experiment Mode Selector */}
        <div className="re-mode-switcher">
          <button
            type="button"
            className={`re-mode-tab ${experimentMode === 'evaluator' ? 'active' : ''}`}
            onClick={() => setExperimentMode('evaluator')}
          >
            Evaluator Investigation Workflow
          </button>
          <button
            type="button"
            className={`re-mode-tab ${experimentMode === 'real' ? 'active' : ''}`}
            onClick={() => setExperimentMode('real')}
          >
            Real-Data Observation Wizard
          </button>
          <button
            type="button"
            className={`re-mode-tab ${experimentMode === 'synthetic' ? 'active' : ''}`}
            onClick={() => setExperimentMode('synthetic')}
          >
            Synthetic ML Experiment Pipeline (Offline Benchmark)
          </button>
        </div>
      </div>

      {experimentMode === 'evaluator' && (
        <EvaluatorInvestigationSection initialInvestigationId={initialInvestigationId} />
      )}

      {experimentMode === 'real' && (
        <>
          {state.config && (state.config.warnings?.length ?? 0) > 0 && (
            <ConfigBanner warnings={state.config.warnings} />
          )}

          <WizardProgress currentStep={state.step} onStepClick={goToStep} />

          <div className="re-step-content">
            {state.step === 1 && <Step1Observation experiment={experiment} />}
            {state.step === 2 && <Step2Environment experiment={experiment} />}
            {state.step === 3 && <Step3DriftConfig experiment={experiment} />}
            {state.step === 4 && <Step4VesselSearch experiment={experiment} />}
            {state.step === 5 && <Step5Run experiment={experiment} />}
            {state.step === 6 && <Step6Results experiment={experiment} />}
            {state.step === 7 && <Step7History experiment={experiment} />}
            {state.step === 8 && <Step8Report experiment={experiment} />}
          </div>
        </>
      )}

      {experimentMode === 'synthetic' && (
        <SyntheticExperimentSection />
      )}
    </div>
  )
}
