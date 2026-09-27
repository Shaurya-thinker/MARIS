import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, Compass, Database, Satellite, Waves, Wind } from 'lucide-react'
import type { ArtifactSummary } from '../../types/investigationApi'
import type { IncidentData } from '../../types/maris'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import { listExperimentRuns } from '../../real-experiment/experimentApi'
import type { ExperimentRunSummary, SlickCharacterization } from '../../real-experiment/experimentTypes'

interface EvidenceViewProps {
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: SimulationScenario | null
  demoIncident: IncidentData
  artifacts: ArtifactSummary[]
  activeId: string
  onNavigateToView: (view: any) => void
}

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Convert m/s u,v components → { speed_kn, bearing_label } */
function uvToKnots(u: number | null | undefined, v: number | null | undefined): { speed: string; dir: string } | null {
  if (u == null || v == null) return null
  const ms = Math.sqrt(u * u + v * v)
  const kn = ms * 1.94384
  // meteorological convention: direction wind is coming FROM
  const deg = (Math.atan2(-u, -v) * 180) / Math.PI
  const bearing = ((deg % 360) + 360) % 360
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
  const label = dirs[Math.round(bearing / 22.5) % 16]
  return { speed: kn.toFixed(1), dir: label }
}

/** Format ISO date → "DD Mon YYYY · HH:MM UTC" */
function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return 'Unknown'
  try {
    const d = new Date(iso)
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    const dd = String(d.getUTCDate()).padStart(2, '0')
    const hh = String(d.getUTCHours()).padStart(2, '0')
    const mm = String(d.getUTCMinutes()).padStart(2, '0')
    return `${dd} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()} · ${hh}:${mm} UTC`
  } catch {
    return iso
  }
}

