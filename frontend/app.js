const API_BASE = window.location.port === "5500" ? "http://127.0.0.1:8000" : "";
const state = { view: "overview", network: null, campaigns: [], lastScan: false, health: null };
const samplePRs = {
  pull_requests: [
    { id: 101, project: "alpha", author: "dev-one", title: "Improve error handling in parser", body: "Add explicit validation for malformed input and return a clear error message. Update the parser tests to cover invalid payloads.", created_at: "2026-10-31T20:10:00Z", account_age_days: 4 },
    { id: 102, project: "beacon", author: "patch-helper", title: "Improve error handling in parser", body: "Add explicit validation for malformed input and return a clear error message. Update the parser tests to cover invalid payloads.", created_at: "2026-10-31T21:02:00Z", account_age_days: 7 },
    { id: 103, project: "cinder", author: "opensource-new", title: "Improve error handling in parser", body: "Add explicit validation for malformed input and return a clear error message. Update the parser tests to cover invalid payloads.", created_at: "2026-10-31T21:47:00Z", account_age_days: 3 },
    { id: 104, project: "delta", author: "marina-dev", title: "Clarify release checklist", body: "Document the release checklist and add a note about reviewing generated changelog entries.", created_at: "2026-10-30T12:15:00Z", account_age_days: 812 },
    { id: 105, project: "alpha", author: "ravi-maintainer", title: "Fix typo in contributor guide", body: "Correct a typo in the contributor guide and improve one sentence for readability.", created_at: "2026-10-29T08:30:00Z", account_age_days: 1240 }
  ]
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const text = (value, fallback = "—") => value === undefined || value === null || value === "" ? fallback : String(value);
const apiUrl = (path) => `${API_BASE}${path}`;

async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.detail || payload.message || `Request failed (${response.status})`);
  return payload;
}

function setAlert(id, message = "", success = false) {
  const element = $(`#${id}`); if (!element) return;
  element.textContent = message; element.classList.toggle("success", success);
}
function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) { button.dataset.originalLabel = button.textContent; button.textContent = busyLabel; button.disabled = true; }
  else { button.textContent = button.dataset.originalLabel || button.textContent; button.disabled = false; }
}
function safeList(values, className = "") {
  const list = document.createElement("ul"); list.className = `evidence-list ${className}`.trim();
  if (!Array.isArray(values) || values.length === 0) { const empty = document.createElement("li"); empty.className = "empty-evidence"; empty.textContent = "No evidence returned."; list.append(empty); return list; }
  values.forEach((value) => { const item = document.createElement("li"); item.textContent = text(value); list.append(item); }); return list;
}
function showView(view) {
  state.view = view;
  $$("[data-view-panel]").forEach((panel) => { const active = panel.dataset.viewPanel === view; panel.hidden = !active; panel.classList.toggle("active", active); });
  $$(".nav-item").forEach((button) => { const active = button.dataset.view === view; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); });
  const title = $(`[data-view-panel="${view}"] h1`); $("#page-title").textContent = title ? title.textContent : "Overview";
}

function updateHealth(healthy, detail) {
  state.health = healthy;
  const dots = $$(".status-dot"); dots.forEach((dot) => dot.classList.toggle("online", healthy)); dots.forEach((dot) => dot.classList.toggle("offline", !healthy));
  $("#api-status-text").textContent = healthy ? "API connected" : "API unavailable";
  $("#sidebar-status").textContent = healthy ? "API connected" : "API unavailable";
  $("#health-heading").textContent = healthy ? "TrustMesh API is online" : "TrustMesh API is unavailable";
  $("#health-detail").textContent = detail || (healthy ? "Local service is responding normally." : "Start the FastAPI service to load network data.");
  $("#overview-health-badge").textContent = healthy ? "Online" : "Offline"; $("#overview-health-badge").className = `status-badge ${healthy ? "online" : "alert"}`;
  $("#endpoint-label").textContent = API_BASE ? `${API_BASE}/api` : "/api (same origin)";
}

