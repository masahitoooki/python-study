import argparse
import configparser  # ★追加：設定ファイルを読む
import csv
import os  # ★追加：環境変数（DB_PASSWORD）を読む
import re  # ★追加：日付の「形」（YYYY-MM-DD）を確認する
import sys
from pathlib import Path  # ★追加：config.ini の場所を決める
from decimal import Decimal, InvalidOperation
from sqlalchemy import create_engine, inspect, MetaData, Table, select, func  # ★変更：MetaData, Table, select, func を追加
from sqlalchemy.dialects.postgresql import insert  # ★追加：ON CONFLICT が使える INSERT（PostgreSQL用）
from sqlalchemy.engine import URL  # ★追加：パスワードの記号も安全に扱える接続先の組み立て
from sqlalchemy.exc import DBAPIError  # ★追加：DB由来のエラーだけを受け取る
from sqlalchemy.sql.sqltypes import (
    Integer, BigInteger, SmallInteger, String,
    Numeric,
    Date,
)
from datetime import datetime

# ★変更：設定値は config.ini に移した（main.py と同じフォルダ）
config_path = Path(__file__).with_name("config.ini")


# ★追加：config.ini を読んで、設定の辞書を返す
def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    try:
        return {
            "db_host": parser["database"]["host"],
            "db_port": int(parser["database"]["port"]),
            "db_name": parser["database"]["name"],
            "db_user": parser["database"]["user"],
            "table_name": parser["batch"]["table_name"],
            "default_csv_path": parser["batch"]["default_csv_path"],
            "log_path": parser["batch"]["log_path"],
            "key_column": parser["batch"]["key_column"],
            "reject_prefix": parser["batch"]["reject_prefix"],  # ★追加
            "csv_encoding": parser["batch"]["csv_encoding"],    #入力CSVの文字コード
        }
    except KeyError as e:
        raise KeyError(f"設定ファイルに項目がありません: {e}")


# ログ出力を1か所にまとめる（日時・本文・区切り線）
# ★変更：ログのファイル名を、引数で受け取る
def write_log(lines, log_path):
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"実行日時：{datetime.now()}\n")
        for line in lines:
            log_file.write(f"{line}\n")
        log_file.write("--------------------\n")


# ★追加：却下した行を、CSVファイルに出す。作ったファイル名を返す（却下がなければ作らない）
def write_reject(reject_prefix, header, rejects):
    if not rejects:
        return None

    # 実行のたびに別ファイルにする（前回のファイルと混ざらないように、日時を付ける）
    reject_path = f"{reject_prefix}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    with open(reject_path, "w", encoding="utf-8", newline="") as reject_file:
        writer = csv.writer(reject_file)
        # 先頭に「行番号」「エラー内容」を足して、あとは元のCSVのまま
        writer.writerow(["行番号", "エラー内容"] + header)
        for line_number, row, messages in rejects:
            writer.writerow([line_number, " / ".join(messages)] + row)

    return reject_path


