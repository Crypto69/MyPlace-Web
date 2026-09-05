import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // `npm run dev` on the Mac talks to the backend running on :8000
    proxy: { '/api': 'http://localhost:8000' },
  },
})
