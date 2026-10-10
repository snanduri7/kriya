import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AppShell, createCockpitRegistry, createKriyaProvider, type UIProvider } from '@kriya-ui/shared/src/cockpit';
import '@kriya-ui/shared/src/styles.css';
import { ElectronHostAdapter, type KriyaHostBridge } from './ElectronHostAdapter';

/**
 * The provider is chosen by the BUILD, never at runtime and never as a fallback: a production build can only contain KriyaProvider
 * (the fixture branch is statically dead and is not bundled); a development build made with VITE_KRIYA_PROVIDER=fixture can only
 * contain FixtureProvider.
 */
async function createProvider(bridge: KriyaHostBridge): Promise<UIProvider> {
  if (import.meta.env.VITE_KRIYA_PROVIDER === 'fixture') {
    const { createFixtureProvider } = await import('@kriya-ui/shared/src/cockpit/providers/fixture');
    return createFixtureProvider();
  }
  return createKriyaProvider(await ElectronHostAdapter.create(bridge));
}

export async function startCockpit(bridge: KriyaHostBridge): Promise<void> {
  const provider = await createProvider(bridge);
  createRoot(document.getElementById('root')!).render(<StrictMode><AppShell provider={provider} registry={createCockpitRegistry()} /></StrictMode>);
}
