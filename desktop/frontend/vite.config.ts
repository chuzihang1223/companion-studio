import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'

export default defineConfig(({ command }) => ({
  root: resolve(__dirname),
  base: command === 'build' ? '/app/' : '/',
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: { outDir: resolve(__dirname, 'dist'), emptyOutDir: true },
}))
