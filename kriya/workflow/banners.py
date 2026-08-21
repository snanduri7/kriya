"""Console/log banner for one Developer + Quality Gates attempt's outcome.

Deliberately separate from kriya/workflow/workflow.py's _log_phase_banner,
which announces a top-level pipeline phase (Planning/Architecture/
Development/Review) exactly once as the pipeline moves forward. A Quality
Gate outcome is a different kind of event: it fires once per attempt
*inside* the single "DEVELOPMENT & QUALITY GATES" phase, and is expected to
repeat across retries - so it gets its own attempt-scoped banner with a
distinct (dashed) border, to read as "attempt outcome" rather than "new
pipeline stage" when scanning a live run's console/log output.

Lives in its own module rather than workflow.py because both
kriya/workflow/attempt.py (the PASSED site) and kriya/workflow/
retry_strategy.py (the FAILED site) need to call it, and workflow.py already
imports from both of those - importing this from workflow.py instead would
be circular.
"""
import logging

logger = logging.getLogger(__name__)

_QUALITY_GATE_BANNER_WIDTH = 70


def log_quality_gate_banner(status: str, attempt_number: int, detail: str = "") -> None:
    """Logs a dashed-border banner for one attempt's Quality Gates outcome.
    `status` is "PASSED" or "FAILED"; PASSED logs at INFO, FAILED at WARNING
    to match the severity of what each previously logged standalone. `detail`
    carries whatever line-level context the call site already had (retry
    mode, budget counts, the error itself) below the banner - the banner
    itself only needs to announce which attempt this is and how it went.

    Deliberately no "N/M" denominator next to attempt_number: there is no
    single fixed ceiling attempt_number can be validly divided against - it
    counts every attempt across full-set, targeted, AND fallback-targeted
    modes combined, while the only caps that exist (ctx.max_retries,
    ctx.targeted_max_retries) each bound just one of those independently-
    tracked budgets, and the targeted budget resets whenever a new failure
    family appears (see retry_strategy.py). An earlier version paired
    attempt_number with ctx.max_retries and printed "Attempt 5/4" on a live
    run - caught by a human reading it, not a test, because nothing here
    enforces the two numbers stay comparable. `detail` (full-set X/Y +
    targeted A/B) is where the real, currently-accurate budget picture
    belongs.

    Purely cosmetic (log-scanning aid for a human watching a run) - never
    gates or changes control flow."""
    bar = "-" * _QUALITY_GATE_BANNER_WIDTH
    title = f"QUALITY GATE - Attempt {attempt_number}: {status}"
    message = f"\n{bar}\n{title.center(_QUALITY_GATE_BANNER_WIDTH)}\n{bar}"
    if detail:
        message += f"\n{detail}"
    (logger.info if status == "PASSED" else logger.warning)(message)
