import { defineConfig } from 'vite';

// ============================================================================
// Vite Config - Voice AI Agent Frontend
// ============================================================================
// Configura el bundler para:
//   1. Servir en desarrollo con proxy al backend (vite dev)
//   2. Build de producción: minificar, ofuscar, generar assets con hash
// ============================================================================

export default defineConfig({
  // Directorio raíz del frontend (donde está index.html)
  root: '.',

  // Base URL para producción (assets relativos)
  base: '/',

  // Servidor de desarrollo
  server: {
    port: 5173,
    // Proxy las peticiones /api al backend de FastAPI
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },

  // Build de producción
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Punto de entrada
    rollupOptions: {
      input: 'index.html',
    },
    // Generar sourcemaps solo en desarrollo
    sourcemap: false,
    // Minificación con Terser (elimina console.log, debugger, comentarios)
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
        // Elimina código muerto
        passes: 2,
      },
      format: {
        // Elimina todos los comentarios
        comments: false,
      },
      mangle: {
        // Renombra variables a identificadores cortos (ofusca parcialmente)
        properties: {
          regex: /^_/,
        },
      },
    },
    // Tamaño de los chunks
    chunkSizeWarningLimit: 1000,
    // Assets con hash para cache busting
    assetsInclude: ['**/*.png', '**/*.jpg', '**/*.svg', '**/*.ico'],
  },
});
