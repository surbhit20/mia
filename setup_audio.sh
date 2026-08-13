#!/usr/bin/env bash
set -euo pipefail

echo "Installing BlackHole (virtual audio driver)..."
brew install blackhole-2ch

echo "Installing terminal-notifier..."
brew install terminal-notifier

echo "Done. Manual steps remain — see SETUP.md."
