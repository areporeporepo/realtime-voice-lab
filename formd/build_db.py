"""Normalize vcwatch's Form D JSONL into a queryable SQLite DB."""
import json, sqlite3, sys, os

SRC = os.path.expanduser("~/vcwatch/data/formd.jsonl")
DST = os.path.join(os.path.dirname(__file__), "formd.db")

MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def iso(d):
    """30-JUN-2026 -> 2026-06-30"""
    try:
        day, mon, yr = d.split("-")
        return f"{yr}-{MONTHS[mon.upper()]:02d}-{int(day):02d}"
    except Exception:
        return None


def num(v):
    return v if isinstance(v, (int, float)) else None


def main():
    if os.path.exists(DST):
        os.remove(DST)
    db = sqlite3.connect(DST)
    db.executescript("""
    CREATE TABLE filings (
      accession TEXT PRIMARY KEY, cik INTEGER, filed_date TEXT,
      issuer TEXT, entity_type TEXT, jurisdiction TEXT, year_inc TEXT,
      inc_within_5y INTEGER, issuer_city TEXT, issuer_state TEXT,
      industry TEXT, investment_fund_type TEXT, is_pooled_fund INTEGER,
      revenue_range TEXT, is_amendment INTEGER, offering_amount TEXT,
      amount_sold REAL, min_investment REAL, n_investors INTEGER,
      security_types TEXT, source_url TEXT);
    CREATE TABLE persons (
      accession TEXT, name TEXT, first TEXT, last TEXT,
      city TEXT, state TEXT, roles TEXT);
    """)

    fil, per = [], []
    with open(SRC) as f:
        for line in f:
            d = json.loads(line)
            acc = d.get("accession")
            fil.append((
                acc, d.get("cik"), iso(d.get("filed_date", "")),
                d.get("issuer"), d.get("entity_type"), d.get("jurisdiction"),
                d.get("year_inc"), int(bool(d.get("inc_within_5y"))),
                d.get("issuer_city"), d.get("issuer_state"), d.get("industry"),
                d.get("investment_fund_type"), int(bool(d.get("is_pooled_fund"))),
                d.get("revenue_range"), int(bool(d.get("is_amendment"))),
                str(d.get("offering_amount")), num(d.get("amount_sold")),
                num(d.get("min_investment")), num(d.get("n_investors")),
                ",".join(d.get("security_types") or []), d.get("source_url")))
            for p in d.get("persons") or []:
                per.append((acc, p.get("name"), p.get("first"), p.get("last"),
                            p.get("city"), p.get("state"),
                            ",".join(p.get("roles") or [])))

    db.executemany("INSERT OR REPLACE INTO filings VALUES (%s)"
                   % ",".join("?" * 21), fil)
    db.executemany("INSERT INTO persons VALUES (?,?,?,?,?,?,?)", per)
    db.executescript("""
    CREATE INDEX ix_f_state    ON filings(issuer_state);
    CREATE INDEX ix_f_industry ON filings(industry);
    CREATE INDEX ix_f_date     ON filings(filed_date);
    CREATE INDEX ix_f_amount   ON filings(amount_sold);
    CREATE INDEX ix_p_last     ON persons(last);
    CREATE INDEX ix_p_name     ON persons(name);
    CREATE INDEX ix_p_acc      ON persons(accession);
    """)
    db.commit()
    print(f"filings={len(fil)}  persons={len(per)}  -> {DST}")
    for q in ("SELECT MIN(filed_date), MAX(filed_date) FROM filings",
              "SELECT COUNT(DISTINCT name) FROM persons"):
        print(q.split("FROM")[0].strip(), "=", db.execute(q).fetchone())


if __name__ == "__main__":
    main()
