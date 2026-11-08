#!/usr/bin/env bash
# BUILD FOR THE IPHONE APP (macOS only).
# Generates ios/Phonarr.xcodeproj from ios/project.yml and opens it in Xcode.
# In Xcode: set your Team under Signing & Capabilities, pick your iPhone, press Run.
# The app talks to the same gateway the web app uses (run ./build-web.sh on the server first).
# Signing, TestFlight and the free vs paid Apple account: docs/DEPLOYMENT.md §4.
set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

[ "$(uname)" = "Darwin" ] || die "The iPhone app builds on a Mac with Xcode 15 or later."
need xcodegen "Install it with: brew install xcodegen"

say "Stamping the build number and commit (ios/Version.xcconfig)"
build=$(git rev-list --count HEAD 2>/dev/null || date +%Y%m%d%H%M)
commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
printf 'CURRENT_PROJECT_VERSION = %s\nAYR_GIT_COMMIT = %s\n' "$build" "$commit" > ios/Version.xcconfig
say "Generating ios/Phonarr.xcodeproj"
(cd ios && xcodegen)
say "Opening Xcode"
open ios/Phonarr.xcodeproj
