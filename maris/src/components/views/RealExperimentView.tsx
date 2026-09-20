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

import React, { useState } from 'react'
import { useExperiment } from '../../real-experiment/useExperiment'
import type { SentinelProduct, VesselFeatures } from '../../real-experiment/experimentTypes'

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
  }
  return labels[step] ?? `Step ${step}`
}

// ---------------------------------------------------------------------------
// Wizard Progress Bar
// ---------------------------------------------------------------------------

function WizardProgress({ currentStep }: { currentStep: number }) {
  return (
    <div className="re-wizard-progress">
      {[1, 2, 3, 4, 5, 6, 7].map(s => (
        <div
          key={s}
          className={`re-wizard-step ${s === currentStep ? 're-active' : ''} ${s < currentStep ? 're-done' : ''}`}
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
// Step 1 — Observation Selection
// ---------------------------------------------------------------------------

function Step1Observation({
  experiment,
}: {
  experiment: ReturnType<typeof useExperiment>
}) {
  const { state, setSearchBbox, setSearchDates, searchProducts, selectProduct, goToStep } = experiment

  const [west, setWest] = useState('-10')
  const [south, setSouth] = useState('35')
  const [east, setEast] = useState('5')
  const [north, setNorth] = useState('45')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState('2024-01-07')
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

      <button
        className="re-btn-primary"
        onClick={handleSearch}
        disabled={searching || !state.config?.sentinel1_configured}
      >
        {searching ? 'Searching…' : 'Search CDSE Catalogue'}
      </button>

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

      <div className="re-field-grid">
        <label className="re-label">
          Backtrack Duration (hours)
          <input
            className="re-input"
            type="range"
            min={1} max={72} step={1}
            value={state.backtrackHours}
            onChange={e => setBacktrackHours(Number(e.target.value))}
          />
          <span className="re-range-value">{state.backtrackHours} h</span>
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
  const { state, setStepHours, setSpillArea, goToStep } = experiment

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">3</span>
        Configure Backward Drift
      </h3>
      <p className="re-step-desc">
        The backward drift engine uses time-reversed Leeway-Euler integration
        (v<sub>drift</sub> = v<sub>current</sub> + α·v<sub>wind</sub>, α = 0.035)
        to reconstruct the historical source candidate zone.
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
          <strong>500 m/hour (heuristic search envelope)</strong>
        </div>
      </div>

      <div className="re-field-grid">
        <label className="re-label">
          Integration Step (hours)
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
        </label>
        <label className="re-label">
          Observed Slick Area (m²) — optional
          <input
            className="re-input"
            type="number"
            placeholder="e.g. 500000"
            value={state.spillAreaM2 ?? ''}
            onChange={e => setSpillArea(e.target.value ? Number(e.target.value) : null)}
            min={0}
          />
          <span className="re-field-hint">Used to set initial source radius R₀ = max(√(area/π), 500 m)</span>
        </label>
      </div>

      <div className="re-disclaimer">
        <strong>Scientific note:</strong> The uncertainty envelope R(τ) = R₀ + 500·τ m is a heuristic
        analytical search boundary, NOT a statistically calibrated confidence region.
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
    try {
      await searchVessels({
        west: bbox.west,
        south: bbox.south,
        east: bbox.east,
        north: bbox.north,
        start: startTime.toISOString(),
        end: obsTime.toISOString(),
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
        Select AIS Vessel Tracks
      </h3>
      <p className="re-step-desc">
        Search for vessels whose AIS positions fall within the observation area and time window.
        Select the vessels you want to include in the attribution analysis.
      </p>

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
          {searching ? 'Searching AIS…' : 'Search AIS Tracks'}
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
                            positions: [],
                          })}
                        />
                      </td>
                      <td className="re-td-mono">{v.mmsi ?? '—'}</td>
                      <td>{v.vessel_name ?? '—'}</td>
                      <td>{v.position_count}</td>
                      <td>{v.first_timestamp.slice(0, 16).replace('T', ' ')}</td>
                      <td>{v.last_timestamp.slice(0, 16).replace('T', ' ')}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <p className="re-empty">No vessels found in this area and time window.</p>
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
  const { state, executeRun, goToStep } = experiment

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">5</span>
        Run Attribution Experiment
      </h3>

      <div className="re-run-summary">
        <div className="re-info-card">
          <div className="re-info-row"><span>Observation:</span><strong>{state.selectedProduct?.title.slice(0, 30) ?? '—'}</strong></div>
          <div className="re-info-row"><span>Backtrack:</span><strong>{state.backtrackHours} h, step {state.stepHours} h</strong></div>
          <div className="re-info-row"><span>ERA5 acquired:</span><strong>{state.environment?.era5_netcdf_path ? '✓' : '✗'}</strong></div>
          <div className="re-info-row"><span>CMEMS acquired:</span><strong>{state.environment?.cmems_netcdf_path ? '✓' : '✗'}</strong></div>
          <div className="re-info-row"><span>Vessels selected:</span><strong>{state.selectedVessels.length}</strong></div>
        </div>
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
  if (!result) return <div className="re-step-panel"><p>No results yet.</p></div>

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
          <span>Completed: {result.created_at.slice(0, 16).replace('T', ' ')} UTC</span>
        </div>
      </div>

      <div className="re-source-zone-card">
        <h4>Reconstructed Source Candidate Zone</h4>
        <div className="re-source-grid">
          <div>
            <div className="re-source-label">Estimated source position</div>
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

      <h4 className="re-section-heading">Vessel Evidence Consistency</h4>
      <p className="re-section-desc">
        Ranked by evidence consistency score [0–1]. Higher = more spatially and temporally
        consistent with the reconstructed source zone. This is NOT a probability of guilt.
      </p>

      {result.vessels.length === 0 ? (
        <p className="re-empty">No vessels were included in this experiment.</p>
      ) : (
        <div className="re-vessel-cards">
          {result.vessels.map((v: VesselFeatures) => (
            <div key={v.vessel_id} className="re-vessel-card">
              <div className="re-vessel-header">
                <div className="re-vessel-rank">#{v.rank}</div>
                <div className="re-vessel-name">{v.vessel_name ?? v.mmsi ?? v.vessel_id}</div>
                <div
                  className="re-vessel-score"
                  style={{ color: scoreColor(v.evidence_consistency_score) }}
                >
                  {(v.evidence_consistency_score * 100).toFixed(1)}%
                </div>
              </div>
              <div className="re-vessel-features">
                <div className="re-feature">
                  <span>Min dist to source</span>
                  <strong>{v.min_source_distance_km != null ? `${v.min_source_distance_km.toFixed(1)} km` : '—'}</strong>
                </div>
                <div className="re-feature">
                  <span>Temporal overlap</span>
                  <strong>{v.temporal_overlap_hours.toFixed(1)} h</strong>
                </div>
                <div className="re-feature">
                  <span>Trajectory overlap</span>
                  <strong>{(v.trajectory_overlap_fraction * 100).toFixed(0)}%</strong>
                </div>
                <div className="re-feature">
                  <span>AIS positions</span>
                  <strong>{v.ais_position_count}</strong>
                </div>
                <div className="re-feature">
                  <span>AIS coverage</span>
                  <strong>{(v.ais_coverage_fraction * 100).toFixed(0)}%</strong>
                </div>
                {v.heading_consistency != null && (
                  <div className="re-feature">
                    <span>Heading consistency</span>
                    <strong>{(v.heading_consistency * 100).toFixed(0)}%</strong>
                  </div>
                )}
              </div>
              {!v.has_meaningful_support && (
                <div className="re-no-support">No meaningful spatial/temporal overlap with source zone</div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="re-disclaimer re-result-disclaimer">
        {result.scientific_disclaimer}
      </div>

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(5)}>← Back</button>
        <button className="re-btn-secondary" onClick={() => goToStep(7)}>View History →</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 7 — History
// ---------------------------------------------------------------------------

function Step7History({ experiment }: { experiment: ReturnType<typeof useExperiment> }) {
  const { state, loadRun, goToStep } = experiment
  const [loading, setLoading] = useState<string | null>(null)

  const handleLoad = async (runId: string) => {
    setLoading(runId)
    try {
      await loadRun(runId)
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="re-step-panel">
      <h3 className="re-step-title">
        <span className="re-step-num">7</span>
        Previous Experiment Runs
      </h3>

      {state.runHistory.length === 0 ? (
        <p className="re-empty">No persisted runs yet. Complete an experiment to save results.</p>
      ) : (
        <div className="re-results-table-wrap">
          <table className="re-table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Observation</th>
                <th>Backtrack</th>
                <th>Source Position</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {state.runHistory.map(run => (
                <tr key={run.run_id}>
                  <td className="re-td-mono re-td-truncate" title={run.run_id}>{run.run_id.slice(0, 8)}…</td>
                  <td className="re-td-mono re-td-truncate" title={run.satellite_product_id}>{run.satellite_product_id.slice(0, 20)}</td>
                  <td>{run.backtrack_hours} h</td>
                  <td>{fmt(run.source_lat, 3)}°N, {fmt(run.source_lon, 3)}°E</td>
                  <td>{run.created_at.slice(0, 16).replace('T', ' ')}</td>
                  <td>
                    <button
                      className="re-btn-small"
                      onClick={() => handleLoad(run.run_id)}
                      disabled={loading === run.run_id}
                    >
                      {loading === run.run_id ? '…' : 'Load'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="re-btn-row">
        <button className="re-btn-ghost" onClick={() => goToStep(1)}>← Start New Experiment</button>
        {state.runResult && (
          <button className="re-btn-secondary" onClick={() => goToStep(6)}>Back to Results</button>
        )}
      </div>
    </div>
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
}

export default function RealExperimentView({ initialMode = 'real', initialInvestigationId }: RealExperimentViewProps) {
  const experiment = useExperiment()
  const { state } = experiment
  const [experimentMode, setExperimentMode] = useState<'real' | 'evaluator' | 'synthetic'>(initialMode)
  const [prevInitialMode, setPrevInitialMode] = useState(initialMode)
  if (prevInitialMode !== initialMode) {
    setPrevInitialMode(initialMode)
    setExperimentMode(initialMode)
  }

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
              ? 'Synthetic Scenario & Attribution Pipeline'
              : experimentMode === 'evaluator'
              ? 'Evaluator Attribution Investigation'
              : 'Real-Data Interactive Attribution Experiment'}
          </h2>
        </div>
        <div className="re-header-sub">
          {experimentMode === 'synthetic'
            ? 'Controlled hydrodynamic scenario simulation and attribution model evaluation.'
            : experimentMode === 'evaluator'
            ? 'Sentinel-1 SAR observation analysis, metocean backward drift trajectory, AIS corridor filtering, and calibrated vessel attribution.'
            : 'Backward drift reconstruction using Sentinel-1 SAR observations, ERA5 reanalysis winds, CMEMS surface currents, and AIS vessel tracks.'}
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
            Synthetic ML Experiment Pipeline
          </button>
        </div>
      </div>

      {experimentMode === 'evaluator' && (
        <EvaluatorInvestigationSection initialInvestigationId={initialInvestigationId} />
      )}

      {experimentMode === 'real' && (
        <>
          {state.config && state.config.warnings.length > 0 && (
            <ConfigBanner warnings={state.config.warnings} />
          )}

          <WizardProgress currentStep={state.step} />

          <div className="re-step-content">
            {state.step === 1 && <Step1Observation experiment={experiment} />}
            {state.step === 2 && <Step2Environment experiment={experiment} />}
            {state.step === 3 && <Step3DriftConfig experiment={experiment} />}
            {state.step === 4 && <Step4VesselSearch experiment={experiment} />}
            {state.step === 5 && <Step5Run experiment={experiment} />}
            {state.step === 6 && <Step6Results experiment={experiment} />}
            {state.step === 7 && <Step7History experiment={experiment} />}
          </div>
        </>
      )}

      {experimentMode === 'synthetic' && (
        <SyntheticExperimentSection />
      )}
    </div>
  )
}
