import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App, type AppDriver } from '@kriya-ui/shared';
import '@kriya-ui/shared/src/styles.css';
import { ElectronHostAdapter, type KriyaHostBridge } from './ElectronHostAdapter';

declare global { interface Window { kriyaHost: KriyaHostBridge; __kriyaDriver?: AppDriver } }

async function start() {
  // Build-time choice: the Run Cockpit replaces the M1 inspector only after parity is accepted (owner decision Q-4).
  if (import.meta.env.VITE_KRIYA_UI === 'cockpit') {
    const { startCockpit } = await import('./cockpit');
    await startCockpit(window.kriyaHost);
    return;
  }
  const host = await ElectronHostAdapter.create(window.kriyaHost);
  const measure = window.kriyaHost.measureMode;
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App host={host} exposeDriver={measure ? (d) => { window.__kriyaDriver = d; } : undefined} />
    </StrictMode>,
  );
}
void start();
