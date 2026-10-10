import { PendingView, useRunEvidence, AvailabilityView, type ModuleDefinition } from '../../core';

function ContextPending() {
  const evidence = useRunEvidence();
  return <AvailabilityView value={evidence}>{() => <PendingView title="Context" needs="recorded prompt sections and context budgets (slice M4)" />}</AvailabilityView>;
}

export const contextModule: ModuleDefinition = {
  id: 'context',
  placement: { page: 'runs', surface: 'center-tab', label: 'Context', order: 20 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId'],
  whenUnavailable: 'explain',
  render: ContextPending,
};
