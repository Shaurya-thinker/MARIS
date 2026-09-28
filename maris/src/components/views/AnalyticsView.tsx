import { useEffect, useState, useMemo } from 'react'
import { ChevronDown, ChevronUp, Compass, Navigation, RefreshCw } from 'lucide-react'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import { listExperimentRuns, getExperimentRun } from '../../real-experiment/experimentApi'
import type { ExperimentRunResult } from '../../real-experiment/experimentTypes'

interface AnalyticsViewProps {
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: SimulationScenario | null
  rankingResult: any
  explainabilityReport: any
  onNavigateToView: (view: any) => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Haversine distance in km */
function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLon = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

/** True bearing from (lat1,lon1) → (lat2,lon2) in degrees */
function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const toRad = (x: number) => (x * Math.PI) / 180
  const dLon = toRad(lon2 - lon1)
  const y = Math.sin(dLon) * Math.cos(toRad(lat2))
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon)
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
}

const COMPASS_POINTS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
function bearingLabel(deg: number): string {
  return COMPASS_POINTS[Math.round(((deg % 360) + 360) / 22.5) % 16]
}

/** Project geographic points onto a 2D SVG canvas */
function projectSteps(steps: Array<{ lon: number; lat: number }>, w: number, h: number, pad = 40) {
  if (steps.length < 2) return steps.map(() => ({ x: w / 2, y: h / 2 }))
  const lons = steps.map((s) => s.lon)
  const lats = steps.map((s) => s.lat)
  const minLon = Math.min(...lons), maxLon = Math.max(...lons)
  const minLat = Math.min(...lats), maxLat = Math.max(...lats)
  const spanLon = maxLon - minLon || 0.001
  const spanLat = maxLat - minLat || 0.001
  return steps.map((s) => ({
    x: pad + ((s.lon - minLon) / spanLon) * (w - pad * 2),
    // SVG Y is inverted; higher lat → lower Y
    y: h - pad - ((s.lat - minLat) / spanLat) * (h - pad * 2),
  }))
}

function buildPolyline(pts: Array<{ x: number; y: number }>): string {
  return pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
}

