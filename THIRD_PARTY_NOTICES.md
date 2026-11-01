# 第三者ライセンスと対応ソース

信楽自身のソースは[MIT](LICENSE)です。以下のライブラリ・モデル・ツールには、上流それぞれのライセンスが適用されます。名称と利用箇所は[CREDITS.md](CREDITS.md)にまとめています。

## 認識・モデル・ネイティブコード

| 対象 | 原文・出典 |
| --- | --- |
| speech.cpp | [MIT原文](third_party/LICENSE-speech.txt)、[固定ソース](https://github.com/nyosegawa/speech.cpp/tree/06590811a90041f935a470ba0e2b85346d2fc041) |
| ggml | [MIT原文](third_party/ggml-LICENSE.txt)、[固定ソース](https://github.com/nyosegawa/ggml/tree/700c9d743ed7cc583c0f1b6507afebb6c0e88bc0) |
| Haas・GGUF | [Apache-2.0原文](third_party/Haas-MODEL-LICENSE.txt)、[Third Intelligence](https://huggingface.co/third-intelligence/Haas-ja-v1.0)、[Sakasegawaの固定GGUF](https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF/tree/6b965d5007ba780585de51549d57a8c1c0d67c7e) |
| 句読点モデル | [モデルに付属する原文](third_party/punctuation-MODEL_LICENSE.txt)、[重みMITの記載を含む固定モデルカード](https://huggingface.co/oboroge0/hayamimi-punct-ja-4class/blob/226c5d4a461ed368264881fc3e6637cbeb68055e/README.md)、[利用通知](third_party/punctuation-NOTICE.md) |
| 句読点の基盤モデル | [SB IntuitionsのMIT原文](third_party/SB-Intuitions-modernbert-MIT.txt)、[modernbert-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m) |
| FineWeb-2学習データの出典 | Hugging Faceとデータセット貢献者、[FineWeb-2 jpn_Jpan](https://huggingface.co/datasets/HuggingFaceFW/fineweb-2)、[ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/) |
| Qwen3.5 | [Apache-2.0原文](third_party/Qwen3.5-Apache-2.0.txt)、[Qwenモデル](https://huggingface.co/Qwen/Qwen3.5-0.8B) |
| llama.cpp | [MIT原文](third_party/llama.cpp-MIT.txt)、[b10566ソース](https://github.com/ggml-org/llama.cpp/tree/b10566) |
| GCCランタイム | [GPLv3原文](third_party/GCC-COPYING3.txt)、[GCC Runtime Library Exception](third_party/GCC-RUNTIME-EXCEPTION.txt) |
| MinGW-w64・winpthreads | [MinGW-w64原文](third_party/MinGW-w64-COPYING.txt)、[winpthreads原文](third_party/MinGW-winpthreads-COPYING.txt) |
| LLVM/OpenMP | [原文](third_party/LLVM-OpenMP.txt)、任意の校正ランタイム等に付属するもの |

上流のライセンス原文やモデルカードは、著作権表示・注記を含めて保持します。句読点モデルのライセンスファイルにあるソース限定の注記も削除せず、重みの条件を明記したモデルカードと併記しています。

## Python・画面ライブラリ

CPythonはPSFライセンス、NumPyはBSD系、sounddeviceはMIT、PortAudioはMIT系、comtypesはMIT、ONNX RuntimeはMIT、tokenizersはApache-2.0です。配布物の`runtime/`内に、各パッケージ付属のライセンスと第三者通知を残しています。ソース実行に使うPython依存パッケージの版は`requirements.txt`で固定し、公開exeの版は同梱の`upstream.json`にも記録します。

PySide6・Shiboken・Qtには、LGPLv3、GPL、Qtの例外条項などが適用される部分があります。信楽の画面では共有ライブラリを動的に利用します。同梱のQtQmlCompilerなど、GPLと例外条項で提供されるツールもあるため、Qt全体をLGPLとは扱いません。

- [LGPLv3原文](third_party/Qt.LGPL-3.0-only.txt)
- [GPLv3原文](third_party/Qt.GPL-3.0-only.txt)・[GPLv2原文](third_party/Qt.GPL-2.0-only.txt)・[Qt GPL例外](third_party/Qt.Qt-GPL-exception-1.0.txt)
- [Qt 6.12.0の対応ソース](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/)
- [PySide6 / Shiboken 6.12.0の対応ソース](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=6.12.0)

共有ライブラリを変更して使う場合は、アプリを終了し、配布フォルダの`runtime/site-packages/PySide6/`や`shiboken6/`を、自分でビルドした対応するABI・版のものへ差し替えられます。別フォルダへ配布物をコピーして試せます。変更・デバッグに必要なリバースエンジニアリングを、このプロジェクトの条件として禁止するものではありません。

## Rustの依存

`rust/haas-flow/Cargo.lock`に版とチェックサムを記録しています。Rustと各クレートのライセンス原文は、公開配布物の`third_party/rust/`に収録します。クレートを追加・更新するときは、そのライセンス原文も更新してください。
