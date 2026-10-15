/**
 * Characterization of the owner's real, successful live Demo 00 run (20261015T200748-08db1057) and of the real failed Greenfield run (20261015T152407-5f87716a): the records are the ones Kriya wrote and the GUI
 * received (handover/DEMO-V2/evidence/live-greenfield-success, live-greenfield-failure). They pin what a person is shown for a run of each kind - never a hand-made approximation of one.
 */
import { describe, expect, it } from 'vitest';
import { activityFromRecords, resultsFromRecords } from './activity';
import { gateSteps } from './gateSteps';
import { formalRequirementStatus, primaryVerification } from './presentation';
import { validationFacts } from './runFacts';
import success from '../testing/live-greenfield-success.records.json';
import failure from '../testing/live-greenfield-failure.records.json';
import type { EvidenceRecord } from './records';

const live = success as unknown as EvidenceRecord[];
const failed = failure as unknown as EvidenceRecord[];

describe('the live Demo 00 run: gate records are grouped into the checks a person counts', () => {
  it('the recorded gate records pair up by structure: validator result + workflow outcome are ONE step; the runtime build prerequisite adds nothing', () => {
    const steps = gateSteps(live);
    expect(steps.map((s) => [s.sources, s.type, s.folded])).toEqual([
      [[56], 'pom_validate', false],
      [[57, 58], 'compile', false],
      [[59, 60], 'targeted_test', false],
      [[64], 'compile', true],          // the build the runtime check repeats as its own prerequisite (no outcome of its own; it passed)
      [[65, 69], 'run_verification', false],
      [[75], 'goal_spec_compliance', false],
      [[78, 79], 'regression_test', false],
    ]);
    expect(steps.find((s) => s.type === 'targeted_test')!.testSummary).toMatchObject({ cases: 1 }); // the count comes from the validator side of the pair
  });

  it('Activity is concise: one build, the unit tests, the runtime check, the regression run - no duplicate-looking lines', () => {
    const texts = activityFromRecords(live).map((e) => e.text);
    expect(texts).toEqual(['Run started', 'Working on the change', 'Creating project files', 'Build passed', 'Tests passed (1 test)', 'Runtime verification passed', 'Regression tests passed (1 test)', 'Run completed']);
    expect(new Set(texts).size).toBe(texts.length);
  });

  it('every record stays reachable: nothing is dropped from the evidence, only grouped for the person', () => {
    const grouped = gateSteps(live).flatMap((s) => s.sources).sort((a, b) => a - b);
    const recorded = live.filter((r) => r.kind === 'gate.result' || r.kind === 'mirror.gate_outcome').map((r) => r.seq).sort((a, b) => a - b);
    expect(grouped).toEqual(recorded);
  });

  it('the Build and Tests tabs list each check once, the two test runs told apart', () => {
    expect(resultsFromRecords(live, 'build').map((r) => r.text)).toEqual(['Build passed']);
    expect(resultsFromRecords(live, 'tests').map((r) => r.text)).toEqual(['Tests passed (1 test)', 'Regression tests passed (1 test)']);
  });

  it('the primary result is build, tests and runtime verification; the formal ledger is separate and says "not fully verified"', () => {
    const gates = validationFacts(live);
    expect(primaryVerification(gates).map((l) => l.text)).toEqual(['Build passed', '1 / 1 tests passed', 'Runtime verification passed']);
    const formal = formalRequirementStatus(gates)!;
    expect(formal.headline).toBe('Requirement ledger: not fully verified');
    expect(formal.notes.map((l) => l.text)).toEqual(['Matches the planned change']);
    expect(primaryVerification(gates).map((l) => l.text).join('|')).not.toMatch(/Requested behavior verified|Overall request/);
  });
});

describe('the live failed Greenfield run: every failure is still visible after the grouping', () => {
  it('each rejected attempt keeps its "rejected" line and reason; no failure is folded into another record', () => {
    const steps = gateSteps(failed);
    expect(steps.filter((s) => s.success === false).every((s) => !s.folded)).toBe(true);
    const texts = activityFromRecords(failed).map((e) => e.text);
    expect(texts.filter((t) => /^Attempt \d+ rejected$/.test(t))).toEqual([1, 2, 3, 4, 5, 6, 7, 8].map((n) => `Attempt ${n} rejected`));
    expect(texts).toContain('A static quality rule was violated.');
    expect(texts).toContain('The model response format was invalid.');
  });
});
