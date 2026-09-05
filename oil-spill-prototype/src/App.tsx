import { Header } from './components/layout/Header'
import { InvestigationWorkspace } from './components/layout/InvestigationWorkspace'
import { MainLayout } from './components/layout/MainLayout'
import { incidentData } from './data/demoData'

function App() {
  return <MainLayout><Header incidentId={incidentData.id} /><InvestigationWorkspace /></MainLayout>
}

export default App