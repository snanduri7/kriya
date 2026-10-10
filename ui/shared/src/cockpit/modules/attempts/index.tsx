import { PendingView, useRunEvidence, AvailabilityView, type ModuleDefinition } from '../../core';

function AttemptsPending() {
  const evidence = useRunEvidence();
  return <AvailabilityView value={evidence}>{() => <PendingView title="Attempts" needs="the attempt table and comparison over retry.delta (slice M4)" />}</AvailabilityView>;
}

export const attemptsModule: ModuleDefinition = {
  id: 'attempts',
  placement: { page: 'runs', surface: 'center-tab', label: 'Attempts', order: 60 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId'],
  whenUnavailable: 'explain',
  render: AttemptsPending,
};
