# WAVファイルによるCPUベンチマーク

開発用CLI `scripts/benchmark_haas_flow.py` です。取得済みのGGUFモデルとhelperを明示し、WAVファイルを録音速度で渡します。マイクは開かず、モデルや実行ファイルの取得も行いません。CLIは開発者向けに同梱しています。待機処理の試作は採用せず、本体の認識・字幕・パラメータは維持しています。既存配布物のhelperを指定した場合は、その版の動作を測定します。

Python 3を使い、プロジェクトのルートで実行します。音声は16 kHz・16 bit・モノラルの非圧縮PCM WAVで、空でない最大305秒のファイルが対象です。比較中は信楽本体や他の認識・ビルド処理を止め、1 workerずつ測定してください。モデル・helper・音声・辞書ファイル・スレッド数を記録し、比較では変更したい条件以外を揃えます。

## 共通の辞書ファイル

UTF-8の `keywords.txt` に認識のヒントを1行1語で用意します。空の辞書は使えません。以下は今回の計測に使った6語です。すべての比較で同じファイルを使ってください。

```text
Gemini=ジェミニ
Claude=クロード
Antigravity=アンチグラビティ,アンチグラビティー
Codex=コーデックス
ChatGPT=チャットジーピーティー
OpenAI=オープンエーアイ
```

## Windowsの例

PowerShellで、以下のパスを用意したファイルの場所に置き換えます。helperと対応するCPUエンジン一式は、同じビルドの配置で用意してください。Windowsではエンジンをhelperからリンクするため、`--library` は指定しません。現行のWindows実行要件は[導入・対応環境](../README.md)と[ビルド方法](BUILD.md)を参照してください。

```powershell
py scripts/benchmark_haas_flow.py `
  --worker "C:\path\to\shigaraki-haas-flow.exe" `
  --model "C:\path\to\Haas-616M-ja-v1.0-Q4_K.gguf" `
  --audio "C:\bench\synthetic.wav" `
  --keywords-file "C:\bench\keywords.txt" `
  --threads 6 --rounds 1 --output "C:\bench\baseline-t6.json"
```

スレッド数を比較するときは `--threads 1`、`2`、`4`、`6` と出力先を変え、順番に実行します。1スレッドへの制限は古いCPUや低性能CPUの再現ではありません。実際の低性能PCでも確認してください。WindowsのCLI経路はモデル不要のテストで確認していますが、Windows実機での音声認識測定は未確認です。

## macOSの例

macOSでは対応するCPU用ライブラリを `--library` で明示します。Linuxもライブラリの指定が必要です。

```sh
python3 scripts/benchmark_haas_flow.py \
  --worker "/path/to/shigaraki-haas-flow" \
  --library "/path/to/libspeech.4.dylib" \
  --model "/path/to/Haas-616M-ja-v1.0-Q4_K.gguf" \
  --audio "/path/to/synthetic.wav" \
  --keywords-file "/path/to/keywords.txt" \
  --threads 6 --rounds 1 --output "/path/to/baseline-t6.json"
```

## 出力の読み方

出力先には新しいJSONファイルを指定します。既存ファイルは上書きしません。標準ではモデル読込後、最初の音声の先頭から最大2秒で準備用の認識を行い、その後に本測定を始めます。録音中の字幕を有効にし、最後の端数サンプルも送ってからStopを送ります。

- 初字幕は、開始から非空の字幕をpipeで受信するまでの時間です。画面描画までの時間は含みません。
- 録音終了からの待ちは、音声長から求めた最後のサンプルの予定時刻から認識完了の受信までです。Stop書込開始から完了受信までの時間も別に記録します。
- workerのCPU秒はuser時間とsystem時間の合計で、CPU使用率ではありません。ピークRSSはモデル読込と準備用の認識を含むworker生存期間全体の値です。
- wakeup counterはmacOSのworker単体の値です。非対応のOSや取得できない項目は `null` と理由を残し、0として補いません。
- 本文は標準では保存せず、SHA-256と字数を記録します。全文比較が必要な場合だけ `--include-text` を加えてください。

複数roundの本文一致は、同じ音声の最初の測定結果と比較します。別のhelperとの比較では、同じ音声・辞書・モデルの結果同士でSHAや全文を確認してください。本文一致は認識の絶対精度を保証しません。

`--model-sha256` には別途確認済みのモデルSHAを記録できます。このオプションでモデルを再検証するわけではありません。workerとライブラリのSHA、モデルの場所・サイズ・更新時刻、各試行の条件もJSONに残ります。少数回の測定から速さや必要CPUの下限を断定せず、Windows実機と実際の入力先での確認は別に行ってください。[今回の計測条件と制約](PERFORMANCE.md)も参照してください。
