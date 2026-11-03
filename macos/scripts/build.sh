#!/bin/zsh
set -euo pipefail
PROJECT_ROOT="${0:A:h:h}"
VERSION=$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$PROJECT_ROOT/scripts/Info.plist")
ARTIFACT_ROOT="${KOEFLOW_ARTIFACT_ROOT:?Set KOEFLOW_ARTIFACT_ROOT to a fresh build directory outside the source tree}"
NATIVE_ROOT="${KOEFLOW_NATIVE_ROOT:?Set KOEFLOW_NATIVE_ROOT to the verified native build directory}"
APP_PATH="$ARTIFACT_ROOT/信楽.app"
[[ ! -e "$APP_PATH" ]] || { print -u2 'Output app already exists; choose a fresh KOEFLOW_ARTIFACT_ROOT.'; exit 1; }
for native_item in files/shigaraki-haas-flow files/libspeech.4.dylib native-manifest.json; do test -f "$NATIVE_ROOT/$native_item"; done
mkdir -p "$ARTIFACT_ROOT"
swift build -c release --jobs "${KOEFLOW_BUILD_JOBS:-2}" --package-path "$PROJECT_ROOT" --scratch-path "${KOEFLOW_SWIFT_BUILD_ROOT:-$ARTIFACT_ROOT/swift-build}"
SWIFT_ROOT="${KOEFLOW_SWIFT_BUILD_ROOT:-$ARTIFACT_ROOT/swift-build}"
mkdir -p "$APP_PATH/Contents/"{MacOS,Frameworks,Resources/MacFiles,Resources/Licenses}
cp "$SWIFT_ROOT/release/KoeFlow" "$APP_PATH/Contents/MacOS/KoeFlow"
# Remove debug symbol paths from the copied release binary before signing it.
xcrun strip -S "$APP_PATH/Contents/MacOS/KoeFlow"
cp "$NATIVE_ROOT/files/shigaraki-haas-flow" "$APP_PATH/Contents/MacOS/shigaraki-haas-flow"
cp "$NATIVE_ROOT/files/libspeech.4.dylib" "$APP_PATH/Contents/Frameworks/libspeech.4.dylib"
cp "$NATIVE_ROOT/native-manifest.json" "$APP_PATH/Contents/Resources/MacFiles/native-manifest.json"
cp "$PROJECT_ROOT/scripts/Info.plist" "$APP_PATH/Contents/Info.plist"
cp "$PROJECT_ROOT/Sources/KoeFlowCore/Resources/"{geography.tsv,geography-source.json} "$APP_PATH/Contents/Resources/"
cp "$PROJECT_ROOT/LICENSE" "$APP_PATH/Contents/Resources/Licenses/信楽-MIT.txt"
ditto "$PROJECT_ROOT/third_party" "$APP_PATH/Contents/Resources/Licenses/third_party"
if [[ -f "$PROJECT_ROOT/Resources/AppIcon.icns" ]]; then cp "$PROJECT_ROOT/Resources/AppIcon.icns" "$APP_PATH/Contents/Resources/"; fi
if [[ "${KOEFLOW_SKIP_DOCS:-0}" != 1 ]]; then
  for name in 信楽_操作マニュアル 信楽_操作早見表 信楽_導入ガイド 信楽_文章整形とアプリ別設定 信楽_連続入力とプロンプトカード 信楽_地名辞書 信楽_音声編集 信楽_文脈改善版について; do
    test -f "$PROJECT_ROOT/docs/$name.html"
    cp "$PROJECT_ROOT/docs/$name.html" "$APP_PATH/Contents/Resources/"
  done
  MANUAL_ROOT="${KOEFLOW_MANUAL_ROOT:-$PROJECT_ROOT/build/manual}"
  for name in 信楽_操作マニュアル 信楽_操作早見表 信楽_導入ガイド; do
    test -f "$MANUAL_ROOT/$name.pdf"
    cp "$MANUAL_ROOT/$name.pdf" "$APP_PATH/Contents/Resources/"
  done
  cp "$PROJECT_ROOT/docs/fonts/OFL.txt" "$APP_PATH/Contents/Resources/Licenses/NotoSansJP-OFL.txt"
