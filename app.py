"""
Research Paper Novelty Checker — Flask Backend
Mock data for now; real embedding-based similarity will be plugged in later.
"""

import json
import os
import random
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")

# ---------------------------------------------------------------------------
# Mock data — will be replaced by real embedding search later
# ---------------------------------------------------------------------------

MOCK_PAPERS = [
    {
        "id": "2301.07041",
        "title": "Scaling Data-Constrained Language Models",
        "authors": ["N. Muennighoff", "A. Rush", "B. Barak", "T. Le Scao", "A. Piktus"],
        "year": 2023,
        "venue": "NeurIPS 2023",
        "citations": 312,
        "abstract": "Large language models have been shown to benefit from scaling both model and data size. However, the amount of available high-quality data is limited. We investigate methods to scale language models when data is constrained, including data repetition and augmentation strategies.",
        "url": "https://arxiv.org/abs/2301.07041",
        "similarity": 0.92,
    },
    {
        "id": "2302.13971",
        "title": "LLaMA: Open and Efficient Foundation Language Models",
        "authors": ["H. Touvron", "T. Lavril", "G. Izacard", "X. Martinet"],
        "year": 2023,
        "venue": "arXiv preprint",
        "citations": 4521,
        "abstract": "We introduce LLaMA, a collection of foundation language models ranging from 7B to 65B parameters. We train our models on trillions of tokens and show that it is possible to train state-of-the-art models using publicly available datasets exclusively.",
        "url": "https://arxiv.org/abs/2302.13971",
        "similarity": 0.88,
    },
    {
        "id": "2305.18290",
        "title": "Direct Preference Optimization: Your Language Model is Secretly a Reward Model",
        "authors": ["R. Rafailov", "A. Sharma", "E. Mitchell", "S. Ermon"],
        "year": 2023,
        "venue": "NeurIPS 2023",
        "citations": 1893,
        "abstract": "We introduce Direct Preference Optimization (DPO), a new parameterization of the reward model in RLHF that enables extracting the optimal policy in closed form, greatly simplifying the fine-tuning process for language models.",
        "url": "https://arxiv.org/abs/2305.18290",
        "similarity": 0.85,
    },
    {
        "id": "2203.15556",
        "title": "Training language models to follow instructions with human feedback",
        "authors": ["L. Ouyang", "J. Wu", "X. Jiang", "D. Almeida"],
        "year": 2022,
        "venue": "NeurIPS 2022",
        "citations": 7832,
        "abstract": "Making language models bigger does not inherently make them better at following a user's intent. We show an avenue for aligning language models with user intent on a wide range of tasks by fine-tuning with human feedback (InstructGPT).",
        "url": "https://arxiv.org/abs/2203.15556",
        "similarity": 0.82,
    },
    {
        "id": "2005.14165",
        "title": "Language Models are Few-Shot Learners",
        "authors": ["T. Brown", "B. Mann", "N. Ryder", "M. Subbiah"],
        "year": 2020,
        "venue": "NeurIPS 2020",
        "citations": 21543,
        "abstract": "We demonstrate that scaling up language models greatly improves task-agnostic, few-shot performance, sometimes even reaching competitiveness with prior state-of-the-art fine-tuning approaches (GPT-3).",
        "url": "https://arxiv.org/abs/2005.14165",
        "similarity": 0.78,
    },
    {
        "id": "2307.09288",
        "title": "Llama 2: Open Foundation and Fine-Tuned Chat Models",
        "authors": ["H. Touvron", "L. Martin", "K. Stone", "P. Albert"],
        "year": 2023,
        "venue": "arXiv preprint",
        "citations": 3201,
        "abstract": "We release Llama 2, a collection of pretrained and fine-tuned large language models (LLMs) ranging in scale from 7 billion to 70 billion parameters. Our fine-tuned LLMs, called Llama 2-Chat, are optimized for dialogue use cases.",
        "url": "https://arxiv.org/abs/2307.09288",
        "similarity": 0.76,
    },
    {
        "id": "1706.03762",
        "title": "Attention Is All You Need",
        "authors": ["A. Vaswani", "N. Shazeer", "N. Parmar", "J. Uszkoreit"],
        "year": 2017,
        "venue": "NeurIPS 2017",
        "citations": 98234,
        "abstract": "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely. Experiments on two machine translation tasks show these models to be superior in quality.",
        "url": "https://arxiv.org/abs/1706.03762",
        "similarity": 0.71,
    },
    {
        "id": "2401.02954",
        "title": "Mixtral of Experts",
        "authors": ["A. Jiang", "A. Sablayrolles", "A. Roux", "A. Mensch"],
        "year": 2024,
        "venue": "arXiv preprint",
        "citations": 487,
        "abstract": "We introduce Mixtral 8x7B, a Sparse Mixture of Experts language model. Mixtral outperforms Llama 2 70B on most benchmarks with 6x faster inference, and matches or outperforms GPT-3.5 on most standard benchmarks.",
        "url": "https://arxiv.org/abs/2401.02954",
        "similarity": 0.69,
    },
    {
        "id": "2310.06825",
        "title": "Mistral 7B",
        "authors": ["A. Jiang", "A. Sablayrolles", "A. Mensch", "C. Bamford"],
        "year": 2023,
        "venue": "arXiv preprint",
        "citations": 1120,
        "abstract": "We introduce Mistral 7B, a 7-billion-parameter language model engineered for superior performance and efficiency. Mistral 7B outperforms the best open 13B model (Llama 2) across all evaluated benchmarks.",
        "url": "https://arxiv.org/abs/2310.06825",
        "similarity": 0.65,
    },
    {
        "id": "2106.09685",
        "title": "LoRA: Low-Rank Adaptation of Large Language Models",
        "authors": ["E. Hu", "Y. Shen", "P. Wallis", "Z. Allen-Zhu"],
        "year": 2021,
        "venue": "ICLR 2022",
        "citations": 5432,
        "abstract": "We propose Low-Rank Adaptation, or LoRA, which freezes the pre-trained model weights and injects trainable rank decomposition matrices into each layer of the Transformer architecture, greatly reducing the number of trainable parameters.",
        "url": "https://arxiv.org/abs/2106.09685",
        "similarity": 0.62,
    },
    {
        "id": "2304.08485",
        "title": "Visual Instruction Tuning",
        "authors": ["H. Liu", "C. Li", "Q. Wu", "Y. J. Lee"],
        "year": 2023,
        "venue": "NeurIPS 2023",
        "citations": 2150,
        "abstract": "We present the first attempt to use language-only GPT-4 to generate multimodal language-image instruction-following data. We introduce LLaVA (Large Language and Vision Assistant), an end-to-end trained large multimodal model.",
        "url": "https://arxiv.org/abs/2304.08485",
        "similarity": 0.58,
    },
    {
        "id": "2201.11903",
        "title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
        "authors": ["J. Wei", "X. Wang", "D. Schuurmans", "M. Bosma"],
        "year": 2022,
        "venue": "NeurIPS 2022",
        "citations": 6721,
        "abstract": "We explore how generating a chain of thought — a series of intermediate reasoning steps — significantly improves the ability of large language models to perform complex reasoning tasks.",
        "url": "https://arxiv.org/abs/2201.11903",
        "similarity": 0.55,
    },
]

