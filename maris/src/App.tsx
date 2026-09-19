import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Header, type MarisView } from './components/layout/Header'
import { AlertsDrawer } from './components/layout/AlertsDrawer'
import { InvestigationWorkspace } from './components/layout/InvestigationWorkspace'
import { MainLayout } from './components/layout/MainLayout'
import { WorkspaceErrorBoundary } from './components/layout/WorkspaceErrorBoundary'
import { OverviewView } from './components/views/OverviewView'
import { InvestigationsView } from './components/views/InvestigationsView'
import { EvidenceView } from './components/views/EvidenceView'
import { VesselsView } from './components/views/VesselsView'
import { AnalyticsView } from './components/views/AnalyticsView'
import { PipelineView } from './components/views/PipelineView'
import { SettingsView } from './components/views/SettingsView'
import { listInvestigations } from './api/investigationApi'
import { candidateVessels as demoCandidateVessels, incidentData as demoIncidentData } from './data/demoData'
import { getSimulationScenarioById, simulationScenarios } from './simulation/simulationEngine'
import type { InvestigationListItem } from './types/investigationApi'
import RealExperimentView from './components/views/RealExperimentView'

function getInitialViewFromLocation(): MarisView {
  if (typeof window === 'undefined') return 'workspace'
  const path = window.location.pathname.replace(/^\/+/, '').toLowerCase()
  if (path === 'evaluator' || path === 'experiment') return 'evaluator'
  if (path === 'investigations') return 'investigations'
  if (path === 'overview') return 'overview'
  if (path === 'evidence') return 'evidence'
  if (path === 'vessels') return 'vessels'
  if (path === 'analytics') return 'analytics'
  if (path === 'pipeline') return 'pipeline'
  if (path === 'settings') return 'settings'
  const hash = window.location.hash.replace(/^#\/?/, '').toLowerCase()
  if (hash === 'evaluator' || hash === 'experiment') return 'evaluator'
  if (hash === 'investigations') return 'investigations'
  if (hash === 'overview') return 'overview'
  if (hash === 'evidence') return 'evidence'
  if (hash === 'vessels') return 'vessels'
  if (hash === 'analytics') return 'analytics'
  if (hash === 'pipeline') return 'pipeline'
  if (hash === 'settings') return 'settings'
  return 'workspace'
}

export function App() {
  const [investigations, setInvestigations] = useState<InvestigationListItem[]>([])
  const [activeInvestigationId, setActiveInvestigationId] = useState<string>('')
  const [activeView, setActiveView] = useState<MarisView>(getInitialViewFromLocation)
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isAlertsDrawerOpen, setIsAlertsDrawerOpen] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(false)
  const [selectedCandidate, setSelectedCandidate] = useState<string>(demoCandidateVessels[0].id)

  const handleSelectView = useCallback((view: MarisView) => {
    setActiveView(view)
    if (typeof window !== 'undefined') {
      const targetPath = view === 'workspace' ? '/' : `/${view}`
      if (window.location.pathname !== targetPath) {
        window.history.pushState(null, '', targetPath)
      }
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined
    const onLocationChange = () => {
      setActiveView(getInitialViewFromLocation())
    }
    window.addEventListener('popstate', onLocationChange)
    window.addEventListener('hashchange', onLocationChange)
    return () => {
      window.removeEventListener('popstate', onLocationChange)
      window.removeEventListener('hashchange', onLocationChange)
    }
  }, [])

  const loadInvestigationsList = useCallback(async () => {
    setIsLoadingList(true)
    setInitError(null)
    try {
      const list = await listInvestigations()
      setInvestigations(list)
      if (list.length > 0) {
        setActiveInvestigationId((curr) => {
          if (curr === 'corsica-2018-demo' || curr.startsWith('simulation-')) return curr
          const exists = list.some((item) => item.id === curr)
          return exists ? curr : list[0].id
        })
      } else {
        setActiveInvestigationId((curr) => (curr === 'corsica-2018-demo' || curr.startsWith('simulation-') ? curr : ''))
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setInitError(`Unable to connect to MARIS backend: ${msg}`)
      setActiveInvestigationId((curr) => (curr === 'corsica-2018-demo' || curr.startsWith('simulation-') ? curr : ''))
    } finally {
      setIsLoadingList(false)
    }
  }, [])

  useEffect(() => {
    loadInvestigationsList()
  }, [loadInvestigationsList])

  const isDemoMode = activeInvestigationId === 'corsica-2018-demo'
  const isSimulationMode = activeInvestigationId.startsWith('simulation-')
  const activeListItem = investigations.find((i) => i.id === activeInvestigationId)
  const activeSimulationScenario = isSimulationMode
    ? getSimulationScenarioById(activeInvestigationId.replace('simulation-', '')) ?? null
    : null

  return (
    <MainLayout>
      <Header
        activeId={activeInvestigationId}
        investigations={investigations}
        activeStatus={activeListItem?.status}
        isDemoMode={isDemoMode}
        isSimulationMode={isSimulationMode}
        isBackendUnavailable={Boolean(initError)}
        simulationScenarios={simulationScenarios}
        onSelectInvestigation={(id) => {
          setActiveInvestigationId(id)
        }}
        onOpenCreateModal={() => setIsCreateModalOpen(true)}
        activeView={activeView}
        onSelectView={handleSelectView}
        onOpenAlertsDrawer={() => setIsAlertsDrawerOpen(true)}
      />

      <AlertsDrawer
        isOpen={isAlertsDrawerOpen}
        onClose={() => setIsAlertsDrawerOpen(false)}
      />

      {initError && (
        <div className="workspace-error-banner" role="alert">
          <div className="error-banner-content">
            <AlertTriangle size={16} />
            <div>
              <strong>Backend Unavailable</strong>
              <p>{initError}</p>
            </div>
          </div>
          <button
            className="secondary-button"
            type="button"
            onClick={loadInvestigationsList}
            disabled={isLoadingList}
          >
            <RefreshCw size={13} className={isLoadingList ? 'spinner' : ''} /> Retry Connection
          </button>
        </div>
      )}

      {/* View Switcher Routing */}
      {activeView === 'workspace' && (
        <WorkspaceErrorBoundary onReset={loadInvestigationsList}>
          <InvestigationWorkspace
            activeId={activeInvestigationId}
            isDemoMode={isDemoMode}
            isSimulationMode={isSimulationMode}
            simulationScenario={activeSimulationScenario}
            investigations={investigations}
            onSelectInvestigation={setActiveInvestigationId}
            isCreateModalOpen={isCreateModalOpen}
            onCloseCreateModal={() => setIsCreateModalOpen(false)}
            onOpenCreateModal={() => setIsCreateModalOpen(true)}
            onInvestigationCreated={loadInvestigationsList}
            backendError={initError}
            onNavigateToEvaluator={() => handleSelectView('evaluator')}
          />
        </WorkspaceErrorBoundary>
      )}

      {activeView === 'evaluator' && (
        <RealExperimentView initialMode="evaluator" />
      )}

      {activeView === 'overview' && (
        <OverviewView
          investigations={investigations}
          activeId={activeInvestigationId}
          onSelectInvestigation={setActiveInvestigationId}
          onNavigateToView={handleSelectView}
          onOpenCreateModal={() => setIsCreateModalOpen(true)}
          simulationScenarios={simulationScenarios}
          isBackendUnavailable={Boolean(initError)}
        />
      )}

      {activeView === 'investigations' && (
        <InvestigationsView
          investigations={investigations}
          activeId={activeInvestigationId}
          onSelectInvestigation={setActiveInvestigationId}
          onNavigateToView={handleSelectView}
          onOpenCreateModal={() => setIsCreateModalOpen(true)}
          simulationScenarios={simulationScenarios}
        />
      )}

      {activeView === 'evidence' && (
        <EvidenceView
          isDemoMode={isDemoMode}
          isSimulationMode={isSimulationMode}
          simulationScenario={activeSimulationScenario}
          demoIncident={demoIncidentData}
          artifacts={[]}
          activeId={activeInvestigationId}
          onNavigateToView={handleSelectView}
        />
      )}

      {activeView === 'vessels' && (
        <VesselsView
          isDemoMode={isDemoMode}
          isSimulationMode={isSimulationMode}
          simulationScenario={activeSimulationScenario}
          demoCandidates={demoCandidateVessels}
          rankingResult={null}
          selectedCandidate={selectedCandidate}
          onSelectCandidate={setSelectedCandidate}
          onNavigateToView={handleSelectView}
        />
      )}

      {activeView === 'analytics' && (
        <AnalyticsView
          isDemoMode={isDemoMode}
          isSimulationMode={isSimulationMode}
          simulationScenario={activeSimulationScenario}
          rankingResult={null}
          explainabilityReport={null}
          onNavigateToView={handleSelectView}
        />
      )}

      {activeView === 'pipeline' && (
        <PipelineView
          completedStages={activeListItem?.status === 'COMPLETED' ? ['B1', 'B2', 'B3', 'C1', 'D1', 'D3', 'E1', 'E2', 'E3', 'F1', 'F2', 'F3'] : ['B1', 'B2', 'B3']}
          currentStage={null}
          workflowStatus={activeListItem?.status}
          onNavigateToView={handleSelectView}
        />
      )}

      {activeView === 'settings' && (
        <SettingsView
          isBackendUnavailable={Boolean(initError)}
          onNavigateToView={handleSelectView}
        />
      )}
    </MainLayout>
  )
}

export default App