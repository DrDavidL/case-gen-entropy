import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    // The backend serves its routes at the root (/sim-ready/..., /oracle/...), not under
    // an /api prefix, so each family is proxied explicitly. Port 5174, not 5173, so this
    // can run alongside direct-sim's dev server.
    proxy: Object.fromEntries(
      ['/sim-ready', '/oracle', '/cases', '/case', '/session', '/preview-case',
       '/edit-case', '/finalize-case', '/regenerate-lrs', '/final-orders', '/health']
        .map(p => [p, { target: 'http://localhost:8000', changeOrigin: true }]),
    ),
  },
  build: { outDir: 'dist' },
})
