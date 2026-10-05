# Manual accessibility walkthrough (concise script; every step PENDING until a human performs it)

Companion to `A11Y_MANUAL_CHECKLIST.md` (item ids in brackets). Perform on the Electron shell (fixture stand-in is fine for
this walkthrough) or the browser test host. Record the result beside each step; leave "pending" where not performed.

Setup: window at normal width; keyboard only until §F; VoiceOver off until §E.

## A. Settings dialog (K2, K3, K4, V1-V3)

| step | do | expect | result |
|---|---|---|---|
| A1 | Tab to "Settings", press Enter | dialog opens; focus is on the "Settings" title (a focus ring on the heading) | pending |
| A2 | Tab repeatedly | Close settings -> Kriya executable field -> Save kriya executable -> Configuration directory -> Save ... -> Recovery workspace -> Save ... -> Preferred editor -> Save preferred editor -> wraps to Close settings | pending |
| A3 | Shift+Tab from Close settings | focus wraps to Save preferred editor | pending |
| A4 | Type `relative/path` in Configuration directory, Tab to its Save, Enter | error text appears under the fields; the field shows an invalid state; focus does not jump away | pending |
| A5 | Clear the field, Save again | "Saved. No snapshot was acquired." appears once | pending |
| A6 | Press Escape | dialog closes; focus is back on the "Settings" button | pending |

## B. Timeline filtering and selection (K6, K7, K8)

| step | do | expect | result |
|---|---|---|---|
| B1 | Acquire a snapshot, Tab into the runs list, ArrowDown to a run, Enter is not needed (selection follows the arrow) | the run opens; the timeline shows its events | pending |
| B2 | Tab to the events list, press ArrowDown twice | the second event is selected and highlighted; the Inspector switches to Evidence and shows that event | pending |
| B3 | Shift+Tab back to "Search recorded events", type `model` | the list narrows while typing; the visible count changes with each keystroke | pending |
| B4 | Type a query that hides the selected event | the notice "selected event #N (...) is hidden by the current filter; it stays selected ..." appears with a button | pending |
| B5 | Tab to "Clear filter to show the selected event", press Enter | the filter clears, the selected event is back and highlighted, focus is on the events list (not lost; the next ArrowDown moves from the selected event) | pending |
| B6 | Tab to "Clear filter" with no filter active | the button is disabled and skipped by Tab | pending |

## C. Inspector tabs (K9, K11)

| step | do | expect | result |
|---|---|---|---|
| C1 | Tab to the inspector tab row | focus lands on the active tab only | pending |
| C2 | ArrowRight x2, End, Home, ArrowLeft | the focused tab changes and its panel shows each time; ArrowLeft from the first tab wraps to the last | pending |
| C3 | Tab from the Gates tab | focus enters the Gates panel (first gate record's Copy button), never a hidden panel | pending |
| C4 | On a gate record with a long output, Tab to "Show more" and press Enter | more output is revealed; focus stays on the control | pending |

## D. Evidence and attribution drawer (K10, V9)

| step | do | expect | result |
|---|---|---|---|
| D1 | With the drawer closed, Tab through its bar | "Show drawer" and one tab only (the active one) are reachable | pending |
| D2 | Press Enter on "Why" | the drawer opens on Why: failure category, failure report, attribution (or "not recorded" with Kriya's reason) | pending |
| D3 | Open `run-negative-evidence-links` (fixtures) -> Why | each evidence id reads "<id> — unresolved reference"; the explanation says no namespace is defined and nothing is verified attribution; "Repeated within the list: ev-negative-1" | pending |
| D4 | Open `run-attribution-demo` -> Why | "none recorded (empty list)" | pending |
| D5 | Press Enter on "Hide drawer" | the panel disappears; Tab reaches only the bar again | pending |

## E. VoiceOver (V1-V10) - turn VoiceOver on, repeat the relevant steps

| step | do | expect | result |
|---|---|---|---|
| E1 | A1 | "Settings, dialog" then "Settings, heading level 2" | pending |
| E2 | A4 | the error is spoken once as an alert; the field reports "invalid data" with the error text as its description | pending |
| E3 | A5 | "Saving…" then "Saved. No snapshot was acquired." once each; nothing repeats while idle | pending |
| E4 | B3 | NOT one announcement per keystroke; after a pause, "N of M recorded events shown (filtered; recorded order kept)" once | pending |
| E5 | B2 | each ArrowDown speaks the newly selected option (number, time, kind, source/authority) | pending |
| E6 | C2 | each tab reads "<name>, tab, N of 5" with its badge text; the panel is announced by the tab name | pending |
| E7 | Open a run whose sections are unavailable (`run-avail-unreadable`) | the availability statements are read as plain text when navigated, not announced as live updates | pending |
| E8 | D3 | each list item reads "<id> — unresolved reference"; nothing reads "resolved" | pending |
| E9 | Gates tab | "attempt N, type, success/failure/result not recorded/result ambiguous", then status and reason-code badges | pending |
| E10 | Toolbar during acquisition | "acquiring…", then the snapshot headline once; no duplicate announcement | pending |

## F. Narrow window (N1-N6) - resize to about 800 px, then about 600 px

| step | do | expect | result |
|---|---|---|---|
| F1 | Toolbar | Timeline/Inspector switch appears; buttons wrap; snapshot state text wraps; no horizontal scrollbar | pending |
| F2 | Trust strip with a long store path | the value wraps inside its item; fully readable; no clipping | pending |
| F3 | Settings | fills the width; Save buttons wrap under their fields; dialog scrolls vertically | pending |
| F4 | Event filter row | search, authority select, Clear filter and the count wrap; all remain visible | pending |
| F5 | Events list | long kinds are truncated with an ellipsis in the row; the full record is in Evidence | pending |
| F6 | Gates tab, long output | output scrolls in its box; Copy and Show more stay visible | pending |

Sign-off: name, date, shell (Electron fixture / browser host / real store), macOS and VoiceOver versions: ______ (pending)
