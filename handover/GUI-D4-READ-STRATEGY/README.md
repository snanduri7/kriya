# Topic: GUI-D4-READ-STRATEGY — how the KUP inspector reads Kriya's WAL store

**Opened because:** gate decision D-4 of `GUI-DESIGN-DISCUSSION/05_GATE.md` (typed refusal, no `immutable=1`) was measured to refuse **every** real store (`07_REVIEW_STOP_POINT_1.md` §1). D-4 says it may be revisited only in a new topic, so this is that topic.

**Process:** the same as `GUI-DESIGN-DISCUSSION/README.md`, kept small:
1. `01_PROPOSAL.md` — Claude.
2. `02_REVIEW.md` — ChatGPT; one review only.
3. `03_GATE.md` — owner.

There is no second review. Anything disputed after the review goes to the owner.

**Out of scope:**
- every other gate decision;
- any change to how Kriya writes its store (journal mode, checkpoints). That would affect the running CAGC experiment's code path and needs its own topic.
