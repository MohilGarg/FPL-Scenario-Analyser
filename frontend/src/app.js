const config = window.APP_CONFIG || {};
const apiBaseUrl = String(config.apiBaseUrl || "").replace(/\/$/, "");
const defaultLeagueId = String(config.defaultLeagueId || "");

const elements = {
  form: document.querySelector("#league-form"),
  input: document.querySelector("#league-id"),
  loading: document.querySelector("#loading-state"),
  loadingMessage: document.querySelector("#loading-message"),
  error: document.querySelector("#error-state"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry-button"),
  analysis: document.querySelector("#analysis"),
  refresh: document.querySelector("#refresh-button"),
  leagueName: document.querySelector("#league-name"),
  gameweek: document.querySelector("#gameweek-label"),
  updatedAt: document.querySelector("#updated-at"),
  lastName: document.querySelector("#last-place-title"),
  lastTeam: document.querySelector("#last-place-team"),
  lastScore: document.querySelector("#last-place-score"),
  lastNote: document.querySelector("#last-place-note"),
  riskCount: document.querySelector("#risk-count"),
  riskList: document.querySelector("#risk-list"),
  safeCount: document.querySelector("#safe-count"),
  safeList: document.querySelector("#safe-list"),
  differentialList: document.querySelector("#differential-list"),
  modelNote: document.querySelector("#model-note"),
};

let currentLeagueId = "";
let requestedScenarioCount = 3;
let coldStartTimer;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusLabel(manager) {
  if (manager.is_current_last) return "Currently last";
  if (manager.status === "can_finish_last") return "Can finish last";
  if (manager.status === "no_modelled_path") return "No route found";
  return "Still unresolved";
}

function setState(name) {
  elements.loading.classList.toggle("hidden", name !== "loading");
  elements.error.classList.toggle("hidden", name !== "error");
  elements.analysis.classList.toggle("hidden", name !== "analysis");
  const submit = elements.form.querySelector("button");
  submit.disabled = name === "loading";
}

function friendlyError(error) {
  if (error.name === "AbortError") {
    return "The data service took too long to respond. A free backend may need a minute to wake up—please retry.";
  }
  if (error instanceof TypeError) {
    return "The analysis service is unavailable. It may be starting up; wait a moment and try again.";
  }
  return error.message || "Something unexpected happened while loading FPL data.";
}

async function loadLeague(leagueId, options = {}) {
  const cleanId = String(leagueId).trim();
  if (!/^\d+$/.test(cleanId) || Number(cleanId) <= 0) {
    elements.errorMessage.textContent = "Enter the numeric classic league ID from your FPL standings URL.";
    setState("error");
    return;
  }
  if (!apiBaseUrl) {
    elements.errorMessage.textContent = "The website backend has not been configured yet. Set API_BASE_URL in the Pages build.";
    setState("error");
    return;
  }

  currentLeagueId = cleanId;
  requestedScenarioCount = options.scenarios || requestedScenarioCount;
  elements.input.value = cleanId;
  elements.loadingMessage.textContent = "Fetching current squads, points and fixtures from FPL.";
  setState("loading");
  clearTimeout(coldStartTimer);
  coldStartTimer = setTimeout(() => {
    elements.loadingMessage.textContent = "The free data service may be waking up. This can take about a minute on the first visit.";
  }, 7000);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch(
      `${apiBaseUrl}/api/league/${encodeURIComponent(cleanId)}?scenarios=${requestedScenarioCount}`,
      { signal: controller.signal, headers: { Accept: "application/json" } },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `The analysis service returned ${response.status}.`);
    }
    localStorage.setItem("fpl-last-place-league", cleanId);
    const url = new URL(window.location.href);
    url.searchParams.set("league", cleanId);
    history.replaceState({}, "", url);
    renderAnalysis(payload, options.openManager);
    setState("analysis");
  } catch (error) {
    elements.errorMessage.textContent = friendlyError(error);
    setState("error");
  } finally {
    clearTimeout(timeout);
    clearTimeout(coldStartTimer);
  }
}

function renderAnalysis(data, openManager) {
  const managersById = new Map(data.managers.map((manager) => [manager.entry_id, manager]));
  const lastManagers = data.summary.current_last_entry_ids.map((id) => managersById.get(id)).filter(Boolean);
  const primaryLast = lastManagers[0];
  const riskManagers = data.managers.filter((manager) => manager.status !== "safe");
  const safeManagers = data.managers.filter((manager) => manager.status === "safe");

  elements.leagueName.textContent = data.league.name;
  elements.gameweek.textContent = `Gameweek ${data.league.gameweek}`;
  elements.updatedAt.textContent = `Updated ${new Date(data.league.fetched_at).toLocaleString()}`;
  elements.lastName.textContent = lastManagers.map((manager) => manager.manager_name).join(" & ");
  elements.lastTeam.textContent = lastManagers.map((manager) => manager.team_name).join(" · ");
  elements.lastScore.textContent = primaryLast?.effective_score ?? "—";
  elements.lastNote.textContent = lastManagers.length > 1
    ? `${lastManagers.length} managers are tied at the bottom right now.`
    : primaryLast?.core_condition || "They are currently holding the forfeit position.";

  elements.riskCount.textContent = `${riskManagers.length} at risk`;
  elements.riskList.innerHTML = riskManagers.map((manager, index) => riskCard(manager, index, openManager)).join("");
  elements.safeCount.textContent = `${safeManagers.length} safe`;
  elements.safeList.innerHTML = safeManagers.length
    ? safeManagers.map(safeCard).join("")
    : '<div class="empty-note">Nobody can be safely ruled out yet.</div>';
  elements.differentialList.innerHTML = data.differentials.length
    ? data.differentials.map(differentialCard).join("")
    : '<div class="empty-note">No remaining effective differentials—the Gameweek may be complete.</div>';

  const model = data.model;
  elements.modelNote.textContent =
    `SAFE uses a practical ${model.minimum_remaining_player_contribution} to +${model.maximum_remaining_player_contribution} ` +
    "range for each player's total remaining Gameweek contribution, including Double Gameweeks. " +
    "It is deliberately conservative, not a literal theoretical maximum. Scenario order is a plausibility heuristic, not a probability.";

  document.querySelectorAll(".show-more").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "Finding more…";
      await loadLeague(currentLeagueId, {
        scenarios: Math.min(12, requestedScenarioCount + 3),
        openManager: Number(button.dataset.manager),
      });
    });
  });
}

