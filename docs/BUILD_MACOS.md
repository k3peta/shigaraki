# Mac版をソースからビルドする

Mac版のソースは`macos/`、共通のRust製認識ヘルパーは`rust/haas-flow/`にあります。Haasのモデル重みはリポジトリに含めません。アプリの初回準備で約352MBのQ4_Kモデルを取得し、サイズとSHA-256を検証します。

## 前提

- macOS 26以降のApple Silicon Mac。Intel Macは対象外です。
- macOS 26以降のSDKが使えるXcodeまたはCommand Line Tools。
- Python 3.11以上、CMake 3.20以上、Make、`patch`。
- Rust/Cargo 1.93.0と`aarch64-apple-darwin`標準ライブラリ。`rustup`で用意します。

初回のビルドでは、固定した上流ソース、Rustクレート、マニュアル生成用のPythonパッケージを取得するため通信します。日本語認識はCPUで動きます。音声認識にApple Intelligenceや外部の推論API契約は必要ありません。Apple Intelligenceを使う文章整形は、別の任意機能です。

以下はリポジトリのルートで実行します。出力はソースの外に置きます。作り直す場合は、`SHIGARAKI_BUILD_ROOT`を未使用の名前に変えてください。ネイティブの作業先と出力先は、互いに別の場所にし、入れ子にもできません。ネイティブの作業先・出力先、アプリ、Release出力が既にある場合、スクリプトは上書きせず停止します。

```sh
git clone https://github.com/k3peta/shigaraki.git
cd shigaraki
git switch --detach macos-v0.5.0
export SHIGARAKI_BUILD_ROOT="$HOME/Build/shigaraki-macos-0.5.0-1"
rustup toolchain install 1.93.0 --profile minimal
rustup target add aarch64-apple-darwin --toolchain 1.93.0
```

## 1. CPU認識エンジンをビルドする

```sh
RUSTUP_TOOLCHAIN=1.93.0 python3 macos/scripts/build-native.py \
  --work "$SHIGARAKI_BUILD_ROOT/native-work" \
  --output-root "$SHIGARAKI_BUILD_ROOT/native" \
  --jobs 4
```

固定したspeech.cpp・ggmlのアーカイブを取得して検証し、同梱のCPU取消差分を適用して、CPUライブラリとRustヘルパーをビルドします。モデル重みは不要です。Rust依存の版は`Cargo.lock`で固定します。

出力は`native/files/shigaraki-haas-flow`、`native/files/libspeech.4.dylib`、`native/native-manifest.json`です。次の工程では、これらを含む`native/`を`KOEFLOW_NATIVE_ROOT`に指定します。コンパイラやビルドパスでバイナリのハッシュが変わるため、公開DMGとのバイト単位の一致を保証する手順ではありません。

## 2. マニュアルを生成する

```sh
python3 -m venv "$SHIGARAKI_BUILD_ROOT/manual-venv"
"$SHIGARAKI_BUILD_ROOT/manual-venv/bin/python" -m pip install \
  -r macos/scripts/requirements-manual.txt

"$SHIGARAKI_BUILD_ROOT/manual-venv/bin/python" macos/scripts/build-manual.py \
  --content manual-content.json --output-dir "$SHIGARAKI_BUILD_ROOT/manual"
"$SHIGARAKI_BUILD_ROOT/manual-venv/bin/python" macos/scripts/build-manual.py \
  --content quick-content.json --output-dir "$SHIGARAKI_BUILD_ROOT/manual"
"$SHIGARAKI_BUILD_ROOT/manual-venv/bin/python" macos/scripts/build-manual.py \
  --content install-content.json --output-dir "$SHIGARAKI_BUILD_ROOT/manual"
```

3種類のPDFを生成し、同じ原稿から`macos/docs/`内のHTML・Markdownも更新します。日本語フォントとOFL原文は`macos/docs/fonts/`に同梱しています。Apple SymbolsはmacOS標準フォントを生成時に参照します。

`--output-dir`を省略した場合、PDFの出力先は`macos/build/manual`です。生成には固定版のPillowとReportLabを使います。これらはマニュアル作成用の依存で、配布アプリの実行には不要です。

## 3. 信楽.appを作る

```sh
KOEFLOW_ARTIFACT_ROOT="$SHIGARAKI_BUILD_ROOT/app" \
KOEFLOW_NATIVE_ROOT="$SHIGARAKI_BUILD_ROOT/native" \
KOEFLOW_MANUAL_ROOT="$SHIGARAKI_BUILD_ROOT/manual" \
KOEFLOW_SIGNING_IDENTITY=- \
  macos/scripts/build.sh
```

開発者証明書がない場合、`KOEFLOW_SIGNING_IDENTITY=-`でローカル検証用のアドホック署名を使えます。Appleによる公証は行いません。自分の署名証明書を使う場合は、この値を自分の署名IDに変えます。他人の証明書や固定名のキーチェーンは必要ありません。

スクリプトはヘルパー・CPUライブラリ・アプリを順に署名し、署名後のサイズとSHA-256を内蔵manifestへ反映して検証します。既存アプリと署名条件を比較する場合だけ、`KOEFLOW_EXISTING_APP`へそのアプリのパスを指定します。アドホック署名では、この比較は使えません。

出力は`$SHIGARAKI_BUILD_ROOT/app/信楽.app`です。初回起動時に、マイクとアクセシビリティの許可、Haasモデルの準備を進めます。別の署名でビルドしたアプリへ切り替えると、許可の確認し直しが必要になる場合があります。[導入ガイド](../macos/docs/信楽_導入ガイド.md)に従ってください。

## 4. DMGを作る

```sh
KOEFLOW_ARTIFACT_ROOT="$SHIGARAKI_BUILD_ROOT/app" \
  macos/scripts/package-release.sh
```

既定の出力先は`$SHIGARAKI_BUILD_ROOT/app/releases/0.5.0/`です。DMGと`SHA256SUMS.txt`を生成します。DMGには「信楽.app」、Applicationsへのリンク、「はじめに.html」を含めます。モデル重みは含めません。別の場所へ出力する場合は`KOEFLOW_RELEASE_ROOT`を指定します。

## テストと確認範囲

```sh
swift test --package-path macos \
  --scratch-path "$SHIGARAKI_BUILD_ROOT/swift-tests" --jobs 2
```

Swiftの自動テストはモデル重みなしで実行できます。これだけでは実マイク、実キー操作、他アプリへの転記、macOSの許可状態は確認できません。配布や署名を変更した場合は、対象Macで初回準備から入力まで確認してください。

認識は短い音声を繰り返し処理する擬似ストリーミング方式です。1回の録音は5分までで、上限では自動転記せず本文を残します。Mac版の機能と現在の確認状況は[macos/README.md](../macos/README.md)、上流の出典と条件は[クレジット](../CREDITS.md)と[第三者ライセンス案内](../THIRD_PARTY_NOTICES.md)を参照してください。
