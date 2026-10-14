# Scenario 00 - expected evidence (greenfield Hello World)

Nothing here has been measured live; it states what the product UI shows for each recorded fact and what a presenter may say. Controlled (no-model) UI evidence: `handover/DEMO-V2/evidence/product-ux-completion/`.

| Moment | What the screen shows | Where it comes from |
|---|---|---|
| Open Folder, empty folder | Project page, goal box and Start Run, a recommended **Set up this project** card | `execution.preflight`: no blockers; `configuration.file` null (packaged defaults) |
| Create Project Configuration | card goes, **ACTION REQUIRED** appears | `project.initialize` wrote `kriya.yaml`; `configuration.approval_status` = required |
| Review & Approve -> Continue to Approval | names of protected settings, native confirmation | SEC-009 approval, same backend path as `kriya authority approve` |
| Start Run | the page becomes the run: Planning, Editing, Validating | launch status + evidence records |
| Change review | the exact diff of every new file (+N lines each), why, verification so far | `change_approval.review`, proved against the review's digests |
| Run completed | what changed (`N files created`), why, verification (Build / Tests / Requested behavior / Safety), Application output only if recorded | RunRecord, trace row, `application_outcome` |
| Runs | one card titled with the goal you typed | `history.list` `display_goal` |

**Application.** `application_outcome` is EXECUTED only when Kriya's Runtime Verification gate recorded running a command; then the page shows the command, the exit code and the bounded, secret-scrubbed output, and
says "Ran successfully" only for a recorded success with exit code 0. Otherwise the section is absent. There is no "Ready to run / Copy command" state: Kriya records no authoritative start instruction.

**Presenter: say** - "the folder was empty", "Kriya showed me the exact files before I approved", "the build and tests are Kriya's own recorded gates". **Do not say** the model is reliable, that the application was run unless the Application section is on screen, or that the setup was required.
