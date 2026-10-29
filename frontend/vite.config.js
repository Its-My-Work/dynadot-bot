import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The TMA and the admin panel are two separate entry HTML files sharing one
// bundle: Telegram loads `index.html` while operators open `/admin.html`, and
// a single build keeps the API client and design tokens in sync between them.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        main: 'index.html',
        admin: 'admin.html',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev-only: lets the TMA call the API without CORS during development.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
  },
});
