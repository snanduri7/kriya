import { PendingView, useRunEvidence, AvailabilityView, type ModuleDefinition } from '../../core';

function WhyPending() {
  const evidence = useRunEvidence();
  return <AvailabilityView value={evidence}>{() => <PendingView title="WHY" needs="diagnosis and gate evidence read through the evidence records (slice M3)" />}</AvailabilityView>;
}

export const whyModule: ModuleDefinition = {
  id: 'why',
  placement: { page: 'runs', surface: 'inspector', label: 'WHY', order: 20 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId'],
  whenUnavailable: 'explain',
  render: WhyPending,
};
