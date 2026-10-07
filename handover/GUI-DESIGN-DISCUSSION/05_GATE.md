# 05 — Gate (owner decision)

**Owner:** Sriram.
**Date:** 2026-10-04.
**Drafted by:** Claude, from the owner's decisions in conversation. The owner made these decisions; this file records them.

**Inputs:**
- `03_PROPOSAL_v2.md`;
- `04_REVIEW_2.md`, which found no BLOCKERs and nothing UNRESOLVED;
- the owner's delivery-order decision after both reviewers' advice;
- ChatGPT agreed to that order on 2026-10-04.

## Decision: **APPROVED WITH CONDITIONS**

`03_PROPOSAL_v2.md` is the approved design, **as amended by the conditions below**. Where they conflict, the conditions win. `01_PROPOSAL.md` is superseded. Where `02a_OWNER_REQUIREMENTS.md` conflicts with this gate, the gate wins: OR-1 "Eclipse first" is replaced by D-1.

## Owner decisions

| # | Decision |
|---|---|
| **D-1. Delivery order** | **M1 = the standalone app:**<br>• KUP v1;<br>• the read-only Kriya inspection adapter and CLI additions;<br>• shared React/TypeScript panels;<br>• the Electron shell (amended by A-1, below);<br>• "open in IDE" links.<br>~~M2 = Eclipse plugin, M3 = VS Code plugin~~ (withdrawn by A-2, below). |
| **D-2. Later topics, not authorized here** | Each needs its own topic folder and gate:<br>• live progress through `kriya ui-server --stdio` (v2's old "M2");<br>• actions and approvals through KUP (v2's old "M3");<br>• the telemetry-persistence work (04 G-1). |
| **D-3. Authorized Kriya change** | The P-25 inspection additions and the minimal initialization / read-adapter changes for strict no-write inspection. Existing CLI behavior must be **byte-identical** when no KUP flag is given. |
| **D-4. Zero-write fallback (decided before measurement)** | **Typed refusal stands.** If strictly write-free reading of the store is impossible in a given state, return `STORE_BUSY` / `READ_ONLY_UNAVAILABLE`, even if that happens often. No `immutable=1`, and no `-shm` exception. Revisit only in a new topic. |
| **D-5. Data gaps** | Accept honest "not recorded" panels in M1 (04 G-1, P-30). Nothing is invented or reconstructed. Telemetry persistence is a separate topic, after the CAGC 80-run analysis. |
| **D-6. History scope** | History stays tied to the history store. No workspace filtering without a recorded ownership link. |
| **D-7. Config refusal** | `ConfigAuthorityError` maps to the typed KUP error `CONFIG_AUTHORITY_REFUSED`. No trust-file injection, and no retry from any shell. |
| **D-8. Eclipse metadata** | Suspended with the plugins (A-2). It applies again if a plugin topic is ever approved. |
| **D-9. Matrix protection** | Until the owner states that the CAGC 80-run analysis is complete:<br>• fixtures and fake processes only;<br>• no runs of the real `kriya` CLI;<br>• no paths under `~/kriya-cagc-v2/` or `~/.kriya`;<br>• no live model runs. |
| **D-10. Repository rules** | **Never push to GitHub.** Commit locally on `codex/fix-demo1-attribution`. Keep Kriya-side changes in **separate, self-contained commits** so the owner can move them to the main Kriya repository later. |


## Owner amendments (2026-10-04, before implementation started; no review reopened)

| # | Amendment |
|---|---|
| **A-1. Shell = Electron, replacing Tauri** | **Reason:** the owner requires the UI to look the same, and stay stable, on every platform. Electron ships its own Chromium engine, so rendering is identical everywhere, and its packaging and auto-update are mature. Tauri uses each operating system's own engine.<br>**Accepted cost:** higher idle memory (about 100–300 MB) and a larger download.<br>**Required hardening:**<br>• `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`;<br>• a strict CSP; no remote content; no `webview` tag;<br>• navigation and new windows blocked;<br>• a preload exposes only a typed allowlist of messages between the UI and the native layer;<br>• only the main process spawns processes, and only the allowlisted `kriya` commands. |
| **A-2. Plugins dropped for now; the design stays ready for them** | No Eclipse or VS Code plugin is planned. Any future plugin needs its own topic folder and gate.<br>**M1 must stay plugin-ready:**<br>• **P-R1:** `ui/shared` is a host-independent package. It must not import Electron or Node APIs. Every host capability goes through one typed `HostAdapter` interface: run a KUP query, open in IDE, copy to clipboard, read and write settings.<br>• **P-R2:** the host contract is a schema in `ui/kup`, next to KUP. It is not ad-hoc Electron messaging.<br>• **P-R3:** a second, minimal **test host** (a plain browser page with a fake `HostAdapter` and fixtures) must render every panel in CI. This proves the panels do not depend on Electron.<br>• **P-R4:** KUP stays language-neutral: JSON Schema, plus a check that generates Java types from it, so a future Java/Eclipse host is not ruled out.<br>• **P-R5:** "open in IDE" stays in M1. It covers navigation to the code without any plugin. |

## Folder status

The design discussion is **closed**. Implementation instructions are in `06_IMPLEMENTATION_INSTRUCTIONS_M1.md`. Any design change from here starts a new topic folder.
