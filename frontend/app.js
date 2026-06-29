const searchScreen = document.getElementById("search-screen");
const reportScreen = document.getElementById("report-screen");
const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchHint = document.getElementById("search-hint");
const backBtn = document.getElementById("back-btn");
const downloadBtn = document.getElementById("download-btn");

let sentimentChartInstance = null;
let volumeChartInstance = null;
let networkInstance = null;

const API_BASE = window.PULSE_API_BASE || "http://localhost:8000";

searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = searchInput.value.trim();
  if (!query) return;

  setLoading(true, `Running the live pipeline for "${query}"…`);

  try {
    const res = await fetch(`${API_BASE}/api/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: query }),
    });
    const body = await res.json();
    if (res.ok && body.ok && body.report) {
      renderReport(body.report);
      showReportScreen();
      setLoading(false);
      return;
    }
    throw new Error(body.error || "Live pipeline returned no data");
  } catch (err) {
    // Backend not reachable, out of API credits, or no data for this name —
    // never dead-end the demo, fall back to the bundled dataset.
    const fallback = findReport(query) || REPORTS["edwin sifuna"];
    searchHint.textContent = `Live pipeline unavailable (${err.message}) — showing the demo report for ${fallback.name} instead.`;
    renderReport(fallback);
    showReportScreen();
    setLoading(false);
  }
});

function setLoading(isLoading, message) {
  const btn = searchForm.querySelector("button");
  btn.disabled = isLoading;
  btn.textContent = isLoading ? "Generating…" : "Generate report";
  if (message) searchHint.textContent = message;
}

backBtn.addEventListener("click", () => {
  reportScreen.classList.add("hidden");
  searchScreen.classList.remove("hidden");
  searchInput.value = "";
  searchHint.textContent = "Try “Edwin Sifuna” for a live demo report.";
});

downloadBtn.addEventListener("click", () => window.print());

function showReportScreen() {
  searchScreen.classList.add("hidden");
  reportScreen.classList.remove("hidden");
}

function renderReport(r) {
  document.getElementById("r-name").textContent = r.name;
  document.getElementById("r-title").textContent = r.title;
  document.getElementById("r-window").textContent = `Window: ${r.window} · Generated ${r.generated}`;
  document.getElementById("r-summary").textContent = r.summary;

  renderSentiment(r.sentiment);
  renderVolume(r.volume);
  renderTopMentions(r.volume.topMentions);
  renderInfluence(r.influence);
  renderNetwork(r.network);
  renderNarratives(r.narratives);
  renderList("risks-list", r.risks);
  renderList("opportunities-list", r.opportunities);
  renderList("trends-list", r.trends);
}

function renderSentiment(s) {
  const ctx = document.getElementById("sentiment-chart");
  if (sentimentChartInstance) sentimentChartInstance.destroy();
  sentimentChartInstance = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Negative", "Neutral", "Positive"],
      datasets: [{
        data: [s.negative, s.neutral, s.positive],
        backgroundColor: ["#fb7185", "#94a3b8", "#86efac"],
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      cutout: "68%",
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.label}: ${c.raw}%` } } },
    },
  });

  const legend = document.getElementById("sentiment-legend");
  legend.innerHTML = `
    <div class="legend-item"><span class="legend-swatch" style="background:#fb7185"></span>Negative<span class="legend-value">${s.negative}%</span></div>
    <div class="legend-item"><span class="legend-swatch" style="background:#94a3b8"></span>Neutral<span class="legend-value">${s.neutral}%</span></div>
    <div class="legend-item"><span class="legend-swatch" style="background:#86efac"></span>Positive<span class="legend-value">${s.positive}%</span></div>
    <div class="muted small" style="margin-top:6px">Based on ${s.totalAnalyzed} sentiment-scored mentions in the analysis window.</div>
  `;
}

