import os
from datetime import date
from flask import Flask, render_template, request, redirect, url_for
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    category_id INTEGER NOT NULL REFERENCES categories(id),
                    amount REAL NOT NULL,
                    txn_date DATE NOT NULL,
                    note TEXT
                )
            """)
            cur.execute("""
                INSERT INTO categories (name)
                SELECT * FROM (VALUES ('餐飲'), ('交通'), ('購物'), ('娛樂'), ('其他')) AS v(name)
                WHERE NOT EXISTS (SELECT 1 FROM categories)
            """)
        conn.commit()


def parse_month(month_str):
    if month_str:
        try:
            y, m = month_str.split("-")
            return int(y), int(m)
        except ValueError:
            pass
    today = date.today()
    return today.year, today.month


def shift_month(year, month, delta):
    m = month - 1 + delta
    y = year + m // 12
    m = m % 12 + 1
    return y, m


@app.route("/")
def index():
    year, month = parse_month(request.args.get("month"))
    month_str = f"{year:04d}-{month:02d}"
    category_id = request.args.get("category_id", type=int)

    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, 1)

    with get_conn() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT t.id, t.amount, t.txn_date, t.note, c.name AS category, c.id AS category_id
                FROM transactions t
                JOIN categories c ON c.id = t.category_id
                WHERE date_trunc('month', t.txn_date) = %s::date
            """
            params = [f"{month_str}-01"]
            if category_id:
                query += " AND c.id = %s"
                params.append(category_id)
            query += " ORDER BY t.txn_date DESC, t.id DESC"
            cur.execute(query, params)
            rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS income,
                    COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0) AS expense
                FROM transactions
                WHERE date_trunc('month', txn_date) = %s::date
                """,
                [f"{month_str}-01"],
            )
            totals = cur.fetchone()
            income = totals["income"]
            expense = totals["expense"]

            cur.execute(
                """
                SELECT c.name AS category, SUM(t.amount) AS total
                FROM transactions t
                JOIN categories c ON c.id = t.category_id
                WHERE date_trunc('month', t.txn_date) = %s::date AND t.amount < 0
                GROUP BY c.name
                ORDER BY total ASC
                """,
                [f"{month_str}-01"],
            )
            chart_rows = cur.fetchall()

            cur.execute("SELECT id, name FROM categories ORDER BY id")
            categories = cur.fetchall()

    chart_labels = [r["category"] for r in chart_rows]
    chart_values = [abs(r["total"]) for r in chart_rows]

    return render_template(
        "index.html",
        rows=rows,
        income=income,
        expense=expense,
        net=income + expense,
        categories=categories,
        today=date.today().isoformat(),
        month_str=month_str,
        prev_month=f"{prev_y:04d}-{prev_m:02d}",
        next_month=f"{next_y:04d}-{next_m:02d}",
        selected_category_id=category_id,
        chart_labels=chart_labels,
        chart_values=chart_values,
    )


@app.route("/add", methods=["POST"])
def add():
    amount = float(request.form["amount"])
    category_id = int(request.form["category_id"])
    txn_date = request.form["txn_date"]
    note = request.form.get("note", "")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO transactions (category_id, amount, txn_date, note) VALUES (%s, %s, %s, %s)",
                (category_id, amount, txn_date, note),
            )
        conn.commit()

    return redirect(url_for("index"))


@app.route("/edit/<int:txn_id>", methods=["GET", "POST"])
def edit(txn_id):
    if request.method == "POST":
        amount = float(request.form["amount"])
        category_id = int(request.form["category_id"])
        txn_date = request.form["txn_date"]
        note = request.form.get("note", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE transactions SET amount=%s, category_id=%s, txn_date=%s, note=%s WHERE id=%s",
                    (amount, category_id, txn_date, note, txn_id),
                )
            conn.commit()
        return redirect(url_for("index"))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, amount, category_id, txn_date, note FROM transactions WHERE id = %s",
                (txn_id,),
            )
            txn = cur.fetchone()
            cur.execute("SELECT id, name FROM categories ORDER BY id")
            categories = cur.fetchall()

    return render_template("edit.html", txn=txn, categories=categories)


@app.route("/delete/<int:txn_id>", methods=["POST"])
def delete(txn_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE id = %s", (txn_id,))
        conn.commit()

    return redirect(url_for("index"))


@app.route("/categories/add", methods=["POST"])
def add_category():
    name = request.form.get("name", "").strip()
    if name:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (name,),
                )
            conn.commit()

    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
