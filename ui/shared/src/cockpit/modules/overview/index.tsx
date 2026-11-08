import { AvailabilityView, ApplicationResult, CopyText, HumanTimeline, KeyValue, Panel, VerificationLines, FormalRequirementDisclosure, formalRequirementStatus, primaryVerification, changeFacts, changeSummary, goalTitle, hasData, humanTimeline, ofKind, payloadOf, BeforeAfter, writeScopeOf, RunCapabilityStrip, RunMemoryLine, ReplayNotice, RequirementTruthBlock, requirementTruthFromEvents, requirementTruthWords, memoryRunIdOf, activeSkills, contextFacts, runCapabilityCells, runEventsOf, useExecution, useRunContext, useRunDetail, useRunEvidence, validationFacts, verificationGroups, type ChangedFile, type ModuleDefinition } from '../../core';

const OUTCOME = { success: { mark: '✓', words: 'Completed' }, failure: { mark: '✕', words: 'Run failed' }, in_progress: { mark: '●', words: 'Run not finished' } } as const;

/** The result of one recorded run, in meaning: what was asked, what happened, what changed, whether it works, what the application printed. Identifiers and mechanism sit under Technical status. */
function Overview() {
  const run = useRunContext();
  const execution = useExecution();
  const memory = execution.preflight.state === 'ready' ? execution.preflight.data.memory : undefined;
  const health = execution.preflight.state === 'ready' ? execution.preflight.data.health ?? [] : [];
  const evidence = useRunEvidence();
  const detail = useRunDetail();
  return (
    <AvailabilityView value={run}>
      {(ctx) => {
        const records = hasData(evidence) ? evidence.data.records : null;
        // capability states are statements about the run's evidence: shown only when that evidence is the verified, sealed chain (an unsealed or unreadable store claims nothing)
        // Verified Project Memory keys a run by the workflow's own run id, which the run's evidence records carry (the trace row's id is a different, shorter identifier)
        const memoryRunId = memoryRunIdOf(records, ctx.runId);
        const sealedEvidence = hasData(evidence) && evidence.data.verify?.status === 'VERIFIED' && evidence.data.verify?.sealed === true;
        const changes = records ? changeFacts(records) : null;
        const gates = records ? validationFacts(records) : null;
        const files: ChangedFile[] | null = changes ? changes.map((c) => ({ path: c.path, additions: c.linesAdded, deletions: c.linesRemoved, changeType: c.kind === 'ADDED' || c.kind === 'CREATED' ? 'added' as const : null })) : null;
        const groups = gates ? verificationGroups(gates) : null;
        const events = hasData(detail) ? detail.data.run_events.data ?? [] : [];
        const app = hasData(detail) && detail.data.application_outcome?.availability === 'recorded' ? detail.data.application_outcome.data ?? null : null;
        const outcome = OUTCOME[(ctx.status === 'success' || ctx.status === 'failure' || ctx.status === 'in_progress' ? ctx.status : 'in_progress') as keyof typeof OUTCOME];
        // The run's FINAL requirement truth from its own events (a stored run shows the same truth the live one did; a run recorded before the event existed shows none, never "verified").
        const truth = ctx.status === 'success' ? requirementTruthFromEvents(events) : null;
        const truthWords = requirementTruthWords(truth);
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            <section className="ck-overview" aria-label="Run result" data-status={ctx.status ?? 'unknown'}>
              <h3 className="ck-result-head"><span aria-hidden="true">{ctx.status ? (truthWords.tone === 'ok' ? outcome.mark : '⚠') : '•'}</span> {ctx.status ? (ctx.status === 'success' ? truthWords.heading : outcome.words) : 'Outcome not recorded'}</h3>
              <p className="ck-result-goal">{goalTitle(ctx.displayGoal)}</p>
              <ReplayNotice runId={ctx.runId} status={ctx.status} />
              {ctx.status === 'success' ? <p>{truthWords.applied ?? 'Your change was recorded as complete.'}</p> : null}
              <RequirementTruthBlock truth={truth} words={truthWords} />

              {sealedEvidence ? <RunCapabilityStrip cells={runCapabilityCells({
                modelCalls: records ? ofKind(records, 'model.response').length : null,
                gates: records ? { total: ofKind(records, 'gate.result').length, passed: ofKind(records, 'gate.result').filter((r) => payloadOf(r).success === true).length } : null,
                attempts: records ? { opened: ofKind(records, 'attempt.opened').length, concluded: ofKind(records, 'attempt.concluded').map((r) => payloadOf(r).succeeded === true) } : null,
                authorityRecorded: records ? ofKind(records, 'authority.snapshot').length > 0 : null,
                skills: activeSkills(hasData(detail) ? detail.data : null),
                knownTargetFiles: hasData(detail) ? contextFacts(runEventsOf(detail.data), null).knownTargets?.files.length ?? null : null,
                memoryEntry: memory?.runs.find((r) => r.evidence_run_id === memoryRunId || r.run_id === memoryRunId) ?? null,
                health,
              })} /> : null}
              <BeforeAfter request={goalTitle(ctx.displayGoal)} files={files} groups={groups ? groups.groups : null}
                concluded={records ? ofKind(records, 'attempt.concluded').map((r) => payloadOf(r).succeeded === true) : []} opened={records ? ofKind(records, 'attempt.opened').length : 0}
                securityScope={records ? writeScopeOf(ofKind(records, 'authority.snapshot').map((r) => payloadOf(r))) : null} />

              <h3>What changed</h3>
              {files === null ? <p className="muted">The changed files come from this run&apos;s evidence, which is not readable here (see Technical status).</p>
                : files.length === 0 ? <p className="muted">No staged change is recorded for this run.</p>
                : <><p className="muted small">{changeSummary(files)}</p><ul aria-label="Changed files">{files.map((c) => <li key={c.path}><span className="mono">{c.path}</span> <span className="muted small">{c.additions !== null && c.deletions !== null ? `+${c.additions} −${c.deletions}` : ''}</span></li>)}</ul></>}

              <h3>Verification</h3>
              {groups === null ? <p className="muted">The verification results come from this run&apos;s evidence, which is not readable here.</p>
                : groups.groups.length === 0 ? <p className="muted">No verification result is recorded for this run.</p>
                : <VerificationLines lines={primaryVerification(groups.technical)} />}

              {app ? <ApplicationResult app={app} /> : null}
              {groups ? <FormalRequirementDisclosure status={formalRequirementStatus(groups.technical)} /> : null}

              <RunMemoryLine memory={memory} runId={memoryRunId} />

              <h3>What happened</h3>
              <HumanTimeline entries={humanTimeline(events, ctx.status)} />
            </section>
            <details className="ck-tech">
              <summary>Technical status</summary>
              <Panel title="Run">
                <KeyValue items={[
                  ['Run ID', <span key="rid"><span className="mono">{ctx.runId}</span> <CopyText text={ctx.runId} what="run ID" /></span>],
                  ['Stored goal', ctx.goal ?? 'not recorded'], ['Stored status', ctx.status === 'in_progress' ? 'Unfinished (the trace row says in_progress; this is not a claim that a run is active)' : ctx.status ?? 'not recorded'],
                  ...(ctx.failureCategory ? [['Failure category', <span key="fc">{ctx.failureCategory} <CopyText text={ctx.failureCategory} what="failure category" /></span>] as const] : []),
                  ['Workspace', ctx.workspace.identity ?? 'not recorded'],
                  ['Baseline revision', ctx.baseline.revision ?? 'not recorded (no baseline is assumed)'], ['Baseline tree', ctx.baseline.treeHash ?? 'not recorded'],
                  ['Evidence store', ctx.evidence.store === 'present' ? `present, capture ${ctx.evidence.captureMode ?? 'not recorded'}` : 'absent'],
                  ['Trace row "attempts" (as stored)', ctx.storedAttempts === null ? 'not recorded' : `${ctx.storedAttempts} - Kriya's retry counter (retries + best-of-N candidates), not the number of attempts made; the attempt count below is derived from the run's evidence`],
                ]} />
              </Panel>
              <AvailabilityView value={evidence}>
                {(data) => {
                  const attempts = ofKind(data.records, 'attempt.opened').length;
                  const concluded = ofKind(data.records, 'attempt.concluded').map((r) => payloadOf(r).succeeded === true);
                  const closed = ofKind(data.records, 'run.closed')[0];
                  return (
                    <Panel title="Recorded outcome">
                      <KeyValue items={[
                        ['Attempts opened', String(attempts)],
                        ['Attempts concluded', concluded.length ? concluded.map((ok, i) => `#${i + 1} ${ok ? 'succeeded' : 'failed'}`).join(', ') : 'none recorded'],
                        ['Run closed', closed ? String(payloadOf(closed).terminal_status ?? 'terminal status not recorded') : 'no run.closed record'],
                      ]} />
                    </Panel>
                  );
                }}
              </AvailabilityView>
            </details>
          </div>
        );
      }}
    </AvailabilityView>
  );
}

export const overviewModule: ModuleDefinition = {
  id: 'overview',
  placement: { page: 'runs', surface: 'center-tab', label: 'Overview', order: 10 },
  requiredCapabilities: ['snapshot'], optionalCapabilities: ['evidence'], selectionDependencies: ['runId'],
  render: Overview,
};
