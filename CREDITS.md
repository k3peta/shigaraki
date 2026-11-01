# クレジットと出典

信楽の音声認識・句読点補完・画面表示は、以下の公開成果とライブラリを利用しています。モデルや推論エンジンの開発成果は、それぞれの作者に帰属します。

## 認識モデルと推論

| 使用箇所 | 作者・プロジェクト | 条件・固定版 |
| --- | --- | --- |
| 日本語認識モデル | Third Intelligence — [Haas-ja-v1.0](https://huggingface.co/third-intelligence/Haas-ja-v1.0)、[Haas-ASR](https://github.com/third-intelligence/Haas-ASR) | Apache-2.0。学習済みモデルを使用 |
| GGUF変換・量子化モデル | Sakasegawa — [Haas-ja-v1.0-GGUF](https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF) | Apache-2.0。Q4_K、revision `6b965d5007ba780585de51549d57a8c1c0d67c7e` |
| CPU推論エンジン | Sakasegawa — [speech.cpp](https://github.com/nyosegawa/speech.cpp) | MIT。0.10.0、commit `06590811a90041f935a470ba0e2b85346d2fc041` |
| テンソル計算 | Georgi Gerganovほか — [ggml](https://github.com/ggml-org/ggml) | MIT。commit `700c9d743ed7cc583c0f1b6507afebb6c0e88bc0` |

Haasのモデル構造・学習データ・評価条件は、Third Intelligenceの[公式モデルカード](https://huggingface.co/third-intelligence/Haas-ja-v1.0)を参照してください。GGUF変換とCPU実行を可能にしたのはSakasegawaの公開成果です。信楽では、これらを組み合わせて録音中の字幕を更新しています。Haasの学習やGGUF変換は信楽では行っていません。

初期調査では[foxn2000によるspeech.cppのfork](https://github.com/foxn2000/speech.cpp)も参照しました。今回の配布に使用したソースは、上表のnyosegawaリポジトリの固定commitです。

## 句読点補完

[oboroge0 / hayamimi-punct-ja-4class](https://huggingface.co/oboroge0/hayamimi-punct-ja-4class)のint8 ONNX重みとトークナイザーを使用します。固定revisionは`226c5d4a461ed368264881fc3e6637cbeb68055e`です。モデルカードには重みがMITと明記されています。

基盤モデルは[SB Intuitions / modernbert-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m)（MIT）、学習テキストはHugging Faceとデータセット貢献者による[FineWeb-2](https://huggingface.co/datasets/HuggingFaceFW/fineweb-2)の`jpn_Jpan`（ODC-By 1.0）です。FineWeb-2の出典を明示し、基盤モデルと句読点モデル両方のライセンス原文を残しています。信楽でモデルの再学習は行っていません。

## 任意の生成校正

Qwenチームの[Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B)（Apache-2.0）を、ggml-orgの[GGUF版](https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF)と[llama.cpp](https://github.com/ggml-org/llama.cpp)（MIT、b10566）で実行します。Qwen重みのrevisionは`807dfd9a6fadbf703207aac3bfb0b2b3040d077c`です。この機能は任意で、初期状態ではオフです。

## アプリと配布に使用するもの

Python Software Foundation / CPython、Qt Project・The Qt Companyほか / PySide6・Qt、NumPy contributors / NumPy、python-sounddevice contributors / sounddevice、PortAudio contributors / PortAudio、comtypes contributors / comtypes、Microsoftほか / ONNX Runtime、Hugging Face / tokenizersを使用しています。

Rust・Rustクレート、MinGW-w64・GCC・winpthreads、NSISもビルドや配布を支えています。詳細なライセンス原文と対応ソースは[第三者ライセンス案内](THIRD_PARTY_NOTICES.md)から確認できます。

## 信楽が追加する部分と謝辞

信楽は、録音操作、大小ウィンドウ、途中字幕の表示制御、Rust側の音声区間管理、辞書と文脈の接続、入力先の検査・転記・復旧、導入手順を追加しています。従来の[声吉Windows版](https://github.com/k3peta/koekichi-win)から派生しており、MITの元の著作権表示を保持しています。

[iiinno / OpenHinoVoice](https://github.com/iiinno/OpenHinoVoice)は、Haas・speech.cppを使う音声入力アプリとして、また出典・公開方法の参考として挙げています。今回の公開用ソースへOpenHinoVoiceのソースファイルは取り込んでいません。

信楽自身のソースは[MIT](LICENSE)で公開します。上流のモデルやライブラリのライセンスをMITへ変更するものではありません。
