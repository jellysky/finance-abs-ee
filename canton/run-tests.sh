#!/usr/bin/env bash
# Run the Serention Daml Script tests on a local in-memory Canton ledger.
# (Daml SDK is x86 -> runs under Rosetta; needs JDK 17 on PATH.)
set -euo pipefail
export JAVA_HOME="/opt/homebrew/opt/openjdk@17"
export PATH="$JAVA_HOME/bin:$HOME/.daml/bin:$PATH"
cd "$(dirname "$0")"
exec daml test
