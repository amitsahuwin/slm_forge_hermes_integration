import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { installFetchInterceptor } from './auth/fetchInterceptor';
import './index.css';

// IMPORTANT: install the global fetch interceptor BEFORE React mounts so
// 100% of API calls — including ones from libraries and dynamically-imported
// modules — pick up the Bearer token. This is the single source of truth for
// browser → API auth, replacing the per-page `authFetch` wiring.
installFetchInterceptor();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