export function EvidenceView({
  isDemoMode,
  isSimulationMode,
  simulationScenario,
  demoIncident,
  artifacts,
  activeId,
  onNavigateToView,
}: EvidenceViewProps) {
  const [showAcquisitionDetails, setShowAcquisitionDetails] = useState(false)
  const [showMetoceanDetails, setShowMetoceanDetails] = useState(false)
  const [latestRun, setLatestRun] = useState<ExperimentRunSummary | null>(null)
  const [loadingRun, setLoadingRun] = useState(true)

  // Fetch the most recent authoritative run for live data
  useEffect(() => {
    let mounted = true
    setLoadingRun(true)
    listExperimentRuns(1)
      .then((res) => {
        if (mounted && res?.runs?.length > 0) setLatestRun(res.runs[0])
      })
      .catch(() => {})
      .finally(() => { if (mounted) setLoadingRun(false) })
    return () => { mounted = false }
  }, [])

  // ── Derive display values ──────────────────────────────────────────────────
  const slick: SlickCharacterization | null = latestRun?.slick_characterization ?? null
  const hasRealData = !isDemoMode && !isSimulationMode && latestRun != null

  const currentImage = isDemoMode
    ? demoIncident.satelliteImage
    : isSimulationMode && simulationScenario
      ? simulationScenario.satelliteScene
      : demoIncident.satelliteImage

  const caseName = isDemoMode
    ? 'Corsica 2018 Historical Reconstruction'
    : isSimulationMode && simulationScenario
      ? simulationScenario.name
      : latestRun
        ? `Run ${latestRun.run_id.slice(0, 12)}…`
        : activeId || 'Active Investigation'

  // Satellite metadata — real vs demo fallback
  const platform = isDemoMode ? 'Sentinel-1A' : slick?.platform ?? 'Sentinel-1'
  const polarisation = isDemoMode ? 'VV + VH Co-polar' : slick?.polarisation ?? 'VV + VH'
  const mode = isDemoMode ? 'IW GRDH' : slick?.mode ?? 'IW'
  const productId = isDemoMode
    ? 'S1A_IW_GRDH_1SDV_20181007T052819'
    : latestRun?.satellite_product_id ?? 'COPERNICUS SENTINEL-1 C-BAND SAR'
  const acquisitionUtc = isDemoMode
    ? '08 Oct 2018 · 05:28 UTC'
    : isSimulationMode
      ? '12 Mar 2025 · 04:15 UTC'
      : fmtUtc(slick?.observation_time ?? latestRun?.observation_time)
  const slickAreaKm2 = slick?.area_km2 != null ? slick.area_km2.toFixed(2) : null
  const confidence = slick?.confidence != null ? `${(slick.confidence * 100).toFixed(0)}%` : null
  const dampingDb = slick?.damping_contrast_db != null ? `${slick.damping_contrast_db.toFixed(1)} dB` : null
  const estimatedAgeH = slick?.estimated_age_hours != null ? `~${slick.estimated_age_hours.toFixed(1)} h` : null
  const detectionMethod = slick?.detection_method ?? null

  // Metocean — derived from first backward step with env data
  // (env step is only available in full ExperimentRunResult, not summary;
  //  for the summary view we use the slick's observation coords as a proxy)
  // We show the env step data when it arrives from the full run; for now derive from summary fields
  const windKn = isDemoMode
    ? { speed: '14.2', dir: 'SW' }
    : null  // backward_steps not in summary; shown as N/A until wired to full run

  const currentKn = isDemoMode
    ? { speed: '0.45', dir: 'NE' }
    : null

  // Product ID display (truncated for overlay)
  const productIdShort = productId.length > 40 ? productId.slice(0, 40) + '…' : productId

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Sensor Intelligence</span>
          <h1 className="view-headline">Satellite &amp; Environmental Evidence</h1>
          <p className="view-lead">
            {hasRealData
              ? <>Real Sentinel-1 acquisition metadata, slick characterization, and metocean forcing for <strong>{caseName}</strong>.</>
              : <>High-resolution SAR backscatter scenes, metocean forcing fields, and registered pipeline artifacts for <strong>{caseName}</strong>.</>
            }
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.65rem' }}>
          <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
            <Compass size={14} /> Back to GIS Workspace
          </button>
          <button className="primary-button" type="button" onClick={() => onNavigateToView('evaluator')}>
            <Satellite size={14} /> Inspect in Evaluator →
          </button>
        </div>
      </div>

      {/* Real-data provenance banner */}
      {hasRealData && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.65rem',
          padding: '0.6rem 1rem',
          borderRadius: 'var(--radius-sm)',
          background: 'rgba(0,200,150,0.07)',
          border: '1px solid rgba(0,200,150,0.22)',
          marginBottom: '1.25rem',
          fontSize: '0.8rem',
          color: 'var(--color-accent)',
        }}>
          <Database size={13} />
          <span>
            <strong>Authoritative data</strong> — populated from <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }}>real_experiments.db</code> run <strong>{latestRun!.run_id.slice(0, 16)}</strong>.
            Satellite metadata sourced from Copernicus CDSE; slick characterization from MARIS Stage B1→B3 pipeline.
          </span>
        </div>
      )}

      {/* Dominant Satellite Imagery Section */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="section-title-line">
          <div>
            <span className="section-kicker">Sentinel-1 Synthetic Aperture Radar</span>
            <h2 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, color: '#fff' }}>Satellite Evidence</h2>
          </div>
          <span className="status-badge status-badge--completed">
            {slick?.detected ? 'Slick Detected' : 'Calibrated SAR Scene'}
          </span>
        </div>

        <div className="satellite-frame" style={{ maxHeight: '520px' }}>
          <img
            src={currentImage}
            alt="Sentinel-1 SAR scene visualization"
            className="satellite-evidence"
            style={{ maxHeight: '520px', width: '100%', objectFit: 'cover' }}
          />
          <span className="satellite-overlay-tag">{productIdShort}</span>
        </div>

        {/* 4-card hero metrics */}
        <div className="telemetry-hero-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Sensor Platform</span>
            <span className="telemetry-hero-value" style={{ fontSize: '1.15rem' }}>{platform}</span>
            <span className="telemetry-hero-caption">C-band SAR (5.405 GHz)</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Acquired UTC</span>
            <span className="telemetry-hero-value mono-num" style={{ fontSize: '0.95rem' }}>{acquisitionUtc}</span>
            <span className="telemetry-hero-caption">{mode} ascending orbit pass</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Slick Area</span>
            <span className={`telemetry-hero-value ${slickAreaKm2 ? 'telemetry-hero-value--accent' : ''}`} style={{ fontSize: '1.2rem' }}>
              {slickAreaKm2 ? `${slickAreaKm2} km²` : isDemoMode ? '18.4 km²' : '—'}
            </span>
            <span className="telemetry-hero-caption">
              {confidence ? `${confidence} confidence` : 'Interferometric Wide (IW)'}
            </span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Slick Classification</span>
            <span className="telemetry-hero-value telemetry-hero-value--accent" style={{ fontSize: '1.15rem' }}>
              {slick ? (slick.detected ? 'Confirmed' : 'Not Detected') : 'Confirmed'}
            </span>
            <span className="telemetry-hero-caption">
              {dampingDb ? `Damping contrast ${dampingDb}` : 'High-confidence dark patch'}
            </span>
          </div>
        </div>

        {/* Progressive Disclosure: Acquisition Details */}
        <div className="disclosure-block">
          <button
            type="button"
            className="disclosure-trigger"
            onClick={() => setShowAcquisitionDetails(!showAcquisitionDetails)}
          >
            <span>{showAcquisitionDetails ? 'Hide acquisition details' : 'View acquisition details'}</span>
            {showAcquisitionDetails ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showAcquisitionDetails && (
            <div className="disclosure-content">
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                <div className="metric">
                  <span>Polarisation Channels</span>
                  <strong>{polarisation}</strong>
                </div>
                <div className="metric">
                  <span>Processing Level</span>
                  <strong>Level-1 GRD Calibrated</strong>
                </div>
                <div className="metric">
                  <span>Acquisition Mode</span>
                  <strong>{mode}</strong>
                </div>
                {detectionMethod && (
                  <div className="metric">
                    <span>Detection Method</span>
                    <strong>{detectionMethod}</strong>
                  </div>
                )}
                {estimatedAgeH && (
                  <div className="metric">
                    <span>Estimated Slick Age</span>
                    <strong>{estimatedAgeH}</strong>
                  </div>
                )}
                {slick?.sensor && (
                  <div className="metric">
                    <span>Sensor</span>
                    <strong>{slick.sensor}</strong>
                  </div>
                )}
                {slick?.centroid_lon != null && slick.centroid_lat != null && (
                  <div className="metric">
                    <span>Slick Centroid</span>
                    <strong className="mono-num">
                      {slick.centroid_lon.toFixed(4)}°E, {slick.centroid_lat.toFixed(4)}°N
                    </strong>
                  </div>
                )}
                {slick?.provenance && (
                  <div className="metric">
                    <span>Data Provenance</span>
                    <strong>{slick.provenance}</strong>
                  </div>
                )}
              </div>

              {/* Full product ID */}
              {productId.length > 0 && (
                <div style={{
                  marginTop: '0.75rem',
                  padding: '0.5rem 0.75rem',
                  borderRadius: 'var(--radius-xs)',
                  background: 'var(--color-bg)',
                  border: '1px solid var(--color-border)',
                  fontSize: '0.72rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-text-subtle)',
                  wordBreak: 'break-all',
                }}>
                  <span style={{ color: 'var(--color-accent)', marginRight: '0.5rem' }}>PRODUCT_ID</span>
                  {productId}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* Environmental & Metocean Forcing Summary */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingTop: '1.5rem', borderTop: '1px solid var(--color-border-subtle)' }}>
        <div className="section-title-line">
          <div>
            <span className="section-kicker">Hydrodynamic &amp; Atmospheric Forcing</span>
            <h2 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, color: '#fff' }}>Metocean Environment</h2>
          </div>
          <span className="status-badge status-badge--completed">ERA5 &amp; CMEMS</span>
        </div>

        {/* Note: backward_steps env data available only in full run fetch */}
        {hasRealData && !windKn && (
          <div style={{
            padding: '0.65rem 1rem',
            borderRadius: 'var(--radius-xs)',
            background: 'rgba(255,190,60,0.06)',
            border: '1px solid rgba(255,190,60,0.18)',
            fontSize: '0.78rem',
            color: 'var(--color-text-muted)',
          }}>
            <strong style={{ color: 'rgba(255,190,60,0.9)' }}>Metocean vectors</strong> — ERA5 wind and CMEMS current sample values are stored in the full experiment run record.
            Open this run in the <button type="button" className="inline-link" onClick={() => onNavigateToView('evaluator')} style={{ background: 'none', border: 'none', color: 'var(--color-accent)', cursor: 'pointer', fontSize: '0.78rem', padding: 0, textDecoration: 'underline' }}>Evaluator tab</button> to inspect all metocean forcing per drift step.
          </div>
        )}

        <div className="telemetry-hero-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
          <div className="telemetry-hero-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="telemetry-hero-label">Wind Velocity &amp; Vector</span>
              <Wind size={16} color="var(--color-accent)" />
            </div>
            <span className={`telemetry-hero-value ${windKn ? 'telemetry-hero-value--accent' : ''}`}>
              {windKn ? `${windKn.speed} kn ${windKn.dir}` : isDemoMode ? '14.2 kn SW' : '— (open run in Evaluator)'}
            </span>
            <span className="telemetry-hero-caption">ECMWF ERA5 10m wind reanalysis</span>
          </div>

          <div className="telemetry-hero-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="telemetry-hero-label">Ocean Current Field</span>
              <Waves size={16} color="var(--color-info)" />
            </div>
            <span className={`telemetry-hero-value ${currentKn ? 'telemetry-hero-value--info' : ''}`}>
              {currentKn ? `${currentKn.speed} kn ${currentKn.dir}` : isDemoMode ? '0.45 kn NE' : '— (open run in Evaluator)'}
            </span>
            <span className="telemetry-hero-caption">Copernicus Marine (CMEMS) surface velocity</span>
          </div>
        </div>

        {/* Progressive Disclosure: Metocean Details */}
        <div className="disclosure-block">
          <button
            type="button"
            className="disclosure-trigger"
            onClick={() => setShowMetoceanDetails(!showMetoceanDetails)}
          >
            <span>{showMetoceanDetails ? 'Hide metocean details' : 'View metocean details'}</span>
            {showMetoceanDetails ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showMetoceanDetails && (
            <div className="disclosure-content">
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {isDemoMode ? (
                  <>
                    <div className="metric"><span>Wave Stokes Drift</span><strong>0.12 kn @ 225°</strong></div>
                    <div className="metric"><span>Sea Surface Temp</span><strong>21.8°C (71.2°F)</strong></div>
                    <div className="metric"><span>Significant Wave Height</span><strong>1.4m (Swell 6.2s)</strong></div>
                    <div className="metric"><span>Drift Leeway Factor</span><strong>3.2% Wind + 100% Current</strong></div>
                  </>
                ) : (
                  <>
                    <div className="metric">
                      <span>ERA5 Source</span>
                      <strong>{latestRun?.satellite_product_id ? 'Copernicus CDSE' : '—'}</strong>
                    </div>
                    <div className="metric">
                      <span>CMEMS Source</span>
                      <strong>Copernicus Marine Service</strong>
                    </div>
                    <div className="metric">
                      <span>Drift Backtrack</span>
                      <strong>{latestRun?.backtrack_hours != null ? `${latestRun.backtrack_hours} h` : '—'}</strong>
                    </div>
                    <div className="metric">
                      <span>Model Version</span>
                      <strong className="mono-num">{latestRun?.model_version ?? '—'}</strong>
                    </div>
                    <div className="metric" style={{ gridColumn: '1 / -1' }}>
                      <span>Step-wise env vectors</span>
                      <strong>Available per-step in Evaluator → Drift Trajectory panel</strong>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Registered Pipeline Artifacts */}
      {artifacts.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingTop: '1.5rem', borderTop: '1px solid var(--color-border-subtle)' }}>
          <div className="section-title-line">
            <div>
              <span className="section-kicker">Registered Artifacts ({artifacts.length})</span>
              <h2 style={{ fontSize: '1.2rem', fontWeight: 700, margin: 0, color: '#fff' }}>Asset Registry</h2>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {artifacts.map((art) => (
              <div
                key={art.asset_id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '0.75rem 1rem',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <div>
                  <strong style={{ color: '#fff', fontSize: '0.85rem' }}>{art.asset_type}</strong>
                  <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--color-text-subtle)', fontFamily: 'var(--font-mono)' }}>
                    {art.location}
                  </span>
                </div>
                <span className="vessel-tag">{art.provider}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* No-data placeholder — shown during load or when no runs exist */}
      {!isDemoMode && !isSimulationMode && !loadingRun && latestRun == null && (
        <div style={{
          padding: '2rem 1.5rem',
          borderRadius: 'var(--radius-sm)',
          background: 'var(--color-surface)',
          border: '1px dashed var(--color-border)',
          textAlign: 'center',
          color: 'var(--color-text-muted)',
          fontSize: '0.83rem',
          marginTop: '1.5rem',
        }}>
          <p style={{ margin: '0 0 0.75rem' }}>No authoritative experiment runs found in <code style={{ fontFamily: 'var(--font-mono)' }}>real_experiments.db</code>.</p>
          <button type="button" className="primary-button" onClick={() => onNavigateToView('evaluator')}>
            <Satellite size={14} /> Run an investigation in Evaluator →
          </button>
        </div>
      )}
    </div>
  )
}