fi
# HFS+ disk images decompose filenames. Seal the same names before signing so
# a drag/copy from the mounted image does not invalidate Japanese resources.
/usr/bin/python3 - "$APP_PATH/Contents/Resources" <<'NORMALIZE_RESOURCE_NAMES'
from pathlib import Path
import sys, unicodedata, uuid
root = Path(sys.argv[1])
changed = 0
for path in sorted(root.rglob('*'), key=lambda value: len(value.parts), reverse=True):
    name = unicodedata.normalize('NFD', path.name)
    if name == path.name:
        continue
    target = path.with_name(name)
    if target.exists() and not path.samefile(target):
        raise SystemExit('Conflicting Unicode resource filenames.')
    # A direct normalization-only rename can be a no-op on APFS.
    temporary = path.with_name('.shigaraki-name-' + uuid.uuid4().hex)
    path.rename(temporary)
    temporary.rename(target)
    changed += 1
print('Normalized resource filenames:', changed)
NORMALIZE_RESOURCE_NAMES
SIGNING_IDENTITY="${KOEFLOW_SIGNING_IDENTITY:-}"
ADHOC=0
[[ "$SIGNING_IDENTITY" != - ]] || ADHOC=1
if [[ -z "$SIGNING_IDENTITY" ]]; then
  SIGNING_IDENTITY="$(security find-identity -v -p codesigning | sed -n 's/.*\([A-F0-9]\{40\}\) "Apple Development:.*$/\1/p' | head -n 1)"
fi
[[ -n "$SIGNING_IDENTITY" ]] || { print -u2 'Set KOEFLOW_SIGNING_IDENTITY to a signing identity, or - for a local ad-hoc build.'; exit 1; }
SIGN_OPTIONS=(--options runtime)
# Ad-hoc builds are for local development; the CPU library remains inside the app.
if (( ADHOC )); then SIGN_OPTIONS=(); fi
# Sign nested code first. The manifest must describe the final signed bytes.
codesign --force "${SIGN_OPTIONS[@]}" --timestamp=none --sign "$SIGNING_IDENTITY" --identifier jp.ys.koekichi.flow.haas "$APP_PATH/Contents/MacOS/shigaraki-haas-flow"
codesign --force "${SIGN_OPTIONS[@]}" --timestamp=none --sign "$SIGNING_IDENTITY" --identifier jp.ys.koekichi.flow.speech "$APP_PATH/Contents/Frameworks/libspeech.4.dylib"
/usr/bin/python3 - "$APP_PATH" <<'NATIVE_MANIFEST'
import pathlib,hashlib,json,sys
app=pathlib.Path(sys.argv[1]); path=app/'Contents/Resources/MacFiles/native-manifest.json'
data=json.loads(path.read_text())
data['layout']='paths relative to Contents'
data['signing_performed']=True
for name,relative in [('shigaraki-haas-flow','MacOS/shigaraki-haas-flow'),('libspeech.4.dylib','Frameworks/libspeech.4.dylib')]:
    file=app/'Contents'/relative
    data['files'][name]={'path':relative,'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
notices={}
for notice in sorted((app/'Contents/Resources/Licenses').rglob('*')):
    if notice.is_file():
        notices.setdefault(hashlib.sha256(notice.read_bytes()).hexdigest(),str(notice.relative_to(app/'Contents')))
for notice in data.get('licenses',[]):
    if notice['sha256'] not in notices:
        raise SystemExit('A native dependency license is missing from the app bundle.')
    notice['path']=notices[notice['sha256']]
path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
NATIVE_MANIFEST
codesign --force "${SIGN_OPTIONS[@]}" --timestamp=none --sign "$SIGNING_IDENTITY" --identifier jp.ys.koekichi.flow "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"
if [[ -n "${KOEFLOW_EXISTING_APP:-}" ]]; then
  (( ! ADHOC )) || { print -u2 'An ad-hoc build cannot preserve an installed developer signing requirement.'; exit 1; }
  REQUIREMENT="$(codesign -d -r- "$KOEFLOW_EXISTING_APP" 2>&1 | sed -n 's/^designated => //p')"
  [[ -n "$REQUIREMENT" ]] || { print -u2 'Could not read the installed app signing requirement.'; exit 1; }
  codesign --verify --strict -R="$REQUIREMENT" "$APP_PATH"
fi
print -r -- "$APP_PATH"
