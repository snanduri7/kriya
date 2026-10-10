import type { HostAdapter } from '../../host/HostAdapter';
import type { UIProvider } from '../core/provider';
import { HostProvider } from './hostProvider';

/** Production provider: supported Kriya read APIs through the typed host. Takes a real host; there is nothing to fall back to. */
export function createKriyaProvider(host: HostAdapter): UIProvider {
  return new HostProvider(host, 'kriya');
}
