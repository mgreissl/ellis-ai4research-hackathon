# Eureka Check

## **What it does**

Analyze academic papers for novelty by finding similar work and computing a novelty score.

![Screenshot](resources/Screenshot%202026-08-20%20at%2014.11.56.png)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source venv/bin/activate
python app.py
```

Open **http://127.0.0.1:5001** in your browser.

## Usage

1. **Upload a PDF** or **search for an existing paper** by title/author
2. Choose a **year cutoff** to filter the literature window
3. Click **Analyze Novelty** → see the similarity graph, traffic-light novelty score, and TLDR