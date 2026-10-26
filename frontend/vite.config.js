import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the React app talks to the FastAPI backend through this proxy,
// so the browser only ever sees one origin and the backend needs no CORS setup.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'node',
  },
});
