import { Activity, Eye, EyeOff, Play, Satellite, Ship, Waves } from 'lucide-react'
import type { IncidentData } from '../../types/maris'

interface IncidentPanelProps { incident: IncidentData; layers: { spill: boolean; drift: boolean; vessels: boolean }; onToggleLayer: (layer: 'spill' | 'drift' | 'vessels') => void; onAnalyze: () => void; isAnalyzing: boolean }

function InfoRow({ label, value }: { label: string; value: string }) { return <div className="info-row"><dt>{label}</dt><dd>{value}</dd></div> }

export function IncidentPanel({ incident, layers, onToggleLayer, onAnalyze, isAnalyzing }: IncidentPanelProps) {
  return <aside className="panel incident-panel" aria-label="Incident and map controls">
    <div className="panel-heading"><div><span className="section-kicker">Case file</span><h2>Incident overview</h2></div><Activity size={18} className="heading-icon" aria-hidden="true" /></div>
    <section className="panel-section"><h3>Incident</h3><dl className="info-list"><InfoRow label="Incident ID" value={incident.id} /><InfoRow label="Region" value={incident.region} /><InfoRow label="Status" value={incident.status} /><InfoRow label="Detected" value={incident.detectionTime} /><InfoRow label="Updated" value={incident.lastUpdated} /></dl></section>
    <section className="panel-section"><div className="section-title-line"><h3>Satellite scene</h3><Satellite size={15} aria-hidden="true" /></div><img className="satellite-evidence" src={incident.satelliteImage} alt="Sentinel-1A SAR visualization of the reconstructed Corsica oil slick" /><dl className="info-list"><InfoRow label="Source" value={incident.satelliteSource} /><InfoRow label="Acquired" value={incident.acquisitionTime} /><InfoRow label="Scene status" value={incident.sceneStatus} /></dl></section>
    <section className="panel-section"><div className="section-title-line"><h3>Spill</h3><Waves size={15} aria-hidden="true" /></div><dl className="info-list"><InfoRow label="Status" value="Detected oil spill" /><InfoRow label="Est. area" value={incident.estimatedArea} /><InfoRow label="Confidence" value={incident.confidence} /></dl></section>
    <section className="panel-section controls-section"><h3>Controls</h3><button className="primary-button" type="button" onClick={onAnalyze} disabled={isAnalyzing}><Play size={15} fill="currentColor" /> {isAnalyzing ? 'Scene queued' : 'Analyze scene'}</button><div className="layer-controls"><LayerButton label="Spill overlay" icon={Waves} active={layers.spill} onClick={() => onToggleLayer('spill')} /><LayerButton label="Drift paths" icon={Activity} active={layers.drift} onClick={() => onToggleLayer('drift')} /><LayerButton label="Vessel tracks" icon={Ship} active={layers.vessels} onClick={() => onToggleLayer('vessels')} /></div></section>
    <div className="demo-note">DEMO SCENARIO - Static placeholder values</div>
  </aside>
}

function LayerButton({ label, icon: Icon, active, onClick }: { label: string; icon: typeof Waves; active: boolean; onClick: () => void }) {
  return <button className={`layer-button${active ? ' is-active' : ''}`} type="button" aria-pressed={active} onClick={onClick}>{active ? <Eye size={15} /> : <EyeOff size={15} />}<Icon size={14} /><span>{label}</span></button>
}