# D5b: RECOVERY-EVIDENCE-EXCEPTION-HEADER-001 evidence

Product commit `00bcbd4`, a correction to D5 (`4834164`, own defect). Found by KNOW A on demo-runtime-5; that run is
kept unchanged at `~/kriya-live-demo/demo-01-skills/evidence/v4/run-a-20261001T130217Z/` (CLASSIFICATION.md).

- `live_runtime5_s4_output.txt`: the s4 runtime failure's captured output, verbatim from that run's stderr.log (also
  `tests/fixtures/d5_runtime_resource/live_runtime5_s4.txt`).
- `d5b_reproducer.py`: model-free; the excerpt a reopened owner is shown, for the runtime-5 stack-frame failure and the
  runtime-3 resource failure.
  - `d5b_reproducer_prefix.txt`: stack-frame case 837 chars, starting at the frame; no IgniteIllegalStateException, no
    message. Resource case correct.
  - `d5b_reproducer_postfix.txt`: stack-frame case starts at
    `class org.apache.ignite.IgniteIllegalStateException: Ignite instance with provided name doesn't exist...` and
    includes `IgniteDemoApp.java:19`. The resource case excerpt is byte-identical before and after (compared with the
    change stashed).
- `mutate_d5b.py` / `mutation_d5b.txt`: 8/8 killed. On the first run `frames-not-skipped` survived (no test had a
  frame more than 5 lines below its header); a deep-frame test was added.
- `full_suite_tail.txt`: 8042 passed, 0 failed, 0 skipped, 14 warnings. The new tests under
  `-W error::ResourceWarning`: 12 passed, none emitted.
- `doctor_v4.json`: v4 config unchanged, run serially: production_ready=true (WARNs as at the earlier dev-checkout
  doctors).
