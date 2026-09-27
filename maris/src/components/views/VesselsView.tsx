import { useEffect, useState, useCallback } from 'react'
import { Compass, Database, RefreshCw, Search, Ship } from 'lucide-react'
import { fetchAisFleet } from '../../real-experiment/experimentApi'
import type { AisFleetVessel, AisFleetResponse } from '../../real-experiment/experimentApi'

interface VesselsViewProps {
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: any
  demoCandidates: any[]
  rankingResult: any
  selectedCandidate: string
  onSelectCandidate: (id: string) => void
  onNavigateToView: (view: any) => void
}

// ── Helpers ─────────────────────────────────────────────────────────────────

const PROVENANCE_LABELS: Record<string, { label: string; color: string }> = {
  NOAA_MARINECADASTRE:  { label: 'NOAA MarineCadastre',      color: '#00c896' },
  MANUAL_REFERENCE:     { label: 'Manual Reference',          color: '#60a5fa' },
  SYNTHETIC_BENCHMARK:  { label: 'Synthetic Benchmark',       color: '#f59e0b' },
  LIVE_AIS_PROVIDER:    { label: 'Live AIS Feed',             color: '#a78bfa' },
  UNVERIFIED_IMPORT:    { label: 'Unverified Import',         color: '#94a3b8' },
}

function provenanceBadge(sourceType: string, isReal: number) {
  const p = PROVENANCE_LABELS[sourceType] ?? { label: sourceType, color: '#94a3b8' }
  return { ...p, isReal: isReal === 1 }
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    return `${String(d.getUTCDate()).padStart(2,'0')} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`
  } catch { return iso }
}

function fmtNum(n: number | null | undefined, unit = ''): string {
  if (n == null) return '—'
  return unit ? `${n}${unit}` : String(n)
}