export function AnalyticsView({
  isDemoMode,
  onNavigateToView,
}: AnalyticsViewProps) {
  const [showModelDetails, setShowModelDetails] = useState(false)
  const [run, setRun] = useState<ExperimentRunResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    setLoading(true)
    setError(null)
    // First get the latest run ID from the summary list, then fetch the full record
    listExperimentRuns(1)
      .then((res) => {
        if (!mounted || !res?.runs?.length) {
          if (mounted) setLoading(false)
          return
        }
        const runId = res.runs[0].run_id
        return getExperimentRun(runId)
      })
      .then((fullRun) => {
        if (mounted && fullRun) setRun(fullRun)
      })
      .catch((e) => {
        if (mounted) setError(e?.message ?? 'Failed to load run')
      })
      .finally(() => { if (mounted) setLoading(false) })
    return () => { mounted = false }
  }, [])

  // ── Computed drift analytics from real backward_steps ──────────────────────
  const analytics = useMemo(() => {
    if (!run) return null
    const steps = run.backward_steps as Array<{
      step: number; lon: number; lat: number; timestamp: string | null
      u_wind_ms?: number | null; v_wind_ms?: number | null
      u_current_ms?: number | null; v_current_ms?: number | null
      uncertainty_radius_m?: number
    }>
    if (!steps?.length) return null

    // Displacement: from first step (observation) to last step (origin)
    const first = steps[0]
    const last = steps[steps.length - 1]
    const observationLon = run.observation_lon ?? first.lon
    const observationLat = run.observation_lat ?? first.lat
    const sourceLon = run.source_lon
    const sourceLat = run.source_lat
    const displacementKm = haversineKm(observationLat, observationLon, sourceLat, sourceLon)

    // Total path length along trajectory
    let totalDistanceKm = 0
    for (let i = 1; i < steps.length; i++) {
      totalDistanceKm += haversineKm(steps[i - 1].lat, steps[i - 1].lon, steps[i].lat, steps[i].lon)
    }

    // Mean drift direction: bearing from source (origin) → observation
    const meanBearing = bearingDeg(sourceLat, sourceLon, observationLat, observationLon)

    // Average speed over backtrack
    const avgSpeedKn = run.backtrack_hours > 0
      ? (totalDistanceKm / 1.852) / run.backtrack_hours  // convert km → nm, then nm/h → kn
      : 0

    // Mean ENV forcing from steps that have UV data
    const envSteps = steps.filter((s) => s.u_wind_ms != null && s.u_current_ms != null)
    let meanWindMag = 0, meanCurrentMag = 0
    if (envSteps.length > 0) {
      const windMags = envSteps.map((s) => Math.sqrt((s.u_wind_ms ?? 0) ** 2 + (s.v_wind_ms ?? 0) ** 2))
      const currMags = envSteps.map((s) => Math.sqrt((s.u_current_ms ?? 0) ** 2 + (s.v_current_ms ?? 0) ** 2))
      meanWindMag = windMags.reduce((a, b) => a + b, 0) / windMags.length
      meanCurrentMag = currMags.reduce((a, b) => a + b, 0) / currMags.length
    }
    const totalForcing = meanWindMag + meanCurrentMag
    const windPct = totalForcing > 0 ? Math.round((meanWindMag / totalForcing) * 100) : 28
    const currentPct = 100 - windPct

    // Mean uncertainty radius
    const uncertaintySteps = steps.filter((s) => s.uncertainty_radius_m != null)
    const meanUncertKm = uncertaintySteps.length > 0
      ? (uncertaintySteps.reduce((a, s) => a + (s.uncertainty_radius_m ?? 0), 0) / uncertaintySteps.length) / 1000
      : null

    return {
      steps,
      observationLon,
      observationLat,
      sourceLon,
      sourceLat,
      displacementKm,
      totalDistanceKm,
      meanBearing,
      meanBearingLabel: bearingLabel(meanBearing),
      avgSpeedKn,
      windPct,
      currentPct,
      meanUncertKm,
      stepCount: steps.length,
      backtrackHours: run.backtrack_hours,
      stepHours: run.step_hours,
      modelVersion: run.model_version,
      vessels: run.vessels ?? [],
    }
  }, [run])

  // ── SVG projection ─────────────────────────────────────────────────────────
  const SVG_W = 800, SVG_H = 240
  const projected = useMemo(() => {
    if (!analytics?.steps?.length) return null
    return projectSteps(
      analytics.steps.map((s) => ({ lon: s.lon, lat: s.lat })),
      SVG_W,
      SVG_H,
    )
  }, [analytics])

  const originPt = projected?.[projected.length - 1]
  const observedPt = projected?.[0]

  // ── Demo fallback values ────────────────────────────────────────────────────
  const disp = analytics ? analytics.displacementKm.toFixed(2) : '18.45'
  const totalDist = analytics ? analytics.totalDistanceKm.toFixed(2) : '22.10'
  const speed = analytics ? analytics.avgSpeedKn.toFixed(2) : '0.78'
  const bearing = analytics ? `${Math.round(analytics.meanBearing)}° ${analytics.meanBearingLabel}` : '028° NNE'
  const windR = analytics?.windPct ?? 28
  const currR = analytics?.currentPct ?? 72

  return (
    <div className="view-container">
      {/* Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Attribution Analytics</span>
          <h1 className="view-headline">Scientific Attribution &amp; Drift Dynamics</h1>
          <p className="view-lead">
            Lagrangian hydrodynamic advection modeling, backward trajectory reconstruction, and multi-channel evidence fusion.
            {run && (
              <span className="re-badge" style={{ marginLeft: '0.65rem', fontSize: '0.72rem' }}>
                Run: {run.run_id?.slice(0, 16)} (real_experiments.db)
              </span>
            )}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.65rem' }}>
          <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
            <Compass size={14} /> Back to GIS Workspace
          </button>
          <button className="primary-button" type="button" onClick={() => onNavigateToView('evaluator')}>
            <Navigation size={14} /> Inspect in Evaluator →
          </button>
        </div>
      </div>

      {/* Loading / error */}
      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.82rem', color: 'var(--color-text-subtle)', marginBottom: '1rem' }}>
          <RefreshCw size={14} className="spinner" /> Loading latest experiment run…
        </div>
      )}
      {error && (
        <div style={{ padding: '0.6rem 1rem', borderRadius: 'var(--radius-xs)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.22)', color: 'rgba(239,68,68,0.85)', fontSize: '0.78rem', marginBottom: '1rem' }}>
          {error} — is the backend running?
        </div>
      )}

      {/* Real-data provenance banner */}
      {analytics && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.65rem',
          padding: '0.6rem 1rem',
          borderRadius: 'var(--radius-sm)',
          background: 'rgba(0,200,150,0.07)',
          border: '1px solid rgba(0,200,150,0.22)',
          marginBottom: '1.25rem',
          fontSize: '0.8rem',
          color: 'var(--color-accent)',
        }}>
          <span>
            <strong>Authoritative drift analytics</strong> — computed live from {analytics.stepCount} backward trajectory steps
            ({analytics.backtrackHours}h backtrack, {analytics.stepHours}h step, model {analytics.modelVersion}).
          </span>
        </div>
      )}

      {/* Drift Trajectory Section */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        <div className="section-title-line">
          <div>
            <span className="section-kicker">Lagrangian Advection Simulation</span>
            <h2 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, color: '#fff' }}>Drift Displacement</h2>
          </div>
          <span className="status-badge status-badge--completed">Runge-Kutta 4th Order</span>
        </div>

        {/* SVG Drift Trajectory Canvas */}
        <div style={{
          height: '240px', width: '100%',
          borderRadius: 'var(--radius-md)',
          background: 'linear-gradient(180deg, #091a1f 0%, #051013 100%)',
          border: '1px solid var(--color-border)',
          position: 'relative', overflow: 'hidden',
        }}>
          <svg width="100%" height="100%" viewBox={`0 0 ${SVG_W} ${SVG_H}`} style={{ position: 'absolute', inset: 0 }}>
            <defs>
              <linearGradient id="driftGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.8" />
                <stop offset="50%" stopColor="var(--color-info)" stopOpacity="0.8" />
                <stop offset="100%" stopColor="var(--color-processing)" stopOpacity="0.9" />
              </linearGradient>
              {/* Uncertainty tube gradient */}
              <linearGradient id="uncertGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="rgba(69,194,177,0.15)" />
                <stop offset="100%" stopColor="rgba(69,194,177,0.04)" />
              </linearGradient>
            </defs>

            {/* Grid */}
            {[60, 120, 180].map((y) => <line key={y} x1="0" y1={y} x2={SVG_W} y2={y} stroke="rgba(255,255,255,0.03)" strokeWidth="1" />)}
            {[200, 400, 600].map((x) => <line key={x} x1={x} y1="0" x2={x} y2={SVG_H} stroke="rgba(255,255,255,0.03)" strokeWidth="1" />)}

            {projected && projected.length > 1 ? (
              <>
                {/* Uncertainty envelope — thicker faded path */}
                <path
                  d={buildPolyline(projected)}
                  fill="none"
                  stroke="rgba(69,194,177,0.12)"
                  strokeWidth="16"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {/* Step markers — individual drift integration steps */}
                {projected.map((pt, i) => (
                  i % Math.max(1, Math.floor(projected.length / 12)) === 0 && (
                    <circle key={i} cx={pt.x} cy={pt.y} r="2" fill="rgba(69,194,177,0.35)" />
                  )
                ))}
                {/* Primary trajectory */}
                <path
                  d={buildPolyline(projected)}
                  fill="none"
                  stroke="url(#driftGradient)"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {/* Source (origin) point */}
                {originPt && (
                  <>
                    <circle cx={originPt.x} cy={originPt.y} r="6" fill="var(--color-processing)" />
                    <circle cx={originPt.x} cy={originPt.y} r="10" fill="none" stroke="var(--color-processing)" strokeWidth="1" strokeOpacity="0.4" />
                    <text x={originPt.x} y={originPt.y + 22} fill="var(--color-text-muted)" fontSize="10" fontFamily="var(--font-mono)" textAnchor="middle">
                      Reconstructed Origin
                    </text>
                    <text x={originPt.x} y={originPt.y + 33} fill="var(--color-text-subtle)" fontSize="9" fontFamily="var(--font-mono)" textAnchor="middle">
                      {run?.source_lat?.toFixed(4)}°N, {run?.source_lon?.toFixed(4)}°E
                    </text>
                  </>
                )}
                {/* Observation (spill detected) point */}
                {observedPt && (
                  <>
                    <circle cx={observedPt.x} cy={observedPt.y} r="6" fill="var(--color-accent)" />
                    <circle cx={observedPt.x} cy={observedPt.y} r="10" fill="none" stroke="var(--color-accent)" strokeWidth="1" strokeOpacity="0.4" />
                    <text x={observedPt.x} y={observedPt.y - 16} fill="var(--color-accent)" fontSize="10" fontFamily="var(--font-mono)" textAnchor="middle">
                      Observed Spill
                    </text>
                    <text x={observedPt.x} y={observedPt.y - 5} fill="rgba(69,194,177,0.5)" fontSize="9" fontFamily="var(--font-mono)" textAnchor="middle">
                      {run?.observation_lat?.toFixed(4)}°N, {run?.observation_lon?.toFixed(4)}°E
                    </text>
                  </>
                )}
              </>
            ) : (
              /* Demo / no-data fallback paths */
              <>
                <path d="M 120 170 Q 280 140 460 90 T 700 45" fill="none" stroke="url(#driftGradient)" strokeWidth="3" />
                <path d="M 120 170 Q 260 160 440 105 T 680 55" fill="none" stroke="rgba(69, 194, 177, 0.2)" strokeWidth="1.5" strokeDasharray="4 4" />
                <circle cx="120" cy="170" r="5" fill="var(--color-processing)" />
                <text x="120" y="195" fill="var(--color-text-muted)" fontSize="11" fontFamily="var(--font-mono)" textAnchor="middle">Reconstructed Origin (D3)</text>
                <circle cx="700" cy="45" r="5" fill="var(--color-accent)" />
                <text x="700" y="32" fill="var(--color-accent)" fontSize="11" fontFamily="var(--font-mono)" textAnchor="middle">Observed Spill (B3)</text>
              </>
            )}
          </svg>
        </div>

        {/* 4 Key Drift Metrics — all derived from real backward_steps */}
        <div className="telemetry-hero-grid">
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Estimated Displacement</span>
            <span className="telemetry-hero-value telemetry-hero-value--accent">{disp} km</span>
            <span className="telemetry-hero-caption">Net Euclidean drift distance (Haversine)</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Mean Drift Speed</span>
            <span className="telemetry-hero-value telemetry-hero-value--info">{speed} kn</span>
            <span className="telemetry-hero-caption">
              {analytics ? `Over ${analytics.backtrackHours}h backtrack, ${totalDist} km path` : 'Integrated surface transport'}
            </span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Mean Drift Direction</span>
            <span className="telemetry-hero-value">{bearing}</span>
            <span className="telemetry-hero-caption">
              {analytics ? `True compass heading (${analytics.stepCount} steps)` : 'True compass heading'}
            </span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Current / Wind Forcing</span>
            <span className="telemetry-hero-value">
              <span style={{ color: 'var(--color-info)' }}>{currR}%</span>
              <span style={{ color: 'var(--color-text-subtle)', fontSize: '1rem', margin: '0 0.15rem' }}> / </span>
              <span style={{ color: 'var(--color-accent)' }}>{windR}%</span>
            </span>
            <span className="telemetry-hero-caption">
              {analytics?.windPct != null && analytics.windPct !== 28
                ? 'CMEMS vs ERA5 from per-step UV vectors'
                : 'CMEMS vs ERA5 influence'}
            </span>
          </div>
        </div>

        {/* Uncertainty radius — only when real data has it */}
        {analytics?.meanUncertKm != null && (
          <div style={{
            padding: '0.65rem 1rem',
            borderRadius: 'var(--radius-sm)',
            background: 'rgba(98,174,232,0.06)',
            border: '1px solid rgba(98,174,232,0.18)',
            fontSize: '0.8rem',
            color: 'var(--color-text-muted)',
            display: 'flex', alignItems: 'center', gap: '0.65rem',
          }}>
            <span style={{ color: 'var(--color-info)', fontWeight: 600 }}>Mean Uncertainty Radius:</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: '#fff' }}>{analytics.meanUncertKm.toFixed(2)} km</span>
            <span style={{ opacity: 0.6 }}>— Gaussian particle spread from drift integration</span>
          </div>
        )}

        {/* Ranked vessels table from the full run */}
        {analytics && analytics.vessels.length > 0 && (
          <div style={{ marginTop: '0.5rem' }}>
            <div className="section-title-line" style={{ marginBottom: '0.65rem' }}>
              <div>
                <span className="section-kicker">Evidence Scoring</span>
                <h3 style={{ fontSize: '1rem', fontWeight: 700, margin: 0, color: '#fff' }}>
                  Candidate Ranking — {analytics.vessels.length} vessels evaluated
                </h3>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {analytics.vessels.slice(0, 8).map((v: any) => {
                const ecs = v.evidence_consistency_score ?? 0
                const pct = Math.round(ecs * 100)
                const isTop = v.rank === 1
                return (
                  <div key={v.vessel_id} style={{
                    display: 'flex', alignItems: 'center', gap: '1rem',
                    padding: '0.6rem 1rem',
                    borderRadius: 'var(--radius-sm)',
                    background: isTop ? 'rgba(0,200,150,0.06)' : 'var(--color-surface)',
                    border: `1px solid ${isTop ? 'rgba(0,200,150,0.25)' : 'var(--color-border)'}`,
                  }}>
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: '1rem', fontWeight: 700,
                      color: isTop ? 'var(--color-accent)' : 'var(--color-text-subtle)',
                      minWidth: 28,
                    }}>
                      {String(v.rank).padStart(2, '0')}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#fff' }}>
                        {v.vessel_name ?? `MMSI ${v.mmsi ?? v.vessel_id}`}
                      </div>
                      <div style={{ fontSize: '0.7rem', color: 'var(--color-text-subtle)', fontFamily: 'var(--font-mono)' }}>
                        MMSI {v.mmsi ?? '—'}{v.min_source_distance_km != null ? ` · ${v.min_source_distance_km.toFixed(1)} km from source` : ''}
                        {v.temporal_overlap_hours != null ? ` · ${v.temporal_overlap_hours.toFixed(1)}h overlap` : ''}
                      </div>
                    </div>
                    {/* ECS bar */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', minWidth: 140 }}>
                      <div style={{ flex: 1, height: 5, borderRadius: 99, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }}>
                        <div style={{
                          width: `${pct}%`, height: '100%', borderRadius: 99,
                          background: isTop ? 'var(--color-accent)' : 'var(--color-info)',
                          transition: 'width 0.6s ease',
                        }} />
                      </div>
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: '0.9rem', fontWeight: 700,
                        color: isTop ? 'var(--color-accent)' : 'var(--color-text-muted)',
                        minWidth: 40, textAlign: 'right',
                      }}>
                        {pct}%
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Attribution Model Formula */}
        <div className="disclosure-block" style={{ marginTop: '0.5rem' }}>
          <button
            type="button"
            className="disclosure-trigger"
            onClick={() => setShowModelDetails(!showModelDetails)}
          >
            <span>{showModelDetails ? 'Hide attribution model' : 'View attribution model formula'}</span>
            {showModelDetails ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showModelDetails && (
            <div className="disclosure-content" style={{ gap: '1.25rem' }}>
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                <div className="metric">
                  <span>Spatial Proximity Weight</span>
                  <strong className="metric--accent">40.0%</strong>
                  <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.62rem' }}>D3 origin zone intersection</small>
                </div>
                <div className="metric">
                  <span>Temporal Match Weight</span>
                  <strong style={{ color: 'var(--color-info)' }}>35.0%</strong>
                  <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.62rem' }}>Release window alignment</small>
                </div>
                <div className="metric">
                  <span>Trajectory Consistency</span>
                  <strong style={{ color: 'var(--color-processing)' }}>25.0%</strong>
                  <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.62rem' }}>Course vs drift major axis</small>
                </div>
              </div>

              <div style={{
                padding: '1rem',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                fontSize: '0.78rem',
                color: 'var(--color-text-muted)',
                lineHeight: 1.6,
              }}>
                <strong style={{ color: '#fff', display: 'block', marginBottom: '0.35rem' }}>
                  F1 Multi-Channel Normalization Formula
                </strong>
                <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--color-accent)' }}>
                  ECS = (0.40 × Spatial + 0.35 × Temporal + 0.25 × Trajectory) ÷ Available Channels
                </code>
                <br /><br />
                {analytics && (
                  <>
                    <strong style={{ color: '#fff' }}>This run — top candidate score breakdown:</strong>
                    <br />
                    {analytics.vessels.slice(0, 1).map((v: any) => (
                      <span key={v.vessel_id}>
                        Spatial: {v.min_source_distance_km != null ? `${v.min_source_distance_km.toFixed(1)} km` : '—'} ·
                        Temporal: {v.temporal_overlap_hours != null ? `${v.temporal_overlap_hours.toFixed(1)}h` : '—'} ·
                        Trajectory: {v.trajectory_overlap_fraction != null ? `${(v.trajectory_overlap_fraction * 100).toFixed(0)}%` : '—'}
                        → ECS = {Math.round((v.evidence_consistency_score ?? 0) * 100)}%
                      </span>
                    ))}
                    <br /><br />
                  </>
                )}
                <span style={{ color: 'var(--color-processing)' }}>
                  * Contextual behavioral observations (E3) and forward drift cross-checks (D1) are strictly non-additive (0.00 contribution to ECS).
                </span>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
