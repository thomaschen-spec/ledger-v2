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


@app.route("/")
def index():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.amount, t.txn_date, t.note, c.name AS category
                FROM transactions t
                JOIN categories c ON c.id = t.category_id
                ORDER BY t.txn_date DESC, t.id DESC
            """)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM transactions
                WHERE date_trunc('month', txn_date) = date_trunc('month', CURRENT_DATE)
            """)
            month_total = cur.fetchone()["total"]

            cur.execute("SELECT id, name FROM categories ORDER BY id")
            categories = cur.fetchall()

    return render_template(
        "index.html",
        rows=rows,
        month_total=month_total,
        categories=categories,
        today=date.today().isoformat(),
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


@app.route("/delete/<int:txn_id>", methods=["POST"])
def delete(txn_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE id = %s", (txn_id,))
        conn.commit()

    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
