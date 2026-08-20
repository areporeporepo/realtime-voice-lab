"""Form D query tools exposed to the realtime voice model.

Every function returns a small dict that is cheap to speak aloud. Results are
capped hard, because a voice model reading 50 rows is unusable.
"""
import os, sqlite3

DB = os.path.join(os.path.dirname(__file__), "formd.db")


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _money(v):
    if not v:
        return "undisclosed"
    if v >= 1e9:
        return f"${v/1e9:.1f} billion"
    if v >= 1e6:
        return f"${v/1e6:.1f} million"
    return f"${v:,.0f}"


def search_filings(state=None, industry=None, min_amount=None,
                   since=None, exclude_funds=True, limit=5):
    """Find Form D filings matching filters. Newest first."""
    q = ["SELECT issuer, issuer_city, issuer_state, industry, filed_date, "
         "amount_sold, n_investors FROM filings WHERE 1=1"]
    a = []
    if state:
        q.append("AND issuer_state = ?"); a.append(state.upper())
    if industry:
        q.append("AND industry LIKE ?"); a.append(f"%{industry}%")
    if min_amount:
        q.append("AND amount_sold >= ?"); a.append(float(min_amount))
    if since:
        q.append("AND filed_date >= ?"); a.append(since)
    if exclude_funds:
        q.append("AND is_pooled_fund = 0")
    q.append("ORDER BY filed_date DESC LIMIT ?"); a.append(min(int(limit), 10))
    with _conn() as c:
        rows = c.execute(" ".join(q), a).fetchall()
    return {"count": len(rows), "results": [
        {"issuer": r["issuer"], "location": f'{r["issuer_city"]}, {r["issuer_state"]}',
         "industry": r["industry"], "filed": r["filed_date"],
         "raised": _money(r["amount_sold"]), "investors": r["n_investors"]}
        for r in rows]}


def top_raises(state=None, industry=None, since=None,
               exclude_funds=True, limit=5):
    """Largest raises by amount sold, matching filters."""
    q = ["SELECT issuer, issuer_state, industry, filed_date, amount_sold "
         "FROM filings WHERE amount_sold > 0"]
    a = []
    if state:
        q.append("AND issuer_state = ?"); a.append(state.upper())
    if industry:
        q.append("AND industry LIKE ?"); a.append(f"%{industry}%")
    if since:
        q.append("AND filed_date >= ?"); a.append(since)
    if exclude_funds:
        q.append("AND is_pooled_fund = 0")
    q.append("ORDER BY amount_sold DESC LIMIT ?"); a.append(min(int(limit), 10))
    with _conn() as c:
        rows = c.execute(" ".join(q), a).fetchall()
    return {"results": [
        {"issuer": r["issuer"], "state": r["issuer_state"],
         "industry": r["industry"], "filed": r["filed_date"],
         "raised": _money(r["amount_sold"])} for r in rows]}


def find_person(name, limit=5):
    """Look up a person by name and return the offerings they are listed on."""
    with _conn() as c:
        rows = c.execute(
            "SELECT p.name, p.roles, p.city, p.state, f.issuer, f.filed_date, "
            "f.amount_sold, f.industry FROM persons p JOIN filings f "
            "ON f.accession = p.accession WHERE p.name LIKE ? "
            "ORDER BY f.filed_date DESC LIMIT ?",
            (f"%{name}%", min(int(limit), 10))).fetchall()
    if not rows:
        return {"found": False, "message": f"No person matching {name}."}
    return {"found": True, "name": rows[0]["name"], "results": [
        {"issuer": r["issuer"], "role": r["roles"], "industry": r["industry"],
         "filed": r["filed_date"], "raised": _money(r["amount_sold"])}
        for r in rows]}


def market_stats(state=None, industry=None, since=None, exclude_funds=True):
    """Aggregate counts and totals. Use for 'how many' / 'how much' questions."""
    q = ["SELECT COUNT(*) n, SUM(amount_sold) total, AVG(amount_sold) avg "
         "FROM filings WHERE 1=1"]
    a = []
    if state:
        q.append("AND issuer_state = ?"); a.append(state.upper())
    if industry:
        q.append("AND industry LIKE ?"); a.append(f"%{industry}%")
    if since:
        q.append("AND filed_date >= ?"); a.append(since)
    if exclude_funds:
        q.append("AND is_pooled_fund = 0")
    with _conn() as c:
        r = c.execute(" ".join(q), a).fetchone()
    return {"filings": r["n"], "total_raised": _money(r["total"]),
            "average_raise": _money(r["avg"])}


TOOLS = {"search_filings": search_filings, "top_raises": top_raises,
         "find_person": find_person, "market_stats": market_stats}

# OpenAI Realtime tool schemas. Same shape works for Gemini and Qwen.
SCHEMAS = [
    {"type": "function", "name": "search_filings",
     "description": "Search 2026 SEC Form D private-offering filings by state, "
                    "industry, minimum amount raised, or filing date.",
     "parameters": {"type": "object", "properties": {
         "state": {"type": "string", "description": "Two-letter state, e.g. CA"},
         "industry": {"type": "string"},
         "min_amount": {"type": "number"},
         "since": {"type": "string", "description": "ISO date YYYY-MM-DD"},
         "exclude_funds": {"type": "boolean",
                           "description": "Exclude pooled investment funds. Default true."},
         "limit": {"type": "integer"}}, "required": []}},
    {"type": "function", "name": "top_raises",
     "description": "Largest private raises by dollar amount, optionally "
                    "filtered by state, industry, or date.",
     "parameters": {"type": "object", "properties": {
         "state": {"type": "string"}, "industry": {"type": "string"},
         "since": {"type": "string"}, "exclude_funds": {"type": "boolean"},
         "limit": {"type": "integer"}}, "required": []}},
    {"type": "function", "name": "find_person",
     "description": "Look up an executive, director, or promoter by name and "
                    "return the offerings they are named on.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "limit": {"type": "integer"}},
         "required": ["name"]}},
    {"type": "function", "name": "market_stats",
     "description": "Aggregate statistics: how many filings and how much total "
                    "money raised, optionally filtered.",
     "parameters": {"type": "object", "properties": {
         "state": {"type": "string"}, "industry": {"type": "string"},
         "since": {"type": "string"}, "exclude_funds": {"type": "boolean"}},
         "required": []}},
]
