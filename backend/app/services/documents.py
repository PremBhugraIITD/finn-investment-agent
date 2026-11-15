"""PDF download, local page-indexed text extraction, chunking and SQLite FTS5 index."""
from __future__ import annotations

import re
import sqlite3
import tempfile
import threading
from typing import Any

import requests
from pypdf import PdfReader

from .. import cache, config

CHUNK_CHARS = 4800   # roughly 1,200 tokens
OVERLAP_CHARS = 600
_db_lock = threading.Lock()


def doc_id_for(url: str) -> str:
    return cache.sha(url, n=16)


def _clean(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # broken hyphenation
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_pdf(url: str):
    """Stream the download straight to a temp file with a hard size cap. The PDF never sits fully in RAM,
    which keeps a large annual report inside a 512 MB instance. Returns (file object at offset 0, size, head)."""
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/pdf,*/*"}
    if "bseindia.com" in url:
        headers["Referer"] = "https://www.bseindia.com/"
    elif "nseindia" in url:
        headers["Referer"] = "https://www.nseindia.com/"
    f = tempfile.TemporaryFile()
    size, head = 0, b""
    try:
        with requests.get(url, headers=headers, timeout=(10, 45), stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if not head:
                    head = chunk[:4096]
                size += len(chunk)
                if size > config.MAX_PDF_BYTES:
                    raise ValueError(f"PDF larger than {config.MAX_PDF_BYTES // 1_000_000} MB")
                f.write(chunk)
        if head[:4] != b"%PDF":
            raise ValueError("Not a PDF response")
        f.seek(0)
        return f, size, head
    except Exception:
        f.close()
        raise


def extract_pages(fileobj) -> tuple[list[dict], int]:
    """Extract only the first config.MAX_PDF_PAGES pages (read lazily from the file). Returns (pages, total_pages)."""
    reader = PdfReader(fileobj)
    total = len(reader.pages)
    pages = []
    for i in range(min(total, config.MAX_PDF_PAGES)):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception:
            t = ""
        pages.append({"n": i + 1, "text": _clean(t)})
    # drop repeated headers/footers (same line on >40% of pages)
    if len(pages) > 8:
        from collections import Counter
        c = Counter()
        for pg in pages:
            for ln in {l.strip() for l in pg["text"].split("\n") if 3 < len(l.strip()) < 90}:
                c[ln] += 1
        noisy = {ln for ln, k in c.items() if k > 0.4 * len(pages)}
        if noisy:
            for pg in pages:
                pg["text"] = "\n".join(l for l in pg["text"].split("\n") if l.strip() not in noisy)
    return pages, total


def get_document(url: str, symbol: str, kind: str, title: str = "", period: str = "",
                 allow_fetch: bool = True) -> dict[str, Any] | None:
    """kind: annual_report | transcript | results | presentation. Cached (gz) with seed fallback."""
    did = doc_id_for(url)
    name = f"doc_{did}.json.gz"
    rec, _, _ = cache.get_layered("docs", name)
    if rec:
        return rec
    if not allow_fetch:
        return None
    try:
        fobj, size, head = fetch_pdf(url)
        try:
            pages, total_pages = extract_pages(fobj)
        finally:
            fobj.close()
    except Exception as e:  # network error, bad PDF, etc.
        return {"doc_id": did, "url": url, "symbol": symbol, "kind": kind, "title": title, "period": period,
                "pages": [], "n_pages": 0, "status": "fetch_failed", "error": str(e)[:200]}
    chars = sum(len(p["text"]) for p in pages)
    status = "ok" if pages and chars / max(len(pages), 1) > 200 else "extraction_incomplete"
    rec = {
        "doc_id": did, "url": url, "symbol": symbol, "kind": kind, "title": title, "period": period,
        "checksum": cache.sha(size, head, n=16), "fetched_at": cache.iso_now(),
        "n_pages": len(pages), "total_pages": total_pages, "truncated": total_pages > len(pages),
        "status": status, "pages": pages,
    }
    cache.put_cache("docs", name, rec)
    return rec


# ---------------- chunking ----------------
def chunk_document(doc: dict) -> list[dict]:
    chunks: list[dict] = []
    buf, start_page, last_page = "", None, None
    for pg in doc.get("pages", []):
        text = pg["text"]
        if not text:
            continue
        if start_page is None:
            start_page = pg["n"]
        buf += f"\n[p.{pg['n']}]\n" + text
        last_page = pg["n"]
        if len(buf) >= CHUNK_CHARS:
            chunks.append(_mk(doc, len(chunks), buf, start_page, last_page))
            buf = buf[-OVERLAP_CHARS:]
            start_page = pg["n"]
    if buf.strip() and start_page is not None:
        chunks.append(_mk(doc, len(chunks), buf, start_page, last_page))
    return chunks


def _mk(doc: dict, i: int, text: str, p0: int, p1: int) -> dict:
    heading = ""
    for ln in text.split("\n"):
        ln = ln.strip()
        if 6 < len(ln) < 80 and ln.upper() == ln and not ln.startswith("[p."):
            heading = ln
            break
    return {"chunk_id": f"{doc['doc_id']}:{i}", "doc_id": doc["doc_id"], "symbol": doc["symbol"],
            "kind": doc["kind"], "page_start": p0, "page_end": p1, "heading": heading, "text": text.strip()}


# ---------------- FTS5 index ----------------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(config.CACHE / "index.sqlite"), timeout=30)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
              "chunk_id UNINDEXED, doc_id UNINDEXED, symbol UNINDEXED, kind UNINDEXED, "
              "page_start UNINDEXED, page_end UNINDEXED, heading, text, tokenize='porter unicode61')")
    c.execute("CREATE TABLE IF NOT EXISTS indexed(doc_id TEXT PRIMARY KEY)")
    return c


def index_document(doc: dict) -> int:
    if not doc or not doc.get("pages"):
        return 0
    with _db_lock:
        c = _conn()
        try:
            if c.execute("SELECT 1 FROM indexed WHERE doc_id=?", (doc["doc_id"],)).fetchone():
                return 0
            ch = chunk_document(doc)
            c.executemany(
                "INSERT INTO chunks(chunk_id,doc_id,symbol,kind,page_start,page_end,heading,text) VALUES(?,?,?,?,?,?,?,?)",
                [(x["chunk_id"], x["doc_id"], x["symbol"], x["kind"], x["page_start"], x["page_end"],
                  x["heading"], x["text"]) for x in ch])
            c.execute("INSERT OR REPLACE INTO indexed(doc_id) VALUES(?)", (doc["doc_id"],))
            c.commit()
            return len(ch)
        finally:
            c.close()


def _fts_query(q: str) -> str:
    terms = [t for t in re.findall(r"[A-Za-z0-9]+", q.lower()) if len(t) > 2]
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(terms)) or '"revenue"'


def search(symbol: str, query: str, kinds: list[str] | None = None, doc_ids: list[str] | None = None,
           limit: int = 4) -> list[dict]:
    sql = ("SELECT chunk_id,doc_id,kind,page_start,page_end,heading,text,bm25(chunks) AS s FROM chunks "
           "WHERE chunks MATCH ? AND symbol=?")
    args: list[Any] = [_fts_query(query), symbol]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        args += doc_ids
    sql += " ORDER BY s LIMIT ?"
    args.append(limit)
    with _db_lock:
        c = _conn()
        try:
            rows = c.execute(sql, args).fetchall()
        finally:
            c.close()
    keys = ["chunk_id", "doc_id", "kind", "page_start", "page_end", "heading", "text", "score"]
    return [dict(zip(keys, r)) for r in rows]


def get_chunk(chunk_id: str) -> dict | None:
    with _db_lock:
        c = _conn()
        try:
            r = c.execute("SELECT chunk_id,doc_id,kind,page_start,page_end,heading,text FROM chunks WHERE chunk_id=?",
                          (chunk_id,)).fetchone()
        finally:
            c.close()
    if not r:
        return None
    return dict(zip(["chunk_id", "doc_id", "kind", "page_start", "page_end", "heading", "text"], r))
