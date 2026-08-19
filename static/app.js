/* ═══════════════════════════════════════════════════════════════
   NoveltyScope — Frontend Application
   ═══════════════════════════════════════════════════════════════ */

(() => {
    "use strict";

    // ── DOM refs ──
    const uploadOverlay = document.getElementById("upload-overlay");
    const dropZone      = document.getElementById("drop-zone");
    const fileInput     = document.getElementById("file-input");
    const browseBtn     = document.getElementById("browse-btn");
    const filePreview   = document.getElementById("file-preview");
    const fileNameEl    = document.getElementById("file-name");
    const removeFileBtn = document.getElementById("remove-file");
    const analyzeBtn    = document.getElementById("analyze-btn");
    const yearCutoff    = document.getElementById("year-cutoff");
    const yearInline    = document.getElementById("year-cutoff-inline");

    const resultsEl     = document.getElementById("results");
    const paperList     = document.getElementById("paper-list");
    const paperCount    = document.getElementById("paper-count");
    const btnBack       = document.getElementById("btn-back");

    const trafficLights     = document.querySelectorAll("#traffic-light .light");
    const trafficLightsLg   = document.querySelectorAll("#detail-traffic-light .light");
    const noveltyScoreEl    = document.getElementById("novelty-score");
    const detailScoreValue  = document.getElementById("detail-score-value");
    const scoreRingFg       = document.getElementById("score-ring-fg");
    const detailTldr        = document.getElementById("detail-tldr");

    const detailNovelty = document.getElementById("detail-novelty");
    const detailPaper   = document.getElementById("detail-paper");
    const detailClose   = document.getElementById("detail-close");
    const detailTitle   = document.getElementById("detail-title");
    const detailAuthors = document.getElementById("detail-authors");
    const detailVenue   = document.getElementById("detail-venue");
    const detailYear    = document.getElementById("detail-year");
    const detailCite    = document.getElementById("detail-citations");
    const detailSim     = document.getElementById("detail-similarity");
    const detailAbstract= document.getElementById("detail-abstract");
    const detailLink    = document.getElementById("detail-link");

    const graphContainer = document.getElementById("graph-container");
    const graphTooltip   = document.getElementById("graph-tooltip");

    let selectedFile   = null;
    let selectedPaper  = null;
    let currentData    = null;
    let simulation     = null;

    // ── Search DOM refs ──
    const searchInput    = document.getElementById("search-input");
    const searchResults  = document.getElementById("search-results");
    const searchPreview  = document.getElementById("search-preview");
    const searchPaperName = document.getElementById("search-paper-name");
    const removeSearchBtn = document.getElementById("remove-search");
    const searchSection   = document.getElementById("search-section");

    // ── Populate inline year selector ──
    for (const opt of yearCutoff.options) {
        const clone = opt.cloneNode(true);
        yearInline.appendChild(clone);
    }
    yearInline.value = yearCutoff.value;

    // ═══════════════════════════════════════════════════════════
    // File Upload
    // ═══════════════════════════════════════════════════════════

    browseBtn.addEventListener("click", () => fileInput.click());
    dropZone.addEventListener("click", () => fileInput.click());

    dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        const file = e.dataTransfer.files[0];
        if (file && file.type === "application/pdf") setFile(file);
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files[0]) setFile(fileInput.files[0]);
    });

    removeFileBtn.addEventListener("click", () => clearFile());

    function setFile(file) {
        selectedFile = file;
        fileNameEl.textContent = file.name;
        filePreview.classList.remove("hidden");
        dropZone.classList.add("hidden");
        // Clear any search selection
        clearSearch(true);
        analyzeBtn.disabled = false;
    }

    function clearFile() {
        selectedFile = null;
        fileInput.value = "";
        filePreview.classList.add("hidden");
        dropZone.classList.remove("hidden");
        if (!selectedPaper) analyzeBtn.disabled = true;
    }

    // ═══════════════════════════════════════════════════════════
    // Search Existing Papers
    // ═══════════════════════════════════════════════════════════

    let searchDebounce = null;

    searchInput.addEventListener("input", () => {
        clearTimeout(searchDebounce);
        const q = searchInput.value.trim();
        if (q.length < 2) {
            searchResults.classList.add("hidden");
            return;
        }
        searchDebounce = setTimeout(async () => {
            try {
                const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
                const papers = await res.json();
                renderSearchResults(papers);
            } catch (err) { console.error(err); }
        }, 200);
    });

    // Close dropdown when clicking outside
    document.addEventListener("click", (e) => {
        if (!e.target.closest(".search-wrapper")) {
            searchResults.classList.add("hidden");
        }
    });

    function renderSearchResults(papers) {
        if (papers.length === 0) {
            searchResults.innerHTML = `<div class="search-result-item"><div class="sr-title" style="color:var(--text-muted)">No papers found</div></div>`;
            searchResults.classList.remove("hidden");
            return;
        }
        searchResults.innerHTML = papers.map((p) => `
            <div class="search-result-item" data-id="${p.id}">
                <div class="sr-title">${esc(p.title)}</div>
                <div class="sr-meta">${esc(p.authors[0])} et al. · ${p.year} · ${p.venue}</div>
            </div>
        `).join("");

        searchResults.querySelectorAll(".search-result-item[data-id]").forEach((el) => {
            el.addEventListener("click", () => {
                const paper = papers.find((p) => p.id === el.dataset.id);
                if (paper) selectSearchPaper(paper);
            });
        });
        searchResults.classList.remove("hidden");
    }

    function selectSearchPaper(paper) {
        selectedPaper = paper;
        searchPaperName.textContent = paper.title;
        searchPreview.classList.remove("hidden");
        searchSection.classList.add("hidden");
        searchResults.classList.add("hidden");
        searchInput.value = "";
        // Clear any file selection
        if (selectedFile) clearFile();
        analyzeBtn.disabled = false;
    }

    function clearSearch(keepButton) {
        selectedPaper = null;
        searchPreview.classList.add("hidden");
        searchSection.classList.remove("hidden");
        searchInput.value = "";
        searchResults.classList.add("hidden");
        if (!keepButton && !selectedFile) analyzeBtn.disabled = true;
    }

    removeSearchBtn.addEventListener("click", () => clearSearch(false));

    // ═══════════════════════════════════════════════════════════
    // Analyze
    // ═══════════════════════════════════════════════════════════

    analyzeBtn.addEventListener("click", async () => {
        if (!selectedFile && !selectedPaper) return;

        // Show loading
        analyzeBtn.querySelector(".btn-text").classList.add("hidden");
        analyzeBtn.querySelector(".btn-loader").classList.remove("hidden");
        analyzeBtn.disabled = true;

        const form = new FormData();
        if (selectedFile) form.append("file", selectedFile);
        if (selectedPaper) form.append("paper_id", selectedPaper.id);
        form.append("year_cutoff", yearCutoff.value);

        try {
            const res = await fetch("/api/analyze", { method: "POST", body: form });
            currentData = await res.json();
            showResults(currentData);
        } catch (err) {
            console.error(err);
            alert("Analysis failed — is the server running?");
        } finally {
            analyzeBtn.querySelector(".btn-text").classList.remove("hidden");
            analyzeBtn.querySelector(".btn-loader").classList.add("hidden");
            analyzeBtn.disabled = false;
        }
    });

    // Re-analyze on inline year change
    yearInline.addEventListener("change", async () => {
        yearCutoff.value = yearInline.value;
        const form = new FormData();
        form.append("file", selectedFile || new Blob());
        form.append("year_cutoff", yearInline.value);
        try {
            const res = await fetch("/api/analyze", { method: "POST", body: form });
            currentData = await res.json();
            showResults(currentData);
        } catch (err) { console.error(err); }
    });

    btnBack.addEventListener("click", () => {
        resultsEl.classList.add("hidden");
        resultsEl.classList.remove("show");
        uploadOverlay.classList.add("visible");
        if (simulation) simulation.stop();
    });

    // ═══════════════════════════════════════════════════════════
    // Render Results
    // ═══════════════════════════════════════════════════════════

    function showResults(data) {
        uploadOverlay.classList.remove("visible");
        resultsEl.classList.remove("hidden");
        requestAnimationFrame(() => resultsEl.classList.add("show"));

        renderNovelty(data.novelty);
        renderPaperList(data.graph.nodes);
        renderGraph(data.graph);
        showNoveltyPanel();
    }

    // ─── Novelty Indicators ───
    function renderNovelty(novelty) {
        const { score, level, tldr } = novelty;

        // Score text
        noveltyScoreEl.textContent = score;
        detailScoreValue.textContent = score;

        // Score ring
        const circumference = 2 * Math.PI * 52; // r=52
        const offset = circumference - (score / 100) * circumference;
        scoreRingFg.style.strokeDashoffset = offset;

        // Ring colour
        const col = level === "high" ? "var(--green)" : level === "medium" ? "var(--yellow)" : "var(--red)";
        scoreRingFg.style.stroke = col;
        noveltyScoreEl.style.color = col;
        detailScoreValue.style.color = col;

        // Traffic lights
        [trafficLights, trafficLightsLg].forEach((lights) => {
            lights.forEach((l) => {
                l.classList.toggle("on", l.dataset.level === level);
            });
        });

        detailTldr.textContent = tldr;
    }

    // ─── Paper List ───
    function renderPaperList(nodes) {
        paperList.innerHTML = "";
        // Skip the "uploaded" node
        const papers = nodes.filter((n) => n.id !== "uploaded");
        paperCount.textContent = papers.length;

        papers.forEach((p) => {
            const el = document.createElement("div");
            el.className = "paper-item";
            el.innerHTML = `
                <div class="pi-title">${esc(p.title)}</div>
                <div class="pi-meta">
                    <span>${esc(p.authors[0])} et al.</span>
                    <span>${p.year}</span>
                    <span class="pi-similarity">${(p.similarity * 100).toFixed(0)}%</span>
                </div>
            `;
            el.addEventListener("click", () => selectPaper(p, el));
            paperList.appendChild(el);
        });
    }

    function selectPaper(paper, el) {
        document.querySelectorAll(".paper-item").forEach((e) => e.classList.remove("active"));
        if (el) el.classList.add("active");
        showPaperDetail(paper);
        highlightNode(paper.id);
    }

    // ─── Right Panel: Detail Views ───
    function showNoveltyPanel() {
        detailNovelty.classList.remove("hidden");
        detailPaper.classList.add("hidden");
    }

    function showPaperDetail(paper) {
        detailNovelty.classList.add("hidden");
        detailPaper.classList.remove("hidden");

        detailTitle.textContent    = paper.title;
        detailAuthors.textContent  = paper.authors.join(", ");
        detailVenue.textContent    = paper.venue;
        detailYear.textContent     = paper.year;
        detailCite.textContent     = paper.citations.toLocaleString();
        detailSim.textContent      = (paper.similarity * 100).toFixed(0) + "%";
        detailAbstract.textContent = paper.abstract;
        if (paper.url) {
            detailLink.href = paper.url;
            detailLink.classList.remove("hidden");
        } else {
            detailLink.classList.add("hidden");
        }
    }

    detailClose.addEventListener("click", () => {
        showNoveltyPanel();
        document.querySelectorAll(".paper-item").forEach((e) => e.classList.remove("active"));
        unhighlightAll();
    });

    // ═══════════════════════════════════════════════════════════
    // D3 Force-Directed Graph
    // ═══════════════════════════════════════════════════════════

    function renderGraph(graphData) {
        const svg = d3.select("#graph-svg");
        svg.selectAll("*").remove();

        const rect = graphContainer.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;

        svg.attr("viewBox", `0 0 ${width} ${height}`);

        // Colour scale by year
        const years = graphData.nodes.map((n) => n.year);
        const yearScale = d3.scaleLinear()
            .domain([d3.min(years), d3.max(years)])
            .range(["#6366f1", "#22d3ee"]);

        // Size scale by citations (sqrt)
        const citeExtent = d3.extent(graphData.nodes.filter(n => n.id !== "uploaded"), (n) => n.citations);
        const sizeScale = d3.scaleSqrt().domain([0, citeExtent[1] || 1]).range([8, 36]);

        // Clone data
        const nodes = graphData.nodes.map((d) => ({ ...d }));
        const links = graphData.edges.map((d) => ({ ...d }));

        // Force simulation
        simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id((d) => d.id).distance(100).strength((d) => d.weight * 0.6))
            .force("charge", d3.forceManyBody().strength(-300))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius((d) => getRadius(d) + 6));

        // Links
        const link = svg.append("g")
            .attr("class", "links")
            .selectAll("line")
            .data(links)
            .join("line")
            .attr("stroke", "rgba(255,255,255,0.08)")
            .attr("stroke-width", (d) => Math.max(1, d.weight * 3));

        // Node groups
        const node = svg.append("g")
            .attr("class", "nodes")
            .selectAll("g")
            .data(nodes)
            .join("g")
            .attr("cursor", "pointer")
            .call(d3.drag()
                .on("start", dragStart)
                .on("drag", dragging)
                .on("end", dragEnd));

        // Node circles
        node.append("circle")
            .attr("r", (d) => getRadius(d))
            .attr("fill", (d) => d.id === "uploaded" ? "#f97316" : yearScale(d.year))
            .attr("stroke", (d) => d.id === "uploaded" ? "#fbbf24" : "rgba(255,255,255,0.12)")
            .attr("stroke-width", (d) => d.id === "uploaded" ? 3 : 1.5)
            .attr("opacity", 0.9);

        // Labels
        node.append("text")
            .text((d) => {
                if (d.id === "uploaded") return "📄 Your Paper";
                const short = d.title.length > 25 ? d.title.slice(0, 22) + "…" : d.title;
                return `${d.authors[0].split(" ").pop()}, ${d.year}`;
            })
            .attr("dy", (d) => getRadius(d) + 14)
            .attr("text-anchor", "middle")
            .attr("fill", "var(--text-secondary)")
            .attr("font-size", "10.5px")
            .attr("font-weight", "500")
            .attr("pointer-events", "none");

        // Interactions
        node.on("mouseover", (event, d) => {
            if (d.id === "uploaded") return;
            graphTooltip.classList.remove("hidden");
            graphTooltip.innerHTML = `
                <div class="tt-title">${esc(d.title)}</div>
                <div class="tt-meta">${esc(d.authors[0])} et al. · ${d.year} · ${d.citations} cit. · ${(d.similarity * 100).toFixed(0)}% sim</div>
            `;
            const [x, y] = d3.pointer(event, graphContainer);
            graphTooltip.style.left = (x + 16) + "px";
            graphTooltip.style.top  = (y - 10) + "px";
        }).on("mouseout", () => {
            graphTooltip.classList.add("hidden");
        }).on("click", (event, d) => {
            if (d.id === "uploaded") return;
            const listItem = paperList.querySelectorAll(".paper-item");
            const idx = graphData.nodes.filter(n => n.id !== "uploaded").findIndex(n => n.id === d.id);
            selectPaper(d, listItem[idx]);
        });

        // Tick
        simulation.on("tick", () => {
            link
                .attr("x1", (d) => d.source.x)
                .attr("y1", (d) => d.source.y)
                .attr("x2", (d) => d.target.x)
                .attr("y2", (d) => d.target.y);

            node.attr("transform", (d) => `translate(${d.x},${d.y})`);
        });

        function getRadius(d) {
            if (d.id === "uploaded") return 22;
            return sizeScale(d.citations);
        }

        function dragStart(event) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            event.subject.fx = event.subject.x;
            event.subject.fy = event.subject.y;
        }
        function dragging(event) {
            event.subject.fx = event.x;
            event.subject.fy = event.y;
        }
        function dragEnd(event) {
            if (!event.active) simulation.alphaTarget(0);
            event.subject.fx = null;
            event.subject.fy = null;
        }

        // Store refs for highlight
        window._graphNodes = node;
        window._graphLinks = link;
        window._graphData  = { nodes, links };
    }

    function highlightNode(id) {
        if (!window._graphNodes) return;
        window._graphNodes.select("circle")
            .attr("opacity", (d) => d.id === id || d.id === "uploaded" ? 1 : 0.25);
        window._graphLinks
            .attr("stroke", (d) =>
                d.source.id === id || d.target.id === id
                    ? "rgba(99,102,241,0.5)"
                    : "rgba(255,255,255,0.03)"
            );
    }

    function unhighlightAll() {
        if (!window._graphNodes) return;
        window._graphNodes.select("circle").attr("opacity", 0.9);
        window._graphLinks.attr("stroke", "rgba(255,255,255,0.08)");
    }

    // ── Helpers ──
    function esc(str) {
        const el = document.createElement("span");
        el.textContent = str;
        return el.innerHTML;
    }
})();
