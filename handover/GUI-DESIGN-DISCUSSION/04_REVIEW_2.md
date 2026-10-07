# 04 — Review 2 (Claude, reviewer). Final review.

**Reviews:** `03_PROPOSAL_v2.md` (ChatGPT).
**Inputs:** `02_REVIEW_1.md`, `02a_OWNER_REQUIREMENTS.md`.
**Baseline:** `61a867fc`.
**Date:** 2026-10-04.
**Scope (README rule 4):** the R1 findings and the v2 changes only. A new issue is allowed only if it is a BLOCKER with MEASURED or TRACED evidence. There are none.

**Spot-checks of v2's TRACED claims (all confirmed):**
- `config.py:2090-2102`;
- `workflow.py:5472` (`prompt_rendered=plan_prompt`);
- `attempt.py:6995-7014` (`context.known_target_package`, with its omission list);
- `attempt.py:2632-2642` (`developer.prompt_composition`).

The local Python ships SQLite 3.37.2 (MEASURED in the review sandbox only, not on the owner's Mac).

## Verdict: ready for the owner's gate

There are no BLOCKERs and nothing is UNRESOLVED. v2 is a strong revision: it is honest about what the data can and cannot show, and its authority boundary is unchanged.

The two things that will decide whether M1 is worth building are both **owner decisions**, set out in §3:
- whether strict zero-write reads of the SQLite file work in common conditions;
- the data gaps in P-30.

## 1. Verdicts on the review-1 findings

| Finding | Verdict | Basis |
|---|---|---|
| R1-1 read-only JSON contract | **RESOLVED** | P-24 to P-27:<br>• a KUP envelope with `schema_version`, `source` and `consistency`;<br>• five operations;<br>• `prompt_rendered` excluded by default (P-25);<br>• cursor and pagination rules;<br>• an availability field for each panel. Extending `traces`, rather than adding a second history command, is a reasonable choice. |
| R1-2 zero-write inspection | **RESOLVED in design; acceptance depends on a measurement** | P-28/P-29: no logging, migration or pragmas; `immutable=1` rejected; typed refusal when reading would need a write; write interception, not just before/after inventories. **Feasibility risk:** see §3, item G-2. |
| R1-3 technology | **RESOLVED** | Superseded by 02a. The P-34 table rules out Tk and a local web server, keeps static HTML as a later export, and makes Electron a fallback decided by you. Prototype criteria are in P-35. |
| R1-4 history store vs workspace | **RESOLVED** | P-22: independent selectors; no ownership claimed without a stored link. |
| R1-5 matrix protection | **RESOLVED** | P-36: fixtures and fake processes only until you lift protection; isolated roots after that. |
| R1-6 version the contract | **RESOLVED** | P-24/P-27: a capabilities handshake; `UNSUPPORTED_SCHEMA_VERSION` vs `INVALID_RESPONSE`; the package version is not used for compatibility. |
| R1-7 location and packaging | **RESOLVED** | P-34: `ui/kup`, `ui/shared`, `ui/eclipse`, `ui/standalone`; no dependency from `kriya/` on any of them. |
| R1-8 corrections | **RESOLVED** | P-32 limits raised to 8 MiB stdout / 1 MiB stderr, checked against a 4 MiB event fixture; sanitisation kept as defence in depth. |

**Owner requirements:** OR-1 to OR-3, A-1 to A-5 and L-1 to L-5 are all ACCEPTED. The data limits are stated explicitly (P-23, P-30) rather than papered over. That is the right answer to 02a's data-dependency clause.

## 2. A correction to my own review 1

In review 1, I said a hostile workspace config "loses its repository-sourced authority". **That was wrong.** v2's P-28 is correct: `load_config` raises `ConfigAuthorityError` when approvals do not cover the violations (`config.py:2101-2102`). It fails closed; nothing is silently stripped.

**Consequence (not a new finding):** a KUP command run against an unapproved workspace will fail. The implementation must map that failure to a typed KUP error, for example `CONFIG_AUTHORITY_REFUSED`, and show it in the trust strip. It must never retry with a trust file. This is suggested as gate condition C-5.

## 3. For the owner at the gate (not findings)

**G-1. Most of the distinctive panels will say "not recorded" for real runs today.**
- P-30 traces what Kriya actually persists:
  - context tiers and omissions: **yes**;
  - token composition: **yes**;
  - per-attempt prompts: **no** (only the planning prompt);
  - rendered capability guidance: **no**;
  - before/after diffs: **no**;
  - causal attribution: **no**.
- So in M1 the Context panel is real. The Prompt, Output, Diff and Why panels are mostly empty on real history, and work fully only on fixtures.
- **My recommendation:**
  - accept that for M1, which is P-38 option 4 ("honest unavailable");
  - open a **separate telemetry topic** to persist per-attempt context packages, guidance, model output and diffs;
  - schedule it **after** the CAGC matrix analysis, because it changes workflow code.

  Without that topic, the GUI's main value stays on fixtures.

**G-2. Zero-write reading of the SQLite file may refuse in common conditions.**
- The facts:
  - Kriya always opens the store in WAL mode (`db.py:9-16`).
  - SQLite can read a WAL database without writing only when the `-wal`/`-shm` side files already exist and their shared-memory locking can be used, or when the database is opened `immutable` (which v2 correctly rejects).
- **INFERRED risk:** with a live writer, or with no side files present, a strictly write-denying read may often have to refuse. M1 would then show "store busy/unavailable" much of the time.
- **Check (half a day, first item of the P-35 prototype, fixtures only):**
  - measure `mode=ro` under write interception for the cases: no side files, side files present, an active writer, a read-only directory;
  - on the target Mac's SQLite version.
- **Decide the fallback before measuring**, so the decision cannot be changed after seeing the result. Either:
  - (a) typed refusal stands, even if common; or
  - (b) the owner defines a narrow exception: changes only to the `-shm` locking bytes, never to the database or `-wal` content.

**G-3. M1 is now large; consider phasing it.**
- M1 includes:
  - the KUP schema plus code generation in two languages;
  - the zero-write read adapter;
  - shared React panels;
  - an Eclipse plugin (Tycho build, update site);
  - a Tauri app (Rust toolchain, process host);
  - accessibility checks on both.
- **Correction of my own draft:** 02a OR-1 said "both delivery forms from day one". The owner's words were "independent UI app **and/or** a plugin to Eclipse to start with". I tightened that into "both in M1".
- **Recommended phasing:**
  - **M1a:** KUP, the read adapter, shared panels and the **Eclipse** plugin.
  - **M1b:** the Tauri standalone app on the same panels.
- This halves the first delivery without changing the architecture. Owner's call.

## 4. Suggested `05_GATE.md` conditions

| # | Condition |
|---|---|
| C-1 | Authorize the P-25 additions and the minimal initialization/read-adapter changes (P-38.1). Existing CLI behavior must be byte-identical when no KUP flags are given (test it). |
| C-2 | Zero-write is an acceptance prerequisite (P-38.2). Choose fallback (a) or (b) from G-2 **now**, before the measurement. |
| C-3 | Eclipse first. Choose M1 = Eclipse + Tauri (as in v2), or M1a/M1b (G-3). Pin the Eclipse release and JDK from the owner's installation. |
| C-4 | Accept "not recorded" panels for M1 (G-1). Open a telemetry topic after the CAGC matrix analysis. |
| C-5 | Map `ConfigAuthorityError` to a typed KUP error. No trust-file injection or retry from any shell. |
| C-6 | Allow ordinary Eclipse metadata for non-persistent, plugin-owned markers (P-38.6). Kriya, source, config and state stay write-free. |
| C-7 | Matrix protection ends only when the owner says the 80-run CAGC analysis is complete. Until then: fixtures and fakes only, and no paths under `~/kriya-cagc-v2/` or `~/.kriya` (P-36). |
| C-8 | Never push to GitHub. The local Eclipse update site and the Tauri build stay local until the owner decides otherwise. |

There is no third review. Next is `05_GATE.md` (owner).
