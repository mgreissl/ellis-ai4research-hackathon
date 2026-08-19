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
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_metadata (
            paper_id TEXT PRIMARY KEY,
            title TEXT,
            year INTEGER,
            arxiv_id TEXT,
            url TEXT,
            venue TEXT
        )
    """)
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
        f"SELECT paper_id, year, arxiv_id, url, venue FROM paper_metadata WHERE paper_id IN ({placeholders})",
        [str(pid) for pid in paper_ids],
    )
    rows = c.fetchall()
    conn.close()
    return {
        r[0]: {"year": r[1], "arxiv_id": r[2], "url": r[3], "venue": r[4]}
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
        INSERT OR REPLACE INTO paper_metadata (paper_id, title, year, arxiv_id, url, venue)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    conn.commit()
    conn.close()


def _scrape_single_paper_metadata(item: tuple[str, str]) -> tuple[str, dict]:
    """
    Scrape publication year and URL for a paper via arXiv API with Crossref fallback.
    Returns (paper_id, {year, url, arxiv_id, venue}).
    """
    pid, title = item
    clean = re.sub(r"[^\w\s]", " ", title).strip()
    clean = re.sub(r"\s+", " ", clean)

    # 1. Try arXiv API with title search
    try:
        q = urllib.parse.quote(clean[:75])
        url = f'https://export.arxiv.org/api/query?search_query=ti:"{q}"&max_results=2'
        req = urllib.request.Request(url, headers={"User-Agent": "EurekaCheck/1.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            content = resp.read().decode("utf-8")
            entries = content.split("<entry>")
            for e in entries[1:]:
                p_m = re.search(r"<published>(\d{4})", e)
                id_m = re.search(r"<id>(http://arxiv.org/abs/([^<]+))</id>", e)
                if p_m:
                    year = int(p_m.group(1))
                    arxiv_url = id_m.group(1) if id_m else f"https://arxiv.org/abs/{id_m.group(2)}" if id_m else ""
                    arxiv_id = id_m.group(2) if id_m else ""
                    return pid, {"year": year, "arxiv_id": arxiv_id, "url": arxiv_url, "venue": "arXiv"}
    except Exception:
        pass

    # 2. Fallback to Crossref
    try:
        q = urllib.parse.quote(clean[:80])
        url = f"https://api.crossref.org/works?query.bibliographic={q}&rows=1"
        req = urllib.request.Request(url, headers={"User-Agent": "EurekaCheck/1.0 (mailto:ai4research@hackathon.org)"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode())
            items = data.get("message", {}).get("items", [])
            if items:
                item_data = items[0]
                d_parts = item_data.get("published-print", item_data.get("published-online", item_data.get("created", {}))).get("date-parts", [[None]])
                year = d_parts[0][0]
                doi_url = item_data.get("URL", "")
                venue = item_data.get("container-title", [""])[0] if item_data.get("container-title") else "Publication"
                if year and 1950 <= int(year) <= 2026:
                    return pid, {"year": int(year), "arxiv_id": "", "url": doi_url, "venue": venue}
    except Exception:
        pass

    # 3. Year regex fallback from title if present
    yr_match = re.search(r"\b(19\d\d|20[0-2]\d)\b", title)
    if yr_match:
        yr = int(yr_match.group(1))
        return pid, {"year": yr, "arxiv_id": "", "url": "", "venue": ""}

    return pid, {"year": None, "arxiv_id": "", "url": "", "venue": ""}


def _resolve_papers_metadata(papers_list: list[dict]) -> dict[str, dict]:
    """
    Resolve publication metadata (year, URL, venue) for a list of papers.
    Uses SQLite cache first, then scrapes missing items in parallel.
    """
    pids = [p["id"] for p in papers_list]
    cached = _get_cached_metadata(pids)

    missing = [p for p in papers_list if p["id"] not in cached or cached[p["id"]]["year"] is None]

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
                records_to_save.append((pid, title, meta["year"], meta["arxiv_id"], meta["url"], meta["venue"]))

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
FIELD_CALIBRATION = {
    # Computer Science
    "cs.CL": {"bounds": (0.66, 0.93), "name": "Computation & Language (cs.CL)"},
    "cs.CV": {"bounds": (0.65, 0.92), "name": "Computer Vision (cs.CV)"},
    "cs.LG": {"bounds": (0.64, 0.91), "name": "Machine Learning (cs.LG)"},
    "cs.AI": {"bounds": (0.62, 0.90), "name": "Artificial Intelligence (cs.AI)"},
    "cs.RO": {"bounds": (0.56, 0.87), "name": "Robotics (cs.RO)"},
    "cs.CR": {"bounds": (0.54, 0.85), "name": "Cryptography & Security (cs.CR)"},
    "cs.NE": {"bounds": (0.56, 0.87), "name": "Neural & Evolutionary (cs.NE)"},
    "cs.IR": {"bounds": (0.58, 0.88), "name": "Information Retrieval (cs.IR)"},
    "cs": {"bounds": (0.58, 0.89), "name": "Computer Science (General)"},

    # Mathematics
    "math.PR": {"bounds": (0.44, 0.78), "name": "Probability (math.PR)"},
    "math.AP": {"bounds": (0.44, 0.78), "name": "Analysis of PDEs (math.AP)"},
    "math.CO": {"bounds": (0.42, 0.76), "name": "Combinatorics (math.CO)"},
    "math.AG": {"bounds": (0.42, 0.76), "name": "Algebraic Geometry (math.AG)"},
    "math": {"bounds": (0.44, 0.78), "name": "Mathematics (General)"},

    # Physics & Astronomy
    "hep-ph": {"bounds": (0.48, 0.84), "name": "High Energy Physics - Phenomenology (hep-ph)"},
    "hep-th": {"bounds": (0.48, 0.84), "name": "High Energy Physics - Theory (hep-th)"},
    "hep": {"bounds": (0.48, 0.84), "name": "High Energy Physics"},
    "quant-ph": {"bounds": (0.50, 0.84), "name": "Quantum Physics (quant-ph)"},
    "gr-qc": {"bounds": (0.46, 0.80), "name": "General Relativity & Quantum Cosmology (gr-qc)"},
    "cond-mat": {"bounds": (0.48, 0.83), "name": "Condensed Matter"},
    "astro-ph": {"bounds": (0.48, 0.83), "name": "Astrophysics"},
    "physics": {"bounds": (0.46, 0.82), "name": "Physics (General)"},

    # Statistics & Electrical Eng & Bio & Finance
    "stat.ML": {"bounds": (0.62, 0.90), "name": "Statistical Machine Learning (stat.ML)"},
    "stat": {"bounds": (0.50, 0.84), "name": "Statistics (General)"},
    "eess": {"bounds": (0.50, 0.82), "name": "Electrical Eng & Systems Science (eess)"},
    "q-bio": {"bounds": (0.48, 0.82), "name": "Quantitative Biology (q-bio)"},
    "q-fin": {"bounds": (0.48, 0.80), "name": "Quantitative Finance (q-fin)"},
    "default": {"bounds": (0.50, 0.88), "name": "General Literature"},
}


def _get_field_info(category: str | None) -> tuple[tuple[float, float], str]:
    if not category or pd.isna(category) or category == "<NA>" or category == "":
        return FIELD_CALIBRATION["default"]["bounds"], FIELD_CALIBRATION["default"]["name"]

    cat_str = str(category).strip()
    if cat_str in FIELD_CALIBRATION:
        return FIELD_CALIBRATION[cat_str]["bounds"], FIELD_CALIBRATION[cat_str]["name"]

    prefix = cat_str.split(".")[0] if "." in cat_str else cat_str.split("-")[0]
    if prefix in FIELD_CALIBRATION:
        return FIELD_CALIBRATION[prefix]["bounds"], f"{FIELD_CALIBRATION[prefix]['name']} ({cat_str})"

    return FIELD_CALIBRATION["default"]["bounds"], f"Field: {cat_str}"


def _compute_novelty(
    similarities: list[float],
    category: str | None = None,
    year_cutoff: int | None = None,
    prior_art_count: int = 0,
    total_count: int = 0,
) -> dict:
    """
    Compute a field-calibrated and cutoff-aware novelty score (0.0 to 1.0 / 0-100%).
    """
    (s_min, s_max), field_name = _get_field_info(category)

    if not similarities:
        # If no prior art exists prior to cutoff, it is highly novel
        if year_cutoff and prior_art_count == 0 and total_count > 0:
            return {
                "score": 95,
                "score_norm": 0.95,
                "level": "high",
                "field": field_name,
                "top_similarity": 0.0,
                "year_cutoff": year_cutoff,
                "prior_art_count": 0,
                "tldr": f"Exceptional historical novelty (0.95 / 1.00) relative to literature published on or before {year_cutoff}. None of the {total_count} related papers existed at this cutoff point, indicating this work was groundbreaking prior art.",
            }
        return {
            "score": 50,
            "score_norm": 0.50,
            "level": "medium",
            "field": field_name,
            "top_similarity": 0.5,
            "year_cutoff": year_cutoff,
            "prior_art_count": prior_art_count,
            "tldr": "Not enough data to assess novelty.",
        }

    top_sim = float(np.mean(similarities[:min(3, len(similarities))]))

    raw_norm = (s_max - top_sim) / (s_max - s_min)
    score_norm = float(np.clip(raw_norm, 0.0, 1.0))
    score = int(round(score_norm * 100))

    cutoff_str = f" relative to literature up to {year_cutoff}" if year_cutoff else ""

    if score >= 65:
        level = "high"
        tldr = (
            f"High novelty ({score_norm:.2f} / 1.00){cutoff_str} in {field_name}. "
            f"Its nearest prior-art similarity of {top_sim:.2f} is well below the domain's "
            f"crowded threshold ({s_max:.2f}), suggesting this contribution occupied an unpopulated "
            f"region of the literature at this cutoff."
        )
    elif score >= 35:
        level = "medium"
        tldr = (
            f"Moderate novelty ({score_norm:.2f} / 1.00){cutoff_str} within {field_name}. "
            f"The nearest prior art has an average similarity of {top_sim:.2f}. While there is "
            f"thematic overlap with earlier methods, it demonstrates meaningful divergence "
            f"from pre-existing work."
        )
    else:
        level = "low"
        tldr = (
            f"Low novelty ({score_norm:.2f} / 1.00){cutoff_str} in {field_name}. "
            f"The closest prior art has a high average similarity of {top_sim:.2f} (near the "
            f"field ceiling of {s_max:.2f}). Several pre-existing works covered closely related "
            f"techniques or problem formulations."
        )

    return {
        "score": score,
        "score_norm": round(score_norm, 3),
        "level": level,
        "field": field_name,
        "top_similarity": round(top_sim, 3),
        "year_cutoff": year_cutoff,
        "prior_art_count": prior_art_count,
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
            "row_idx": int(idx),
        })
    return jsonify(results)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Accepts a paper_id (from search) or PDF upload + year_cutoff.
    Enriches papers with publication years, applies date cutoff, and returns
    field-calibrated novelty score & similarity graph.
    """
    paper_id_str = request.form.get("paper_id")
    row_idx_str = request.form.get("row_idx")
    year_cutoff_str = request.form.get("year_cutoff")
    year_cutoff = int(year_cutoff_str) if year_cutoff_str and year_cutoff_str != "all" else None
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
    center_meta["url"] = c_meta.get("url") or f"https://scholar.google.com/scholar?q={urllib.parse.quote(center_meta['title'])}"
    center_meta["venue"] = c_meta.get("venue") or center_meta.get("arxiv_category")

    # Enrich similar papers with resolved metadata and prior_art flags
    for p in similar_papers:
        meta = metadata_map.get(p["id"], {})
        p["year"] = meta.get("year")
        p["url"] = meta.get("url") or f"https://scholar.google.com/scholar?q={urllib.parse.quote(p['title'])}"
        p["venue"] = meta.get("venue") or p.get("arxiv_category")

        # Classify relation to year cutoff
        if year_cutoff is not None and p["year"] is not None:
            p["is_prior_art"] = p["year"] <= year_cutoff
            p["status"] = "prior" if p["year"] <= year_cutoff else "subsequent"
        else:
            p["is_prior_art"] = True
            p["status"] = "all"

    # Compute novelty score evaluated specifically against PRIOR ART (<= cutoff)
    if year_cutoff is not None:
        prior_art_sims = [p["similarity"] for p in similar_papers if p.get("is_prior_art")]
    else:
        prior_art_sims = [p["similarity"] for p in similar_papers]

    novelty = _compute_novelty(
        prior_art_sims,
        category=center_meta.get("arxiv_category"),
        year_cutoff=year_cutoff,
        prior_art_count=len(prior_art_sims),
        total_count=len(similar_papers),
    )

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

    # Pairwise similarities among retrieved papers
    top_indices = [idx for idx, _ in top_results]
    if len(top_indices) > 1:
        top_vecs = np.asarray(VECTORS[top_indices], dtype=np.float32)
        sim_matrix = top_vecs @ top_vecs.T

        threshold = 0.52
        for i in range(len(top_indices)):
            for j in range(i + 1, len(top_indices)):
                sim = float(sim_matrix[i, j])
                if sim > threshold:
                    edges.append({
                        "source": similar_papers[i]["id"],
                        "target": similar_papers[j]["id"],
                        "weight": sim,
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
