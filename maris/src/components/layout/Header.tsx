import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  BarChart3,
  Bell,
  Check,
  ChevronDown,
  CircleDot,
  Clock,
  Compass,
  Database,
  FileText,
  Layers,
  Plus,
  Radio,
  Settings,
  ShieldAlert,
  Ship,
  Target,
  Zap,
} from 'lucide-react'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import type { InvestigationListItem, InvestigationStatus } from '../../types/investigationApi'

export type MarisView =
  | 'workspace'
  | 'evaluator'
  | 'overview'
  | 'investigations'
  | 'evidence'
  | 'vessels'
  | 'analytics'
  | 'pipeline'
  | 'settings'

interface HeaderProps {
  activeId: string
  investigations: InvestigationListItem[]
  activeStatus?: InvestigationStatus
  isDemoMode: boolean
  isSimulationMode: boolean
  isBackendUnavailable?: boolean
  simulationScenarios: SimulationScenario[]
  onSelectInvestigation: (id: string) => void
  onOpenCreateModal: () => void
  activeView: MarisView
  onSelectView: (view: MarisView) => void
  onOpenAlertsDrawer: () => void
}

export function Header({
  activeId,
  investigations,
  activeStatus,
  isDemoMode,
  isSimulationMode,
  isBackendUnavailable,
  simulationScenarios,
  onSelectInvestigation,
  onOpenCreateModal,
  activeView,
  onSelectView,
  onOpenAlertsDrawer,
}: HeaderProps) {
  const [utcTime, setUtcTime] = useState('')
  const [isSelectorOpen, setIsSelectorOpen] = useState(false)
  const selectorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (selectorRef.current && !selectorRef.current.contains(event.target as Node)) {
        setIsSelectorOpen(false)
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsSelectorOpen(false)
      }
    }
    if (isSelectorOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleKeyDown)
      return () => {
        document.removeEventListener('mousedown', handleClickOutside)
        document.removeEventListener('keydown', handleKeyDown)
      }
    }
  }, [isSelectorOpen])

  function getSelectedInvestigationName() {
    if (activeId === 'corsica-2018-demo') {
      return 'Corsica 2018 Demo (Historical Case)'
    }
    if (activeId.startsWith('simulation-')) {
      const scenarioId = activeId.replace('simulation-', '')
      const scenario = simulationScenarios.find((s) => s.id === scenarioId)
      return scenario ? scenario.name : 'Simulation Investigation'
    }
    const inv = investigations.find((i) => i.id === activeId)
    if (inv) {
      return `${inv.name} [${inv.status}]`
    }
    if (isBackendUnavailable) {
      return '(Backend service unavailable)'
    }
    if (investigations.length === 0) {
      return '(No live investigations registered)'
    }
    return activeId || 'Select Investigation'
  }

  function getStatusLabel() {
    if (isSimulationMode) return 'Simulation Mode'
    if (isDemoMode) return 'Historical Demo'
    if (isBackendUnavailable) return 'Backend Offline'
    if (!activeStatus) return investigations.length === 0 ? 'No Investigations' : 'Connected'
    return activeStatus
  }

  function getStatusClass() {
    if (isSimulationMode) return 'system-status--processing'
    if (isDemoMode) return 'system-status--demo'
    if (isBackendUnavailable) return 'system-status--failed'
    switch (activeStatus) {
      case 'COMPLETED':
        return 'system-status--completed'
      case 'PROCESSING':
        return 'system-status--processing'
      case 'FAILED':
        return 'system-status--failed'
      case 'CREATED':
      default:
        return 'system-status--created'
    }
  }

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
      <nav className="topbar__nav" aria-label="Mission Views">
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

        <button
          type="button"
          className={`nav-tab-btn ${activeView === 'settings' ? 'is-active' : ''}`}
          onClick={() => onSelectView('settings')}
        >
          <Settings size={14} />
          <span>Settings</span>
        </button>
      </nav>

      {/* Header Controls & Telemetry Readouts */}
      <div className="topbar__controls">
        {/* Real-time UTC Mission Time Clock */}
        <div className="utc-clock mono-num" title="Coordinated Universal Time (UTC)">
          <Clock size={12} />
          <span>{utcTime || '00:00:00 UTC'}</span>
        </div>

        {/* Case Switcher Dropdown (MARIS Custom Floating Menu) */}
        <div className="investigation-select-wrapper" ref={selectorRef}>
          <button
            id="investigation-select-trigger"
            type="button"
            className={`investigation-select-trigger ${isSelectorOpen ? 'is-open' : ''}`}
            onClick={() => setIsSelectorOpen((prev) => !prev)}
            aria-haspopup="listbox"
            aria-expanded={isSelectorOpen}
            aria-label="Select active investigation case"
            title={getSelectedInvestigationName()}
          >
            <span className="investigation-select-trigger-text">
              {getSelectedInvestigationName()}
            </span>
            <ChevronDown size={13} className={`select-arrow-icon ${isSelectorOpen ? 'is-open' : ''}`} />
          </button>

          {/* Floating MARIS Dropdown Menu */}
          {isSelectorOpen && (
            <div
              className="investigation-dropdown-menu"
              role="listbox"
              aria-label="Investigation cases"
            >
              {/* 1. Live G1 Investigations */}
              <div className="dropdown-section">
                <div className="dropdown-section-header">Live G1 Investigations</div>
                {investigations.length > 0 ? (
                  investigations.map((inv) => {
                    const isSelected = activeId === inv.id
                    return (
                      <button
                        key={inv.id}
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        className={`dropdown-option ${isSelected ? 'is-selected' : ''}`}
                        onClick={() => {
                          onSelectInvestigation(inv.id)
                          setIsSelectorOpen(false)
                        }}
                      >
                        <span className="option-label">
                          {inv.name} <span className="option-status-tag">[{inv.status}]</span>
                        </span>
                        {isSelected && <Check size={13} className="option-check" />}
                      </button>
                    )
                  })
                ) : (
                  <div className="dropdown-option dropdown-option--disabled">
                    <span>{isBackendUnavailable ? '(Backend service unavailable)' : '(No live investigations registered)'}</span>
                  </div>
                )}
              </div>

              {/* 2. Simulation Investigations */}
              <div className="dropdown-section">
                <div className="dropdown-section-header">Simulation Investigations</div>
                {simulationScenarios.map((scenario) => {
                  const val = `simulation-${scenario.id}`
                  const isSelected = activeId === val
                  return (
                    <button
                      key={scenario.id}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      className={`dropdown-option ${isSelected ? 'is-selected' : ''}`}
                      onClick={() => {
                        onSelectInvestigation(val)
                        setIsSelectorOpen(false)
                      }}
                    >
                      <span className="option-label">{scenario.name}</span>
                      {isSelected && <Check size={13} className="option-check" />}
                    </button>
                  )
                })}
              </div>

              {/* 3. Historical Benchmarks */}
              <div className="dropdown-section">
                <div className="dropdown-section-header">Historical Benchmarks</div>
                {(() => {
                  const isSelected = activeId === 'corsica-2018-demo'
                  return (
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      className={`dropdown-option ${isSelected ? 'is-selected' : ''}`}
                      onClick={() => {
                        onSelectInvestigation('corsica-2018-demo')
                        setIsSelectorOpen(false)
                      }}
                    >
                      <span className="option-label">Corsica 2018 Demo (Historical Case)</span>
                      {isSelected && <Check size={13} className="option-check" />}
                    </button>
                  )
                })()}
              </div>
            </div>
          )}
        </div>

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

        {/* Telemetry Status Pill */}
        <div className="topbar__meta">
          <span className={`system-status ${getStatusClass()}`}>
            <span className="status-dot-pulse" aria-hidden="true" />
            <span>{getStatusLabel()}</span>
          </span>

          {/* Alerts Drawer Button */}
          <button
            className="icon-button"
            type="button"
            onClick={onOpenAlertsDrawer}
            aria-label="View system alerts"
            title="System telemetry alerts"
          >
            <Bell size={15} />
            <span className="alert-badge-count">4</span>
          </button>
        </div>
      </div>
    </header>
  )
}