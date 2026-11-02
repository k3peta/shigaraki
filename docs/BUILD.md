# ソースから実行・ビルドする

画面・マイク・転記はPython、Haasの認識制御はRust、CPU推論はspeech.cppです。ソースにモデル重みやWindows用実行ファイルは含めていません。ライセンスと固定版は[CREDITS.md](../CREDITS.md)と[第三者ライセンス案内](../THIRD_PARTY_NOTICES.md)を参照してください。

## 前提

開発用Python 3.11以上、Rust/Cargo 1.93.0、CMake 3.20以上、Make、`patch`が必要です。Windows向け認識エンジンのビルドには、MinGW-w64の`x86_64-w64-mingw32-gcc`・`g++`・`windres`・`objdump`と、UCRT・静的C++ライブラリを使用します。MinGW GCC 16.1.0によるmacOSからのクロスビルドを確認しています。Windows上のネイティブビルドとMSVC構成は未確認です。

macOSでは、例えばHomebrewの`python@3.11`・`cmake`・`make`・`mingw-w64`・`nsis`でツールを用意できます。Rustは[rustup公式案内](https://rustup.rs/)に従い、次の版とターゲットを入れます。パッケージ更新によりツールの版は変わるため、実際の版も記録してください。

```sh
rustup toolchain install 1.93.0 --profile minimal
rustup default 1.93.0
rustup target add x86_64-pc-windows-gnu --toolchain 1.93.0
```

## 1. Python環境とモデル素材

```sh
git clone https://github.com/k3peta/shigaraki.git
cd shigaraki
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/fetch_assets.py
```

WindowsのPowerShellでは、`py -3.11 -m venv .venv`、`.\.venv\Scripts\Activate.ps1`で仮想環境を利用できます。

`fetch_assets.py`は固定revisionの句読点用重み・トークナイザーを取得し、サイズとSHA-256を検証します。モデル素材は`punctuation/`へ配置されます。Haasの約352MBの重みは、アプリの初回準備で取得します。生成校正も使う場合だけ、`python scripts/fetch_assets.py --with-proofreader`で公式llama.cpp b10566のWindows CPU実行ファイルを取得します。Qwenの重みはアプリで生成校正を有効にしたときに取得します。

## 2. Windows用CPUヘルパー

```sh
python scripts/build_cpu_engine.py \
  --work build/cpu \
  --output speech/shigaraki-haas-flow.exe \
  --update-worker-pin
```

固定したspeech.cppとggmlのアーカイブを取得してハッシュを検証し、CPUライブラリとRustプログラムをビルドします。GPU・OpenMP・BLASは無効、AVX2・FMA・F16Cは有効です。モデル重みを使わずにビルドできます。取得した固定ソースに、同梱の`third_party/speech-cpu-abort.patch`をSHA-256確認後に適用します。この差分はCPU計算の取消を細かく確認するもので、公開APIやモデル重みは変更しません。

`--work`と`--output`には新規の場所を指定します。既存の成果物は上書きされません。やり直す場合は別の作業ディレクトリと出力先を選びます。`--update-worker-pin`は、生成したexeのサイズとSHA-256を`engine.py`へ反映すると同時に、対応するビルド記録を`resources/cpu-build.json`へ保存します。この指定がない場合、ソースの検証値とビルド記録は変更されません。コンパイラやビルドパスによってexeのハッシュは変わり得るため、公開exeとのバイト単位の一致を保証する手順ではありません。

ログと`build-result.json`は`--work`の下に残ります。対応するソースとライセンスも、固定commitから再取得できます。

## 3. Windowsで実行する

Windows側へ、ソース・`speech/`・`punctuation/`を同じ配置でコピーします。任意の生成校正を使うときは`proofreader/`も必要です。Windows側のPython仮想環境に同じ`requirements.txt`をインストールしてから実行します。

```powershell
python -m shigaraki_win run
```

アプリの準備画面からHaasモデルを取得し、マイクを選択します。Mac/Linuxでは通常の録音・転記アプリとしての起動は対象外です。

## 4. テストと画面プレビュー

```sh
python scripts/test.py
python -m shigaraki_win render-preview build/previews
```

テストはモデル重みなしで実行できます。2種類のPythonテストを別プロセスで実行し、Qtのウィンドウとアプリを明示的に終了してからRustのホスト用テストを実行します。実マイク・他アプリへの実転記・Windowsの保護設定は、このテストだけでは確認できません。

Windows GNU環境ではRustテストにも静的ライブラリが必要です。その場合は、前のビルドで生成された`native/`を`SPEECH_STATIC_DIR`へ指定します。

## 5. セットアップ用exeを作る

```sh
python scripts/build_shigaraki_installer.py \
  /path/to/verified-windows-payload \
  dist/Shigaraki-Windows-0.1.0a6-setup.exe \
  --build build/installer --version 0.1.0a6
```

これは検証済みのWindows配布フォルダをNSISでパッケージ化する工程です。Mac用のPythonやDLLをWindows配布物へコピーしても動作しません。Windows用の入力を準備する例は次のとおりです。

```sh
python -m pip download -r requirements-release.txt \
  --dest build/wheels --only-binary=:all: --platform win_amd64 \
  --python-version 314 --implementation cp --abi cp314 --abi abi3
python -m pip install -r requirements-release.txt \
  --target build/windows-site-packages --no-compile --no-index \
  --find-links build/wheels --only-binary=:all: --platform win_amd64 \
  --python-version 314 --implementation cp --abi cp314 --abi abi3
python scripts/fetch_assets.py --with-proofreader
```

Python公式の[3.14.8埋込み版x64 ZIP](https://www.python.org/ftp/python/3.14.8/python-3.14.8-embeddable-amd64.zip)を`build/`へ保存します。組立スクリプトが固定SHA-256で検証します。

```sh
python scripts/build_windows_payload.py build/payload \
  --python-zip build/python-3.14.8-embeddable-amd64.zip \
  --dependencies build/windows-site-packages \
  --helper speech/shigaraki-haas-flow.exe \
  --punctuation punctuation --proofreader proofreader
```

`--proofreader`は任意です。指定しない配布物では生成校正を使えません。必要な上流ライセンス、対応ソースの案内、アプリとRustのソース、文書を含めて組み立てます。既存の出力、ハッシュの異なる素材、依存版の不一致、設定やログの混入は拒否します。

この`build/payload`を、先ほどのNSISコマンドの入力に指定します。最終配布物の版とハッシュは`upstream.json`と`SHA256SUMS.txt`に残ります。Python依存の解決結果は、ビルド時の配布物内の記録も確認してください。コンパイラや依存パッケージを更新した場合は、Windows実機で改めて動作を確認します。

## Windows署名の検証

現在の公開exeは未署名です。以下は署名を付ける機能ではなく、将来の署名済み配布物をWindowsで検証するための手順です。

PowerShell 7で、信頼済みの署名証明書に記載されたSubjectを指定します。Windowsによる署名の検証、指定した発行者との完全一致、タイムスタンプの存在を確認し、一つでも条件を満たさないファイルがあれば終了コード1を返します。OSの信頼設定は変更しません。

```powershell
pwsh -NoProfile -File scripts/verify_windows_signatures.ps1 -FilePath .\signed-setup.exe -ExpectedSubject '証明書に記載されたSubject' -ReportPath .\signature-report.json
```

この確認だけでSmart App Controlが有効なPCでの起動を保証するものではありません。インストーラに加え、アプリ本体・認識用ヘルパー・アンインストーラ、実行時に使う第三者ライブラリも確認し、対象PCで導入と音声入力を試します。
