import { Bell, CircleDot, Radio } from 'lucide-react'

export function Header({ incidentId }: { incidentId: string }) {
  return <header className="topbar">
    <div className="brand-lockup"><div className="brand-mark" aria-hidden="true"><Radio size={18} /></div><div><div className="brand-name">MARIS</div><div className="brand-description">Marine Intelligence &amp; Spill Attribution System</div></div></div>
    <div className="topbar__meta"><span className="incident-chip"><span>Incident</span> {incidentId}</span><span className="system-status"><CircleDot size={12} aria-hidden="true" /> System nominal</span><button className="icon-button" type="button" aria-label="View investigation alerts" title="Investigation alerts"><Bell size={17} /></button></div>
  </header>
}