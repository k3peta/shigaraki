# 信楽 — Haasを使ったローカル日本語音声入力

WindowsとmacOS向けの、無料で使えるローカル日本語音声入力アプリです。HaasをCPUで動かし、録音中から途中字幕を表示します。音声認識にGPUや推論APIの契約は必要ありません。

| 利用するOS | ダウンロード | 導入・操作 | ソースからのビルド |
| --- | --- | --- | --- |
| Windows x64 | [Windows版0.1.0a8ベータ](https://github.com/k3peta/shigaraki/releases/tag/v0.1.0a8) | [Windows導入ガイド](docs/INSTALL.md) | [Windowsビルド手順](docs/BUILD.md) |
| macOS 26以降・Apple Silicon | [Mac版0.5.1プレリリース](https://github.com/k3peta/shigaraki/releases/tag/macos-v0.5.1) | [Mac導入ガイド](macos/docs/信楽_導入ガイド.md)・[使い方](macos/README.md) | [Macビルド手順](docs/BUILD_MACOS.md) |

Mac版はDMG内の「信楽.app」をApplicationsへコピーし、初回の許可と約352MBのモデル準備をアプリ内で進めます。Appleの公証は未実施です。実マイク・実キー・他アプリへの実転記の確認状況は、[Mac版の案内](macos/README.md#確認状況と制約)をご覧ください。

Windows 0.1.0a8・Mac 0.5.1では、アプリやWebサイトごとに、よく話す内容や優先して認識させたい用語を登録できます。入力先のアプリや取得できたサイトのドメインが完全に一致する場合に、認識の参考に使う任意設定です。登録しなくても普段どおり入力でき、文章の文体を変える整形とは別の機能です。

## Windows版

Windows向けのベータ版です。Haasを使うこの版を、今後の信楽の本流とします。左Ctrlを押して話すと、録音中から途中字幕を表示し、キーを離すと入力先へ貼り付けます。GPUや推論APIの契約は不要です。

音声認識には、[Third IntelligenceのHaas-ja-v1.0](https://huggingface.co/third-intelligence/Haas-ja-v1.0)を使用しています。[SakasegawaによるGGUF変換版](https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF)を、[speech.cpp](https://github.com/nyosegawa/speech.cpp)のCPUエンジンで実行します。これらの成果に支えられて、信楽は録音・途中字幕・辞書・他アプリへの転記を一つの操作にまとめています。

![メインパネルのプレビュー](docs/images/main.png)

図は同じ画面コードから生成したプレビューです。Windows実機のスクリーンショットではありません。

## Windows版のダウンロードと使い方

[Windows 0.1.0a8のリリース](https://github.com/k3peta/shigaraki/releases/tag/v0.1.0a8)から、`Shigaraki-Windows-0.1.0a8-setup.exe`を取得して更新できます。[図入り導入ガイド](docs/INSTALL.md)に、インストール・モデル準備・マイク・操作方法をまとめています。

初回の準備が終わるとメインパネルを使えます。インストーラーから続けて起動した場合は、起動後のモデル準備が終わると自動で開きます。手動で初回起動した場合は、準備完了後に「音声入力を使い始める」を押して開きます。入力パネルで話すには、メインパネルを閉じ、入力先の欄をクリックしてから左Ctrlを長押しします。

現在の配布exeは未署名です。SmartScreenの警告では「詳細情報」→「実行」で進める場合がありますが、Smart App Controlが有効な環境では起動がブロックされることがあります。ダウンロード前に[Windowsの警告と暫定手順](docs/INSTALL.md#windows-security)をご確認ください。

- 入力パネルで入力：メインパネルを閉じて入力欄を選び、左Ctrlを長押しして話します。離すと確定して貼り付けます。
- メインパネルを開く：入力パネルで録音していないときに、左Ctrlを短く2回押します。
- 連続入力：メインパネルでは左Ctrlを短く1回押すと録音を開始し、もう1回押すと停止します。録音ボタンでも開始・停止できます。停止した文章は保持し、この段階では貼り付けません。再開すると、保持した文章に改行を入れて追記します。
- メインパネルから転記：左Ctrlを短く2回押します。録音中なら停止して最後の認識を待ち、未転記の文章を入力先へ一度だけ貼り付けて閉じます。転記済みの文章は、この操作ではもう一度貼り付けません。
- 履歴：メインパネルの「履歴」タブで、保存した入力を新しい順に読み返してコピーできます。[使い方](docs/HISTORY.md)
- 認識結果の回収：転記に失敗しても、メインパネルの「最後の入力をコピー」から文章を取り出せます。

メインパネルが開いている間、入力パネルは表示しません。以前の方式の信楽もインストールできますが、起動して使うのは一方だけです。キーは設定で変更できます。

初期辞書はOpenAI、ChatGPT、Codex、Antigravity、Claude、Geminiの6語です。「アンチグラビティ」をAntigravityにするなど、読みからの表記補正と認識候補に使います。大学名などの個人用語は各自で登録してください。ユーザー登録を優先し、更新前に保存した辞書は保持します。

## Windows版で使っている技術

| 用途 | 採用しているもの |
| --- | --- |
| 日本語音声認識 | Third Intelligence / Haas-ja-v1.0、Sakasegawa / Q4_K GGUF |
| CPU推論 | Sakasegawa / speech.cpp 0.10.0、ggml |
| 音声区間・途中字幕の制御 | 信楽のRust製補助プログラム |
| 画面・マイク・転記 | Python、PySide6 / Qt、sounddevice / PortAudio、comtypes |
| 高速な句読点補完 | oboroge0 / hayamimi-punct-ja-4class、ONNX Runtime、tokenizers |
| 任意の生成校正 | Qwen / Qwen3.5-0.8B、ggml-org / GGUF、llama.cpp。初期設定はオフ |

作者・出典・モデルの来歴と各ライセンスは、[クレジット](CREDITS.md)と[第三者ライセンス案内](THIRD_PARTY_NOTICES.md)に記載しています。信楽自身のソースコードは[MIT](LICENSE)です。モデルや同梱ライブラリにはそれぞれのライセンスが適用されます。

## Windows版の対象環境と現在の制約

Windows x64、AVX2・FMA・F16C対応CPUを対象としています。主な利用対象はWindows 11です。快適に使えるCPU・メモリの下限は、動作報告を集めて確認しています。

Haas版は音声区間を繰り返し認識して途中字幕を更新します。字幕は確定前に書き換わることがあり、停止後にも最後の認識を待ちます。長く話し続けたときの遅延と、固有名詞の精度は改善中です。すべてのアプリでの転記を保証するものではありません。

ソースの再ビルド、合成音声、回帰テストは確認しています。Windowsノートでの利用報告もありますが、対応環境全体の試験は完了していません。[計測条件と既知の制約](docs/PERFORMANCE.md)を分けて記録しています。

## Windows版の通信・保存と設定

モデルの取得時に通信します。モデル準備後の音声認識・句読点補完・任意の生成校正はPC内で処理します。音声や入力文を外部の推論サービスへ送信する機能はありません。任意の生成校正は、認証付きのPC内ループバック通信を使います。

入力先の周辺文とウィンドウタイトルを文脈として使う設定は、初期状態でオンです。前面で見たWebページのタイトルは最大3件・3分間、メモリだけに保持します。ブラウザの履歴ファイルやページ本文は取得しません。設定からオフにできます。

復旧用の最後の入力と、初期状態でオンの認識ログは、PC内に平文で保存します。ログには本文・日時・入力先アプリ・転記結果などが含まれます。保存先は`%LOCALAPPDATA%\ShigarakiHaasPreview`です。設定で認識ログをオフにできます。アプリを削除しても、このデータは残します。

## フィードバックと開発

[動作報告・不具合・改善要望](https://github.com/k3peta/shigaraki/issues/new/choose)を歓迎します。「このPC・このアプリで快適だった」という報告も役立ちます。分かる範囲で、OSと信楽の版、CPU・メモリ、字幕が出始める速さ、話し続けたときの遅れ、停止後の待ち時間を教えてください。

ソースから実行・ビルドする手順は[Windows版](docs/BUILD.md)と[Mac版](docs/BUILD_MACOS.md)、変更の提案方法は[CONTRIBUTING.md](CONTRIBUTING.md)にまとめています。
