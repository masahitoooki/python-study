import os

import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

# DB接続（main.py と同じ接続先。パスワードは環境変数 DB_PASSWORD から読む）
url = URL.create(
    "postgresql+psycopg",
    username="macbook",
    password=os.environ.get("DB_PASSWORD"),
    host="localhost",
    port=5432,
    database="python_study",
)
engine = create_engine(url)

# SQL の SELECT 結果を、DataFrame（表）として読み込む
df = pd.read_sql("SELECT * FROM orders_practice ORDER BY order_id", engine)

print("--- 形状（行数, 列数）---")
print(df.shape)
print("--- 列の型 ---")
print(df.dtypes)
print("--- 先頭5行 ---")
print(df.head())

# 全体の集計（SQLの1本目と同じ内容）
print("--- 全体の集計 ---")
print("row_count   :", len(df))
print("amount_count:", df["amount"].count())
print("null_count  :", df["amount"].isna().sum())
print("amount_sum  :", df["amount"].sum())
print("amount_avg  :", round(df["amount"].mean(), 1))

# 支店別の集計（SQL: GROUP BY branch）
by_branch = (
    df.groupby("branch")
    .agg(
        order_count=("order_id", "count"),
        amount_count=("amount", "count"),
        amount_sum=("amount", "sum"),
        amount_avg=("amount", "mean"),
    )
    .round({"amount_avg": 1})
    .sort_values("amount_sum", ascending=False)
)
print("--- 支店別の集計 ---")
print(by_branch)

# 月の列を作る（SQL: TO_CHAR(order_date, 'YYYY-MM')）
df["order_month"] = pd.to_datetime(df["order_date"]).dt.strftime("%Y-%m")

# 月別の集計（SQL: GROUP BY order_month）
by_month = (
    df.groupby("order_month")
    .agg(
        order_count=("order_id", "count"),
        amount_count=("amount", "count"),
        amount_sum=("amount", "sum"),
    )
)
print("--- 月別の集計 ---")
print(by_month)

# 月 × category の集計（SQL: GROUP BY order_month, category）
by_month_category = (
    df.groupby(["order_month", "category"])
    .agg(
        order_count=("order_id", "count"),
        amount_sum=("amount", "sum"),
    )
)
print("--- 月 × category の集計 ---")
print(by_month_category)

plt.rcParams["font.family"] = "Hiragino Sans"  # Macの日本語フォント


def style_axes(ax):
    """縦軸の3桁区切り・横線・枠線をそろえる（グラフ共通の見た目）"""
    ax.yaxis.set_major_formatter("{x:,.0f}")
    ax.grid(axis="y", color="#e5e5e2", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


# グラフ1：月別の合計金額（棒グラフ）
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.bar(by_month.index, by_month["amount_sum"], color="#2a78d6", width=0.6)
ax.set_title("月別の合計金額（金額が空の6件は除く）")
ax.set_xlabel("注文月")
ax.set_ylabel("合計金額（円）")
style_axes(ax)
fig.tight_layout()
fig.savefig("monthly_sum.png", dpi=150)
print("グラフを monthly_sum.png に保存しました")

# グラフ2：月 × category の積み上げ棒グラフ
pivot = by_month_category["amount_sum"].unstack("category")  # 行=月、列=category
print("--- 月 × category（横に並べた表）---")
print(pivot)

categories = ["家電", "日用品", "食品"]
colors = ["#2a78d6", "#eb6834", "#1baf7a"]  # 青・オレンジ・緑（categoryごとに固定）

fig, ax = plt.subplots(figsize=(8, 4.5))
bottom = pd.Series(0.0, index=pivot.index)  # 積み上げの「下の位置」
for category, color in zip(categories, colors):
    ax.bar(
        pivot.index,
        pivot[category],
        bottom=bottom,
        color=color,
        width=0.6,
        edgecolor="white",
        linewidth=1.5,
        label=category,
    )
    bottom = bottom + pivot[category]
ax.set_title("月別の合計金額（category別）")
ax.set_xlabel("注文月")
ax.set_ylabel("合計金額（円）")
ax.legend(frameon=False)
style_axes(ax)
fig.tight_layout()
fig.savefig("monthly_by_category.png", dpi=150)
print("グラフを monthly_by_category.png に保存しました")