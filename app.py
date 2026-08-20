"""
Eureka Check — Research Paper Novelty Checker
Backend with fast vector similarity, domain calibration, and online metadata/date enrichment with SQLite caching.
"""

import os
import re
import json
import sqlite3
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")

# ---------------------------------------------------------------------------
# Load data once at startup
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "metadata_cache.db")

print("[startup] Loading papers.parquet …")
PAPERS = pd.read_parquet(os.path.join(DATA_DIR, "papers.parquet"))
print(f"[startup] Loaded {len(PAPERS):,} papers")

print("[startup] Memory-mapping embeddings …")
VECTORS = np.load(
    os.path.join(DATA_DIR, "embeddings-001.npy"), mmap_mode="r"
)
print(f"[startup] Embeddings shape: {VECTORS.shape}, dtype: {VECTORS.dtype}")

assert len(PAPERS) == len(VECTORS), "papers and embeddings must have the same number of rows"

# Build a paper_id → row index lookup for fast search-by-id
PAPER_ID_TO_IDX = pd.Series(PAPERS.index, index=PAPERS["paper_id"]).to_dict()

# Pre-compute float32 chunks for fast dot products (avoids repeated conversion)
NUM_CHUNKS = 20
CHUNK_BOUNDARIES = np.array_split(np.arange(len(VECTORS)), NUM_CHUNKS)

