# python-study

Python と PostgreSQL の個人学習用リポジトリです（実務ではなく個人学習で作成）。

実務では金融システム開発で SQL（DB2）を使い、データの抽出・加工や、処理前後の件数照合・整合性確認を担当しています。
その考え方を Python で再現することを意識して作っています。

## 内容

| ファイル | 内容 |
|---|---|
| `main.py` | CSV をチェックして PostgreSQL に登録するバッチ |
| `analysis.py` | DB のデータを pandas で集計し、グラフを作るスクリプト |
| `config.ini` | `main.py` の設定（接続先・登録先テーブル・文字コードなど） |
| `orders_practice.csv` | 練習用の注文データ（架空のデータ、150件） |

## main.py：CSV チェック・DB 登録バッチ

チェック内容は、DB のテーブル定義から自動で決めます（項目名や桁数をコードに書いていません）。

**チェック**
- ヘッダーがテーブルの列と一致するか
- 1行の項目数
- 必須項目（NOT NULL）の空欄
- 整数型、NUMERIC の桁数・小数桁数
- VARCHAR の文字数
- DATE の形式（YYYY-MM-DD）と、実在する日付か
- CSV 内のキー重複
- 値の前後の空白（半角・全角）は取り除いてからチェック

**登録**
- エラーの行は、行番号とエラー内容を付けて `reject_日時.csv` に出し、正常な行だけを登録
- 空欄は NULL で登録
- 登録済みのキーは `INSERT ... ON CONFLICT DO NOTHING` でスキップ
- 登録前後の件数を数え、DB が返した登録件数と一致しなければロールバック（件数確認と登録を1つのトランザクションで実行）

**運用まわり**
- 設定は `config.ini` に分離。DB のパスワードはファイルに書かず、環境変数 `DB_PASSWORD` から読む
- 入力 CSV はコマンドライン引数で指定
- 終了コードは成功 0、失敗 1（シェルで結果を判定できる）
- 実行結果をログファイルに出力
- DB エラーは SQL 文や値を出さず要点だけ表示、文字コード違いは直す場所が分かるメッセージを表示

## analysis.py：集計とグラフ

`orders_practice` テーブルを pandas で読み込み、SQL で書いた集計と同じ内容を pandas で再現しています。

- 全体の件数・合計・平均、金額が空の件数
- 支店別、月別、月 × カテゴリ別の集計（SQL の `GROUP BY` に相当）
- 月別の合計金額の棒グラフ、カテゴリ別の積み上げ棒グラフ（PNG で保存）

## 使っている技術

Python、pandas、SQLAlchemy、psycopg、matplotlib、PostgreSQL 16

## 実行方法

```
export DB_PASSWORD=（DBのパスワード）
python main.py orders_practice.csv
python analysis.py
```

## 今後

大量データ処理、ETL・データパイプラインを学習予定です。