# Edges between papers (pairs of paper indices with weights)
MOCK_EDGES = [
    (0, 1, 0.85), (0, 2, 0.60), (0, 5, 0.78),
    (1, 5, 0.90), (1, 7, 0.72), (1, 8, 0.80),
    (2, 3, 0.88), (2, 11, 0.55),
    (3, 4, 0.82), (3, 11, 0.65),
    (4, 6, 0.70), (4, 3, 0.75),
    (5, 7, 0.83), (5, 8, 0.86),
    (6, 9, 0.50), (6, 4, 0.60),
    (7, 8, 0.92), (7, 1, 0.70),
    (9, 6, 0.45), (9, 10, 0.40),
    (10, 11, 0.48), (10, 2, 0.42),
    (11, 3, 0.58),
]


def _compute_novelty(year_cutoff: int) -> dict:
    """
    Mock novelty computation.
    Returns novelty score (0-100) and a TLDR of what makes it novel.
    Will be replaced by real embedding-based computation later.
    """
    # Filter papers by year cutoff
    filtered = [p for p in MOCK_PAPERS if p["year"] >= year_cutoff]
    if not filtered:
        filtered = MOCK_PAPERS[:3]

    # Mock novelty score — randomised but deterministic per cutoff
    random.seed(year_cutoff * 42)
    score = random.randint(35, 85)

    if score >= 70:
        level = "high"
        tldr = (
            "This paper introduces a substantially novel approach that has limited "
            "overlap with existing literature. The core contribution — combining "
            "sparse mixture-of-experts with instruction tuning — has not been "
            "explored in prior work within the selected time window. Key differentiators "
            "include a new training paradigm and evaluation framework."
        )
    elif score >= 40:
        level = "medium"
        tldr = (
            "This paper presents an incremental but meaningful contribution. While "
            "the underlying techniques (transformer architectures, RLHF) are well-"
            "established, the specific combination and application context show "
            "moderate novelty. Several closely related works exist but address "
            "slightly different problem formulations."
        )
    else:
        level = "low"
        tldr = (
            "This paper closely mirrors existing approaches in the literature. "
            "Multiple highly similar works have been published within the selected "
            "time window, covering the same methods and evaluation setup. Consider "
            "differentiating the contribution further."
        )

    return {
        "score": score,
        "level": level,
        "tldr": tldr,
        "papers": filtered,
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
    Search existing papers by title or author keywords.
    Returns up to 8 matching results for the autocomplete dropdown.
    """
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify([])

    results = []
    for p in MOCK_PAPERS:
        title_match = q in p["title"].lower()
        author_match = any(q in a.lower() for a in p["authors"])
        id_match = q in p["id"]
        if title_match or author_match or id_match:
            results.append({
                "id": p["id"],
                "title": p["title"],
                "authors": p["authors"],
                "year": p["year"],
                "venue": p["venue"],
            })
    return jsonify(results[:8])


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Accepts a PDF upload + year cutoff.
    Returns mock similar papers, novelty score, and graph data.
    """
    year_cutoff = int(request.form.get("year_cutoff", 2018))

    # In the future we'd extract text from the PDF, embed it, and search.
    # For now we return mock data.
    result = _compute_novelty(year_cutoff)

    # Build graph data for the frontend
    filtered_ids = {p["id"] for p in result["papers"]}
    id_to_idx = {p["id"]: i for i, p in enumerate(MOCK_PAPERS)}

    nodes = []
    for p in result["papers"]:
        nodes.append({
            "id": p["id"],
            "title": p["title"],
            "authors": p["authors"],
            "year": p["year"],
            "venue": p["venue"],
            "citations": p["citations"],
            "abstract": p["abstract"],
            "url": p["url"],
            "similarity": p["similarity"],
        })

    # Add the uploaded paper as the center node
    nodes.insert(0, {
        "id": "uploaded",
        "title": "Your Paper",
        "authors": ["You"],
        "year": 2024,
        "venue": "Uploaded",
        "citations": 0,
        "abstract": "The paper you uploaded for analysis.",
        "url": None,
        "similarity": 1.0,
    })

    edges = []
    # Connect uploaded paper to all similar papers
    for p in result["papers"]:
        edges.append({
            "source": "uploaded",
            "target": p["id"],
            "weight": p["similarity"],
        })
    # Connect similar papers to each other
    for i, j, w in MOCK_EDGES:
        if i < len(MOCK_PAPERS) and j < len(MOCK_PAPERS):
            src = MOCK_PAPERS[i]["id"]
            tgt = MOCK_PAPERS[j]["id"]
            if src in filtered_ids and tgt in filtered_ids:
                edges.append({"source": src, "target": tgt, "weight": w})

    return jsonify({
        "novelty": {
            "score": result["score"],
            "level": result["level"],
            "tldr": result["tldr"],
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)