# ---------------------------------------------------------------------------
# Metadata Cache DB (SQLite)
# ---------------------------------------------------------------------------
def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
def _init_db():
    """Initialize SQLite database for caching publication metadata and dates."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_metadata (
            paper_id TEXT PRIMARY KEY,
            title TEXT,
            year INTEGER,
            pub_date TEXT,
            arxiv_id TEXT,
            url TEXT,
            venue TEXT
        )
    """)
    # Ensure pub_date column exists if table was created previously
    try:
        c.execute("ALTER TABLE paper_metadata ADD COLUMN pub_date TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

_init_db()


def _get_cached_metadata(paper_ids: list[str]) -> dict:
    """Retrieve metadata from SQLite cache for requested paper_ids."""
    if not paper_ids:
        return {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    placeholders = ",".join("?" for _ in paper_ids)
    c.execute(
        f"SELECT paper_id, year, pub_date, arxiv_id, url, venue FROM paper_metadata WHERE paper_id IN ({placeholders})",
        [str(pid) for pid in paper_ids],
    )
    rows = c.fetchall()
    conn.close()
    return {
        r[0]: {"year": r[1], "pub_date": r[2] or (str(r[1]) if r[1] else None), "arxiv_id": r[3], "url": r[4], "venue": r[5]}
        for r in rows
    }


def _save_cached_metadata_batch(records: list[tuple]):
    """Save resolved metadata into SQLite cache."""
    if not records:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executemany(
        """
        INSERT OR REPLACE INTO paper_metadata (paper_id, title, year, pub_date, arxiv_id, url, venue)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    conn.commit()
    conn.close()


def _scrape_single_paper_metadata(item: tuple[str, str]) -> tuple[str, dict]:
    """
    Scrape publication year, exact date, and URL for a paper via arXiv API with Crossref fallback.
    Returns (paper_id, {year, pub_date, url, arxiv_id, venue}).
    """
    pid, title = item
    clean = re.sub(r"[^\w\s]", " ", title).strip()
    clean = re.sub(r"\s+", " ", clean)

    # 1. Try arXiv API with title search
    try:
        q_enc = urllib.parse.quote(f'ti:"{clean[:65]}"')
        url = f"https://export.arxiv.org/api/query?search_query={q_enc}&max_results=2"
        req = urllib.request.Request(url, headers={"User-Agent": "EurekaCheck/1.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            content = resp.read().decode("utf-8")
            entries = content.split("<entry>")
            for e in entries[1:]:
                p_m = re.search(r"<published>(\d{4}-\d{2}-\d{2})", e)
                id_m = re.search(r"<id>(http://arxiv.org/abs/([^<]+))</id>", e)
                if p_m:
                    pub_date = p_m.group(1)
                    year = int(pub_date[:4])
                    arxiv_url = id_m.group(1) if id_m else f"https://arxiv.org/abs/{id_m.group(2)}" if id_m else ""
                    arxiv_id = id_m.group(2) if id_m else ""
                    return pid, {"year": year, "pub_date": pub_date, "arxiv_id": arxiv_id, "url": arxiv_url, "venue": "arXiv"}
    except Exception:
        pass

    # 2. Fallback to Crossref
    try:
        q = urllib.parse.quote(clean[:80])
        url = f"https://api.crossref.org/works?query.bibliographic={q}&rows=1"
        req = urllib.request.Request(url, headers={"User-Agent": "EurekaCheck/1.0 (mailto:ai4research@hackathon.org)"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode())
            items = data.get("message", {}).get("items", [])
            if items:
                item_data = items[0]
                d_parts = item_data.get("published-print", item_data.get("published-online", item_data.get("created", {}))).get("date-parts", [[None]])
                parts = d_parts[0]
                year = parts[0] if parts and len(parts) > 0 else None
                month = parts[1] if parts and len(parts) > 1 else None
                day = parts[2] if parts and len(parts) > 2 else None
                
                pub_date = None
                if year and month and day:
                    pub_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                elif year and month:
                    pub_date = f"{int(year):04d}-{int(month):02d}"
                elif year:
                    pub_date = f"{int(year):04d}"

                doi_url = item_data.get("URL", "")
                venue = item_data.get("container-title", [""])[0] if item_data.get("container-title") else "Publication"
                if year and 1950 <= int(year) <= 2026:
                    return pid, {"year": int(year), "pub_date": pub_date, "arxiv_id": "", "url": doi_url, "venue": venue}
    except Exception:
        pass

    # 3. Year regex fallback from title if present
    yr_match = re.search(r"\b(19\d\d|20[0-2]\d)\b", title)
    if yr_match:
        yr = int(yr_match.group(1))
        return pid, {"year": yr, "pub_date": str(yr), "arxiv_id": "", "url": "", "venue": ""}

    return pid, {"year": None, "pub_date": None, "arxiv_id": "", "url": "", "venue": ""}


def _resolve_papers_metadata(papers_list: list[dict]) -> dict[str, dict]:
    """
    Resolve publication metadata (year, URL, venue) for a list of papers.
    Uses SQLite cache first, then scrapes missing items in parallel.
    """
    pids = [p["id"] for p in papers_list]
    cached = _get_cached_metadata(pids)

    missing = [p for p in papers_list if p["id"] not in cached or (cached[p["id"]]["year"] is None and cached[p["id"]].get("pub_date") is None)]

    if missing:
        to_scrape = [(p["id"], p["title"]) for p in missing]
        records_to_save = []
        with ThreadPoolExecutor(max_workers=min(8, len(to_scrape))) as executor:
            scraped = list(executor.map(_scrape_single_paper_metadata, to_scrape))

        for pid, meta in scraped:
            cached[pid] = meta
            # Find title
            title = next((p["title"] for p in missing if p["id"] == pid), "")
            if meta["year"] is not None:
                records_to_save.append((pid, title, meta["year"], meta.get("pub_date"), meta["arxiv_id"], meta["url"], meta["venue"]))

        if records_to_save:
            _save_cached_metadata_batch(records_to_save)

    return cached


# ---------------------------------------------------------------------------
# Cosine Similarity Search
# ---------------------------------------------------------------------------
def _cosine_topk(query_vec: np.ndarray, k: int = 25, exclude_idx: int = -1) -> list[tuple[int, float]]:
    """
    Find the top-k most similar papers to `query_vec` using dot product
    (vectors are L2-normalised, so dot product = cosine similarity).
    Returns list of (row_index, similarity_score).
    """
    query = np.asarray(query_vec, dtype=np.float32)

    scores = np.concatenate([
        np.asarray(VECTORS[chunk], dtype=np.float32) @ query
        for chunk in CHUNK_BOUNDARIES
    ])

    if exclude_idx >= 0:
        scores[exclude_idx] = -np.inf

    top_indices = np.argpartition(scores, -k)[-k:]
    top_indices = top_indices[np.argsort(-scores[top_indices])]

    return [(int(idx), float(scores[idx])) for idx in top_indices]


def _paper_row_to_dict(idx: int, similarity: float = 0.0) -> dict:
    """Convert a DataFrame row to a JSON-friendly dict."""
    row = PAPERS.iloc[idx]
    return {
        "id": str(row["paper_id"]),
        "title": str(row["title"]) if pd.notna(row["title"]) else "Untitled",
        "authors": str(row["authors"]) if pd.notna(row["authors"]) else "Unknown",
        "arxiv_category": str(row["arxiv_category"]) if pd.notna(row["arxiv_category"]) else "",
        "similarity": round(similarity, 4),
    }


# ---------------------------------------------------------------------------
# Field Calibration & Cutoff-Aware Novelty Scoring
# ---------------------------------------------------------------------------
# s_novel = similarity at or below which a paper in this domain is considered 100% novel (score = 1.0)
# S_DUP = similarity at or above which two papers are considered near-duplicates (score = 0.0)
S_DUP = 0.975

FIELD_CALIBRATION = {
    # Computer Science & AI
    "cs.CL": {"name": "Computation & Language", "s_novel": 0.65},
    "cs.CV": {"name": "Computer Vision", "s_novel": 0.65},
    "cs.LG": {"name": "Machine Learning", "s_novel": 0.66},
    "cs.AI": {"name": "Artificial Intelligence", "s_novel": 0.64},
    "cs.RO": {"name": "Robotics", "s_novel": 0.62},
    "cs.NE": {"name": "Neural & Evolutionary", "s_novel": 0.60},
    "cs.CR": {"name": "Cryptography & Security", "s_novel": 0.58},
    "cs.IR": {"name": "Information Retrieval", "s_novel": 0.62},
    "cs.DC": {"name": "Distributed Computing", "s_novel": 0.55},
    "cs.SE": {"name": "Software Engineering", "s_novel": 0.56},
    "cs.DB": {"name": "Databases", "s_novel": 0.55},
    "cs.HC": {"name": "Human-Computer Interaction", "s_novel": 0.58},
    "cs":    {"name": "Computer Science (General)", "s_novel": 0.60},

    # Statistics & Data Science
    "stat.ML": {"name": "Statistical Machine Learning", "s_novel": 0.64},
    "stat.ME": {"name": "Methodology", "s_novel": 0.54},
    "stat.TH": {"name": "Statistical Theory", "s_novel": 0.50},
    "stat":    {"name": "Statistics (General)", "s_novel": 0.55},

    # Mathematics
    "math.OC": {"name": "Optimization & Control", "s_novel": 0.55},
    "math.PR": {"name": "Probability Theory", "s_novel": 0.46},
    "math.ST": {"name": "Mathematical Statistics", "s_novel": 0.50},
    "math.NA": {"name": "Numerical Analysis", "s_novel": 0.48},
    "math.CO": {"name": "Combinatorics", "s_novel": 0.44},
    "math.FA": {"name": "Functional Analysis", "s_novel": 0.42},
    "math.AP": {"name": "Analysis of PDEs", "s_novel": 0.42},
    "math":    {"name": "Mathematics (General)", "s_novel": 0.46},

    # Physics
    "physics.soc-ph": {"name": "Social Physics", "s_novel": 0.52},
    "quant-ph":       {"name": "Quantum Physics", "s_novel": 0.50},
    "cond-mat":       {"name": "Condensed Matter", "s_novel": 0.48},
    "hep-th":         {"name": "High Energy Physics - Theory", "s_novel": 0.44},
    "hep-ph":         {"name": "High Energy Physics - Phenom", "s_novel": 0.46},
    "astro-ph":       {"name": "Astrophysics", "s_novel": 0.50},
    "gr-qc":          {"name": "General Relativity & Quantum", "s_novel": 0.44},
    "physics":         {"name": "Physics (General)", "s_novel": 0.48},

    # Quantitative Biology & Finance
    "q-bio": {"name": "Quantitative Biology", "s_novel": 0.52},
    "q-fin": {"name": "Quantitative Finance", "s_novel": 0.52},
    "eess":  {"name": "Electrical & Systems Engineering", "s_novel": 0.54},
    "econ":  {"name": "Economics", "s_novel": 0.50},

    "default": {"name": "General Domain", "s_novel": 0.55},
}


def _get_field_info(category: str | None) -> tuple[float, str]:
    """Retrieve calibrated domain threshold and friendly name for arXiv category."""
    if not category or pd.isna(category):
        return FIELD_CALIBRATION["default"]["s_novel"], "General Domain"

    cat_str = str(category).strip()

    if cat_str in FIELD_CALIBRATION:
        return FIELD_CALIBRATION[cat_str]["s_novel"], f"{FIELD_CALIBRATION[cat_str]['name']} ({cat_str})"

    prefix = cat_str.split(".")[0] if "." in cat_str else cat_str.split("-")[0]
    if prefix in FIELD_CALIBRATION:
        return FIELD_CALIBRATION[prefix]["s_novel"], f"{FIELD_CALIBRATION[prefix]['name']} ({cat_str})"

    return FIELD_CALIBRATION["default"]["s_novel"], f"Field: {cat_str}"


# ---------------------------------------------------------------------------
# Preprint & Author Revision Detection
# ---------------------------------------------------------------------------
def _normalize_name(name: str) -> str:
    parts = re.sub(r"[^\w\s]", "", name.lower()).split()
    return parts[-1] if parts else ""


def _parse_authors(author_str: str | None) -> set[str]:
    if not author_str or pd.isna(author_str) or author_str == "Unknown" or author_str == "<NA>":
        return set()
    names = re.split(r"[,;]|\band\b", str(author_str))
    return {_normalize_name(n) for n in names if _normalize_name(n)}


def _is_self_work(title_a: str, authors_a: str, title_b: str, authors_b: str, sim: float) -> bool:
    """
    Detect if paper B is an author preprint, revision, or earlier draft of paper A.
    """
    set_a = _parse_authors(authors_a)
    set_b = _parse_authors(authors_b)

    if set_a and set_b:
        overlap = len(set_a & set_b)
        union = len(set_a | set_b)
        jaccard = overlap / union if union else 0
        if jaccard >= 0.33 and sim > 0.82:
            return True
        first_a = list(set_a)[0]
        first_b = list(set_b)[0]
        if first_a == first_b and sim > 0.86:
            return True

    # Title revision / subtitle overlap check
    words_a = set(re.sub(r"[^\w\s]", "", title_a.lower()).split())
    words_b = set(re.sub(r"[^\w\s]", "", title_b.lower()).split())
    if words_a and words_b:
        t_overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
        if t_overlap >= 0.65 and sim > 0.88:
            return True

    # Extremely high embedding similarity (near identical text)
    if sim >= 0.965:
        return True

    return False


def _compute_novelty(
    similarities: list[float],
    category: str | None = None,
    year_cutoff: int | None = None,
    prior_art_count: int = 0,
    total_count: int = 0,
    self_works_count: int = 0,
) -> dict:
    """
    Compute a field-calibrated and cutoff-aware novelty score (0.0 to 1.0 / 0-100%).
    Excludes author self-works/preprints from penalizing novelty.
    """
    s_novel, field_name = _get_field_info(category)

    if not similarities:
        if year_cutoff and prior_art_count == 0 and total_count > 0:
            return {
                "score": 95,
                "score_norm": 0.95,
                "level": "high",
                "field": field_name,
                "top_similarity": 0.0,
                "year_cutoff": year_cutoff,
                "prior_art_count": 0,
                "self_works_count": self_works_count,
                "tldr": f"Exceptional historical novelty (0.95 / 1.00) relative to literature published before {year_cutoff}. None of the {total_count} related papers existed at this cutoff point, indicating this work was groundbreaking prior art.",
            }
        return {
            "score": 50,
            "score_norm": 0.50,
            "level": "medium",
            "field": field_name,
            "top_similarity": 0.5,
            "year_cutoff": year_cutoff,
            "prior_art_count": prior_art_count,
            "self_works_count": self_works_count,
            "tldr": "Not enough data to assess novelty.",
        }

    top_sim = float(np.mean(similarities[:min(3, len(similarities))]))

    # Continuous scaling: S_DUP (0.975) -> 0.0, s_novel -> 1.0
    raw_norm = (S_DUP - top_sim) / (S_DUP - s_novel)
    score_norm = float(np.clip(raw_norm, 0.0, 1.0))
    score = int(round(score_norm * 100))

    cutoff_str = f" relative to literature published before {year_cutoff}" if year_cutoff else ""
    self_note = f" (excluded {self_works_count} author preprint/revision from prior art)" if self_works_count > 0 else ""

    if score >= 65:
        level = "high"
        tldr = (
            f"High novelty ({score_norm:.2f} / 1.00){cutoff_str} in {field_name}{self_note}. "
            f"Its nearest prior-art similarity of {top_sim:.2f} is well below typical subfield clusters, "
            f"suggesting this contribution introduces distinct techniques or explores an uncrowded problem domain."
        )
    elif score >= 35:
        level = "medium"
        tldr = (
            f"Moderate novelty ({score_norm:.2f} / 1.00){cutoff_str} in {field_name}{self_note}. "
            f"The nearest prior art has an average similarity of {top_sim:.2f}. While there is "
            f"clear thematic overlap with existing methods, it demonstrates meaningful divergence "
            f"from pre-existing work."
        )
    else:
        level = "low"
        tldr = (
            f"Low novelty ({score_norm:.2f} / 1.00){cutoff_str} in {field_name}{self_note}. "
            f"The closest prior art has a high average similarity of {top_sim:.2f}, reflecting a "
            f"dense literature cluster with multiple closely related papers covering similar methods or formulations."
        )

    return {
        "score": score,
        "score_norm": round(score_norm, 3),
        "level": level,
        "field": field_name,
        "top_similarity": round(top_sim, 3),
        "year_cutoff": year_cutoff,
        "prior_art_count": prior_art_count,
        "self_works_count": self_works_count,
        "tldr": tldr,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/search")
def search_papers():
    """
    Search papers by title keyword (case-insensitive substring match).
    Returns up to 10 matching results for the autocomplete dropdown.
    """
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify([])

    mask = PAPERS["title"].str.lower().str.contains(q, na=False)
    matches = PAPERS[mask].head(10)

    results = []
    pids = [str(r["paper_id"]) for _, r in matches.iterrows()]
    cached = _get_cached_metadata(pids)

    for idx, row in matches.iterrows():
        pid = str(row["paper_id"])
        meta = cached.get(pid, {})
        results.append({
            "id": pid,
            "title": str(row["title"]) if pd.notna(row["title"]) else "Untitled",
            "authors": str(row["authors"]) if pd.notna(row["authors"]) else "Unknown",
            "arxiv_category": str(row["arxiv_category"]) if pd.notna(row["arxiv_category"]) else "",
            "year": meta.get("year"),
            "pub_date": meta.get("pub_date"),
            "row_idx": int(idx),
        })
    return jsonify(results)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Accepts a paper_id (from search) or PDF upload + year_cutoff.
    Enriches papers with publication years & dates, detects author preprints/revisions,
    applies strict chronological cutoff, and returns field-calibrated novelty score & similarity graph.
    """
    paper_id_str = request.form.get("paper_id")
    row_idx_str = request.form.get("row_idx")
    year_cutoff_str = request.form.get("year_cutoff", "auto")
    k = int(request.form.get("k", 18))

    query_idx = None
    if row_idx_str is not None:
        query_idx = int(row_idx_str)
    elif paper_id_str is not None:
        pid = int(paper_id_str)
        if pid in PAPER_ID_TO_IDX:
            query_idx = PAPER_ID_TO_IDX[pid]

    if query_idx is None:
        return jsonify({"error": "No valid paper selected. PDF embedding is not yet supported."}), 400

    query_vec = VECTORS[query_idx]
    center_meta = _paper_row_to_dict(query_idx, similarity=1.0)

    # Find top candidate similar papers
    top_results = _cosine_topk(query_vec, k=k, exclude_idx=query_idx)
    similar_papers = [_paper_row_to_dict(idx, sim) for idx, sim in top_results]

    # Resolve publication metadata (dates, URLs, venues) for center and candidate papers
    all_to_resolve = [center_meta] + similar_papers
    metadata_map = _resolve_papers_metadata(all_to_resolve)

    # Enrich center paper with resolved metadata
    c_meta = metadata_map.get(center_meta["id"], {})
    center_meta["year"] = c_meta.get("year")
    center_meta["pub_date"] = c_meta.get("pub_date")
    center_meta["url"] = c_meta.get("url") or f"https://scholar.google.com/scholar?q={urllib.parse.quote(center_meta['title'])}"
    center_meta["venue"] = c_meta.get("venue") or center_meta.get("arxiv_category")

    # Determine effective cutoff mode
    is_auto_mode = (year_cutoff_str == "auto" or not year_cutoff_str)
    effective_year_cutoff = None
    if is_auto_mode:
        effective_year_cutoff = center_meta.get("year")
    elif year_cutoff_str != "all":
        try:
            effective_year_cutoff = int(year_cutoff_str)
        except ValueError:
            effective_year_cutoff = center_meta.get("year")

    # Enrich similar papers with resolved metadata, preprint detection, and strict prior_art flags
    self_works_count = 0
    for p in similar_papers:
        meta = metadata_map.get(p["id"], {})
        p["year"] = meta.get("year")
        p["pub_date"] = meta.get("pub_date")
        p["url"] = meta.get("url") or f"https://scholar.google.com/scholar?q={urllib.parse.quote(p['title'])}"
        p["venue"] = meta.get("venue") or p.get("arxiv_category")

        # Detect author preprints / revisions / self-works
        is_self = _is_self_work(
            center_meta["title"], center_meta["authors"],
            p["title"], p["authors"],
            p["similarity"]
        )
        p["is_self_work"] = is_self

        # Classify relation to cutoff / prior art
        if is_self:
            p["is_prior_art"] = False
            p["status"] = "self"
            self_works_count += 1
        elif is_auto_mode:
            # Auto mode: evaluate strictly prior to the paper itself
            p_date = p.get("pub_date")
            c_date = center_meta.get("pub_date")
            p_yr = p.get("year")
            c_yr = center_meta.get("year")

            if p_date and c_date and p_date != c_date:
                # Direct ISO date comparison (e.g. "2017-03-01" < "2017-06-12")
                is_prior = p_date < c_date
            elif p_yr is not None and c_yr is not None:
                # If exact month missing: strict '<' ensures successors from the same year are excluded!
                is_prior = p_yr < c_yr
            else:
                is_prior = False

            p["is_prior_art"] = is_prior
            p["status"] = "prior" if is_prior else "subsequent"
        elif effective_year_cutoff is not None and p["year"] is not None:
            # Manual year cutoff selected (e.g. <= 2018)
            is_prior = p["year"] <= effective_year_cutoff
            p["is_prior_art"] = is_prior
            p["status"] = "prior" if is_prior else "subsequent"
        else:
            p["is_prior_art"] = True
            p["status"] = "prior"

    # Compute novelty score evaluated specifically against external PRIOR ART (excluding self-works and successors)
    if effective_year_cutoff is not None or is_auto_mode:
        competing_prior_art = [p["similarity"] for p in similar_papers if p.get("is_prior_art") and not p.get("is_self_work")]
    else:
        competing_prior_art = [p["similarity"] for p in similar_papers if not p.get("is_self_work")]

    novelty = _compute_novelty(
        competing_prior_art,
        category=center_meta.get("arxiv_category"),
        year_cutoff=effective_year_cutoff,
        prior_art_count=len(competing_prior_art),
        total_count=len(similar_papers),
        self_works_count=self_works_count,
    )
    novelty["cutoff_mode"] = year_cutoff_str
    novelty["paper_year"] = center_meta.get("year")
    novelty["paper_date"] = center_meta.get("pub_date")

    # Build graph nodes
    nodes = [
        {**center_meta, "id": "uploaded", "is_center": True, "status": "center"},
    ]
    for p in similar_papers:
        nodes.append({**p, "is_center": False})

    # Build graph edges
    edges = []
    for p in similar_papers:
        edges.append({
            "source": "uploaded",
            "target": p["id"],
            "weight": p["similarity"],
        })

    # Compute pairwise similarities among similar papers for cluster connections
    sim_indices = [PAPER_ID_TO_IDX[int(p["id"])] for p in similar_papers if int(p["id"]) in PAPER_ID_TO_IDX]
    if len(sim_indices) > 1:
        sub_vecs = np.asarray(VECTORS[sim_indices], dtype=np.float32)
        pw_sims = sub_vecs @ sub_vecs.T

        for i in range(len(sim_indices)):
            for j in range(i + 1, len(sim_indices)):
                w = float(pw_sims[i, j])
                if w >= 0.72:
                    edges.append({
                        "source": similar_papers[i]["id"],
                        "target": similar_papers[j]["id"],
                        "weight": round(w, 4),
                    })

    return jsonify({
        "novelty": novelty,
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001, use_reloader=False)
