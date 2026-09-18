"""Build and validate the §17.1 Olist product question bank (zero paid calls).

Authors 30 development + 10 regression + 10 closed questions with reference
SQL, validates every reference query twice: (1) executable against the live
product database (read-only DSN), and (2) compliant with the frozen
QueryEngine AST policy — the same executor surface the agent operates under.
Emits three JSONL files with row counts and result digests.

Usage: uv run --env-file .env python scripts/build_product_question_bank.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

from commerce_agent.product_eval.scoring import result_digest
from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine.errors import SqlPolicyViolation

OUT_DIR = Path("data/product-eval")

# questions whose question text declares an ascending ordering — the scorer
# compares sequences, not multisets (codex F2: ordered-contract split)
_ORDERED_QUESTIONS = {"dev-01", "dev-10", "reg-06", "reg-08", "closed-01", "closed-10"}


def q(qid: str, visibility: str, question: str, sql: str, tables: list[str]) -> dict:
    return {
        "question_id": qid,
        "visibility": visibility,
        "question": question,
        "gold_sql": " ".join(sql.split()),
        "gold_tables": tables,
    }


QUESTIONS: list[dict] = [
    # ── Development 30 ──────────────────────────────────────────────────────
    q("dev-01", "development", "2018 年的订单数是多少？按年和月两列分组（年、月），按年月升序。",
      "SELECT EXTRACT(YEAR FROM order_purchase_timestamp) AS order_year, EXTRACT(MONTH FROM order_purchase_timestamp) AS order_month, COUNT(DISTINCT order_id) AS order_count FROM retail.orders WHERE order_purchase_timestamp >= '2018-01-01' AND order_purchase_timestamp < '2019-01-01' GROUP BY 1, 2 ORDER BY 1, 2", ["orders"]),
    q("dev-02", "development", "各支付方式的支付金额（payment_amount = SUM(payment_value)）是多少？按金额降序。",
      "SELECT payment_type, SUM(payment_value) AS payment_amount FROM retail.order_payments GROUP BY 1 ORDER BY 2 DESC", ["order_payments"]),
    q("dev-03", "development", "已交付（order_status = 'delivered'）订单的商品额（item_amount = SUM(price)）总额是多少？",
      "SELECT SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id WHERE o.order_status = 'delivered'", ["orders", "order_items"]),
    q("dev-04", "development", "客户所在州（customer_state）的商品额前 10 名是多少？按金额降序。",
      "SELECT c.customer_state, SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id JOIN retail.customers c ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["customers", "orders", "order_items"]),
    q("dev-05", "development", "各英文产品类别（product_category_name_english）的商品额前 10 名是多少？",
      "SELECT t.product_category_name_english, SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items", "products", "product_category_name_translation"]),
    q("dev-06", "development", "全部订单的整体平均评价分是多少？按唯一 review_id 去重后计算。",
      "SELECT AVG(review_score) AS avg_review_score FROM (SELECT DISTINCT review_id, review_score FROM retail.order_reviews) r", ["order_reviews"]),
    q("dev-07", "development", "各支付方式的平均分期数（payment_installments）是多少？",
      "SELECT payment_type, AVG(payment_installments) AS avg_installments FROM retail.order_payments GROUP BY 1 ORDER BY 1", ["order_payments"]),
    q("dev-08", "development", "全部订单的运费总额（freight_amount = SUM(freight_value)）是多少？",
      "SELECT SUM(freight_value) AS freight_amount FROM retail.order_items", ["order_items"]),
    q("dev-09", "development", "卖家数量最多的前 5 个州（seller_state）各有多少卖家？",
      "SELECT seller_state, COUNT(DISTINCT seller_id) AS seller_count FROM retail.sellers GROUP BY 1 ORDER BY 2 DESC LIMIT 5", ["sellers"]),
    q("dev-10", "development", "2017 年的支付金额（payment_amount）按年和月两列分组是多少？按年月升序。",
      "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, EXTRACT(MONTH FROM o.order_purchase_timestamp) AS order_month, SUM(pay.payment_value) AS payment_amount FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id WHERE o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-01-01' GROUP BY 1, 2 ORDER BY 1, 2", ["orders", "order_payments"]),
    q("dev-11", "development", "未交付（order_status <> 'delivered'）订单的商品额总额是多少？",
      "SELECT SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id WHERE o.order_status <> 'delivered'", ["orders", "order_items"]),
    q("dev-12", "development", "各英文产品类别的平均运费（freight_value）前 10 名是多少？",
      "SELECT t.product_category_name_english, AVG(oi.freight_value) AS avg_freight FROM retail.order_items oi JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items", "products", "product_category_name_translation"]),
    q("dev-13", "development", "评分为 5 的唯一评论（按 review_id 去重）有多少条？",
      "SELECT COUNT(DISTINCT review_id) AS five_star_reviews FROM retail.order_reviews WHERE review_score = 5", ["order_reviews"]),
    q("dev-14", "development", "客户数量最多的前 10 个城市（customer_city）各有多少客户？",
      "SELECT customer_city, COUNT(DISTINCT customer_id) AS customer_count FROM retail.customers GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["customers"]),
    q("dev-15", "development", "已交付订单从下单到客户签收的平均天数是多少？（时间戳差折算为天，只统计有签收时间的订单）",
      "SELECT AVG(order_delivered_customer_date - order_purchase_timestamp) / 86400.0 AS avg_delivery_days FROM retail.orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL", ["orders"]),
    q("dev-16", "development", "商品目录中共有多少个产品（products 行数）？",
      "SELECT COUNT(*) AS product_count FROM retail.products", ["products"]),
    q("dev-17", "development", "各英文产品类别的评价数（COUNT(DISTINCT review_id)）前 10 名是多少？",
      "SELECT t.product_category_name_english, COUNT(DISTINCT r.review_id) AS review_count FROM retail.order_reviews r JOIN retail.orders o ON o.order_id = r.order_id JOIN retail.order_items oi ON oi.order_id = o.order_id JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_reviews", "orders", "order_items", "products", "product_category_name_translation"]),
    q("dev-18", "development", "各卖家城市（seller_city）的商品额前 10 名是多少？",
      "SELECT s.seller_city, SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.sellers s ON s.seller_id = oi.seller_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["sellers", "order_items"]),
    q("dev-19", "development", "各订单状态（order_status）的订单数是多少？",
      "SELECT order_status, COUNT(DISTINCT order_id) AS order_count FROM retail.orders GROUP BY 1 ORDER BY 2 DESC", ["orders"]),
    q("dev-20", "development", "一次性付清（分期数 = 1）的支付金额占总支付金额的比例是多少？",
      "SELECT SUM(payment_value) FILTER (WHERE payment_installments = 1) / (SELECT SUM(payment_value) FROM retail.order_payments) AS single_installment_share FROM retail.order_payments", ["order_payments"]),
    q("dev-21", "development", "运费与商品额比值（SUM(freight_value) / SUM(price)）最高的前 10 个英文产品类别是多少？",
      "SELECT t.product_category_name_english, SUM(oi.freight_value) / SUM(oi.price) AS freight_to_item_ratio FROM retail.order_items oi JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items", "products", "product_category_name_translation"]),
    q("dev-22", "development", "有多少个产品类别名（product_category_name）缺少英文翻译？",
      "SELECT COUNT(DISTINCT p.product_category_name) AS untranslated_categories FROM retail.products p LEFT JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE t.product_category_name IS NULL AND p.product_category_name IS NOT NULL", ["products", "product_category_name_translation"]),
    q("dev-23", "development", "2016 年的订单数是多少？",
      "SELECT COUNT(DISTINCT order_id) AS order_count FROM retail.orders WHERE order_purchase_timestamp >= '2016-01-01' AND order_purchase_timestamp < '2017-01-01'", ["orders"]),
    q("dev-24", "development", "有评价记录的订单数（distinct order_id）是多少？",
      "SELECT COUNT(DISTINCT order_id) AS reviewed_orders FROM retail.order_reviews", ["order_reviews"]),
    q("dev-25", "development", "客户州 × 支付方式的支付金额前 10 名是多少？",
      "SELECT c.customer_state, pay.payment_type, SUM(pay.payment_value) AS payment_amount FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id JOIN retail.customers c ON c.customer_id = o.customer_id GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10", ["customers", "orders", "order_payments"]),
    q("dev-26", "development", "平均单价（AVG(price)）最高的前 10 个产品（product_id）是多少？",
      "SELECT oi.product_id, AVG(oi.price) AS avg_price FROM retail.order_items oi GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items"]),
    q("dev-27", "development", "评论内容非 NULL（review_comment_message 非 NULL）的评论行数是多少？",
      "SELECT COUNT(*) AS commented_reviews FROM retail.order_reviews WHERE review_comment_message IS NOT NULL", ["order_reviews"]),
    q("dev-28", "development", "各卖家州（seller_state）的平均评价分前 10 名是多少？",
      "SELECT s.seller_state, AVG(r.review_score) AS avg_review_score FROM retail.order_reviews r JOIN retail.orders o ON o.order_id = r.order_id JOIN retail.order_items oi ON oi.order_id = o.order_id JOIN retail.sellers s ON s.seller_id = oi.seller_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["sellers", "order_items", "orders", "order_reviews"]),
    q("dev-29", "development", "平均每个订单包含多少个订单项（order_items 行数 / distinct order_id）？",
      "SELECT COUNT(*) * 1.0 / COUNT(DISTINCT order_id) AS avg_items_per_order FROM retail.order_items", ["order_items"]),
    q("dev-30", "development", "单笔支付的最高金额是多少？",
      "SELECT MAX(payment_value) AS max_payment FROM retail.order_payments", ["order_payments"]),
    # ── Regression 10 ───────────────────────────────────────────────────────
    q("reg-01", "regression", "各客户州的每客户商品额（SUM(price) / distinct customer_id）前 10 名是多少？",
      "SELECT c.customer_state, SUM(oi.price) / COUNT(DISTINCT o.customer_id) AS item_amount_per_customer FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id JOIN retail.customers c ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["customers", "orders", "order_items"]),
    q("reg-02", "regression", "各年度（EXTRACT 年份）的订单数与商品额分别是多少？按年升序。",
      "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, COUNT(DISTINCT o.order_id) AS order_count, SUM(oi.price) AS item_amount FROM retail.orders o JOIN retail.order_items oi ON oi.order_id = o.order_id GROUP BY 1 ORDER BY 1", ["orders", "order_items"]),
    q("reg-03", "regression", "各客户州的平均送达天数（时间戳差折算为天，只统计已交付且有签收时间）降序排列是多少？",
      "SELECT c.customer_state, AVG(o.order_delivered_customer_date - o.order_purchase_timestamp) / 86400.0 AS avg_delivery_days FROM retail.orders o JOIN retail.customers c ON c.customer_id = o.customer_id WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", ["customers", "orders"]),
    q("reg-04", "regression", "2018 年分期数 × 支付方式的支付金额前 10 名是多少？",
      "SELECT pay.payment_installments, pay.payment_type, SUM(pay.payment_value) AS payment_amount FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id WHERE o.order_purchase_timestamp >= '2018-01-01' AND o.order_purchase_timestamp < '2019-01-01' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10", ["orders", "order_payments"]),
    q("reg-05", "regression", "商品额最高的前 10 个卖家州（seller_state）的合计商品额是多少？",
      "SELECT SUM(item_amount) AS top10_state_item_amount FROM (SELECT s.seller_state AS seller_state, SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.sellers s ON s.seller_id = oi.seller_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10) top10", ["order_items", "sellers"]),
    q("reg-06", "regression", "按年和季度（两列：年、季度）的平均评价分趋势是多少？",
      "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, EXTRACT(QUARTER FROM o.order_purchase_timestamp) AS order_quarter, AVG(r.review_score) AS avg_review_score FROM retail.order_reviews r JOIN retail.orders o ON o.order_id = r.order_id GROUP BY 1, 2 ORDER BY 1, 2", ["order_reviews", "orders"]),
    q("reg-07", "regression", "英文产品类别 × 客户州的商品额前 10 名是多少？",
      "SELECT t.product_category_name_english, c.customer_state, SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id JOIN retail.customers c ON c.customer_id = o.customer_id JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10", ["customers", "orders", "order_items", "products", "product_category_name_translation"]),
    q("reg-08", "regression", "每个年度（EXTRACT 年份）各支付方式的支付金额是多少？按年与支付方式排序。",
      "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, pay.payment_type, SUM(pay.payment_value) AS payment_amount FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id GROUP BY 1, 2 ORDER BY 1, 2", ["orders", "order_payments"]),
    q("reg-09", "regression", "各英文产品类别的平均运费与平均单价之比（AVG(freight_value) / AVG(price)）最高的前 10 名是多少？",
      "SELECT t.product_category_name_english, AVG(oi.freight_value) / AVG(oi.price) AS freight_price_ratio FROM retail.order_items oi JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items", "products", "product_category_name_translation"]),
    q("reg-10", "regression", "各客户州的平均订单支付金额（SUM(payment_value) / COUNT(DISTINCT order_id)）前 10 名是多少？",
      "SELECT c.customer_state, SUM(pay.payment_value) / COUNT(DISTINCT pay.order_id) AS avg_payment_per_order FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id JOIN retail.customers c ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["customers", "orders", "order_payments"]),
    # ── Closed 10 ───────────────────────────────────────────────────────────
    q("closed-01", "closed", "2017 年的商品额按年和月两列分组是多少？按年月升序。",
      "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, EXTRACT(MONTH FROM o.order_purchase_timestamp) AS order_month, SUM(oi.price) AS item_amount FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id WHERE o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-01-01' GROUP BY 1, 2 ORDER BY 1, 2", ["orders", "order_items"]),
    q("closed-02", "closed", "已交付订单的支付金额（payment_amount）总额是多少？",
      "SELECT SUM(pay.payment_value) AS payment_amount FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id WHERE o.order_status = 'delivered'", ["orders", "order_payments"]),
    q("closed-03", "closed", "订单数最多的前 10 个英文产品类别各有多少订单（distinct order_id）？",
      "SELECT t.product_category_name_english, COUNT(DISTINCT oi.order_id) AS order_count FROM retail.order_items oi JOIN retail.products p ON p.product_id = oi.product_id JOIN retail.product_category_name_translation t ON t.product_category_name = p.product_category_name GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items", "products", "product_category_name_translation"]),
    q("closed-04", "closed", "已交付订单的平均运费（freight_value）是多少？",
      "SELECT AVG(oi.freight_value) AS avg_freight FROM retail.order_items oi JOIN retail.orders o ON o.order_id = oi.order_id WHERE o.order_status = 'delivered'", ["orders", "order_items"]),
    q("closed-05", "closed", "卖家数量最多的前 10 个城市（seller_city）各有多少卖家？",
      "SELECT seller_city, COUNT(DISTINCT seller_id) AS seller_count FROM retail.sellers GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["sellers"]),
    q("closed-06", "closed", "评分为 1 的唯一评论（按 review_id 去重）有多少条？",
      "SELECT COUNT(DISTINCT review_id) AS one_star_reviews FROM retail.order_reviews WHERE review_score = 1", ["order_reviews"]),
    q("closed-07", "closed", "没有客户签收时间（order_delivered_customer_date 为空）的订单数是多少？",
      "SELECT COUNT(DISTINCT order_id) AS undelivered_orders FROM retail.orders WHERE order_delivered_customer_date IS NULL", ["orders"]),
    q("closed-08", "closed", "客户州维度的平均分期数前 10 名是多少？",
      "SELECT c.customer_state, AVG(pay.payment_installments) AS avg_installments FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id JOIN retail.customers c ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["customers", "orders", "order_payments"]),
    q("closed-09", "closed", "单件产品维度平均运费最高的前 10 个产品（product_id）是多少？",
      "SELECT oi.product_id, AVG(oi.freight_value) AS avg_freight FROM retail.order_items oi GROUP BY 1 ORDER BY 2 DESC LIMIT 10", ["order_items"]),
    q("closed-10", "closed", "2018 年每季度（EXTRACT 年与季度两列）的支付金额是多少？按年、季度升序。",
      "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, EXTRACT(QUARTER FROM o.order_purchase_timestamp) AS order_quarter, SUM(pay.payment_value) AS payment_amount FROM retail.order_payments pay JOIN retail.orders o ON o.order_id = pay.order_id WHERE o.order_purchase_timestamp >= '2018-01-01' AND o.order_purchase_timestamp < '2019-01-01' GROUP BY 1, 2 ORDER BY 1, 2", ["orders", "order_payments"]),
]


def main() -> int:
    dsn = os.environ.get("PRODUCT_DATABASE_DSN")
    if not dsn:
        raise SystemExit("PRODUCT_DATABASE_DSN missing from environment")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    policy = AstPolicy()
    files: dict[str, list[str]] = {"development": [], "regression": [], "closed": []}
    failures: list[str] = []
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        for item in QUESTIONS:
            try:
                policy.validate(item["gold_sql"])
            except SqlPolicyViolation as error:
                failures.append(f"{item['question_id']}: policy {error}")
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(item["gold_sql"])
                    columns = [d[0] for d in cur.description]
                    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            except Exception as error:  # noqa: BLE001 - validation report
                failures.append(f"{item['question_id']}: execute {type(error).__name__}")
                continue
            problems = []
            if not item["gold_sql"].lstrip().upper().startswith("SELECT"):
                problems.append("not-select")
            if len(rows) == 0:
                problems.append("empty")
            if len(rows) > 500:
                problems.append(f"too-many-rows:{len(rows)}")
            for table in item["gold_tables"]:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='retail' AND table_name=%s",
                        (table,),
                    )
                    if cur.fetchone() is None:
                        problems.append(f"missing-table:{table}")
            if problems:
                failures.append(f"{item['question_id']}: {','.join(problems)}")
                continue
            record = {
                **item,
                "row_order": "ascending"
                if item["question_id"] in _ORDERED_QUESTIONS
                else "unordered",
                "gold_columns_count": len(columns),
                "gold_row_count": len(rows),
                "gold_result_sha256": result_digest(rows),
            }
            files[item["visibility"]].append(
                json.dumps(record, ensure_ascii=False, sort_keys=True)
            )
    for name, lines in files.items():
        path = OUT_DIR / f"{name}.jsonl"
        path.write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8", newline="\n"
        )
        print(f"{path}: {len(lines)} questions")
    if failures:
        print("VALIDATION FAILURES:")
        for failure in failures:
            print(" ", failure)
        return 1
    print(f"all {len(QUESTIONS)} reference queries validated (execute + AST policy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