function renderOverview() {
  const network = state.network;
  $("#stat-projects").textContent = network ? text(network.projects?.length, "0") : "—";
  $("#stat-trusts").textContent = network ? text(network.trusts?.length, "0") : "—";
  if (network) {
    const signals = Array.isArray(network.signals) ? network.signals : [];
    const vouches = signals.filter((signal) => signal.type === "vouch").length;
    const denounces = signals.filter((signal) => signal.type === "denounce").length;
    $("#stat-signals").textContent = `${vouches} / ${denounces}`;
  } else $("#stat-signals").textContent = "—";
  $("#stat-campaigns").textContent = state.lastScan ? String(state.campaigns.length) : "—";
}

function renderProjects(projects = []) {
  const list = $("#project-list"); list.replaceChildren();
  if (!projects.length) { const empty = document.createElement("p"); empty.className = "muted-copy"; empty.textContent = "No projects returned by the API."; list.append(empty); return; }
  projects.forEach((project) => { const row = document.createElement("div"); row.className = "project-item"; const avatar = document.createElement("span"); avatar.className = "project-avatar"; avatar.textContent = text(project.name || project.id, "?").slice(0, 1).toUpperCase(); const details = document.createElement("div"); const name = document.createElement("strong"); name.textContent = text(project.name || project.id); const id = document.createElement("small"); id.textContent = text(project.id); details.append(name, id); row.append(avatar, details); list.append(row); });
}
function renderNetwork(network) {
  renderProjects(network?.projects || []); const svg = $("#network-graph"); const empty = $("#network-empty"); svg.replaceChildren();
  if (!network || !Array.isArray(network.projects) || network.projects.length === 0) { svg.hidden = true; empty.hidden = false; $("#network-summary").textContent = "Load network data to render the graph."; return; }
  svg.hidden = false; empty.hidden = true; $("#network-summary").textContent = `${network.projects.length} projects · ${(network.trusts || []).length} directional relationships`;
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs"); const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker"); marker.setAttribute("id", "arrow"); marker.setAttribute("markerWidth", "8"); marker.setAttribute("markerHeight", "8"); marker.setAttribute("refX", "7"); marker.setAttribute("refY", "3"); marker.setAttribute("orient", "auto"); const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path"); arrow.setAttribute("d", "M0,0 L0,6 L7,3 z"); arrow.setAttribute("fill", "#5e7076"); marker.append(arrow); defs.append(marker); svg.append(defs);
  const positions = {}; const center = { x: 380, y: 215 }; const radius = Math.min(150, 80 + network.projects.length * 15); network.projects.forEach((project, index) => { const angle = -Math.PI / 2 + (index * Math.PI * 2 / network.projects.length); positions[project.id] = { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius }; });
  const trusts = Array.isArray(network.trusts) ? network.trusts : []; trusts.forEach((trust) => { const from = positions[trust.from]; const to = positions[trust.to]; if (!from || !to) return; const line = document.createElementNS("http://www.w3.org/2000/svg", "line"); line.setAttribute("x1", from.x); line.setAttribute("y1", from.y); line.setAttribute("x2", to.x); line.setAttribute("y2", to.y); line.setAttribute("class", "network-edge trusted"); line.setAttribute("marker-end", "url(#arrow)"); svg.append(line); });
  network.projects.forEach((project) => { const position = positions[project.id]; const group = document.createElementNS("http://www.w3.org/2000/svg", "g"); const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle"); circle.setAttribute("cx", position.x); circle.setAttribute("cy", position.y); circle.setAttribute("r", "31"); circle.setAttribute("class", "network-node"); const label = document.createElementNS("http://www.w3.org/2000/svg", "text"); label.setAttribute("x", position.x); label.setAttribute("y", position.y + 4); label.setAttribute("class", "network-label"); label.textContent = text(project.name || project.id).slice(0, 13); group.append(circle, label); svg.append(group); });
}
async function loadNetwork() { try { const network = await request("/api/network"); state.network = network; renderOverview(); renderNetwork(network); } catch (error) { setAlert("network-alert", error.message); renderNetwork(null); } }
async function checkHealth() { try { const result = await request("/api/health"); updateHealth(result.status === "ok", result.status === "ok" ? "Local service is responding normally." : text(result.status)); } catch (error) { updateHealth(false, "Unable to reach the local API. Check that FastAPI is running on port 8000."); } }

function renderReview(result) {
  const container = $("#review-result"); container.className = "review-content"; container.replaceChildren(); $("#decision-badge").textContent = text(result.decision, "Unknown"); $("#decision-badge").className = `status-badge ${result.decision === "vouched" ? "online" : "neutral"}`;
  const intro = document.createElement("div"); intro.className = "decision-intro"; const decision = document.createElement("strong"); decision.textContent = text(result.decision, "Unknown"); const explanation = document.createElement("p"); explanation.textContent = "Decision based on signals trusted by the reviewing project."; intro.append(decision, explanation); container.append(intro);
  [["Trusted vouches", result.trusted_vouches, "trusted"], ["Trusted denounces", result.trusted_denounces, "ignored"], ["Ignored untrusted denounces", result.ignored_denounces, "ignored"], ["Supporting evidence", result.evidence, "trusted"]].forEach(([heading, values, className]) => { const block = document.createElement("section"); block.className = "evidence-block"; const title = document.createElement("h3"); title.textContent = heading; block.append(title, safeList(values, className)); container.append(block); });
}
async function evaluateContributor(event) { event.preventDefault(); const project = $("#review-project").value.trim(); const contributor = $("#review-contributor").value.trim(); if (!project || !contributor) { setAlert("review-alert", "Enter both a reviewing project and contributor ID."); return; } const button = $("#evaluate-button"); setBusy(button, true, "Evaluating..."); setAlert("review-alert"); try { const result = await request(`/api/evaluate?project=${encodeURIComponent(project)}&contributor=${encodeURIComponent(contributor)}`); renderReview(result); } catch (error) { setAlert("review-alert", error.message); } finally { setBusy(button, false); } }

function renderCampaigns(campaigns) { const container = $("#campaign-results"); container.replaceChildren(); $("#campaign-count").textContent = `${campaigns.length} found`; if (!campaigns.length) { const empty = document.createElement("div"); empty.className = "result-placeholder compact"; const mark = document.createElement("span"); mark.className = "empty-mark"; mark.textContent = "⌁"; const title = document.createElement("strong"); title.textContent = "No campaigns found"; const note = document.createElement("p"); note.textContent = "The backend returned no coordinated campaigns for this batch."; empty.append(mark, title, note); container.append(empty); } campaigns.forEach((campaign) => { const card = document.createElement("article"); card.className = "campaign-card"; const header = document.createElement("div"); header.className = "campaign-card-header"; const titleBlock = document.createElement("div"); const eyebrow = document.createElement("h3"); eyebrow.textContent = "CAMPAIGN"; const id = document.createElement("div"); id.className = "campaign-id"; id.textContent = text(campaign.id); titleBlock.append(eyebrow, id); const score = document.createElement("span"); score.className = "similarity"; score.textContent = `${text(campaign.similarity_score, "0")} similarity`; header.append(titleBlock, score); const facts = document.createElement("div"); facts.className = "campaign-facts"; [ `${Array.isArray(campaign.pr_ids) ? campaign.pr_ids.length : 0} PRs`, `${text(campaign.project_count, "0")} projects`, `IDs: ${(campaign.pr_ids || []).map(text).join(", ") || "none"}` ].forEach((value) => { const fact = document.createElement("span"); fact.className = "fact"; fact.textContent = value; facts.append(fact); }); const evidence = document.createElement("div"); evidence.className = "campaign-evidence"; const evidenceTitle = document.createElement("h3"); evidenceTitle.textContent = "EVIDENCE"; evidence.append(evidenceTitle); (Array.isArray(campaign.evidence) ? campaign.evidence : []).forEach((item) => { const p = document.createElement("p"); p.textContent = text(item); evidence.append(p); }); card.append(header, facts, evidence); container.append(card); }); updateCampaignSelect(); renderOverview(); }
function updateCampaignSelect() { const select = $("#campaign-select"); select.replaceChildren(); if (!state.campaigns.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No campaigns available"; select.append(option); $("#analyze-button").disabled = true; return; } state.campaigns.forEach((campaign, index) => { const option = document.createElement("option"); option.value = String(index); option.textContent = text(campaign.id, `Campaign ${index + 1}`); select.append(option); }); $("#analyze-button").disabled = false; }
function loadSamples() { $("#pr-editor").value = JSON.stringify(samplePRs, null, 2); setAlert("scanner-alert", "Sample batch loaded. Send it to the backend when ready.", true); }
async function scanContributions() { let payload; try { payload = JSON.parse($("#pr-editor").value); } catch { setAlert("scanner-alert", "The editor does not contain valid JSON."); return; } if (!payload || !Array.isArray(payload.pull_requests)) { setAlert("scanner-alert", "JSON must contain a pull_requests array."); return; } const button = $("#scan-button"); setBusy(button, true, "Scanning..."); setAlert("scanner-alert"); try { const result = await request("/api/scan", { method: "POST", body: JSON.stringify(payload) }); state.campaigns = Array.isArray(result.campaigns) ? result.campaigns : []; state.lastScan = true; renderCampaigns(state.campaigns); setAlert("scanner-alert", `Scan complete: ${text(result.total_prs, payload.pull_requests.length)} PRs analyzed.`, true); } catch (error) { setAlert("scanner-alert", error.message); } finally { setBusy(button, false); } }

function renderAssessment(result) { const container = $("#assessment-result"); container.className = "assessment-content"; container.replaceChildren(); $("#model-label").textContent = text(result.model, "Model unavailable"); const summary = document.createElement("p"); summary.className = "summary"; summary.textContent = text(result.summary, "No summary returned."); const meta = document.createElement("div"); meta.className = "assessment-meta"; const risk = document.createElement("span"); risk.className = "risk"; risk.textContent = `Risk: ${text(result.risk_level, "not returned")}`; const used = document.createElement("span"); used.textContent = result.used_model ? "Local model used" : "Model not used"; meta.append(risk, used); const block = document.createElement("section"); block.className = "evidence-block"; const title = document.createElement("h3"); title.textContent = "REFERENCED EVIDENCE"; block.append(title, safeList(result.evidence_referenced, "trusted")); container.append(summary, meta, block); }
async function analyzeCampaign() { const index = Number($("#campaign-select").value); const campaign = state.campaigns[index]; if (!campaign) return; const button = $("#analyze-button"); setBusy(button, true, "Analyzing..."); setAlert("assessment-alert", "Gemma is reviewing the campaign evidence..."); try { const result = await request("/api/explain", { method: "POST", body: JSON.stringify({ evidence: campaign }) }); renderAssessment(result); setAlert("assessment-alert", "Assessment returned by the local model.", true); } catch (error) { setAlert("assessment-alert", `AI assessment unavailable: ${error.message}`); } finally { setBusy(button, false); } }

async function refreshCurrent() { await checkHealth(); if (state.view === "overview" || state.view === "network") await loadNetwork(); }
$$("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
$("#review-form").addEventListener("submit", evaluateContributor); $("#sample-button").addEventListener("click", loadSamples); $("#scan-button").addEventListener("click", scanContributions); $("#analyze-button").addEventListener("click", analyzeCampaign); $("#refresh-button").addEventListener("click", refreshCurrent); $("#network-refresh").addEventListener("click", loadNetwork); $("#seed-button").addEventListener("click", async () => { const button = $("#seed-button"); setBusy(button, true, "Loading..."); setAlert("overview-alert"); try { const result = await request("/api/seed-demo", { method: "POST", body: JSON.stringify({}) }); setAlert("overview-alert", text(result.message, "Demo data ready."), true); await loadNetwork(); } catch (error) { setAlert("overview-alert", error.message); } finally { setBusy(button, false); } });
renderOverview(); updateHealth(false, "Checking the local TrustMesh service..."); refreshCurrent();
