/** The fixture host's `skills.*` / `tools.*` answers are in exactly the shape Kriya's backend emits: they validate against the generated validators (the same JSON Schema
 * the Python tests validate Kriya's real output against). Without this, component tests could pass on shapes the real producer never sends. */
import { validateData } from '@kriya-ui/kup';
import { describe, expect, it } from 'vitest';
import type { KupRequest } from '../../../model/kup';
import { FixtureHost, type FixtureContextMode } from './fixtureHost';

const W = '/fixture/workspace';
const requests: KupRequest[] = [
  { operation: 'skills.list', workspace: W }, { operation: 'skills.describe', workspace: W, skill_id: 'python-testing' }, { operation: 'skills.describe', workspace: W, skill_id: 'absent' },
  { operation: 'tools.list', workspace: W }, { operation: 'tools.describe', workspace: W, tool_id: 'mcp:filesystem' }, { operation: 'tools.describe', workspace: W, tool_id: 'mcp:absent' },
  { operation: 'tools.approval_state', workspace: W, tool_id: 'mcp:issues' }, { operation: 'tools.approval_state', workspace: W, tool_id: 'plugin:core_tools' }, { operation: 'tools.approval_state', workspace: W, tool_id: 'mcp:absent' },
];

describe.each<FixtureContextMode>(['full', 'empty', 'many', 'unavailable'])('fixture catalog answers (%s) validate against the KUP schema', (mode) => {
  it.each(requests)('$operation', async (request) => {
    const env = await new FixtureHost({ workspace: W, contextMode: mode }).query(request);
    expect(env.error).toBeNull();
    const checked = validateData(request.operation, env.data);
    expect(checked.ok ? [] : checked.errors).toEqual([]);
  });
});

describe('the schema is what makes "approved" impossible', () => {
  it('an approval state outside NOT_APPROVED / UNKNOWN / NOT_APPLICABLE fails validation', () => {
    const good = { workspace: { path: W, name: 'w', identity: 'x' }, available: true, unavailable_reason: null, authority_status: 'no_findings', found: true, tool_id: 'mcp:a',
      approval: { state: 'UNKNOWN', reason: 'RECORDS_NEED_LIVE_TOOL_IDENTITY', records_on_file: 1, recorded_tool_names: ['t'] } };
    expect(validateData('tools.approval_state', good).ok).toBe(true);
    expect(validateData('tools.approval_state', { ...good, approval: { ...good.approval, state: 'APPROVED' } }).ok).toBe(false);
    expect(validateData('tools.approval_state', { ...good, approval: { ...good.approval, reason: 'ANYTHING_ELSE' } }).ok).toBe(false);
  });
});