# ★変更：CSVを1行ずつ検証して、「登録してよい行」と「却下する行」に分けて返す
#   header       : CSVのヘッダー行
#   good_rows    : DBに渡せる値に変換済みの行（{列名: 値} のリスト）
#   rejects      : 却下する行（(行番号, 元の行, [エラー内容]) のリスト）
#   header_error : ヘッダーがDBと違うときの説明（問題なければ None）
# ★変更：入力CSVの文字コード（csv_encoding）を引数で受け取る
def validate_csv(csv_path, csv_columns, key_column, csv_encoding):
    good_rows = []
    rejects = []
    expected_columns = [column["name"] for column in csv_columns]

    # CSVチェック
    with open(csv_path, "r", encoding=csv_encoding) as file:
        reader = csv.reader(file)

        # ★変更：中身が空のファイルは、わかるメッセージで止める
        header = next(reader, None)
        if header is None:
            raise Exception("CSVが空です（ヘッダー行がありません）")

        # ★追加：ヘッダーの各項目名も、前後の空白（半角・全角）を取ってから比較する
        #   例："   id" → "id"
        header = [name.strip() for name in header]       

        # DBの列定義とCSVヘッダーを比較
        if header != expected_columns:
            # ★変更：ファイル全体の問題なので、データ行をすべて却下して返す
            header_error = (
                "CSVの項目定義がDBと一致しません\n"
                f"CSV: {header}\n"
                f"DB : {expected_columns}"
            )
            for line_number, row in enumerate(reader, start=2):
                rejects.append((line_number, row, ["ヘッダーがDBと一致しません"]))
            return header, good_rows, rejects, header_error

        # 重複判定用。キー項目の位置と、見た値→最初に出た行番号
        key_index = expected_columns.index(key_column)
        seen_keys = {}

        for line_number, row in enumerate(reader, start=2):

            # 項目数チェック（項目数が違う行は、これ以上調べられないので却下）
            if len(row) != len(expected_columns):
                rejects.append((
                    line_number,
                    row,
                    [f"{len(row)}項目 / 正常{len(expected_columns)}項目"],
                ))
                continue

            # ★追加：前後の空白（半角・全角）を取った値で、検証と登録をする
            #   （却下ファイルに出す「元の行」は、row のまま残す）
            #   例："  田中 " → "田中"、空白だけ "   " → ""（空欄として扱う）
            cells = [cell.strip() for cell in row]

            # ★追加：この行のエラー内容と、DBに渡す値を、行ごとに集める
            row_errors = []
            values = {}

            # CSV内の重複チェック（空欄は必須チェック側で扱う）
            key_value = cells[key_index]  # ★変更：row → cells
            if key_value != "":
                if key_value in seen_keys:
                    row_errors.append(
                        f"{key_column}が{seen_keys[key_value]}行目と"
                        f"重複しています ({key_value})"
                    )
                else:
                    seen_keys[key_value] = line_number

            # DB定義を使って全項目を自動チェック
            for index, column in enumerate(csv_columns):
                column_name = column["name"]
                column_type = column["type"]
                value = cells[index]  # ★変更：row → cells（空白を取った値）

                # ブランクはNULL許可列だけ許可、NOT NULL列はエラー
                if value == "":
                    if not column["nullable"]:
                        row_errors.append(
                            f"{column_name}は必須項目です (NOT NULL)"
                        )
                    values[column_name] = None  # DBのNULL
                    continue

                # INTEGER系
                if isinstance(
                    column_type,
                    (Integer, BigInteger, SmallInteger)
                ):
                    try:
                        values[column_name] = int(value)
                    except ValueError:
                        row_errors.append(
                            f"{column_name}の型が不正です ({value})"
                        )

                # NUMERIC(桁数, 小数桁数)
                elif isinstance(column_type, Numeric):

                    # 数値として読めるか確認（abc や NaN はエラー）
                    try:
                        number = Decimal(value)
                        if not number.is_finite():
                            raise InvalidOperation
                    except InvalidOperation:
                        row_errors.append(
                            f"{column_name}の型が不正です ({value})"
                        )
                        continue

                    # DBに定義された桁数・小数桁数を取得（手書き不要）
                    precision = column_type.precision
                    scale = column_type.scale

                    if precision is not None and scale is not None:
                        try:
                            # DBの小数桁数に丸める
                            rounded = number.quantize(
                                Decimal(1).scaleb(-scale)
                            )
                        except InvalidOperation:
                            # 巨大すぎて丸められない値は桁数超過
                            row_errors.append(
                                f"{column_name}の桁数が上限超過です "
                                f"({value} / NUMERIC({precision},{scale}))"
                            )
                            continue

                        # 丸めて値が変わるなら小数点以下が不正
                        # （DBは黙って丸めて登録するため、ここで検出する）
                        # 例：NUMERIC(3,0)で 12.4 → 12 に変わるのでエラー
                        #     30.0 → 30 は値が同じなので通る
                        if rounded != number:
                            row_errors.append(
                                f"{column_name}の小数点以下が不正です "
                                f"({value} / NUMERIC({precision},{scale}))"
                            )
                            continue

                        # 桁数チェック
                        digits = len(rounded.as_tuple().digits)
                        if digits > precision:
                            row_errors.append(
                                f"{column_name}の桁数が上限超過です "
                                f"({value} / NUMERIC({precision},{scale}))"
                            )
                        else:
                            values[column_name] = rounded  # ★DBに渡す値（Decimal）
                    else:
                        values[column_name] = number

                # ★追加：DATE（日付）。YYYY-MM-DD の形だけ受け付ける
                elif isinstance(column_type, Date):

                    #まず形を確認(2026/10/15や20261015は却下)
                    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                        row_errors.append(
                            f"{column_name}の日付形式が不正です"
                            f"({value} / YYYY-MM-DD)"
                        )
                        continue

                    #次に、実在する日付か確認
                    try:
                        values[column_name] = datetime.strptime(
                            value, "%Y-%m-%d"
                        ).date() #DBに渡す（日付型）
                    except ValueError:
                        row_errors.append(
                            f"{column_name}は存在しない日付です({value})"
                        )

                # VARCHARなど文字列系
                elif isinstance(column_type, String):

                    max_length = column_type.length

                    if (
                        max_length is not None
                        and len(value) > max_length
                    ):
                        row_errors.append(
                            f"{column_name}の文字数が上限超過です "
                            f"({len(value)}文字 / 上限{max_length}文字)"
                        )
                    else:
                        values[column_name] = value

                # 上記以外の型は、まだチェックせず、文字列のまま渡す  # ★変更：日付は上で扱うようになった
                else:
                    values[column_name] = value

            # ★追加：エラーがあれば却下、なければ登録対象
            if row_errors:
                rejects.append((line_number, row, row_errors))
            else:
                good_rows.append(values)

    return header, good_rows, rejects, None


