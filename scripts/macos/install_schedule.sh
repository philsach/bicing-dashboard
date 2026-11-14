#!/bin/bash
# Set up (or remove) the weekly Bicing data update on macOS.
#
#   scripts/macos/install_schedule.sh            install / reinstall
#   scripts/macos/install_schedule.sh --uninstall
#
# Creates a Python environment in .venv/, then registers a launchd job that runs
# scripts/macos/weekly_update.sh every Monday at 10:00. If the Mac is asleep
# then, macOS runs it when it wakes up (not if it was switched off).
# Log: ~/Library/Logs/bicing-dashboard-update.log
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.bicing-dashboard.weekly-update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/bicing-dashboard-update.log"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  git -C "$REPO" worktree remove --force "$REPO/.update-worktree" 2>/dev/null || true
  echo "Removed the weekly update. (.venv/ was kept; delete it if you like.)"
  exit 0
fi

case "$REPO" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "Note: the repository is in Desktop/Documents/Downloads. macOS privacy protection may block"
    echo "background jobs there. If the log shows 'Operation not permitted', move the repository"
    echo "(e.g. to ~/code/bicing-dashboard) or give /bin/bash Full Disk Access in System Settings."
    echo ;;
esac

command -v python3 >/dev/null || { echo "python3 not found. Install it with: xcode-select --install"; exit 1; }
echo "Setting up Python environment in .venv/ ..."
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -r "$REPO/scripts/requirements.txt"
chmod +x "$REPO/scripts/macos/weekly_update.sh"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/scripts/macos/weekly_update.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>10</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed: runs every Monday at 10:00."
echo "Run it right now:   launchctl kickstart gui/$(id -u)/$LABEL"
echo "Watch the log:      tail -f $LOG"
command -v gh >/dev/null || echo "Tip: install the GitHub CLI (brew install gh, then gh auth login) so it can open pull requests for you."
