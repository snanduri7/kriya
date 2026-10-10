/** DEV/TEST ONLY entry. Production code must never import this module (a tripwire test enforces it). */
import type { UIProvider } from '../../core/provider';
import { HostProvider } from '../hostProvider';
import { FixtureHost, type FixtureHostOptions } from './fixtureHost';

export { FixtureHost, FIXTURE_SNAPSHOT_ID, type FixtureHostOptions } from './fixtureHost';
export { SCENARIOS, SCENARIO_IDS, type Scenario } from './scenarios';

/** Deterministic dev/test provider: the same HostProvider as production, over the in-memory fixture host. */
export function createFixtureProvider(options: FixtureHostOptions = {}): UIProvider {
  return new HostProvider(new FixtureHost(options), 'fixture');
}
