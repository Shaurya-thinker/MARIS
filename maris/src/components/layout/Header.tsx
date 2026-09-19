import { Bell, CircleDot, Plus, Radio, ShieldAlert } from 'lucide-react'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import type { InvestigationListItem, InvestigationStatus } from '../../types/investigationApi'

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
}: HeaderProps) {
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
      <div className="brand-lockup">
        <div className="brand-mark" aria-hidden="true">
          <Radio size={18} />
        </div>
        <div>
          <div className="brand-name">MARIS</div>
          <div className="brand-description">Marine Intelligence &amp; Spill Attribution System</div>
        </div>
      </div>

      <div className="topbar__controls">
        <label htmlFor="investigation-select" className="sr-only">
          Select Investigation
        </label>
        <select
          id="investigation-select"
          className="investigation-select"
          value={activeId}
          onChange={(e) => onSelectInvestigation(e.target.value)}
        >
          <optgroup label="Live G1 Investigations">
            {investigations.map((inv) => (
              <option key={inv.id} value={inv.id}>
                {inv.name} ({inv.id}) [{inv.status}]
              </option>
            ))}
            {investigations.length === 0 && (
              <option value="" disabled>
                {isBackendUnavailable ? '(Backend service unavailable)' : '(No live investigations registered)'}
              </option>
            )}
          </optgroup>
          <optgroup label="Simulation Investigations">
            {simulationScenarios.map((scenario) => (
              <option key={scenario.id} value={`simulation-${scenario.id}`}>
                {scenario.name}
              </option>
            ))}
          </optgroup>
          <optgroup label="Explicit Historical / Demo Cases">
            <option value="corsica-2018-demo">Corsica 2018 Demo (Historical Reconstructed Case)</option>
          </optgroup>
        </select>

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

      <div className="topbar__meta">
        <span className={`system-status ${getStatusClass()}`}>
          {activeStatus === 'FAILED' ? <ShieldAlert size={13} aria-hidden="true" /> : <CircleDot size={12} aria-hidden="true" />}
          <span>{getStatusLabel()}</span>
        </span>
        <button
          className="icon-button"
          type="button"
          aria-label="View investigation alerts"
          title="Investigation alerts"
        >
          <Bell size={17} />
        </button>
      </div>
    </header>
  )
}