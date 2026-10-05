"""Re-run of the P4 'typed failure converted to success' mutant. Its first form
was incomplete (campaign defect): it set three of the four flags
GenerationState.final_workflow_quality_passed() requires, never
quality_gates_succeeded, so it could not change the outcome. The original
SURVIVED entries stay in p4_mutation_results.json and
p4_mutation_rerun_success_conversion.json (first re-run, after the reproducer
also asserted quality_gates_passed false); this faithful form's result is
p4_mutation_rerun_success_conversion_v2.json."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p4_mutation_campaign as campaign  # noqa: E402 - sibling evidence script

campaign.M = [("typed-failure-converted-to-success (faithful: all four success flags)", campaign.RS,
               "    state.no_progress_terminated = True\n    state.no_progress_reason = VERIFICATION_RETRY_NO_CHANGE_POSSIBLE\n",
               "    state.no_progress_terminated = True\n    state.no_progress_reason = VERIFICATION_RETRY_NO_CHANGE_POSSIBLE\n"
               "    state.candidate_gates_succeeded = state.overall_attempt_succeeded = True\n"
               "    state.terminal_regression_succeeded = state.quality_gates_succeeded = True\n")]
campaign.OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p4_mutation_rerun_success_conversion_v2.json")
sys.exit(campaign.main())
