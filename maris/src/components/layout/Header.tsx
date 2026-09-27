import { useEffect, useRef, useState } from 'react'
import {
  BarChart3,
  ChevronDown,
  Compass,
  FileText,
  Layers,
  Radio,
  Ship,
  Sliders,
  Target,
  Zap,
} from 'lucide-react'
import { gsap, useGSAP, isReducedMotion, TIMINGS, EASINGS } from '../../lib/motion'

export type MarisView =
  | 'workspace'
  | 'evaluator'
  | 'overview'
  | 'investigations'
  | 'evidence'
  | 'vessels'
  | 'analytics'
  | 'pipeline'

interface HeaderProps {
  activeView: MarisView
  onSelectView: (view: MarisView) => void
  onOpenCreateModal?: () => void
  // Optional legacy props for backwards compatibility with callers
  activeId?: string
  investigations?: unknown[]
  activeStatus?: unknown
  isDemoMode?: boolean
  isSimulationMode?: boolean
  isBackendUnavailable?: boolean
  simulationScenarios?: unknown[]
  onSelectInvestigation?: (id: string) => void
  onOpenAlertsDrawer?: () => void
}

export function Header({
  activeView,
  onSelectView,
  onOpenCreateModal,
}: HeaderProps) {
  const navRef = useRef<HTMLElement>(null)
  const indicatorRef = useRef<HTMLDivElement>(null)
  const [isAdvancedOpen, setIsAdvancedOpen] = useState(false)
  const advancedDropdownRef = useRef<HTMLDivElement>(null)

  const isAdvancedActive = ['evidence', 'vessels', 'analytics', 'pipeline'].includes(activeView)
  const isInvestigationsActive = activeView === 'investigations' || activeView === 'overview'

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (advancedDropdownRef.current && !advancedDropdownRef.current.contains(e.target as Node)) {
        setIsAdvancedOpen(false)
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setIsAdvancedOpen(false)
      }
    }
    if (isAdvancedOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleKeyDown)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isAdvancedOpen])

  useGSAP(
    () => {
      const nav = navRef.current
      const indicator = indicatorRef.current
      if (!nav || !indicator) return

      const activeBtn = nav.querySelector<HTMLButtonElement>('.nav-tab-btn.is-active')
      if (!activeBtn) {
        gsap.to(indicator, { opacity: 0, duration: TIMINGS.fast })
        return
      }

      const targetLeft = activeBtn.offsetLeft + 6
      const targetWidth = Math.max(0, activeBtn.offsetWidth - 12)

      if (isReducedMotion()) {
        gsap.set(indicator, {
          x: targetLeft,
          width: targetWidth,
          opacity: 1,
        })
        return
      }

      if (!indicator.dataset.initialized) {
        gsap.set(indicator, {
          x: targetLeft,
          width: targetWidth,
          opacity: 1,
        })
        indicator.dataset.initialized = 'true'
      } else {
        gsap.to(indicator, {
          x: targetLeft,
          width: targetWidth,
          opacity: 1,
          duration: TIMINGS.base,
          ease: EASINGS.out,
        })
      }
    },
    { dependencies: [activeView], scope: navRef }
  )

  return (
    <header className="topbar">
      {/* Brand Lockup */}
      <div className="brand-lockup" onClick={() => onSelectView('workspace')}>
        <div className="brand-mark" aria-hidden="true">
          <Radio size={17} />
        </div>
        <div className="brand-text">
          <div className="brand-name">
            MARIS <span className="brand-tag">MISSION CONTROL</span>
          </div>
          <div className="brand-description">Marine Intelligence &amp; Spill Attribution System</div>
        </div>
      </div>

      {/* Navigation View Switcher (Apple-Precision Tabs) */}
      <nav className="topbar__nav" ref={navRef} aria-label="Mission Views">
        <div className="nav-active-indicator" ref={indicatorRef} aria-hidden="true" />
        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'workspace' ? 'is-active' : ''}`}
          onClick={() => {
            setIsAdvancedOpen(false)
            onSelectView('workspace')
          }}
        >
          <Compass size={14} />
          <span>Workspace</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'evaluator' ? 'is-active' : ''}`}
          onClick={() => {
            setIsAdvancedOpen(false)
            onSelectView('evaluator')
          }}
          title="Evaluator Investigation Workflow (6-Step Flow)"
        >
          <Target size={14} />
          <span>Evaluator</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${isInvestigationsActive ? 'is-active' : ''}`}
          onClick={() => {
            setIsAdvancedOpen(false)
            onSelectView('investigations')
          }}
        >
          <FileText size={14} />
          <span>Investigations</span>
        </button>

        {/* Advanced Menu (Evidence, Vessels, Analytics, Pipeline) */}
        <div className="nav-dropdown-wrapper" ref={advancedDropdownRef} style={{ position: 'relative' }}>
          <button
            type="button"
            className={`nav-tab-btn ${isAdvancedActive ? 'is-active' : ''}`}
            onClick={() => setIsAdvancedOpen((prev) => !prev)}
            aria-expanded={isAdvancedOpen}
            aria-haspopup="true"
            title="Advanced technical and supporting views"
          >
            <Sliders size={14} />
            <span>Advanced</span>
            <ChevronDown
              size={12}
              style={{
                transition: 'transform 0.2s ease',
                transform: isAdvancedOpen ? 'rotate(180deg)' : 'rotate(0deg)',
              }}
            />
          </button>

          {isAdvancedOpen && (
            <div
              className="nav-dropdown-menu"
              role="menu"
              style={{
                position: 'absolute',
                top: 'calc(100% + 6px)',
                left: 0,
                minWidth: '210px',
                background: 'var(--color-surface, #0d1e24)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius-sm)',
                boxShadow: '0 8px 24px rgba(0, 0, 0, 0.45)',
                padding: '0.35rem',
                zIndex: 100,
                display: 'flex',
                flexDirection: 'column',
                gap: '0.2rem',
              }}
            >
              <button
                type="button"
                role="menuitem"
                className={`nav-dropdown-item ${activeView === 'evidence' ? 'is-active' : ''}`}
                onClick={() => {
                  setIsAdvancedOpen(false)
                  onSelectView('evidence')
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.45rem 0.65rem',
                  background: activeView === 'evidence' ? 'rgba(69, 194, 177, 0.12)' : 'transparent',
                  color: activeView === 'evidence' ? '#fff' : 'var(--color-text-muted)',
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  fontSize: '0.8rem',
                  fontWeight: activeView === 'evidence' ? 600 : 500,
                  cursor: 'pointer',
                  textAlign: 'left',
                  width: '100%',
                }}
              >
                <Layers size={14} style={{ color: activeView === 'evidence' ? 'var(--color-accent)' : 'inherit' }} />
                <span>Evidence &amp; Artifacts</span>
              </button>

              <button
                type="button"
                role="menuitem"
                className={`nav-dropdown-item ${activeView === 'vessels' ? 'is-active' : ''}`}
                onClick={() => {
                  setIsAdvancedOpen(false)
                  onSelectView('vessels')
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.45rem 0.65rem',
                  background: activeView === 'vessels' ? 'rgba(69, 194, 177, 0.12)' : 'transparent',
                  color: activeView === 'vessels' ? '#fff' : 'var(--color-text-muted)',
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  fontSize: '0.8rem',
                  fontWeight: activeView === 'vessels' ? 600 : 500,
                  cursor: 'pointer',
                  textAlign: 'left',
                  width: '100%',
                }}
              >
                <Ship size={14} style={{ color: activeView === 'vessels' ? 'var(--color-accent)' : 'inherit' }} />
                <span>AIS Fleet Registry</span>
              </button>

              <button
                type="button"
                role="menuitem"
                className={`nav-dropdown-item ${activeView === 'analytics' ? 'is-active' : ''}`}
                onClick={() => {
                  setIsAdvancedOpen(false)
                  onSelectView('analytics')
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.45rem 0.65rem',
                  background: activeView === 'analytics' ? 'rgba(69, 194, 177, 0.12)' : 'transparent',
                  color: activeView === 'analytics' ? '#fff' : 'var(--color-text-muted)',
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  fontSize: '0.8rem',
                  fontWeight: activeView === 'analytics' ? 600 : 500,
                  cursor: 'pointer',
                  textAlign: 'left',
                  width: '100%',
                }}
              >
                <BarChart3 size={14} style={{ color: activeView === 'analytics' ? 'var(--color-accent)' : 'inherit' }} />
                <span>Attribution Analytics</span>
              </button>

              <button
                type="button"
                role="menuitem"
                className={`nav-dropdown-item ${activeView === 'pipeline' ? 'is-active' : ''}`}
                onClick={() => {
                  setIsAdvancedOpen(false)
                  onSelectView('pipeline')
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.45rem 0.65rem',
                  background: activeView === 'pipeline' ? 'rgba(69, 194, 177, 0.12)' : 'transparent',
                  color: activeView === 'pipeline' ? '#fff' : 'var(--color-text-muted)',
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  fontSize: '0.8rem',
                  fontWeight: activeView === 'pipeline' ? 600 : 500,
                  cursor: 'pointer',
                  textAlign: 'left',
                  width: '100%',
                }}
              >
                <Zap size={14} style={{ color: activeView === 'pipeline' ? 'var(--color-accent)' : 'inherit' }} />
                <span>Pipeline Architecture</span>
              </button>
            </div>
          )}
        </div>
      </nav>

      {/* Header Controls */}
      <div className="topbar__controls" />
    </header>
  )
}