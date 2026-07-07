from fastmcp import FastMCP
import json
import os
import aiosqlite  # Changed: sqlite3 → aiosqlite
import tempfile
# Use temporary directory which should be writable
TEMP_DIR = tempfile.gettempdir()
DB_PATH = os.path.join(TEMP_DIR, "expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

print(f"Database path: {DB_PATH}")

mcp = FastMCP("ExpenseTracker")

def init_db():  # Keep as sync for initialization
    try:
        # Use synchronous sqlite3 just for initialization
        import sqlite3
        with sqlite3.connect(DB_PATH) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""
                CREATE TABLE IF NOT EXISTS expenses(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT DEFAULT '',
                    note TEXT DEFAULT ''
                )
            """)
            # Test write access
            c.execute("INSERT OR IGNORE INTO expenses(date, amount, category) VALUES ('2000-01-01', 0, 'test')")
            c.execute("DELETE FROM expenses WHERE category = 'test'")
            print("Database initialized successfully with write access")
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise

# Initialize database synchronously at module load
init_db()

def _load_categories() -> dict:
    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _validate_category_subcategory(category: str, subcategory: str = "") -> str | None:
    cats = _load_categories()
    if not cats:
        return None

    if category not in cats:
        valid = ", ".join(sorted(cats.keys()))
        return f"Invalid category '{category}'. Valid categories: {valid}"

    if subcategory and subcategory not in cats[category]:
        valid = ", ".join(cats[category])
        return f"Invalid subcategory '{subcategory}' for category '{category}'. Valid subcategories: {valid}"

    return None

@mcp.tool()
async def add_expense(date, amount, category, subcategory="", note=""):  # Changed: added async
    '''Add a new expense entry to the database.'''
    validation_error = _validate_category_subcategory(category, subcategory)
    if validation_error:
        return {"status": "error", "message": validation_error}

    try:
        async with aiosqlite.connect(DB_PATH) as c:  # Changed: added async
            cur = await c.execute(  # Changed: added await
                "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
                (date, amount, category, subcategory, note)
            )
            expense_id = cur.lastrowid
            await c.commit()  # Changed: added await
            return {"status": "success", "id": expense_id, "message": "Expense added successfully"}
    except Exception as e:  # Changed: simplified exception handling
        if "readonly" in str(e).lower():
            return {"status": "error", "message": "Database is in read-only mode. Check file permissions."}
        return {"status": "error", "message": f"Database error: {str(e)}"}
    
@mcp.tool()
async def list_expenses(start_date, end_date):  # Changed: added async
    '''List expense entries within an inclusive date range.'''
    try:
        async with aiosqlite.connect(DB_PATH) as c:  # Changed: added async
            cur = await c.execute(  # Changed: added await
                """
                SELECT id, date, amount, category, subcategory, note
                FROM expenses
                WHERE date BETWEEN ? AND ?
                ORDER BY date DESC, id DESC
                """,
                (start_date, end_date)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]  # Changed: added await
    except Exception as e:
        return {"status": "error", "message": f"Error listing expenses: {str(e)}"}

@mcp.tool()
async def summarize(start_date, end_date, category=None):  # Changed: added async
    '''Summarize expenses by category within an inclusive date range.'''
    try:
        async with aiosqlite.connect(DB_PATH) as c:  # Changed: added async
            query = """
                SELECT category, SUM(amount) AS total_amount, COUNT(*) as count
                FROM expenses
                WHERE date BETWEEN ? AND ?
            """
            params = [start_date, end_date]

            if category:
                query += " AND category = ?"
                params.append(category)

            query += " GROUP BY category ORDER BY total_amount DESC"

            cur = await c.execute(query, params)  # Changed: added await
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]  # Changed: added await
    except Exception as e:
        return {"status": "error", "message": f"Error summarizing expenses: {str(e)}"}

@mcp.tool()
async def delete_expense(expense_id):
    '''Delete an expense entry by id.'''
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": "Expense not found"}
            return {"status": "success", "id": expense_id, "message": "Expense deleted"}
    except Exception as e:
        if "readonly" in str(e).lower():
            return {"status": "error", "message": "Database is in read-only mode. Check file permissions."}
        return {"status": "error", "message": f"Database error: {str(e)}"}

@mcp.tool()
async def update_expense(expense_id, date=None, amount=None, category=None, subcategory=None, note=None):
    '''Update an expense entry by id. Only pass fields you want to change.'''
    fields = {}
    if date is not None:
        fields["date"] = date
    if amount is not None:
        fields["amount"] = amount
    if category is not None:
        fields["category"] = category
    if subcategory is not None:
        fields["subcategory"] = subcategory
    if note is not None:
        fields["note"] = note

    if not fields:
        return {"status": "error", "message": "No fields to update"}

    try:
        async with aiosqlite.connect(DB_PATH) as c:
            if "category" in fields or "subcategory" in fields:
                cur = await c.execute(
                    "SELECT category, subcategory FROM expenses WHERE id = ?",
                    (expense_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return {"status": "error", "message": "Expense not found"}

                effective_category = fields.get("category", row[0])
                effective_subcategory = fields.get("subcategory", row[1])
                validation_error = _validate_category_subcategory(effective_category, effective_subcategory)
                if validation_error:
                    return {"status": "error", "message": validation_error}

            set_clause = ", ".join(f"{key} = ?" for key in fields)
            params = list(fields.values()) + [expense_id]
            cur = await c.execute(f"UPDATE expenses SET {set_clause} WHERE id = ?", params)
            if cur.rowcount == 0:
                return {"status": "error", "message": "Expense not found"}
            await c.commit()

            cur = await c.execute(
                "SELECT id, date, amount, category, subcategory, note FROM expenses WHERE id = ?",
                (expense_id,),
            )
            cols = [d[0] for d in cur.description]
            row = await cur.fetchone()
            return {"status": "success", "id": expense_id, "expense": dict(zip(cols, row))}
    except Exception as e:
        if "readonly" in str(e).lower():
            return {"status": "error", "message": "Database is in read-only mode. Check file permissions."}
        return {"status": "error", "message": f"Database error: {str(e)}"}

@mcp.tool()
async def search_expenses(
    start_date=None,
    end_date=None,
    category=None,
    subcategory=None,
    note_keyword=None,
    min_amount=None,
    max_amount=None,
    limit=50,
):
    '''Search expense entries with optional filters.'''
    try:
        limit = min(max(1, limit), 200)
        conditions = []
        params = []

        if start_date is not None and end_date is not None:
            conditions.append("date BETWEEN ? AND ?")
            params.extend([start_date, end_date])
        elif start_date is not None:
            conditions.append("date >= ?")
            params.append(start_date)
        elif end_date is not None:
            conditions.append("date <= ?")
            params.append(end_date)

        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if subcategory is not None:
            conditions.append("subcategory = ?")
            params.append(subcategory)
        if note_keyword is not None:
            conditions.append("note LIKE ?")
            params.append(f"%{note_keyword}%")
        if min_amount is not None:
            conditions.append("amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            conditions.append("amount <= ?")
            params.append(max_amount)

        query = "SELECT id, date, amount, category, subcategory, note FROM expenses"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY date DESC, id DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]
    except Exception as e:
        return {"status": "error", "message": f"Error searching expenses: {str(e)}"}

@mcp.resource("expense:///categories", mime_type="application/json")  # Changed: expense:// → expense:///
def categories():
    try:
        # Provide default categories if file doesn't exist
        default_categories = {
            "categories": [
                "Food & Dining",
                "Transportation",
                "Shopping",
                "Entertainment",
                "Bills & Utilities",
                "Healthcare",
                "Travel",
                "Education",
                "Business",
                "Other"
            ]
        }
        
        try:
            with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return json.dumps(default_categories, indent=2)
    except Exception as e:
        return f'{{"error": "Could not load categories: {str(e)}"}}'

# Start the server
if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
    # mcp.run()