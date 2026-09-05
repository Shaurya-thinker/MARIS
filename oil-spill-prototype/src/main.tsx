import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/global.css'
import 'maplibre-gl/dist/maplibre-gl.css'
import App from './App'

const rootElement = document.getElementById('root')

if (!rootElement) {
  throw new Error('MARIS root element was not found')
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)