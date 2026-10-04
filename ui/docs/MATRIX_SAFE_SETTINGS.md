# Settings UI during matrix protection

Date: 2026-10-04. Implemented by ChatGPT in the Antigravity checkout after the owner asked for useful features with minimal Mac load while the matrix continues. Baseline for these edits: `f4a213e`. Changes are local and uncommitted.

## Delivered

- Settings button and keyboard-accessible panel in the shared UI, using the existing HostAdapter only.
- Independently saved Kriya executable, configuration directory, recovery workspace and preferred editor.
- Blank configuration directory uses operator HOME; configuration and recovery workspace remain independent.
- Absolute-path/control-character input checks, save errors and explicit no-acquisition feedback. Existing main-process validation remains authoritative.
- Changes to executable/configuration/workspace reset the displayed browsing session, including pin, selection and pending-result state. No automatic acquisition. The new session refreshes only inspection observations through the existing host.
- Electron host information is refreshed after a setting save, so the trust strip reflects the new configuration directory.
- Settings take keyboard focus, background content is inert, Tab stays in the panel, Escape closes it and closing restores the Settings-button focus.

No new protocol operation, environment variable, Kriya permission or real-store access is introduced. Existing matrix-mode gating remains unchanged. No edits under `kriya/`, no live models, no Kriya CLI calls, no Electron launch, no dependency download, full-suite run, Java build, soak or packaging run occurred.

## Verification

MEASURED: shared suite 42 tests passed with one active worker (about 4.2 seconds), including four new settings tests covering independent configuration/workspace persistence, relative-path rejection and blank default, persistence refusal, and invalidating a pinned session without another acquisition. Shared and standalone TypeScript checks passed, shared lint passed, shared dependency-boundary check passed, and Git whitespace checks passed. Checks ran sequentially. An attempt to lower process priority was unavailable in the sandbox; test concurrency remained limited to one worker.

Manual Electron/VoiceOver verification of the new panel is still outstanding; earlier owner acceptance of the existing UI does not establish accessibility of this addition. The form uses typed paths; native file/folder picking would require a separately validated host capability. Host-side existence checks continue at command dispatch as before.

## Follow-up work suitable while the matrix runs

Work in short, sequential fixture-only batches: search/filter improvements for recorded events, clearer empty/error states, and lightweight fixture-based interaction checks. Avoid another soak, full check command, package builds, downloads or real Kriya integration during this protected interval. Preserve the handoff's holds on real-store integration, full-suite acceptance and main-repository merge. Never push to GitHub.
