import os
import functools
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-in-render-env")

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    category_id INTEGER NOT NULL REFERENCES categories(id),
                    amount REAL NOT NULL,
                    txn_date DATE NOT NULL,
                    note TEXT,
                    user_id INTEGER REFERENCES users(id)
                )
            """)
            # 舊資料相容：欄位可能是舊版留下的，補齊 user_id 欄位與唯一限制
            cur.execute("ALTER TABLE categories ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)")
            cur.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)")
            cur.execute("ALTER TABLE categories DROP CONSTRAINT IF EXISTS categories_name_key")
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'categories_name_user_unique'
                    ) THEN
                        ALTER TABLE categories ADD CONSTRAINT categories_name_user_unique UNIQUE (name, user_id);
                    END IF;
                END $$;
            """)

            # 第一個註冊帳號自動繼承所有舊資料（沒有 user_id 的資料）
            cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
            first_user = cur.fetchone()
            if first_user:
                cur.execute("UPDATE categories SET user_id = %s WHERE user_id IS NULL", (first_user["id"],))
                cur.execute("UPDATE transactions SET user_id = %s WHERE user_id IS NULL", (first_user["id"],))
        conn.commit()


def current_user_id():
    return session.get("user_id")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = None
        if not username or not password:
            error = "帳號密碼都要填"
        with get_conn() as conn:
            with conn.cursor() as cur:
                if not error:
                    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                    if cur.fetchone():
                        error = "這個帳號已經有人用了"
                if error:
                    return render_template("login.html", mode="register", error=error, username=username)
                cur.execute("SELECT COUNT(*) AS n FROM users")
                is_first_user = cur.fetchone()["n"] == 0
                cur.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                    (username, generate_password_hash(password)),
                )
                user_id = cur.fetchone()["id"]
                if not is_first_user:
                    # 第一個帳號會在下面 init_db() 繼承舊資料的分類，這裡只給「後來註冊」的新帳號預設分類
                    cur.executemany(
                        "INSERT INTO categories (name, user_id) VALUES (%s, %s)",
                        [(n, user_id) for n in ("餐飲", "交通", "購物", "娛樂", "其他")],
                    )
            conn.commit()
        if is_first_user:
            init_db()  # 讓舊資料立刻歸戶到第一個帳號
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS n FROM categories WHERE user_id = %s", (user_id,))
                    if cur.fetchone()["n"] == 0:
                        # 全新資料庫、沒有舊資料可繼承，補上預設分類
                        cur.executemany(
                            "INSERT INTO categories (name, user_id) VALUES (%s, %s)",
                            [(n, user_id) for n in ("餐飲", "交通", "購物", "娛樂", "其他")],
                        )
                conn.commit()
        session["user_id"] = user_id
        session["username"] = username
        return redirect(url_for("index"))

    return render_template("login.html", mode="register", error=None, username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
                user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", mode="login", error="帳號或密碼錯誤", username=username)
        session["user_id"] = user["id"]
        session["username"] = username
        return redirect(url_for("index"))

    return render_template("login.html", mode="login", error=None, username="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
@login_required
def index():
    uid = current_user_id()
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
                WHERE date_trunc('month', t.txn_date) = %s::date AND t.user_id = %s
            """
            params = [f"{month_str}-01", uid]
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
                WHERE date_trunc('month', txn_date) = %s::date AND user_id = %s
                """,
                [f"{month_str}-01", uid],
            )
            totals = cur.fetchone()
            income = totals["income"]
            expense = totals["expense"]

            cur.execute(
                """
                SELECT c.name AS category, SUM(t.amount) AS total
                FROM transactions t
                JOIN categories c ON c.id = t.category_id
                WHERE date_trunc('month', t.txn_date) = %s::date AND t.amount < 0 AND t.user_id = %s
                GROUP BY c.name
                ORDER BY total ASC
                """,
                [f"{month_str}-01", uid],
            )
            chart_rows = cur.fetchall()

            cur.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY id", (uid,))
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
        session_username=session.get("username"),
    )


@app.route("/add", methods=["POST"])
@login_required
def add():
    uid = current_user_id()
    amount = float(request.form["amount"])
    category_id = int(request.form["category_id"])
    txn_date = request.form["txn_date"]
    note = request.form.get("note", "")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM categories WHERE id = %s AND user_id = %s", (category_id, uid))
            if not cur.fetchone():
                return redirect(url_for("index"))
            cur.execute(
                "INSERT INTO transactions (category_id, amount, txn_date, note, user_id) VALUES (%s, %s, %s, %s, %s)",
                (category_id, amount, txn_date, note, uid),
            )
        conn.commit()

    return redirect(url_for("index"))


@app.route("/edit/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit(txn_id):
    uid = current_user_id()
    if request.method == "POST":
        amount = float(request.form["amount"])
        category_id = int(request.form["category_id"])
        txn_date = request.form["txn_date"]
        note = request.form.get("note", "")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM categories WHERE id = %s AND user_id = %s", (category_id, uid))
                if not cur.fetchone():
                    return redirect(url_for("index"))
                cur.execute(
                    "UPDATE transactions SET amount=%s, category_id=%s, txn_date=%s, note=%s WHERE id=%s AND user_id=%s",
                    (amount, category_id, txn_date, note, txn_id, uid),
                )
            conn.commit()
        return redirect(url_for("index"))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, amount, category_id, txn_date, note FROM transactions WHERE id = %s AND user_id = %s",
                (txn_id, uid),
            )
            txn = cur.fetchone()
            if not txn:
                return redirect(url_for("index"))
            cur.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY id", (uid,))
            categories = cur.fetchall()

    return render_template("edit.html", txn=txn, categories=categories)


@app.route("/delete/<int:txn_id>", methods=["POST"])
@login_required
def delete(txn_id):
    uid = current_user_id()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE id = %s AND user_id = %s", (txn_id, uid))
        conn.commit()

    return redirect(url_for("index"))


@app.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    uid = current_user_id()
    name = request.form.get("name", "").strip()
    if name:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO categories (name, user_id) VALUES (%s, %s) ON CONFLICT (name, user_id) DO NOTHING",
                    (name, uid),
                )
            conn.commit()

    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
