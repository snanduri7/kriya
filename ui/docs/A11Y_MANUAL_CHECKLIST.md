# Manual acceptance checklist: keyboard, VoiceOver, narrow window (status: PENDING a human run)

Scope: the controls added or changed after the owner's earlier acceptance - the Settings dialog, the timeline search and
filters, event selection, the Gates and Evidence tabs, the attribution "Why" drawer. Every item below is **pending** until a
person performs it on the Electron shell (fixture `kriya` stand-in while D-9 holds) or the browser test host. The automated
DOM tests (`ui/shared/test/a11y.test.tsx`) cover roles, names, focus order and announcements in jsdom; they do not verify
rendering, VoiceOver speech or layout.

Inspection record (2026-10-04, from source, before changes): see `## Defects found by inspection` below for what was fixed
and what was judged acceptable.

## Keyboard only (no pointer)

| # | check | expected | status |
|---|---|---|---|
| K1 | Tab from the trust strip through the toolbar | Settings, Acquire, Refresh, snapshot select, then the runs filter and runs list, in reading order | pending |
| K2 | Open Settings with Enter on the Settings button | focus lands on the "Settings" title; Tab reaches Close, then each field and its Save button, then the editor select and its Save; Tab from the last control wraps to Close; Shift+Tab from Close wraps to the last control | pending |
| K3 | Escape in Settings | dialog closes; focus returns to the Settings button (or, if that button is disabled by a pending request, to the application, never to the page body) | pending |
| K4 | Save an invalid path with the keyboard | the field is announced as invalid with the error text; focus stays in the field row | pending |
| K5 | Runs list: arrows, Home, End, PageDown | selection moves; the selected run loads; focus stays on the list | pending |
| K6 | Events list: ArrowDown/Up, Home, End | selection moves one event at a time; the Inspector switches to Evidence and shows the selected event | pending |
| K7 | Type in "Search recorded events", then clear with the "Clear filter" button | list narrows as you type; selection kept; the button is reachable by Tab and disabled when no filter is active | pending |
| K8 | Filter so the selected event is hidden, Tab to "Clear filter to show the selected event", press Enter | the filter clears, the selected event is back in the list, focus is on the events list (not lost) | pending |
| K9 | Inspector tabs: ArrowRight/Left/Home/End | the focused tab changes and its panel shows; Tab from a tab goes into the panel content, never into a hidden panel | pending |
| K10 | Drawer closed: Tab through its bar | only "Show drawer", "Diff", "Why" are reachable; pressing Why opens the drawer on Why | pending |
| K11 | Gates tab: Tab through gate records | each record's output has a Copy and (if long) Show more button; no record is skipped | pending |

## VoiceOver (macOS)

| # | check | expected | status |
|---|---|---|---|
| V1 | Open Settings | "Settings, dialog" is announced, then the title; the help text is read with each field | pending |
| V2 | Save a setting | "Saving…" then "Saved. No snapshot was acquired." announced once each; no repeated announcements while idle | pending |
| V3 | Save an invalid path | the error is announced as an alert once; the field reports "invalid data" | pending |
| V4 | Type in the event search | the count is NOT announced per keystroke; after a pause "N of M recorded events shown (filtered; recorded order kept)" is announced once | pending |
| V5 | Change the authority filter | the control reads as "Filter by authority, pop up button"; the count is announced once after the change | pending |
| V6 | Move through the events list | each option reads its number, time, kind and source/authority; the active option is announced when arrowing | pending |
| V7 | Inspector tabs | each reads "tab, N of 5" with its availability badge text; the panel is announced by the tab's name | pending |
| V8 | Unavailable sections | read as plain text ("Run events: unreadable - reason"), not as live announcements | pending |
| V9 | Why tab, attribution | each evidence id reads "<id> — unresolved reference"; the explanation sentence follows; nothing reads as "resolved" or "verified" | pending |
| V10 | Gates tab | each record reads "attempt N · type · success/failure/result not recorded/result ambiguous" with status and reason-code badges | pending |

## Narrow window (about 800 px wide and below)

| # | check | expected | status |
|---|---|---|---|
| N1 | Toolbar | the Timeline/Inspector pane switch appears; buttons wrap without overlapping; the snapshot state text wraps | pending |
| N2 | Trust strip with a long store path and snapshot id | values wrap within their item; no clipping, no horizontal scrollbar; the full recorded text is readable | pending |
| N3 | Settings dialog | fills the window width; inputs and Save buttons wrap below their labels; the dialog scrolls when taller than the window | pending |
| N4 | Event filter row | search box, authority select, Clear filter and the count wrap onto new lines; nothing is hidden | pending |
| N5 | Events list rows with long kinds or messages | the kind is truncated with an ellipsis in the row (full text in the Evidence tab); no control is hidden | pending |
| N6 | Gates tab with a long output | output scrolls inside its box; Copy and Show more stay visible | pending |

## Defects found by inspection (2026-10-04) and their disposition

Fixed in `ui/shared` (tests in `test/a11y.test.tsx`):
1. Every unavailable-section statement was a `role="status"` live region, so opening a run with several unavailable
   sections fired several announcements of static text. Now `role="note"`.
2. The event-filter count was a live region updated on every keystroke. Now the visible count updates at once and a
   visually hidden status region announces the final count after a 350 ms pause.
3. "Clear filter to show the selected event" removed itself on activation and left focus on `<body>`. Focus now moves to
   the events list, whose active option is the still-selected event.
4. The virtualized listboxes exposed no active option (`aria-activedescendant` was undefined). The focused list now
   names the selected option.
5. The Settings error was a global alert not associated with any field. The rejected field is now `aria-invalid` and
   described by the error.
6. Settings initial focus was the Close button (announced before the dialog's purpose). Initial focus is now the dialog
   title; Close remains the first Tab stop.
7. Closing Settings while the Settings button was disabled (a request pending) dropped focus to `<body>`. Focus now falls
   back to the application root.
8. The timeline search box carried both a label and an `aria-label` (two name sources); the duplicate was removed.
9. The "selected event hidden" notice carried an `aria-label` on a plain `div` (not exposed). Removed; the text itself is
   the notice.
10. The drawer's tabs pointed at panels that did not exist while the drawer was closed. Empty hidden panels now exist.
11. The narrow-window pane switch was two toggle buttons without a group name. Now a group named "Visible pane".
12. Trust-strip values were clipped with an ellipsis and readable only through a hover tooltip. They now wrap.

Judged acceptable (no change): inspector tabs already follow the roving-tabindex pattern with arrow/Home/End and automatic
activation; the attempt chips and compared-file chips are toggle buttons in named groups; availability and result states
carry text beside colour; the Why drawer's unresolved references and the Gates results are stated in words; event rows
truncate long kinds with an ellipsis while the Evidence tab shows the full record; the snapshot-state status region reports
each acquisition phase once.

Not verified here: visual rendering, actual screen-reader speech and layout at real window sizes (items above).
