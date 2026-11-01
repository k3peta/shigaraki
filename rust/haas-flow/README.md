# Haas認識制御

Windows用Haasテスト版の音声区間分けと途中字幕を管理するRust製の補助プログラムです。画面、マイク取得、貼り付けは従来のPython・Qt側に残しています。認識モデルはHaas Q4_Kのままで、モデルの計算はspeech.cppのCPUエンジンが担当します。

`app.py` → `rust_session.py` → `rust_worker.py` → このプログラム → speech.cppのC APIの順に動きます。Python側から送るのは新しく録音した音声だけです。Rust側で最新音声をまとめ、古い途中認識の依頼が並ばないようにします。発話の区切りで字幕を確定しても録音は続き、`stop`の後に最後の認識を完了します。

Windowsではspeech.cpp 0.10.0のCPU用静的ライブラリをリンクします。Rust 1.93.0、MinGW-w64 GCC 16.1.0、AVX2・F16C・FMAを有効にしたCPUビルドで確認しています。GPU、OpenMP、追加の推論用DLLは使いません。Windows MSVC向けのビルドは対象にしていません。

```sh
SPEECH_STATIC_DIR=/path/to/speech-cpu-build \
CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=x86_64-w64-mingw32-gcc \
cargo build --locked --release --target x86_64-pc-windows-gnu
```

完成した`shigaraki-haas-flow.exe`を配布物の`speech/`へ入れ、`engine.py`のサイズとSHA-256を更新します。クレートのライセンスとRustの著作権表示も同梱します。モデルとspeech.cppの版・ハッシュは配布物の`upstream.json`にも残します。

標準入力と標準出力のJSON Linesだけで通信します。音声は16kHz・モノラルのPCM16です。録音IDと音声の連番を検査し、認識区間は最長12秒、音声全体は最大305秒に制限します。アプリが300秒で停止した直後の末尾も受け取るため、5秒の余裕があります。取消後の結果は次の録音に混ぜません。エラー時は最後の字幕を回収できるようPython側にも保持します。

Rustのテストは`cargo test`で実行します。Python側の対象テストは`test_shigaraki*.py`と`test_haas*.py`です。旧声吉用のテストも同じフォルダに残っているため、Haas版の検証では対象を指定します。旧`haas_session.py`は比較用です。現在のアプリは`rust_session.py`を使用します。

MacのCPU検証は`--library`で同じ版のC APIライブラリを指定して実行できます。`compat-timing`機能は旧版の字幕開始条件との比較用です。Macの合成音声試験では、初表示を早める効果は認められましたが、停止後の待ち時間は改善していません。Rust化だけで推論が高速になるわけではなく、Windows実機での速度・精度・操作の確認は別途必要です。
