const config = window.APP_CONFIG || {};
const apiBaseUrl = String(config.apiBaseUrl || "").replace(/\/$/, "");
const defaultLeagueId = String(config.defaultLeagueId || "");
const VALID_VIEWS = new Set(["overview", "scenarios", "differentials", "managers"]);
const REFRESH_INTERVAL_MS = 90_000;

const elements = {
  landing: document.querySelector("#landing"),
  form: document.querySelector("#league-form"),
  input: document.querySelector("#league-id"),
  recent: document.querySelector("#recent-leagues"),
  recentList: document.querySelector("#recent-list"),
  loading: document.querySelector("#loading-state"),
  loadingMessage: document.querySelector("#loading-message"),
  error: document.querySelector("#error-state"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry-button"),
  analysis: document.querySelector("#analysis"),
  refresh: document.querySelector("#refresh-button"),
  changeLeague: document.querySelector("#change-league-button"),
  leagueName: document.querySelector("#league-name"),
  gameweek: document.querySelector("#gameweek-label"),
  gameweekStatus: document.querySelector("#gameweek-status"),
  updatedAt: document.querySelector("#updated-at"),
  demoLabel: document.querySelector("#demo-label"),
  demoSelect: document.querySelector("#demo-select"),
  gameweekSelect: document.querySelector("#gameweek-select"),
  differentialScope: document.querySelector("#differential-scope"),
  comparisonPanel: document.querySelector("#comparison-panel"),
  phaseNote: document.querySelector("#phase-note"),
  overview: document.querySelector("#overview-content"),
  scenarioManager: document.querySelector("#scenario-manager"),
  scenarioDetail: document.querySelector("#scenario-detail"),
  compareA: document.querySelector("#compare-a"),
  compareB: document.querySelector("#compare-b"),
  comparison: document.querySelector("#comparison-result"),
  differentials: document.querySelector("#differential-content"),
  managers: document.querySelector("#manager-list"),
  modelNote: document.querySelector("#model-note"),
  tieRule: document.querySelector("#tie-rule"),
  themeToggle: document.querySelector("#theme-toggle"),
  themeLabel: document.querySelector("#theme-label"),
  toast: document.querySelector("#toast"),
};

const query = new URLSearchParams(window.location.search);
const appState = {
  data: null,
  leagueId: "",
  demoMode: query.get("demo") || "",
  view: VALID_VIEWS.has(query.get("view")) ? query.get("view") : "overview",
  selectedManager: Number(query.get("manager")) || null,
  gameweek: /^\d+$/.test(query.get("gw") || "") ? Number(query.get("gw")) : null,
  generation: 0,
  managerRequests: new Map(),
  comparisonController: null,
  tieRule: query.get("ties") === "strict" ? "strict" : "include",
  requestController: null,
  refreshTimer: null,
  toastTimer: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function plural(value, word) {
  return `${value} ${word}${value === 1 ? "" : "s"}`;
}

function parseLeagueId(value) {
  return window.FPLInput.parseLeagueId(value);
}

function statusLabel(manager) {
  if (manager.is_current_last) return "Currently last";
  if (manager.is_bottom) return "Bottom tie";
  if (manager.status === "can_finish_last") return "At risk";
  if (manager.status === "safe") return "Safe";
  if (manager.status === "no_modelled_path") return "No modelled path";
  return "Unresolved";
}

function statusClass(manager) {
  if (manager.is_bottom) return "status-current";
  if (manager.status === "safe") return "status-safe";
  if (manager.status === "can_finish_last") return "status-risk";
  return "status-unresolved";
}

function setPageState(name) {
  elements.landing.classList.toggle("hidden", name !== "landing");
  elements.loading.classList.toggle("hidden", name !== "loading");
  elements.error.classList.toggle("hidden", name !== "error");
  elements.analysis.classList.toggle("hidden", name !== "analysis");
  document.body.classList.toggle("analysis-loaded", name === "analysis");
  const submit = elements.form.querySelector("button[type='submit']");
  submit.disabled = name === "loading";
}

function friendlyError(error) {
  if (error.name === "AbortError") {
    return "The analysis service took too long to respond. It may still be waking up; please try again.";
  }
  if (error instanceof TypeError) {
    return "The analysis service is unavailable. Wait a moment and try again.";
  }
  return error.message || "Something unexpected happened while loading FPL data.";
}

function responseError(status, detail) {
  if (status === 404) return "We could not find that public classic league. Check the ID or URL.";
  if (status === 403) return "That league is private or unavailable through the public FPL data.";
  if (status === 400) return detail || "That league cannot be analysed. Check the league ID.";
  if (status === 409) return typeof detail === "string" ? detail : "Data for this Gameweek is not available. Choose another Gameweek.";
  if (status === 422) return "Check the league ID, Gameweek and selected managers, then try again.";
  if (status >= 500) return "FPL data is temporarily unavailable. Please try again shortly.";
  return "The analysis service could not complete that request. Please try again.";
}

function endpoint(detail = "summary") {
  const path = appState.demoMode
    ? `/api/demo/${encodeURIComponent(appState.demoMode)}`
    : `/api/league/${encodeURIComponent(appState.leagueId)}`;
  return `${apiBaseUrl}${path}?detail=${detail}&scenarios=3&ties=${appState.tieRule}${appState.gameweek && !appState.demoMode ? `&gameweek=${appState.gameweek}` : ""}`;
}

async function loadAnalysis(options = {}) {
  if (!apiBaseUrl) {
    elements.errorMessage.textContent = "The website backend has not been configured.";
    setPageState("error");
    return;
  }
  if (!appState.demoMode && !appState.leagueId) {
    elements.errorMessage.textContent = "Enter a numeric league ID or paste an FPL standings URL.";
    setPageState("error");
    return;
  }

  clearTimeout(appState.refreshTimer);
  appState.requestController?.abort();
  appState.comparisonController?.abort();
  const controller = new AbortController();
  const generation = ++appState.generation;
  appState.requestController = controller;
  appState.managerRequests.clear();
  elements.retry.classList.remove("hidden");
  elements.refresh.disabled = true;
  elements.loadingMessage.textContent = appState.demoMode
    ? "Running the demo state through the analysis engine."
    : "Fetching current squads, points and fixtures from FPL.";
  if (!options.background) setPageState("loading");
  const coldStartTimer = setTimeout(() => {
    elements.loadingMessage.textContent = "Starting the analysis service… The first visit can take about a minute.";
  }, 7_000);
  const timeout = setTimeout(() => controller.abort(), 90_000);

  try {
    const response = await fetch(endpoint(), {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(responseError(response.status, payload.detail));
    if (generation !== appState.generation) return;
    appState.data = payload;
    if (!appState.demoMode) rememberLeague(appState.leagueId, payload.league.name);
    renderApplication();
    setPageState("analysis");
    updateUrl();
    scheduleRefresh();
    if (options.background) showToast("Analysis refreshed");
    if (payload.league.status !== "complete" && payload.summary.scenario_search_state !== "broad") {
      // Paint scores/safety first; then solve one candidate at a time, never every manager
      // with an enlarged search limit just because one person pressed Show more.
      hydrateScenarios(payload, generation);
    }
  } catch (error) {
    if (generation !== appState.generation) return;
    if (options.background && appState.data) {
      showToast("Refresh failed — showing the previous update");
      scheduleRefresh();
    } else {
      elements.errorMessage.textContent = friendlyError(error);
      setPageState("error");
    }
  } finally {
    clearTimeout(timeout);
    clearTimeout(coldStartTimer);
    if (generation === appState.generation) elements.refresh.disabled = false;
  }
}

function detailEndpoint(suffix) {
  const id = appState.data.league.id;
  const params = new URLSearchParams({ ties: appState.tieRule });
  if (appState.demoMode) params.set("demo", appState.demoMode);
  else if (appState.gameweek) params.set("gameweek", appState.gameweek);
  return `${apiBaseUrl}/api/league/${id}/${suffix}?${params}`;
}

async function hydrateScenarios(data, generation) {
  for (const manager of data.managers.filter((item) => item.status !== "safe")) {
    if (generation !== appState.generation) return;
    await loadManagerScenarios(manager.entry_id, 3, generation);
  }
}

async function loadManagerScenarios(entryId, count, generation = appState.generation) {
  if (appState.managerRequests.has(entryId)) return appState.managerRequests.get(entryId);
  const work = (async () => {
    const controller = new AbortController();
    const parentSignal = appState.requestController?.signal;
    const cancel = () => controller.abort();
    parentSignal?.addEventListener("abort", cancel, { once: true });
    const timeout = setTimeout(cancel, 60_000);
    try {
      const response = await fetch(`${detailEndpoint(`managers/${entryId}/scenarios`)}&scenarios=${count}`, { signal: controller.signal });
      const payload = await response.json();
      if (!response.ok) throw new Error(responseError(response.status, payload.detail));
      if (generation !== appState.generation) return;
      if (payload.league.fetched_at !== appState.data.league.fetched_at) {
        loadAnalysis({ background: true });
        return;
      }
      const index = appState.data.managers.findIndex((item) => item.entry_id === entryId);
      payload.manager.search.requested_count = count;
      appState.data.managers[index] = payload.manager;
      renderApplication();
    } catch (error) {
      if (generation !== appState.generation) return;
      const manager = appState.data.managers.find((item) => item.entry_id === entryId);
      manager.search.error = true;
      renderApplication();
      showToast("Scenario search unavailable. Scores and safety are still shown; retry below.");
    } finally {
      clearTimeout(timeout);
      parentSignal?.removeEventListener("abort", cancel);
      if (generation === appState.generation) appState.managerRequests.delete(entryId);
    }
  })();
  appState.managerRequests.set(entryId, work);
  return work;
}

function renderApplication() {
  const expanded = new Map([...document.querySelectorAll("details[data-card]")].map((card) => [card.dataset.card, card.open]));
  const data = appState.data;
  const statusText = {
    upcoming: "Upcoming",
    in_progress: data.league.fixtures.live ? "Live" : "In progress",
    complete: "Complete",
  }[data.league.status];
  elements.leagueName.textContent = data.league.name;
  elements.gameweek.textContent = `Gameweek ${data.league.gameweek} · ${statusText}`;
  elements.gameweekStatus.className = `status-dot ${data.league.fixtures.live ? "live" : data.league.status}`;
  elements.updatedAt.textContent = `Last updated: ${new Date(data.league.fetched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  elements.demoLabel.classList.toggle("hidden", !data.demo?.active);
  elements.demoSelect.value = appState.demoMode;
  elements.tieRule.value = appState.tieRule;
  elements.gameweekSelect.innerHTML = (data.league.available_gameweeks || [{ id: data.league.gameweek }]).slice().reverse().map((gw) => `<option value="${gw.id}">GW ${gw.id}${gw.id === data.league.current_gameweek ? " · Current" : " · Final"}</option>`).join("");
  elements.gameweekSelect.value = String(data.league.gameweek);
  elements.gameweekSelect.disabled = Boolean(appState.demoMode);
  elements.scenarioManager.closest("label").classList.toggle("hidden", data.league.status === "complete");

  renderPhaseNote(data);
  renderOverview(data);
  renderScenarioView(data);
  renderDifferentials(data);
  renderManagers(data);
  renderModelNote(data);
  activateView(appState.view, false);
  bindDynamicControls();
  document.querySelectorAll("details[data-card]").forEach((card) => { if (expanded.has(card.dataset.card)) card.open = expanded.get(card.dataset.card); });
}

function renderPhaseNote(data) {
  const messages = {
    early: "This is a broad early-Gameweek state. Remaining effective differentials are more useful than exhaustive scenario examples right now.",
    late: "The remaining fixture set is narrow enough for focused last-place scenarios.",
    live: data.demo?.active ? "Demo Mode: a fixed live-match example, not live scores." : `A match is live. Updates every ${Math.max(90, data.cache?.ttl_seconds || 90)} seconds while this tab is visible.`,
    complete: "This Gameweek is complete. Final bottom standings are shown and remaining scenarios are suppressed.",
  };
  elements.phaseNote.textContent = data.league.history_note || messages[data.league.phase];
}

function renderOverview(data) {
  if (data.league.status === "complete") {
    elements.overview.innerHTML = completedOverview(data);
    return;
  }
  const managers = managerMap(data);
  const bottom = data.summary.bottom_entry_ids.map((id) => managers.get(id)).filter(Boolean);
  const primary = bottom[0];
  const atRisk = data.managers.filter((manager) => manager.status !== "safe");
  const safe = data.managers.filter((manager) => manager.status === "safe");
  const names = bottom.map((manager) => manager.manager_name).join(" & ");
  const teams = bottom.map((manager) => manager.team_name).join(" · ");
  const upcomingLevel = data.league.status === "upcoming" && bottom.length === data.managers.length;
  elements.overview.innerHTML = `
    <article class="last-card">
      <span class="card-label">${upcomingLevel ? "Gameweek yet to start" : bottom.length > 1 ? "Currently tied at the bottom" : "Currently last"}</span>
      <div class="last-card-main">
        <div><h2>${upcomingLevel ? "All level before kick-off" : escapeHtml(names)}</h2><p class="team-name">${upcomingLevel ? "Use Differentials to see what can separate the bottom group." : escapeHtml(teams)}</p></div>
        <div class="score-block"><strong>${primary?.effective_score ?? "—"}</strong><span>effective GW points</span></div>
      </div>
      <div class="last-stats">
        ${upcomingLevel ? "" : bottom.map((manager) => `<span class="stat-chip">${bottom.length > 1 ? `${escapeHtml(manager.manager_name)}: ` : ""}${plural(manager.remaining_player_count, "relevant player")} remaining${manager.transfer_cost ? ` · −${manager.transfer_cost} hit` : ""}</span>`).join("")}
        <span class="stat-chip">${data.model.ties_count_as_last ? "A bottom tie counts" : "Strictly lowest only"}</span>
      </div>
    </article>
    <section class="section-block" aria-labelledby="risk-heading">
      <div class="section-header"><div><h2 id="risk-heading">Still at risk</h2><p>Open a manager for their core condition and example routes to last.</p></div><span class="count-badge">${atRisk.length}</span></div>
      <div class="risk-grid">${atRisk.map((manager, index) => overviewRiskCard(manager, index, data)).join("")}</div>
    </section>
    ${safeOverview(safe)}
  `;
}

function completedOverview(data) {
  const managers = managerMap(data);
  const bottom = data.summary.bottom_entry_ids.map((id) => managers.get(id)).filter(Boolean);
  const strictTie = !data.model.ties_count_as_last && bottom.length > 1;
  const names = bottom.map((manager) => manager.manager_name).join(" & ");
  const score = bottom[0]?.effective_score ?? "—";
  const headline = strictTie ? "No sole last-place manager" : `${names} ${bottom.length > 1 ? "finish" : "finishes"} last`;
  return `
    <article class="completed-card">
      <span class="card-label">Gameweek ${data.league.gameweek} complete</span>
      <h2>${escapeHtml(headline)}</h2>
      <p class="team-name">${strictTie ? `${bottom.length} managers share the lowest score under the strict-last rule.` : `${score} effective points`}</p>
      <table class="bottom-table">
        <thead><tr><th>Bottom standings</th><th>Team</th><th>Points</th></tr></thead>
        <tbody>${data.managers.slice(0, 5).map((manager) => `<tr><td>${escapeHtml(manager.manager_name)}</td><td>${escapeHtml(manager.team_name)}</td><td>${manager.effective_score}</td></tr>`).join("")}</tbody>
      </table>
      <p class="fine-print">Open Managers for squads, hits and chips, or choose another Gameweek above.</p>
    </article>`;
}

function overviewRiskCard(manager, index, data) {
  const scenarios = manager.scenarios;
  let scenarioContent;
  if (scenarios.length) {
    scenarioContent = `<div class="scenario-list">${scenarios.map((scenario) => scenarioCard(scenario, manager)).join("")}</div>`;
  } else if (data.summary.scenario_search_state === "broad") {
    scenarioContent = emptyState("Too many routes to summarise yet", "Use Differentials now; focused scenarios will become useful as fixtures finish.");
  } else if (manager.status === "no_modelled_path") {
    scenarioContent = emptyState("No modelled path found", "The bounded event vocabulary was exhausted. This is not the same as SAFE.");
  } else {
    scenarioContent = pendingScenarioState(manager);
  }
  const canLoadMore = manager.search.error || (scenarios.length && !manager.search.exhausted && (manager.search.requested_count || 3) < 12);
  return `
    <details class="risk-card" data-card="risk-${manager.entry_id}" ${index === 0 && data.league.phase !== "early" ? "open" : ""}>
      <summary>
        <span class="manager-identity"><strong>${escapeHtml(manager.manager_name)}</strong><span>${escapeHtml(manager.team_name)} · ${plural(manager.remaining_player_count, "player")} remaining</span></span>
        <span class="card-meta"><span class="status-badge ${statusClass(manager)}">${statusLabel(manager)}</span><span class="points">${manager.effective_score}<small>GW pts</small></span></span>
      </summary>
      <div class="risk-body">
        <div class="risk-facts"><span class="stat-chip">${manager.bottom_gap ? `+${manager.bottom_gap} from bottom` : "At the bottom"}</span>${manager.transfer_cost ? `<span class="stat-chip">−${manager.transfer_cost} hit</span>` : ""}</div>
        ${conditionBox(manager)}
        ${scenarioContent}
        ${canLoadMore ? `<button class="show-more" type="button" data-manager="${manager.entry_id}">${manager.search.error ? "Retry scenarios" : "Show more scenarios"}</button>` : ""}
        ${manager.scenarios.length ? '<p class="fine-print">These are examples, not an exhaustive list of every valid route.</p>' : ""}
      </div>
    </details>`;
}

function safeOverview(safe) {
  if (!safe.length) return `<section class="section-block">${emptyState("Nobody is safely clear yet", "The practical remaining-points envelope still overlaps for every manager.")}</section>`;
  return `
    <details class="safe-section" data-card="safe">
      <summary><span>Safe from the forfeit</span><span class="count-badge">${safe.length}</span></summary>
      <div class="safe-grid">${safe.map((manager) => `
        <article class="safe-card">
          <div class="safe-card-header"><strong>${escapeHtml(manager.manager_name)}</strong><span>${manager.effective_score} pts</span></div>
          <details data-card="safe-${manager.entry_id}"><summary>Why safe?</summary><p>${escapeHtml(manager.safety_detail || manager.safety_reason)}</p>
          ${manager.safety_explanation ? `<p>Current: ${manager.effective_score}. Worst-case remaining change: ${manager.safety_explanation.worst_remaining_change}. Minimum final score: ${manager.bounds.lower}. This uses the practical −10/+35 envelope, not a theoretical limit.</p>` : ""}</details>
        </article>`).join("")}</div>
    </details>`;
}

function renderScenarioView(data) {
  const candidates = data.managers.filter((manager) => manager.status !== "safe");
  if (data.league.status === "complete") {
    elements.scenarioManager.innerHTML = "";
    elements.scenarioDetail.innerHTML = emptyState("Final results only", "This Gameweek is complete. See Overview for the final bottom standings and Managers for the score breakdown.");
    populateComparison(data);
    return;
  }
  if (!candidates.length) {
    elements.scenarioManager.innerHTML = "";
    elements.scenarioDetail.innerHTML = emptyState("Everyone is resolved", "No manager remains in the scenario search.");
    populateComparison(data);
    return;
  }
  if (!candidates.some((manager) => manager.entry_id === appState.selectedManager)) {
    appState.selectedManager = candidates[0].entry_id;
  }
  elements.scenarioManager.innerHTML = candidates.map((manager) => `<option value="${manager.entry_id}">${escapeHtml(manager.manager_name)} — ${manager.effective_score} pts</option>`).join("");
  elements.scenarioManager.value = String(appState.selectedManager);
  const selected = candidates.find((manager) => manager.entry_id === appState.selectedManager);
  const scenarios = selected.scenarios;
  let list;
  if (scenarios.length) {
    list = `<div class="scenario-list">${scenarios.map((scenario) => scenarioCard(scenario, selected)).join("")}</div>`;
  } else if (data.summary.scenario_search_state === "broad") {
    list = emptyState("Scenario space is still broad", "Use the Differentials view until more fixtures have finished.");
  } else if (selected.status === "no_modelled_path") {
    list = emptyState("No modelled path", "The bounded vocabulary found no route. The manager is not marked SAFE.");
  } else {
    list = pendingScenarioState(selected);
  }
  elements.scenarioDetail.innerHTML = `
    <div class="scenario-summary">
      <div class="summary-cell"><span>Current points</span><strong>${selected.effective_score}</strong></div>
      <div class="summary-cell"><span>Gap to bottom</span><strong>${selected.bottom_gap ? `+${selected.bottom_gap}` : "At bottom"}</strong></div>
      <div class="summary-cell"><span>Players remaining</span><strong>${selected.remaining_player_count}</strong></div>
      <div class="summary-cell"><span>Examples shown</span><strong>${scenarios.length}</strong></div>
    </div>
    ${conditionBox(selected)}
    ${list}
    ${selected.search.error || (scenarios.length && !selected.search.exhausted && (selected.search.requested_count || 3) < 12) ? `<button class="show-more" type="button" data-manager="${selected.entry_id}">${selected.search.error ? "Retry scenarios" : "Show more scenarios"}</button>` : ""}
    ${scenarios.length ? `<p class="fine-print">Other valid routes may exist. ${escapeHtml(data.model.scenario_vocabulary_note)}</p>` : ""}
  `;
  populateComparison(data);
}

function conditionBox(manager) {
  return manager.core_condition ? `<div class="condition-box"><small>Core condition</small>${escapeHtml(manager.core_condition)}</div>` : "";
}

function pendingScenarioState(manager) {
  if (manager.search.error) return emptyState("Scenarios unavailable", "Scores remain available. Retry the scenario request below.");
  if (!manager.search.loaded) return emptyState("Checking scenario routes…", "Scores and practical safety checks are ready. Examples load next.");
  return emptyState("Unresolved", "No concise route was found within the bounded search. This does not mean SAFE.");
}

function scenarioCard(scenario, manager) {
  const scores = scenario.bottom_scores.map((item) => `${escapeHtml(item.manager_name)} ${item.score}`).join(" · ");
  return `
    <article class="scenario-card">
      <div class="scenario-top"><strong>Scenario ${scenario.rank}</strong><span class="plausibility">${escapeHtml(scenario.plausibility)}</span></div>
      <p>${escapeHtml(scenario.description)}</p>
      <div class="scenario-footer"><span>Bottom scores: ${scores}</span><button class="copy-button" type="button" data-copy-manager="${manager.entry_id}" data-copy-rank="${scenario.rank}">Copy</button></div>
    </article>`;
}

function populateComparison(data) {
  const options = data.managers.map((manager) => `<option value="${manager.entry_id}">${escapeHtml(manager.manager_name)}</option>`).join("");
  const ids = new Set(data.managers.map((manager) => manager.entry_id));
  const previousA = ids.has(Number(elements.compareA.value)) ? Number(elements.compareA.value) : appState.selectedManager || data.managers[0]?.entry_id;
  const previousB = ids.has(Number(elements.compareB.value)) ? Number(elements.compareB.value) : data.managers.find((manager) => manager.entry_id !== previousA)?.entry_id;
  elements.compareA.innerHTML = options;
  elements.compareB.innerHTML = options;
  elements.compareA.value = String(previousA || "");
  elements.compareB.value = String(previousB || "");
  renderComparison(data);
}

async function renderComparison(data) {
  if (!elements.comparisonPanel.open) return;
  const managers = managerMap(data);
  const a = managers.get(Number(elements.compareA.value));
  const b = managers.get(Number(elements.compareB.value));
  if (!a || !b || a.entry_id === b.entry_id) {
    appState.comparisonController?.abort();
    appState.comparisonController = null;
    delete elements.comparison.dataset.key;
    elements.comparison.innerHTML = "Choose two different managers.";
    elements.comparison.className = "comparison-result";
    return;
  }
  const key = `${appState.generation}:${a.entry_id}:${b.entry_id}`;
  if (elements.comparison.dataset.key === key) return;
  elements.comparison.dataset.key = key;
  appState.comparisonController?.abort();
  const controller = new AbortController();
  appState.comparisonController = controller;
  const timeout = setTimeout(() => controller.abort(), 60_000);
  elements.comparison.className = "comparison-result";
  elements.comparison.textContent = "Comparing remaining effective exposure…";
  try {
    const response = await fetch(`${detailEndpoint("compare")}&a=${a.entry_id}&b=${b.entry_id}`, { signal: controller.signal });
    const comparison = await response.json();
    if (!response.ok) throw new Error(responseError(response.status, comparison.detail));
    if (appState.comparisonController !== controller) return;
    elements.comparison.innerHTML = `
      <strong>${escapeHtml(comparison.position)}</strong>
      <p>${escapeHtml(comparison.a.name)}: ${comparison.a.score} pts · ${escapeHtml(comparison.b.name)}: ${comparison.b.score} pts</p>
      <p>${escapeHtml(comparison.condition)}</p>
      ${comparison.shared_players.length ? `<p>Cancelling shared exposure: ${comparison.shared_players.map((item) => `${escapeHtml(item.player_name)} (${item.a_multiplier}× each)`).join(", ")}.</p>` : ""}
      ${comparison.differentials.length ? `<ul>${comparison.differentials.map((item) => `<li>${escapeHtml(item.player_name)}: ${item.a_multiplier}× for ${escapeHtml(comparison.a.name)}, ${item.b_multiplier}× for ${escapeHtml(comparison.b.name)}.</li>`).join("")}</ul>` : ""}
      <p class="fine-print">${escapeHtml(comparison.scope_note)}</p>`;
  } catch (error) {
    if (appState.comparisonController !== controller) return;
    elements.comparison.textContent = friendlyError(error);
    delete elements.comparison.dataset.key;
  } finally { clearTimeout(timeout); }
}

function renderDifferentials(data) {
  const all = elements.differentialScope.value === "all";
  const players = all ? data.all_differentials || data.differentials : data.differentials;
  if (!players.length) {
    elements.differentials.innerHTML = emptyState("No useful remaining differentials", data.league.status === "complete" ? "All fixtures are complete." : "Remaining exposure is identical across the relevant managers.");
    return;
  }
  const managers = managerMap(data);
  const ids = all ? data.managers.map((manager) => manager.entry_id) : data.differential_manager_ids;
  elements.differentials.innerHTML = `
    <div class="table-wrap"><table class="differential-table">
      <thead><tr><th>Player</th><th>Fixture / state</th><th>Now</th>${ids.map((id) => `<th>${escapeHtml(managers.get(id)?.manager_name || id)}</th>`).join("")}</tr></thead>
      <tbody>${players.map((player) => {
        const exposure = new Map(player.exposures.map((item) => [item.entry_id, item]));
        const fixtures = player.fixtures.map((fixture) => `${fixture.finished ? "Finished vs" : fixture.started ? "Live vs" : "Not started vs"} ${fixture.opponent}`).join(" · ");
        return `<tr>
          <td class="player-cell"><strong>${escapeHtml(player.player_name)}</strong><span>${escapeHtml(player.team_name)} · ${player.position}</span></td>
          <td><span class="fixture-copy ${player.fixture_status === "live" ? "fixture-live" : ""}">${escapeHtml(fixtures)}${player.has_double_gameweek_remaining ? " · DGW" : ""}</span></td>
          <td>${player.current_points} pts<br><span class="fixture-copy">${player.minutes} min</span></td>
          ${ids.map((id) => { const item = exposure.get(id); const value = item?.multiplier || 0; return `<td><span class="multiplier ${value ? "active" : ""}">${value}×</span>${item?.note ? `<br><span class="fixture-copy">${escapeHtml(item.note)}</span>` : ""}</td>`; }).join("")}
        </tr>`;
      }).join("")}</tbody>
    </table></div><p class="fine-print">Multipliers show the current effective lineup. Pending starters, legal autosubs and vice-captain takeover can change exposure; inspect Managers for those situations.</p>`;
}

function renderManagers(data) {
  elements.managers.innerHTML = data.managers.map((manager) => `
    <details class="manager-card" data-card="manager-${manager.entry_id}">
      <summary>
        <span class="manager-identity"><strong>${escapeHtml(manager.manager_name)}</strong><span>${escapeHtml(manager.team_name)} · ${plural(manager.remaining_player_count, "player")} remaining</span></span>
        <span class="card-meta"><span class="status-badge ${statusClass(manager)}">${statusLabel(manager)}</span><span class="points">${manager.effective_score}<small>effective</small></span></span>
      </summary>
      <div class="manager-body">
        <div class="manager-facts">
          <div class="manager-fact"><span>Counted points before hits</span><strong>${manager.raw_official_points}</strong></div>
          <div class="manager-fact"><span>Transfer deduction</span><strong>${manager.transfer_cost ? `−${manager.transfer_cost}` : "None"}</strong></div>
          <div class="manager-fact"><span>Captain</span><strong>${escapeHtml(manager.captain || "—")}</strong></div>
          <div class="manager-fact"><span>Vice-captain</span><strong>${escapeHtml(manager.vice_captain || "—")}</strong></div>
        </div>
        ${manager.active_chip ? `<p class="fine-print">Active chip: ${escapeHtml(manager.active_chip)}</p>` : ""}
        ${manager.official_points !== manager.raw_official_points ? `<p class="fine-print">FPL-reported GW points before hits: ${manager.official_points}. The live effective score can differ while substitutions and points settle.</p>` : ""}
        ${manager.score_source === "official_final" ? '<p class="fine-print">Historical result uses FPL’s final Gameweek points and official pick multipliers, minus this Gameweek’s transfer cost.</p>' : ""}
        <p class="fine-print">${(manager.score_notes || []).map(escapeHtml).join(" ")}</p>
        <div class="table-wrap"><table class="squad-table">
          <thead><tr><th>Player</th><th>Role</th><th>Multiplier</th><th>Points</th><th>Minutes</th><th>Fixture state</th></tr></thead>
          <tbody>${manager.squad.map(squadRow).join("")}</tbody>
        </table></div>
      </div>
    </details>`).join("");
}

function squadRow(player) {
  const flags = [player.is_captain ? "C" : "", player.is_vice_captain ? "V" : ""].filter(Boolean).join(" · ");
  const role = player.selection === "starter" ? "Starter" : `Bench ${player.bench_order}`;
  const remaining = player.remaining_fixtures.map((fixture) => `${fixture.started ? "live vs" : "vs"} ${fixture.opponent}`).join(" · ");
  const state = remaining || (player.fixture_status === "blank" ? "No fixture" : "Finished");
  const autosub = {
    subbed_in: "Subbed in",
    subbed_out: "Subbed out",
    possible: "Autosub possible",
    pending: "Pending",
    bench_boost: "Bench Boost",
  }[player.autosub_status] || "";
  return `<tr>
    <td class="player-cell"><strong>${escapeHtml(player.player_name)} ${flags ? `<span class="player-flags">${flags}</span>` : ""}</strong><span>${escapeHtml(player.team_name)} · ${player.position}</span></td>
    <td>${role}${autosub ? `<br><span class="autosub-note">${autosub}</span>` : ""}${player.captaincy_status ? `<br><span class="autosub-note">${escapeHtml(player.captaincy_status)}</span>` : ""}${player.conditional_exposure ? `<br><span class="autosub-note">${escapeHtml(player.conditional_exposure)}</span>` : ""}</td>
    <td>${player.effective_multiplier}×</td><td>${player.official_points ?? "Unavailable"}</td><td>${player.minutes ?? "Unavailable"}</td><td>${escapeHtml(state)}</td>
  </tr>`;
}

function renderModelNote(data) {
  elements.modelNote.textContent = `SAFE uses a practical ${data.model.minimum_remaining_player_contribution} to +${data.model.maximum_remaining_player_contribution} range for each player’s total contribution across the rest of this Gameweek, including a Double Gameweek. These are conservative pruning settings, not theoretical football limits. Scenario order is a plausibility heuristic, never a probability.`;
}

function managerMap(data) {
  return new Map(data.managers.map((manager) => [manager.entry_id, manager]));
}

function emptyState(title, copy) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong>${escapeHtml(copy)}</div>`;
}

function activateView(view, update = true) {
  appState.view = VALID_VIEWS.has(view) ? view : "overview";
  document.querySelectorAll("[role='tab']").forEach((tab) => {
    const active = tab.dataset.view === appState.view;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `view-${appState.view}`);
  });
  if (update) updateUrl();
}

function bindDynamicControls() {
  document.querySelectorAll(".show-more").forEach((button) => {
    button.onclick = () => {
      appState.selectedManager = Number(button.dataset.manager);
      const manager = appState.data.managers.find((item) => item.entry_id === appState.selectedManager);
      const count = Math.min(12, (manager.search.requested_count || 3) + (manager.search.error ? 0 : 3));
      button.disabled = true;
      button.textContent = "Finding more…";
      loadManagerScenarios(manager.entry_id, count);
    };
  });
  document.querySelectorAll(".copy-button").forEach((button) => {
    button.onclick = () => copyScenario(button);
  });
}

async function copyScenario(button) {
  const manager = appState.data.managers.find((item) => item.entry_id === Number(button.dataset.copyManager));
  const scenario = manager?.scenarios.find((item) => item.rank === Number(button.dataset.copyRank));
  if (!scenario) return;
  try {
    await navigator.clipboard.writeText(scenario.share_text);
    showToast("Scenario copied for your group chat");
  } catch {
    showToast("Copy was blocked by the browser");
  }
}

function openDemo(mode) {
  appState.demoMode = mode;
  appState.leagueId = "";
  appState.selectedManager = null;
  appState.gameweek = null;
  appState.view = "overview";
  document.querySelector(".settings-menu").open = false;
  loadAnalysis();
}

function updateUrl() {
  const url = new URL(window.location.href);
  if (appState.demoMode) {
    url.searchParams.set("demo", appState.demoMode);
    url.searchParams.delete("league");
  } else {
    url.searchParams.delete("demo");
    if (appState.leagueId) url.searchParams.set("league", appState.leagueId);
  }
  url.searchParams.set("view", appState.view);
  if (appState.selectedManager && appState.view === "scenarios") url.searchParams.set("manager", appState.selectedManager);
  else url.searchParams.delete("manager");
  if (appState.tieRule === "strict") url.searchParams.set("ties", "strict");
  else url.searchParams.delete("ties");
  if (appState.gameweek && !appState.demoMode) url.searchParams.set("gw", appState.gameweek);
  else url.searchParams.delete("gw");
  history.replaceState({}, "", url);
}

function rememberLeague(id, name) {
  const recent = readRecent().filter((item) => item.id !== id);
  recent.unshift({ id, name });
  window.FPLInput.write("fpl-recent-leagues", JSON.stringify(recent.slice(0, 4)));
  window.FPLInput.write("fpl-last-place-league", id);
  renderRecent();
}

function readRecent() {
  try {
    const value = JSON.parse(window.FPLInput.read("fpl-recent-leagues") || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function renderRecent() {
  const recent = readRecent();
  elements.recent.classList.toggle("hidden", !recent.length);
  elements.recentList.innerHTML = recent.map((item) => `<button class="recent-button" type="button" data-league="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</button>`).join("");
  elements.recentList.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      appState.demoMode = "";
      appState.gameweek = null;
      appState.selectedManager = null;
      appState.view = "overview";
      appState.leagueId = button.dataset.league;
      elements.input.value = appState.leagueId;
      loadAnalysis();
    });
  });
}

function scheduleRefresh() {
  clearTimeout(appState.refreshTimer);
  if (appState.demoMode || !appState.data?.league.fixtures.live || document.hidden) return;
  appState.refreshTimer = setTimeout(() => loadAnalysis({ background: true }), Math.max(REFRESH_INTERVAL_MS, (appState.data.cache?.ttl_seconds || 90) * 1000));
}

function showToast(message) {
  clearTimeout(appState.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  appState.toastTimer = setTimeout(() => elements.toast.classList.add("hidden"), 3_000);
}

function setTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  window.FPLInput.write("fpl-theme", dark ? "dark" : "light");
  elements.themeLabel.textContent = dark ? "Light mode" : "Dark mode";
  elements.themeToggle.setAttribute("aria-label", dark ? "Use light mode" : "Use dark mode");
  document.querySelector('meta[name="theme-color"]').content = dark ? "#11151c" : "#f4f6f8";
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const leagueId = parseLeagueId(elements.input.value);
  if (!leagueId) {
    elements.errorMessage.textContent = "Enter a numeric classic league ID or paste its FPL standings URL.";
    setPageState("error");
    elements.retry.classList.add("hidden");
    return;
  }
  appState.demoMode = "";
  appState.leagueId = leagueId;
  appState.selectedManager = null;
  appState.gameweek = null;
  appState.view = "overview";
  elements.input.value = leagueId;
  loadAnalysis();
});
elements.retry.addEventListener("click", () => {
  if (appState.leagueId || appState.demoMode) loadAnalysis();
  else setPageState("landing");
});
elements.refresh.addEventListener("click", () => loadAnalysis({ background: true }));
function changeLeague() {
  clearTimeout(appState.refreshTimer);
  appState.requestController?.abort();
  ++appState.generation;
  setPageState("landing");
  elements.input.focus();
}
elements.changeLeague.addEventListener("click", changeLeague);
document.querySelector("#error-change-league").addEventListener("click", changeLeague);
document.querySelector("#current-gameweek-button").addEventListener("click", () => { appState.gameweek = null; loadAnalysis(); });
elements.demoSelect.addEventListener("change", () => {
  if (elements.demoSelect.value) openDemo(elements.demoSelect.value);
  else {
    appState.demoMode = "";
    appState.leagueId = parseLeagueId(window.FPLInput.read("fpl-last-place-league")) || defaultLeagueId;
    appState.gameweek = null;
    loadAnalysis();
  }
});
elements.gameweekSelect.addEventListener("change", () => {
  const chosen = Number(elements.gameweekSelect.value);
  appState.gameweek = chosen === appState.data.league.current_gameweek ? null : chosen;
  appState.selectedManager = null;
  appState.view = "overview";
  loadAnalysis();
});
elements.differentialScope.addEventListener("change", () => renderDifferentials(appState.data));
elements.comparisonPanel.addEventListener("toggle", () => { if (appState.data) renderComparison(appState.data); });
elements.scenarioManager.addEventListener("change", () => {
  appState.selectedManager = Number(elements.scenarioManager.value);
  renderScenarioView(appState.data);
  bindDynamicControls();
  updateUrl();
});
elements.compareA.addEventListener("change", () => renderComparison(appState.data));
elements.compareB.addEventListener("change", () => renderComparison(appState.data));
elements.tieRule.addEventListener("change", () => {
  appState.tieRule = elements.tieRule.value;
  if (appState.data) loadAnalysis();
});
elements.themeToggle.addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
document.querySelectorAll("[role='tab']").forEach((tab) => {
  tab.addEventListener("click", () => activateView(tab.dataset.view));
  tab.addEventListener("keydown", (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[role='tab']")];
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(tabs.indexOf(tab) + direction + tabs.length) % tabs.length];
    next.focus();
    activateView(next.dataset.view);
  });
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(appState.refreshTimer);
  else scheduleRefresh();
});

setTheme(document.documentElement.dataset.theme);
elements.tieRule.value = appState.tieRule;
renderRecent();

const queryLeague = parseLeagueId(query.get("league"));
const rememberedLeague = parseLeagueId(window.FPLInput.read("fpl-last-place-league"));
if (query.has("league") && !queryLeague && !appState.demoMode) {
  elements.errorMessage.textContent = "The shared link contains an invalid league ID. Choose Change league and enter a numeric ID or FPL classic league URL.";
  elements.retry.classList.add("hidden");
  setPageState("error");
} else if (appState.demoMode) {
  loadAnalysis();
} else {
  appState.leagueId = queryLeague || rememberedLeague || defaultLeagueId;
  if (appState.leagueId) {
    elements.input.value = appState.leagueId;
    loadAnalysis();
  } else {
    setPageState("landing");
  }
}
