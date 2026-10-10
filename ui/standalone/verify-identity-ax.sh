#!/bin/sh
# Launches the PACKAGED Kriya.app and checks what the person sees through macOS Accessibility: process names, menu-bar titles, the Kriya/File/Window
# menus, the window title (via the Window menu) - failing on any "Electron". Usage: verify-identity-ax.sh [path/to/Kriya.app] [report file]
# Uses a throw-away --user-data-dir and the repository's own kriya executable; quits through the app's own "Quit Kriya" menu item.
set -u
APP="${1:-$(cd "$(dirname "$0")" && pwd)/out/Kriya.app}"; REPORT="${2:-/dev/stdout}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
D="$(mktemp -d /tmp/kriya-id.XXXXXX)"
cat > "$D/kriya-ui-settings.json" <<JSON
{"kriyaExecutable":"$ROOT/.venv/bin/kriya","workspacePath":null,"configDirectory":null,"recentWorkspaces":[]}
JSON
HOME="$HOME" KRIYA_UI_ALLOW_REAL_KRIYA=1 "$APP/Contents/MacOS/Kriya" --user-data-dir="$D" >/dev/null 2>&1 &
sleep 7
{
  echo "app bundle: $(basename "$APP")  executable: $(ls "$APP/Contents/MacOS")"
  echo "bundle id: $(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$APP/Contents/Info.plist")  name: $(/usr/libexec/PlistBuddy -c 'Print CFBundleName' "$APP/Contents/Info.plist")  display: $(/usr/libexec/PlistBuddy -c 'Print CFBundleDisplayName' "$APP/Contents/Info.plist")"
  echo "processes:"; ps -axo comm= | grep -i "Kriya.app" | sed 's#.*/##' | sort | uniq -c
  osascript <<'AS' 2>&1
tell application "System Events"
  tell process "Kriya"
    set out to "menu bar: "
    set bar to name of every menu bar item of menu bar 1
    repeat with m in bar
      if m is not missing value then set out to out & m & " | "
    end repeat
    set out to out & linefeed
    repeat with mn in {"Kriya", "File", "Window"}
      set txt to ""
      set theItems to name of every menu item of menu 1 of menu bar item (mn as string) of menu bar 1
      repeat with i in theItems
        if i is not missing value then set txt to txt & i & " | "
      end repeat
      set out to out & "menu " & mn & ": " & txt & linefeed
    end repeat
    return out
  end tell
end tell
AS
} > "$D/report.txt"
osascript -e 'tell application "System Events" to tell process "Kriya" to click menu item "Quit Kriya" of menu 1 of menu bar item "Kriya" of menu bar 1' >/dev/null 2>&1
sleep 3
echo "still running after Quit Kriya: $(pgrep -f "$APP/Contents/MacOS/Kriya" | wc -l | tr -d ' ') process(es); orphan kup-stdio: $(pgrep -f 'kriya.*kup-stdio|kup-stdio' | wc -l | tr -d ' ')" >> "$D/report.txt"
cat "$D/report.txt" > "$REPORT" 2>/dev/null || cat "$D/report.txt"
if grep -qi "electron" "$D/report.txt"; then echo "IDENTITY: FAIL (Electron text is visible)"; exit 1; fi
grep -q "About Kriya" "$D/report.txt" && grep -q "Hide Kriya" "$D/report.txt" && grep -q "Quit Kriya" "$D/report.txt" || { echo "IDENTITY: FAIL (Kriya menu items missing)"; exit 1; }
echo "IDENTITY: PASS"