# 入力CSVと設定を受け取り、成功なら 0、失敗なら 1 を返す
# ★変更：設定（cfg）を引数で受け取る
def main(csv_path, cfg):
    table_name = cfg["table_name"]
    key_column = cfg["key_column"]
    log_path = cfg["log_path"]

    # ★変更：接続先は config.ini の値から組み立てる。パスワードは環境変数から（なければなし）
    db_url = URL.create(
        "postgresql+psycopg",
        username=cfg["db_user"],
        password=os.environ.get("DB_PASSWORD"),
        host=cfg["db_host"],
        port=cfg["db_port"],
        database=cfg["db_name"],
    )
    # engine は main の中で作る（import しただけでは DB に触れない）
    engine = create_engine(db_url)

    try:
        # PostgreSQLのusersテーブル定義を取得
        inspector = inspect(engine)

        # ★変更：id（主キー）も含めて、DBの全列をCSVの対象にする（idはCSVから入れる）
        csv_columns = inspector.get_columns(table_name)

        # ★変更：検証は関数に任せる。登録してよい行（rows）と、却下する行（rejects）に分かれる
        header, rows, rejects, header_error = validate_csv(
            csv_path, csv_columns, key_column, cfg["csv_encoding"] # ★変更：文字コードを渡す
        )

        # ★追加：却下した行があれば、CSVファイルに出す
        reject_path = write_reject(cfg["reject_prefix"], header, rejects)

        # テーブル定義をDBから読む（列名を手書きしない）
        table = Table(table_name, MetaData(), autoload_with=engine)

        # 件数確認と登録を1つのトランザクションにする
        #   途中でエラーになったら、全件が取り消される（一部だけ登録されない）
        with engine.begin() as conn:
            # 全件を読まず、件数だけDBに数えてもらう
            before_count = conn.execute(
                select(func.count()).select_from(table)
            ).scalar_one()

            inserted_keys = []
            if rows:
                # key_column が登録済みの行は、DBがスキップする
                #   （key_column だけを指定しているので、idだけが重複した行はスキップされず、エラーになる）
                stmt = (
                    insert(table)
                    .on_conflict_do_nothing(index_elements=[key_column])
                    .returning(table.c[key_column])
                )
                inserted_keys = conn.execute(stmt, rows).all()

            after_count = conn.execute(
                select(func.count()).select_from(table)
            ).scalar_one()

            csv_count = len(rows) + len(rejects)  # ★変更：却下した行も含めたCSVの件数
            expected_count = len(inserted_keys)  # DBが「登録した」と返した件数
            skipped_count = len(rows) - expected_count
            actual_count = after_count - before_count

            print("CSV件数:", csv_count)
            print("却下件数:", len(rejects))  # ★追加
            print("登録済みのためスキップ:", skipped_count)
            print("追加前:", before_count)
            print("追加後:", after_count)
            print("登録件数（DBの応答）:", expected_count)
            print("実際の追加件数（件数の差）:", actual_count)

            # 一致しなければ、例外でトランザクションごと取り消す
            if expected_count != actual_count:
                raise Exception("追加件数が一致しません")

        print("件数一致")

        # ★追加：却下した行を、画面とログに出す
        reject_lines = [
            f"{line_number}行目: " + " / ".join(messages)
            for line_number, row, messages in rejects
        ]
        if header_error:
            print(header_error)
            reject_lines.insert(0, header_error.replace("\n", " / "))
        if rejects:
            print(f"却下: {len(rejects)}件 → {reject_path}")  # ★変更：出力先も表示
            for text in reject_lines:
                print("  " + text)

        write_log([
            f"入力CSV: {csv_path}",
            f"CSV件数: {csv_count}",
            f"却下件数: {len(rejects)}",  # ★追加
            f"却下ファイル: {reject_path}",  # ★追加
            f"登録済みのためスキップ: {skipped_count}",
            f"追加前: {before_count}",
            f"追加後: {after_count}",
            f"登録件数（DBの応答）: {expected_count}",
            f"実際の追加件数（件数の差）: {actual_count}",
            "件数一致",
            *reject_lines,  # ★追加
        ], log_path)

        # ★変更：却下した行（またはヘッダー不一致）があれば、正常な行は登録済みでも、失敗（1）にする
        if rejects or header_error:
            return 1

        return 0  # 成功

    # ★追加：DBが返したエラーは、SQL文や行の値を出さず、要点だけにする
    #   （DBAPIError は、重複・接続失敗など、DB由来のエラーの親クラス）
    except DBAPIError as e:
        diag = getattr(e.orig, "diag", None)  # psycopgが持つ、DBのエラー内容
        parts = [
            text for text in (
                getattr(diag, "message_primary", None),  # 例：duplicate key value violates ...
                getattr(diag, "message_detail", None),   # 例：Key (id)=(20) already exists.
            ) if text
        ] or [str(e.orig)]

        print("エラー: DBでエラーが発生しました")
        for text in parts:
            print("  " + text)

        write_log([
            f"入力CSV: {csv_path}",
            f"エラー：DBでエラーが発生しました: {' / '.join(parts)}",
        ], log_path)

        return 1  # 失敗

    # ★追加：CSVの文字コードが設定（csv_encoding）と合わないときは、直す場所が分かる表示にする
    #   （UnicodeDecodeError は、CSVを読むときの文字コードエラー。Exception より先に書く）
    except UnicodeDecodeError:
        encoding = cfg["csv_encoding"]
        print("エラー：CSVの文字コードが設定と合っていません")
        print(f"  config.ini の csv_encoding（現在: {encoding}）を、CSVの文字コードに合わせてください")
        print("  UTF-8 → utf-8-sig / Shift_JIS → cp932")

        write_log([
            f"入力CSV： {csv_path}",
            f"エラー：CSVの文字コードが設定と合っていません（csv_encoding = {encoding}）",
        ], log_path)

        return 1 # 失敗
      
    except Exception as e:
        print("エラー:", e)

        write_log([f"入力CSV: {csv_path}", f"エラー：{e}"], log_path)  # ★変更

        return 1  # 失敗

    finally:
        print("処理終了")


# python main.py で直接実行したときだけ、処理を始める
if __name__ == "__main__":
    # ★変更：先に設定を読む。読めなければ、メッセージを出して、失敗（1）で終わる
    try:
        cfg = load_config(config_path)
    except Exception as e:
        print("設定エラー:", e)
        sys.exit(1)

    # コマンドライン引数で入力CSVを指定する（省略したら config.ini の default_csv_path）
    parser = argparse.ArgumentParser(description="CSVを検証して、DBに登録する")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=cfg["default_csv_path"],  # ★変更
        help=f"入力CSVのパス（省略時: {cfg['default_csv_path']}）",
    )
    args = parser.parse_args()
    sys.exit(main(args.csv_path, cfg))  # ★変更