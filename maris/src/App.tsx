import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Header } from './components/layout/Header'
import { InvestigationWorkspace } from './components/layout/InvestigationWorkspace'
import { MainLayout } from './components/layout/MainLayout'
import { listInvestigations } from './api/investigationApi'
import type { InvestigationListItem } from './types/investigationApi'

export function App() {
  const [investigations, setInvestigations] = useState<InvestigationListItem[]>([])
  const [activeInvestigationId, setActiveInvestigationId] = useState<string>('')
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(false)

  const loadInvestigationsList = useCallback(async () => {
    setIsLoadingList(true)
    setInitError(null)
    try {
      const list = await listInvestigations()
      setInvestigations(list)
      if (list.length > 0) {
        setActiveInvestigationId((curr) => {
          if (curr === 'corsica-2018-demo') return curr
          const exists = list.some((item) => item.id === curr)
          return exists ? curr : list[0].id
        })
      } else {
        // No live investigations registered
        setActiveInvestigationId((curr) => (curr === 'corsica-2018-demo' ? curr : ''))
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      // Store error - do not silently replace live backend with demo!
      setInitError(`Unable to connect to MARIS backend: ${msg}`)
      setActiveInvestigationId((curr) => (curr === 'corsica-2018-demo' ? '' : curr))
    } finally {
      setIsLoadingList(false)
    }
  }, [])

  useEffect(() => {
    loadInvestigationsList()
  }, [loadInvestigationsList])

  const isDemoMode = activeInvestigationId === 'corsica-2018-demo'
  const activeListItem = investigations.find((i) => i.id === activeInvestigationId)

  return (
    <MainLayout>
      <Header
        activeId={activeInvestigationId}
        investigations={investigations}
        activeStatus={activeListItem?.status}
        isDemoMode={isDemoMode}
        isBackendUnavailable={Boolean(initError)}
        onSelectInvestigation={setActiveInvestigationId}
        onOpenCreateModal={() => setIsCreateModalOpen(true)}
      />

      {initError && (
        <div className="workspace-error-banner" role="alert">
          <div className="error-banner-content">
            <AlertTriangle size={18} />
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

      <InvestigationWorkspace
        activeId={activeInvestigationId}
        isDemoMode={isDemoMode}
        investigations={investigations}
        onSelectInvestigation={setActiveInvestigationId}
        isCreateModalOpen={isCreateModalOpen}
        onCloseCreateModal={() => setIsCreateModalOpen(false)}
        onOpenCreateModal={() => setIsCreateModalOpen(true)}
        backendError={initError}
      />
    </MainLayout>
  )
}

export default App