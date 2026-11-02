#!/bin/zsh
set -euo pipefail
PROJECT_ROOT="${0:A:h:h}"
ARTIFACT_ROOT="${KOEFLOW_ARTIFACT_ROOT:?Set KOEFLOW_ARTIFACT_ROOT to the newly built app directory}"
APP_PATH="$ARTIFACT_ROOT/信楽.app"
VERSION=$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP_PATH/Contents/Info.plist")
RELEASE_ROOT="${KOEFLOW_RELEASE_ROOT:-$ARTIFACT_ROOT/releases/$VERSION}"
[[ ! -e "$RELEASE_ROOT" ]] || { print -u2 'Release output exists; choose a fresh KOEFLOW_RELEASE_ROOT.'; exit 1; }
codesign --verify --deep --strict "$APP_PATH"
for name in 信楽_操作マニュアル 信楽_操作早見表 信楽_導入ガイド; do test -f "$APP_PATH/Contents/Resources/$name.html"; test -f "$APP_PATH/Contents/Resources/$name.pdf"; done
STAGING_ROOT="$RELEASE_ROOT/stage"
mkdir -p "$STAGING_ROOT"
ditto "$APP_PATH" "$STAGING_ROOT/信楽.app"
ln -s /Applications "$STAGING_ROOT/Applications"
cp "$APP_PATH/Contents/Resources/信楽_導入ガイド.html" "$STAGING_ROOT/はじめに.html"
DMG_PATH="$RELEASE_ROOT/信楽-$VERSION-AppleSilicon.dmg"
DMG_WORK="$RELEASE_ROOT/.shigaraki-$VERSION-$(uuidgen).dmg"
hdiutil create -volname 信楽 -srcfolder "$STAGING_ROOT" -format UDZO "$DMG_WORK"
mv -n "$DMG_WORK" "$DMG_PATH"
(cd "$RELEASE_ROOT" && shasum -a 256 "${DMG_PATH:t}" > SHA256SUMS.txt)
print -r -- "$DMG_PATH"
