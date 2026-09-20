import { useRef } from 'react'
import {
  Activity,
  BarChart3,
  Compass,
  FileText,
  Layers,
  Plus,
  Radio,
  Ship,
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
  onOpenCreateModal: () => void
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
          onClick={() => onSelectView('workspace')}
        >
          <Compass size={14} />
          <span>Workspace</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'evaluator' ? 'is-active' : ''}`}
          onClick={() => onSelectView('evaluator')}
          title="Evaluator Investigation Workflow (6-Step Flow)"
        >
          <Target size={14} />
          <span>Evaluator</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'overview' ? 'is-active' : ''}`}
          onClick={() => onSelectView('overview')}
        >
          <Activity size={14} />
          <span>Overview</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'investigations' ? 'is-active' : ''}`}
          onClick={() => onSelectView('investigations')}
        >
          <FileText size={14} />
          <span>Investigations</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'evidence' ? 'is-active' : ''}`}
          onClick={() => onSelectView('evidence')}
        >
          <Layers size={14} />
          <span>Evidence</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'vessels' ? 'is-active' : ''}`}
          onClick={() => onSelectView('vessels')}
        >
          <Ship size={14} />
          <span>Vessels</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'analytics' ? 'is-active' : ''}`}
          onClick={() => onSelectView('analytics')}
        >
          <BarChart3 size={14} />
          <span>Analytics</span>
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'pipeline' ? 'is-active' : ''}`}
          onClick={() => onSelectView('pipeline')}
        >
          <Zap size={14} />
          <span>Pipeline</span>
        </button>
      </nav>

      {/* Header Controls */}
      <div className="topbar__controls">
        {/* New Investigation Action */}
        <button
          className="header-new-btn"
          type="button"
          onClick={onOpenCreateModal}
          title="Create a new live investigation"
        >
          <Plus size={14} />
          <span>New Investigation</span>
        </button>
      </div>
    </header>
  )
}