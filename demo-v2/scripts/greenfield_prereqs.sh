#!/usr/bin/env bash
# Scenario 00 prerequisites, checked WITHOUT a model: Java 17+ and Maven are available to the desktop app's child environment (a fixed PATH, no login shell).
# Whether the Maven repository has every plugin the model will pick is NOT knowable here (the model chooses the pom): that is reported as informational, never as a pass.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
FIXED_PATH="/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
RC=0
JAVA_VERSION="$(PATH="$FIXED_PATH" java -version 2>&1 | head -1 || true)"
JAVA_MAJOR="$(printf '%s' "$JAVA_VERSION" | sed -n 's/.*version "\([0-9]*\).*/\1/p')"
if [ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -ge 17 ]; then echo "PASS  java $JAVA_MAJOR is on the app's fixed PATH ($JAVA_VERSION)"; else echo "FAIL  Java 17+ is not on the app's fixed PATH ($FIXED_PATH): '$JAVA_VERSION'"; RC=1; fi
if PATH="$FIXED_PATH" command -v mvn >/dev/null 2>&1; then echo "PASS  mvn is on the app's fixed PATH ($(PATH="$FIXED_PATH" command -v mvn))"; else echo "FAIL  mvn is not on the app's fixed PATH ($FIXED_PATH)"; RC=1; fi
M2="${HOME}/.m2/repository"
if [ -d "$M2/org/apache/maven/plugins/maven-compiler-plugin" ] && [ -d "$M2/org/apache/maven/plugins/maven-surefire-plugin" ]; then
  echo "INFO  the local Maven repository holds the compiler and surefire plugins; whether it holds the exact versions and test libraries the model chooses is only known during the run"
else
  echo "INFO  the local Maven repository ($M2) does not hold the compiler/surefire plugins: an OFFLINE build of a new project will fail unless the model picks versions that are available"
fi
exit "$RC"
