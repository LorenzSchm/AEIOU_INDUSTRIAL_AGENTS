import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  // The build is committed into the Python package so `aeiou serve` and Docker work without Node.
  build: { outDir: '../src/aeiou/web', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  // Keep the browser's Host header so the backend's same-origin guard on POSTs still passes in dev.
  server: { port: 5173, proxy: { '/api': { target: 'http://localhost:8765', changeOrigin: false } } },
})
