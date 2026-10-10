import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App, type AppDriver } from '@kriya-ui/shared';
import '@kriya-ui/shared/src/styles.css';
import { ElectronHostAdapter, type KriyaHostBridge } from './ElectronHostAdapter';

declare global { interface Window { kriyaHost: KriyaHostBridge; __kriyaDriver?: AppDriver } }

async function start() {
  // Build-time choice. The Run Cockpit IS the product (M1 inspector retired from shipped UI, owner decision Q-4, parity in 08_M7). The inspector
  // exists only in an explicit legacy build (`npm run build:inspector-legacy`, VITE_KRIYA_UI=inspector), for the M1 evidence tests; there is no runtime route to it.
  if (import.meta.env.VITE_KRIYA_UI !== 'inspector') {
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