function riskCard(manager, index, openManager) {
  const shouldOpen = Number(openManager) === manager.entry_id || (!openManager && index === 0);
  const scenarios = manager.scenarios.length
    ? manager.scenarios.map(scenarioCard).join("")
    : `<div class="empty-note">${manager.status === "no_modelled_path"
      ? "The bounded event vocabulary was exhausted without finding a route."
      : "No route was found within the current search width. This does not mean the manager is safe."}</div>`;
  const canLoadMore = manager.scenarios.length > 0 && !manager.search.exhausted && requestedScenarioCount < 12;
  const statusClass = manager.is_current_last ? "status-current" : "status-possible";
  return `
    <details class="manager-card" ${shouldOpen ? "open" : ""}>
      <summary>
        <div class="manager-identity">
          <span class="manager-rank">${index + 1}</span>
          <span class="manager-names">
            <strong>${escapeHtml(manager.manager_name)}</strong>
            <span>${escapeHtml(manager.team_name)}</span>
          </span>
        </div>
        <div class="manager-meta">
          <span class="status-pill ${statusClass}">${statusLabel(manager)}</span>
          <span class="manager-score">${manager.effective_score}<small>GW pts</small></span>
          <span class="chevron" aria-hidden="true">⌄</span>
        </div>
      </summary>
      <div class="manager-detail">
        <p class="condition"><strong>Core condition:</strong> ${escapeHtml(manager.core_condition || "No additional swing is currently needed.")}</p>
        <div class="scenario-list">${scenarios}</div>
        ${canLoadMore ? `<button class="show-more" type="button" data-manager="${manager.entry_id}">Show more scenarios</button>` : ""}
        ${manager.search.truncated_player_fixtures ? `<p class="search-note">${manager.search.truncated_player_fixtures} lower-impact player-fixture combinations sit outside the current search width, so the result remains conservative.</p>` : ""}
      </div>
    </details>`;
}

function scenarioCard(scenario) {
  return `
    <article class="scenario">
      <span class="scenario-number">${scenario.rank}</span>
      <div>
        <p>${escapeHtml(scenario.description)}</p>
        <small>Plausibility rank ${scenario.plausibility_cost}</small>
      </div>
      <span class="scenario-scores">${scenario.candidate_final_score} vs ${scenario.next_lowest_score}</span>
    </article>`;
}

function safeCard(manager) {
  const chip = manager.active_chip ? ` · ${escapeHtml(manager.active_chip)}` : "";
  return `
    <article class="safe-card" title="${escapeHtml(manager.safety_reason || "Safe within the configured bounds")}">
      <span class="safe-name">
        <strong>${escapeHtml(manager.manager_name)}</strong>
        <span>${escapeHtml(manager.team_name)}${chip}</span>
      </span>
      <span class="safe-score"><strong>${manager.effective_score}</strong><span class="safe-tick">✓</span></span>
    </article>`;
}

function differentialCard(player) {
  const fixtures = player.fixtures.map((fixture) => `${fixture.started ? "Live" : "vs"} ${escapeHtml(fixture.opponent)}`).join(" · ");
  const exposures = player.exposures
    .map((item) => `<span class="exposure">${escapeHtml(item.manager_name)} <b>×${item.multiplier}</b></span>`)
    .join("");
  return `
    <article class="differential-card">
      <div class="player-top">
        <strong>${escapeHtml(player.player_name)}</strong>
        <span class="position-pill">${escapeHtml(player.position)}</span>
      </div>
      <p class="fixture-line">${escapeHtml(player.team_name)} · ${fixtures}</p>
      <div class="exposure-list">${exposures}</div>
    </article>`;
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  requestedScenarioCount = 3;
  loadLeague(elements.input.value, { scenarios: 3 });
});
elements.retry.addEventListener("click", () => loadLeague(currentLeagueId || elements.input.value));
elements.refresh.addEventListener("click", () => loadLeague(currentLeagueId, { scenarios: requestedScenarioCount }));

const queryLeague = new URLSearchParams(window.location.search).get("league");
const rememberedLeague = localStorage.getItem("fpl-last-place-league");
const initialLeague = queryLeague || rememberedLeague || defaultLeagueId;
if (initialLeague) {
  elements.input.value = initialLeague;
  loadLeague(initialLeague, { scenarios: 3 });
}
