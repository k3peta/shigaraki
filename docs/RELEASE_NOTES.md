# 0.1.0a3 — 最初のGitHub公開ベータ

Haasを使ったWindows版のソースコードをMITで公開しました。音声認識はThird IntelligenceのHaas-ja-v1.0、GGUF変換とCPU推論はSakasegawaのspeech.cpp系の成果を利用しています。作者・出典・各ライセンスを[CREDITS.md](../CREDITS.md)と[第三者ライセンス案内](../THIRD_PARTY_NOTICES.md)に記載しました。

録音中の途中字幕、左Ctrlの長押し入力、左Ctrl2回による大ウィンドウの開閉、連続入力、辞書、転記失敗時のコピーに対応しています。認識制御はRust、画面はPython・Qtです。通常版とHaas版は両方インストールできますが、同時使用はできません。

この公開版では、導入・ビルド・テスト手順、ライセンス原文、動作報告フォームを整備しました。公開前のa2から、認識の区間管理やモデルは変更していません。認識ヘルパーは同じ固定ソースから再ビルドしています。

GPUや外部推論APIは不要です。初回に約352MBのHaasモデルを取得します。高速句読点用の約44MBは同梱し、任意のQwen生成校正は初期状態でオフです。

低速CPUで快適に動く下限は確認中です。途中字幕の遅れ、停止後の待ち時間、固有名詞、入力先ごとの転記には改善の余地があります。署名のないベータ版として公開しています。

[ダウンロードと導入](INSTALL.md)・[ビルド方法](BUILD.md)・[計測条件](PERFORMANCE.md)・[動作報告](https://github.com/k3peta/shigaraki/issues/new/choose)