export function VesselsView({
  onNavigateToView,
}: VesselsViewProps) {
  const [fleet, setFleet] = useState<AisFleetResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [activeSource, setActiveSource] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [debouncedSearch, setDebouncedSearch] = useState('')

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchAisFleet({
        limit: 200,
        offset: 0,
        search: debouncedSearch || undefined,
        source_type: activeSource || undefined,
      })
      setFleet(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load AIS fleet registry')
    } finally {
      setLoading(false)
    }
  }, [debouncedSearch, activeSource])

  useEffect(() => { load() }, [load])

  const stats = fleet?.stats
  const vessels = fleet?.vessels ?? []
  const sourceBreakdown = fleet?.source_breakdown ?? []

  return (
    <div className="view-container">
      {/* Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Maritime Surveillance</span>
          <h1 className="view-headline">AIS Fleet Registry</h1>
          <p className="view-lead">
            All vessels registered in <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85em' }}>ais_vessels.db</code> — 
            real imported tracks, curated historical references, and synthetic benchmarks with full provenance tracking.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.65rem' }}>
          <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
            <Compass size={14} /> Back to GIS Workspace
          </button>
          <button className="secondary-button" type="button" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spinner' : ''} /> Refresh
          </button>
        </div>
      </div>

      {/* Database Stats Hero */}
      {stats && (
        <div className="telemetry-hero-grid" style={{ marginBottom: '1.25rem' }}>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Total Vessels</span>
            <span className="telemetry-hero-value telemetry-hero-value--accent" style={{ fontSize: '2rem' }}>
              {stats.total_vessels.toLocaleString()}
            </span>
            <span className="telemetry-hero-caption">Unique vessel identities in registry</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">AIS Positions</span>
            <span className="telemetry-hero-value telemetry-hero-value--info" style={{ fontSize: '2rem' }}>
              {stats.total_positions.toLocaleString()}
            </span>
            <span className="telemetry-hero-caption">
              {stats.real_positions.toLocaleString()} real · {stats.synthetic_or_manual_positions.toLocaleString()} benchmark
            </span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Data Sources</span>
            <span className="telemetry-hero-value" style={{ fontSize: '2rem' }}>
              {stats.total_sources}
            </span>
            <span className="telemetry-hero-caption">Distinct provenance origins</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Import Batches</span>
            <span className="telemetry-hero-value" style={{ fontSize: '2rem' }}>
              {stats.total_batches}
            </span>
            <span className="telemetry-hero-caption">Verified import transactions</span>
          </div>
        </div>
      )}

      {/* Source type provenance pills */}
      {sourceBreakdown.length > 0 && (
        <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          <button
            type="button"
            className={!activeSource ? 'primary-button' : 'secondary-button'}
            style={{ padding: '0.35rem 0.85rem', fontSize: '0.75rem' }}
            onClick={() => setActiveSource('')}
          >
            All Types
          </button>
          {sourceBreakdown.map((s) => {
            const p = PROVENANCE_LABELS[s.source_type] ?? { label: s.source_type, color: '#94a3b8' }
            const isActive = activeSource === s.source_type
            return (
              <button
                key={s.source_type}
                type="button"
                style={{
                  padding: '0.35rem 0.85rem',
                  fontSize: '0.75rem',
                  borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${isActive ? p.color : 'var(--color-border)'}`,
                  background: isActive ? `${p.color}22` : 'var(--color-surface)',
                  color: isActive ? p.color : 'var(--color-text-muted)',
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                }}
                onClick={() => setActiveSource(isActive ? '' : s.source_type)}
              >
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: p.color, flexShrink: 0 }} />
                {p.label} <span style={{ opacity: 0.6 }}>({s.cnt})</span>
              </button>
            )
          })}
        </div>
      )}

      {/* Search bar */}
      <div style={{ position: 'relative', maxWidth: '480px', marginBottom: '1.25rem' }}>
        <Search size={14} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--color-text-subtle)' }} />
        <input
          type="text"
          placeholder="Search by vessel name or MMSI…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            width: '100%',
            padding: '0.55rem 0.85rem 0.55rem 2.2rem',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            color: '#fff',
            fontSize: '0.82rem',
          }}
        />
      </div>

      {/* Error state */}
      {error && (
        <div style={{
          padding: '1rem 1.25rem',
          borderRadius: 'var(--radius-sm)',
          background: 'rgba(239,68,68,0.08)',
          border: '1px solid rgba(239,68,68,0.25)',
          color: 'rgba(239,68,68,0.9)',
          fontSize: '0.82rem',
          marginBottom: '1rem',
        }}>
          <Database size={14} style={{ marginRight: '0.5rem', display: 'inline' }} />
          {error} — is the backend running?
          <button type="button" className="secondary-button" style={{ marginLeft: '1rem', padding: '0.25rem 0.65rem', fontSize: '0.72rem' }} onClick={load}>
            Retry
          </button>
        </div>
      )}

      {/* Fleet table */}
      {loading && !fleet ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--color-text-muted)' }}>
          <RefreshCw size={20} className="spinner" style={{ marginBottom: '0.75rem' }} />
          <p style={{ margin: 0, fontSize: '0.85rem' }}>Loading AIS Fleet Registry…</p>
        </div>
      ) : vessels.length === 0 ? (
        <div style={{
          padding: '3.5rem 1.5rem',
          textAlign: 'center',
          color: 'var(--color-text-muted)',
          borderRadius: 'var(--radius-sm)',
          border: '1px dashed var(--color-border)',
        }}>
          <Ship size={28} style={{ marginBottom: '0.75rem', opacity: 0.35 }} />
          <p style={{ margin: '0 0 0.5rem', fontSize: '0.9rem' }}>No vessels found{search ? ` matching "${search}"` : ''}.</p>
          {search && (
            <button type="button" className="secondary-button" style={{ fontSize: '0.78rem' }} onClick={() => setSearch('')}>
              Clear search
            </button>
          )}
        </div>
      ) : (
        <>
          {/* Results count */}
          <div style={{ fontSize: '0.78rem', color: 'var(--color-text-subtle)', marginBottom: '0.65rem' }}>
            Showing <strong style={{ color: '#fff' }}>{vessels.length}</strong> of <strong style={{ color: '#fff' }}>{fleet?.total_matching ?? vessels.length}</strong> vessels
          </div>

          {/* Vessel cards */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
            {vessels.map((v) => {
              const badge = provenanceBadge(v.source_type, v.is_real_observation)
              const isExpanded = expandedId === v.vessel_id

              return (
                <article
                  key={v.vessel_id}
                  style={{
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--color-surface)',
                    border: `1px solid ${isExpanded ? badge.color + '55' : 'var(--color-border)'}`,
                    padding: '1rem 1.25rem',
                    transition: 'all var(--transition-fast)',
                  }}
                >
                  {/* Row: icon + name + type + provenance + positions */}
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: '1rem', cursor: 'pointer', flexWrap: 'wrap' }}
                    onClick={() => setExpandedId(isExpanded ? null : v.vessel_id)}
                  >
                    {/* Vessel icon */}
                    <div style={{
                      width: 38, height: 38, borderRadius: 'var(--radius-sm)',
                      background: `${badge.color}18`,
                      border: `1px solid ${badge.color}44`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0,
                    }}>
                      <Ship size={17} color={badge.color} />
                    </div>

                    {/* Name + MMSI */}
                    <div style={{ flex: 1, minWidth: 140 }}>
                      <div style={{ fontSize: '0.95rem', fontWeight: 700, color: '#fff' }}>
                        {v.vessel_name ?? `MMSI ${v.mmsi}`}
                      </div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--color-text-subtle)', fontFamily: 'var(--font-mono)' }}>
                        MMSI {v.mmsi ?? '—'}{v.imo ? ` · IMO ${v.imo}` : ''}{v.call_sign ? ` · ${v.call_sign}` : ''}
                      </div>
                    </div>

                    {/* Vessel type */}
                    <div style={{ minWidth: 110 }}>
                      <span style={{ fontSize: '0.72rem', color: 'var(--color-text-subtle)', display: 'block' }}>Type</span>
                      <span style={{ fontSize: '0.82rem', color: 'var(--color-text-muted)' }}>{v.vessel_type ?? '—'}</span>
                    </div>

                    {/* Provenance badge */}
                    <span style={{
                      fontSize: '0.68rem',
                      padding: '0.2rem 0.55rem',
                      borderRadius: 'var(--radius-xs)',
                      background: `${badge.color}1a`,
                      border: `1px solid ${badge.color}44`,
                      color: badge.color,
                      fontWeight: 600,
                      whiteSpace: 'nowrap',
                    }}>
                      {badge.label}
                    </span>

                    {/* Position count */}
                    <div style={{ textAlign: 'right', minWidth: 70 }}>
                      <span style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--color-accent)', fontFamily: 'var(--font-mono)' }}>
                        {v.position_count.toLocaleString()}
                      </span>
                      <span style={{ fontSize: '0.68rem', color: 'var(--color-text-subtle)', display: 'block' }}>positions</span>
                    </div>

                    {/* Expand chevron */}
                    <span style={{ color: 'var(--color-text-subtle)', fontSize: '0.75rem', marginLeft: 'auto' }}>
                      {isExpanded ? '▲' : '▼'}
                    </span>
                  </div>

                  {/* Expanded detail panel */}
                  {isExpanded && (
                    <div style={{
                      marginTop: '1rem',
                      paddingTop: '1rem',
                      borderTop: '1px solid var(--color-border-subtle)',
                    }}>
                      <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))' }}>
                        <div className="metric">
                          <span>Flag</span>
                          <strong>{v.flag_country ?? '—'}</strong>
                        </div>
                        <div className="metric">
                          <span>Length</span>
                          <strong className="mono-num">{fmtNum(v.length, ' m')}</strong>
                        </div>
                        <div className="metric">
                          <span>Beam</span>
                          <strong className="mono-num">{fmtNum(v.width, ' m')}</strong>
                        </div>
                        <div className="metric">
                          <span>Draft</span>
                          <strong className="mono-num">{fmtNum(v.draft, ' m')}</strong>
                        </div>
                        <div className="metric">
                          <span>First Seen</span>
                          <strong className="mono-num">{fmtDate(v.first_seen)}</strong>
                        </div>
                        <div className="metric">
                          <span>Last Seen</span>
                          <strong className="mono-num">{fmtDate(v.last_seen)}</strong>
                        </div>
                        <div className="metric">
                          <span>Real Observation</span>
                          <strong style={{ color: v.is_real_observation ? 'var(--color-accent)' : 'var(--color-text-subtle)' }}>
                            {v.is_real_observation ? 'Yes' : 'No (Benchmark)'}
                          </strong>
                        </div>
                        <div className="metric">
                          <span>Coverage</span>
                          <strong style={{ fontSize: '0.75rem' }}>
                            {fmtDate(v.coverage_start)} → {fmtDate(v.coverage_end)}
                          </strong>
                        </div>
                      </div>

                      {/* Provider + geographic coverage */}
                      {(v.provider_name || v.geographic_coverage) && (
                        <div style={{
                          marginTop: '0.75rem',
                          padding: '0.6rem 0.85rem',
                          borderRadius: 'var(--radius-xs)',
                          background: 'var(--color-bg)',
                          border: '1px solid var(--color-border)',
                          fontSize: '0.75rem',
                          color: 'var(--color-text-subtle)',
                        }}>
                          {v.provider_name && <div><span style={{ color: badge.color }}>Provider:</span> {v.provider_name}</div>}
                          {v.geographic_coverage && <div style={{ marginTop: '0.2rem' }}><span style={{ color: 'var(--color-text-muted)' }}>Coverage:</span> {v.geographic_coverage}</div>}
                        </div>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </div>

          {fleet && fleet.total_matching > fleet.vessels.length && (
            <div style={{ textAlign: 'center', marginTop: '1.5rem', fontSize: '0.8rem', color: 'var(--color-text-subtle)' }}>
              Showing first {fleet.vessels.length} of {fleet.total_matching.toLocaleString()} matching vessels. Use search to narrow results.
            </div>
          )}
        </>
      )}
    </div>
  )
}
