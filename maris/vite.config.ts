/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    // Switch from forks (default) to threads to avoid Windows IPC timeout
    pool: 'threads',
    // Give heavy React+MapLibre mocks enough time to initialise
    testTimeout: 30000,
    hookTimeout: 30000,
    // Silence noisy console noise from render errors in tests
    silent: false,
  },
})