function renderVolume(v) {
  const metrics = document.getElementById("volume-metrics");
  metrics.innerHTML = `
    <div class="metric-box"><div class="value">${v.total}</div><div class="label">Total mentions</div></div>
    <div class="metric-box"><div class="value">${v.platforms}</div><div class="label">Platforms</div></div>
    <div class="metric-box"><div class="value">${v.last72h}</div><div class="label">Last 72h</div></div>
    <div class="metric-box"><div class="value">${(v.engagement.youtubeViews / 1000).toFixed(0)}K</div><div class="label">YouTube views</div></div>
    <div class="metric-box"><div class="value">${v.engagement.linkedinLikes}</div><div class="label">LinkedIn likes</div></div>
  `;

  const ctx = document.getElementById("volume-chart");
  if (volumeChartInstance) volumeChartInstance.destroy();
  volumeChartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: v.byPlatform.map((p) => p.platform),
      datasets: [{
        data: v.byPlatform.map((p) => p.count),
        backgroundColor: "rgba(125, 211, 252, 0.55)",
        borderRadius: 6,
        maxBarThickness: 28,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#9aa3b8", font: { size: 11 } }, grid: { display: false } },
        y: { ticks: { color: "#9aa3b8" }, grid: { color: "rgba(255,255,255,0.06)" } },
      },
    },
  });
}

function renderTopMentions(items) {
  const el = document.getElementById("top-mentions");
  el.innerHTML = items.map((m) => `
    <div class="mention-item">
      <span class="tag ${m.sentiment}">${m.sentiment}</span>
      <span class="muted">${m.platform}</span>
      <a href="${m.url}" target="_blank" rel="noopener">${m.headline}</a>
      <span class="muted small" style="text-align:right">${m.metric}</span>
    </div>
  `).join("");
}

function renderInfluence(list) {
  const el = document.getElementById("influence-list");
  el.innerHTML = list.map((i) => `
    <div class="influence-item">
      <span class="rank">#${i.rank}</span>
      <span class="score">${i.score}</span>
      <span class="who">${i.who}${i.platform ? ` <span class="muted small">(${i.platform})</span>` : ""}</span>
      <span class="note">${i.note}</span>
      <span class="sentiment-num ${i.sentiment >= 0 ? "pos" : "neg"}">${i.sentiment > 0 ? "+" : ""}${i.sentiment}</span>
    </div>
  `).join("");
}

function renderNarratives(list) {
  const el = document.getElementById("narratives-list");
  el.innerHTML = list.map((n) => `
    <div class="narrative-item">
      <div class="n-title">${n.label}</div>
      <div class="n-meta">Strength ${n.strength} · ${n.mentions} mentions · growth ${(n.growth * 100).toFixed(0)}%</div>
      <p>${n.description}</p>
    </div>
  `).join("");
}

function renderList(id, items) {
  document.getElementById(id).innerHTML = items.map((t) => `<li>${t}</li>`).join("");
}

function renderNetwork(net) {
  const groupColors = {};
  net.legend.forEach((g) => (groupColors[g.group] = g.color));

  const nodes = new vis.DataSet(net.nodes.map((n) => ({
    id: n.id,
    label: n.label,
    value: n.value,
    color: { background: groupColors[n.group] || "#7dd3fc", border: "rgba(255,255,255,0.4)" },
    font: { color: "#0b1020", face: "Inter", size: 13 },
  })));

  const edges = new vis.DataSet(net.edges.map((e, i) => ({
    id: i,
    from: e.from,
    to: e.to,
    label: e.label,
    dashes: !!e.dashed,
    color: { color: e.dashed ? "rgba(148,163,184,0.55)" : "rgba(125,211,252,0.45)" },
    font: { color: "#9aa3b8", size: 10, strokeWidth: 0, background: "rgba(11,16,32,0.7)" },
    smooth: { type: "continuous" },
  })));

  const container = document.getElementById("network-graph");
  const options = {
    physics: { stabilization: true, barnesHut: { gravitationalConstant: -3000, springLength: 140 } },
    interaction: { hover: true, zoomView: true, dragView: true },
    nodes: { shape: "dot", scaling: { min: 14, max: 42 }, shadow: true },
    edges: { arrows: { to: { enabled: false } }, shadow: false },
  };

  if (networkInstance) networkInstance.destroy();
  networkInstance = new vis.Network(container, { nodes, edges }, options);

  const legendEl = document.getElementById("network-legend");
  legendEl.innerHTML = net.legend.map((g) => `
    <div class="legend-item"><span class="legend-swatch" style="background:${g.color}"></span>${g.label}</div>
  `).join("");
}
