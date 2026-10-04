import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App, type AppDriver } from '@kriya-ui/shared';
import '@kriya-ui/shared/src/styles.css';
import { BrowserFixtureHost } from './BrowserFixtureHost';

const params = new URLSearchParams(location.search);
const host = new BrowserFixtureHost('', params.get('scenario'), Number(params.get('delay') ?? 0));
declare global { interface Window { __kriyaDriver?: AppDriver } }
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App host={host} exposeDriver={(d) => { window.__kriyaDriver = d; }} />
  </StrictMode>,
);
