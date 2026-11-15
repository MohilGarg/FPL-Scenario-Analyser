/* Presentation only. Scores, safety and exposures all come from the Python API. */
const config = window.APP_CONFIG || {};
const apiBaseUrl = String(config.apiBaseUrl || "").replace(/\/$/, "");
const REFRESH_INTERVAL_MS = 90_000;
const SEPARATOR_PREVIEW = 12;

for (const link of document.querySelectorAll("#assistant-link, #assistant-tab")) {
  if (apiBaseUrl) link.href = `${apiBaseUrl}/assistant/`;
  else link.addEventListener("click", (event) => { event.preventDefault(); showToast("The private backend is not configured."); });
}

const $ = (selector) => document.querySelector(selector);
const elements = {
  landing: $("#landing"),
  form: $("#league-form"),
  input: $("#league-id"),
  recent: $("#recent-leagues"),
  recentList: $("#recent-list"),
  loading: $("#loading-state"),
  loadingMessage: $("#loading-message"),
  error: $("#error-state"),
  errorMessage: $("#error-message"),
  retry: $("#retry-button"),
  analysis: $("#analysis"),
  refresh: $("#refresh-button"),
  changeLeague: $("#change-league-button"),
  leagueName: $("#league-name"),
  gameweek: $("#gameweek-label"),
  gameweekStatus: $("#gameweek-status"),
  updatedAt: $("#updated-at"),
  gameweekSelect: $("#gameweek-select"),
  live: $("#live-content"),
  tieRule: $("#tie-rule"),
  themeToggle: $("#theme-toggle"),
  themeLabel: $("#theme-label"),
  toast: $("#toast"),
};

const query = new URLSearchParams(window.location.search);
// Older shared links used view=overview/scenarios/... and view=analytics.
const requestedView = query.get("view");
const appState = {
  data: null,
  leagueId: "",
  view: requestedView === "season" || requestedView === "analytics" ? "season" : "live",
  gameweek: /^\d+$/.test(query.get("gw") || "") ? Number(query.get("gw")) : null,
  tieRule: query.get("ties") === "strict" ? "strict" : "include",
  generation: 0,
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

function emptyState(title, copy) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong>${escapeHtml(copy)}</div>`;
}

function setPageState(name) {
  elements.landing.classList.toggle("hidden", name !== "landing");
  elements.loading.classList.toggle("hidden", name !== "loading");
  elements.error.classList.toggle("hidden", name !== "error");
  elements.analysis.classList.toggle("hidden", name !== "analysis");
  document.body.classList.toggle("analysis-loaded", name === "analysis");
  elements.form.querySelector("button[type='submit']").disabled = name === "loading";
}

function friendlyError(error) {
  if (error.name === "AbortError") return "The analysis service took too long to respond. It may still be waking up; please try again.";
  if (error instanceof TypeError) return "The analysis service is unavailable. Wait a moment and try again.";
  return error.message || "Something unexpected happened while loading FPL data.";
}

function responseError(status, detail) {
  if (status === 404) return "We could not find that public classic league. Check the ID or URL.";
  if (status === 403) return "That league is private or unavailable through the public FPL data.";
  if (status === 400) return detail || "That league cannot be analysed. Check the league ID.";
  if (status === 409) return typeof detail === "string" ? detail : "Data for this Gameweek is not available. Choose another Gameweek.";
  if (status === 422) return "Check the league ID and Gameweek, then try again.";
  if (status >= 500) return "FPL data is temporarily unavailable. Please try again shortly.";
  return "The analysis service could not complete that request. Please try again.";
}

/* ---------- Loading ---------- */

async function loadLive(options = {}) {
  if (appState.view === "season") { await loadSeason(options); return; }
  if (!apiBaseUrl) { showError("The website backend has not been configured."); return; }
  if (!appState.leagueId) { showError("Enter a numeric league ID or paste an FPL standings URL."); return; }

  clearTimeout(appState.refreshTimer);
  appState.requestController?.abort();
  const controller = new AbortController();
  const generation = ++appState.generation;
  appState.requestController = controller;
  elements.retry.classList.remove("hidden");
  elements.refresh.disabled = true;
  elements.loadingMessage.textContent = "Fetching current squads, points and fixtures from FPL.";
  if (!options.background) setPageState("loading");
  const coldStartTimer = setTimeout(() => {
    elements.loadingMessage.textContent = "Starting the analysis service… The first visit can take about a minute.";
  }, 7_000);
  const timeout = setTimeout(() => controller.abort(), 90_000);
  const params = new URLSearchParams({ ties: appState.tieRule });
  if (appState.gameweek) params.set("gameweek", appState.gameweek);

  try {
    const response = await fetch(`${apiBaseUrl}/api/league/${encodeURIComponent(appState.leagueId)}?${params}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(responseError(response.status, payload.detail));
    if (generation !== appState.generation) return;
    appState.data = payload;
    rememberLeague(appState.leagueId, payload.league.name);
    renderLive();
    setPageState("analysis");
    activateView("live", false);
    updateUrl();
    scheduleRefresh();
    if (options.background) showToast("Scores refreshed");
  } catch (error) {
    if (generation !== appState.generation) return;
    if (options.background && appState.data) {
      showToast("Refresh failed — showing the previous update");
      scheduleRefresh();
    } else {
      showError(friendlyError(error));
    }
  } finally {
    clearTimeout(timeout);
    clearTimeout(coldStartTimer);
    if (generation === appState.generation) elements.refresh.disabled = false;
  }
}

function showError(message, retry = true) {
  elements.errorMessage.textContent = message;
  elements.retry.classList.toggle("hidden", !retry);
  setPageState("error");
}

async function loadSeason(options = {}) {
  clearTimeout(appState.refreshTimer);
  appState.requestController?.abort();
  const generation = ++appState.generation;
  appState.gameweek = null;
  elements.refresh.disabled = true;
  setPageState("analysis");
  activateView("season", false);
  elements.leagueName.textContent = appState.data?.league.id === Number(appState.leagueId) ? appState.data.league.name : `League ${appState.leagueId}`;
  elements.gameweek.textContent = "Season";
  elements.updatedAt.textContent = "";
  elements.gameweekStatus.className = "status-dot";
  updateUrl();
  await window.FPLAnalytics.open({
    leagueId: appState.leagueId,
    apiBaseUrl,
    refresh: Boolean(options.background),
    onSummary: (data) => {
      if (generation !== appState.generation) return;
      rememberLeague(appState.leagueId, data.league.name);
      elements.leagueName.textContent = data.league.name;
      elements.gameweek.textContent = `Season · Gameweek ${data.league.current_gameweek}`;
      elements.updatedAt.textContent = `History checked: ${new Date(data.fetched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    },
  });
  if (generation === appState.generation) elements.refresh.disabled = false;
}

/* ---------- Live page ---------- */

function renderLive() {
  const data = appState.data;
  const open = new Set([...elements.live.querySelectorAll("details[data-card][open]")].map((card) => card.dataset.card));
  const statusText = { upcoming: "Upcoming", in_progress: data.league.fixtures.live ? "Live" : "In progress", complete: "Complete" }[data.league.status];
  elements.leagueName.textContent = data.league.name;
  elements.gameweek.textContent = `Gameweek ${data.league.gameweek} · ${statusText}`;
  elements.gameweekStatus.className = `status-dot ${data.league.fixtures.live ? "live" : data.league.status}`;
  elements.updatedAt.textContent = `Last updated: ${new Date(data.league.fetched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  elements.tieRule.value = appState.tieRule;
  elements.gameweekSelect.innerHTML = (data.league.available_gameweeks || [{ id: data.league.gameweek }])
    .slice().reverse()
    .map((gw) => `<option value="${gw.id}">GW ${gw.id}${gw.id === data.league.current_gameweek ? " · Current" : " · Final"}</option>`)
    .join("");
  elements.gameweekSelect.value = String(data.league.gameweek);

  elements.live.innerHTML = data.league.status === "complete" ? finalResult(data) : liveView(data);
  elements.live.querySelectorAll("details[data-card]").forEach((card) => { if (open.has(card.dataset.card)) card.open = true; });
}

function liveView(data) {
  const atRisk = data.managers.filter((m) => m.status === "at_risk");
  const safe = data.managers.filter((m) => m.status === "safe");
  const f = data.league.fixtures;
  const progress = data.league.status === "upcoming"
    ? `No fixtures have started. ${plural(f.total, "fixture")} to play.`
    : `${f.finished} of ${plural(f.total, "fixture")} finished${f.live ? ` · ${f.live} live now` : ""}.`;
  let headline;
  if (data.summary.last_place_decided && atRisk.length === 1) {
    headline = `Only ${atRisk[0].manager_name} can still finish last`;
  } else {
    headline = `${plural(atRisk.length, "manager")} can still finish last`;
  }
  return `
    <section class="live-summary" aria-labelledby="live-headline">
      <p class="kicker">${escapeHtml(progress)}</p>
      <h2 id="live-headline">${escapeHtml(headline)}</h2>
      <p>${data.summary.last_place_decided && atRisk.length === 1 ? "Everyone else is safe under the stated envelope." : `${plural(safe.length, "manager")} safe under the stated envelope.`}</p>
    </section>

    <section class="section-block" aria-labelledby="standings-heading">
      <div class="view-heading"><div><h3 id="standings-heading">Bottom standings</h3><p>Lowest first. Open a manager to see their squad.</p></div></div>
      <div class="manager-list">${atRisk.map(managerCard).join("") || emptyState("Nobody is at risk", "Every manager is safe under the stated envelope.")}</div>
      ${safe.length ? `<details class="safe-section" data-card="safe-group"><summary><span>Safe</span><span class="count-badge">${safe.length}</span></summary><div class="manager-list">${safe.map(managerCard).join("")}</div></details>` : ""}
    </section>

    ${separators(data)}
    ${assumptions(data)}`;
}

function finalResult(data) {
  const bottom = data.managers.filter((m) => data.summary.bottom_entry_ids.includes(m.entry_id));
  const strictTie = !data.model.ties_count_as_last && bottom.length > 1;
  const names = bottom.map((m) => m.manager_name).join(" & ");
  const score = bottom[0]?.effective_score ?? "—";
  const headline = strictTie ? "No sole last-place manager" : `${names} ${bottom.length > 1 ? "finish" : "finishes"} last`;
  return `
    <article class="completed-card">
      <span class="card-label">Gameweek ${data.league.gameweek} final result</span>
      ${data.league.results_final ? "" : '<p class="fine-print">All matches have finished. Points stay provisional until FPL marks the Gameweek final.</p>'}
      <h2>${escapeHtml(headline)}</h2>
      <p class="team-name">${strictTie ? `${bottom.length} managers share the lowest score (${score}) under the strict-last rule.` : `${score} points, transfer deductions included.`}</p>
      ${data.league.history_note ? `<p class="fine-print">${escapeHtml(data.league.history_note)}</p>` : ""}
    </article>
    <section class="section-block" aria-labelledby="final-heading">
      <div class="view-heading"><div><h3 id="final-heading">Final standings</h3><p>Lowest first. Open a manager to see their squad.</p></div></div>
      <div class="manager-list">${data.managers.map(managerCard).join("")}</div>
    </section>`;
}

function statusBadge(manager) {
  if (manager.status === "last") return '<span class="status-badge status-current">Last</span>';
  if (manager.status === "clear") return "";
  const current = manager.is_current_last ? '<span class="status-badge status-current">Currently last</span>' : "";
  const state = manager.status === "safe"
    ? '<span class="status-badge status-safe">Safe</span>'
    : '<span class="status-badge status-risk">At risk</span>';
  return current + state;
}

function managerCard(manager) {
  const live = manager.status === "safe" || manager.status === "at_risk";
  const remaining = live ? ` · ${plural(manager.remaining_player_count, "player")} left` : "";
  return `
    <details class="manager-card" data-card="manager-${manager.entry_id}">
      <summary>
        <span class="manager-identity"><strong>${escapeHtml(manager.manager_name)}</strong><span>${escapeHtml(manager.team_name)}${remaining}${manager.transfer_cost ? ` · −${manager.transfer_cost} hit` : ""}</span></span>
        <span class="card-meta"><span class="badge-row">${statusBadge(manager)}</span><span class="points">${manager.effective_score}<small>pts</small></span></span>
      </summary>
      <div class="manager-body">
        ${live ? `<p class="safety-line">${escapeHtml(safetyLine(manager))}</p>` : ""}
        <div class="manager-facts">
          <div class="manager-fact"><span>Points before hits</span><strong>${manager.raw_official_points}</strong></div>
          <div class="manager-fact"><span>Transfer deduction</span><strong>${manager.transfer_cost ? `−${manager.transfer_cost}` : "None"}</strong></div>
          <div class="manager-fact"><span>Captain</span><strong>${escapeHtml(manager.captain || "—")}</strong></div>
          <div class="manager-fact"><span>Vice-captain</span><strong>${escapeHtml(manager.vice_captain || "—")}</strong></div>
        </div>
        ${manager.active_chip ? `<p class="fine-print">Active chip: ${escapeHtml(manager.active_chip)}</p>` : ""}
        ${manager.score_source === "official_final" ? '<p class="fine-print">Uses FPL’s final Gameweek points and official multipliers, minus this Gameweek’s transfer cost.</p>' : ""}
        <div class="table-wrap"><table class="squad-table">
          <thead><tr><th>Player</th><th>Role</th><th>Points</th><th>Minutes</th><th>Fixture</th></tr></thead>
          <tbody>${manager.squad.map(squadRow).join("")}</tbody>
        </table></div>
        <p class="fine-print">${(manager.score_notes || []).map(escapeHtml).join(" ")}</p>
      </div>
    </details>`;
}

function safetyLine(manager) {
  const range = `Final score between ${manager.bounds.lower} and ${manager.bounds.upper} under the envelope.`;
  if (manager.status === "safe") return `Safe: ${manager.safety_detail} ${range}`;
  return `At risk: ${manager.safety_detail} ${range}`;
}

function squadRow(player) {
  const weight = player.effective_multiplier;
  const flags = [
    player.is_captain ? (weight === 3 ? "TC ×3" : weight > 1 ? `C ×${weight}` : "C") : "",
    player.is_vice_captain ? `VC${weight > 1 ? ` ×${weight}` : ""}` : "",
  ].filter(Boolean).join(" · ");
  const role = player.selection === "starter" ? "Starter" : `Bench ${player.bench_order}`;
  const remaining = player.remaining_fixtures.map((fixture) => `${fixture.started ? "live vs" : "vs"} ${fixture.opponent}`).join(" · ");
  const state = remaining || (player.fixture_status === "blank" ? "No fixture" : "Finished");
  const autosub = { subbed_in: "Subbed in", subbed_out: "Subbed out", possible: "Autosub possible", pending: "Pending", bench_boost: "Bench Boost" }[player.autosub_status] || "";
  const notes = [autosub, player.captaincy_status, player.conditional_exposure].filter(Boolean).map((note) => `<br><span class="autosub-note">${escapeHtml(note)}</span>`).join("");
  const news = player.availability?.news ? `<span class="fine-print">${escapeHtml(player.availability.news)}</span>` : "";
  return `<tr>
    <td class="player-cell"><strong>${escapeHtml(player.player_name)} ${flags ? `<span class="player-flags">${flags}</span>` : ""}</strong><span>${escapeHtml(player.team_name)} · ${player.position}</span>${news}</td>
    <td>${role}${notes}</td>
    <td>${player.official_points ?? "—"}</td><td>${player.minutes ?? "—"}</td><td>${escapeHtml(state)}</td>
  </tr>`;
}

function separators(data) {
  if (data.summary.last_place_decided) return "";
  const players = data.differentials;
  const heading = `<div class="view-heading"><div><h3 id="separators-heading">What separates the bottom</h3><p>Remaining players whose effective multiplier differs between managers who can still finish last. Shared exposure cancels and is left out.</p></div></div>`;
  if (!players.length) {
    return `<section class="section-block" aria-labelledby="separators-heading">${heading}${emptyState("No remaining differences", "The managers at risk have identical exposure to every remaining player.")}</section>`;
  }
  const managers = new Map(data.managers.map((m) => [m.entry_id, m]));
  const ids = data.differential_manager_ids;
  const table = (rows) => `<div class="table-wrap"><table class="differential-table">
      <thead><tr><th>Player</th><th>Fixture</th><th>Now</th>${ids.map((id) => `<th>${escapeHtml(managers.get(id)?.manager_name || id)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((player) => {
        const exposure = new Map(player.exposures.map((item) => [item.entry_id, item]));
        const fixtures = player.fixtures.filter((fixture) => !fixture.finished).map((fixture) => `${fixture.started ? "Live vs" : "vs"} ${fixture.opponent}`).join(" · ") || "Finished";
        return `<tr>
          <td class="player-cell"><strong>${escapeHtml(player.player_name)}</strong><span>${escapeHtml(player.team_name)} · ${player.position}</span></td>
          <td><span class="fixture-copy ${player.fixture_status === "live" ? "fixture-live" : ""}">${escapeHtml(fixtures)}${player.has_double_gameweek_remaining ? " · DGW" : ""}</span></td>
          <td>${player.current_points} pts<br><span class="fixture-copy">${player.minutes} min</span></td>
          ${ids.map((id) => { const item = exposure.get(id); const value = item?.multiplier || 0; return `<td><span class="multiplier ${value ? "active" : ""}">${value}×</span>${item?.note ? `<br><span class="fixture-copy">${escapeHtml(item.note)}</span>` : ""}</td>`; }).join("")}
        </tr>`;
      }).join("")}</tbody>
    </table></div>`;
  const shown = players.slice(0, SEPARATOR_PREVIEW);
  const rest = players.slice(SEPARATOR_PREVIEW);
  return `<section class="section-block" aria-labelledby="separators-heading">${heading}
    ${table(shown)}
    ${rest.length ? `<details class="safe-section" data-card="all-separators"><summary><span>Show all ${players.length} players</span><span class="count-badge">+${rest.length}</span></summary>${table(rest)}</details>` : ""}
    <p class="fine-print">Largest multiplier differences first. Multipliers reflect the current effective lineup, captaincy and chips. Pending starters, legal autosubs and vice-captain takeover can still change them.</p>
  </section>`;
}

function assumptions(data) {
  const ties = data.model.ties_count_as_last ? "A tie for the lowest score counts as last." : "Only a strictly lowest score counts as last.";
  return `<details class="model-note" data-card="assumptions">
    <summary>How SAFE and AT RISK are decided</summary>
    <p>${escapeHtml(data.model.safety_note)}</p>
    <p>${escapeHtml(ties)} Change this in Settings.</p>
  </details>`;
}

/* ---------- Navigation, URL and settings ---------- */

function activateView(view, load = true) {
  const previous = appState.view;
  appState.view = view === "season" ? "season" : "live";
  document.querySelectorAll("[role='tab']").forEach((tab) => {
    const active = tab.dataset.view === appState.view;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.toggle("hidden", panel.id !== `view-${appState.view}`));
  elements.gameweekSelect.closest("label").classList.toggle("hidden", appState.view === "season");
  if (!load || previous === appState.view) return;
  if (appState.view === "season") loadSeason();
  else { window.FPLAnalytics.cancel(); loadLive(); }
}

function updateUrl() {
  const url = new URL(window.location.href);
  if (appState.leagueId) url.searchParams.set("league", appState.leagueId);
  url.searchParams.set("view", appState.view);
  if (appState.view !== "season") url.searchParams.delete("section");
  url.searchParams.delete("manager");
  url.searchParams.delete("demo");
  if (appState.tieRule === "strict") url.searchParams.set("ties", "strict");
  else url.searchParams.delete("ties");
  if (appState.gameweek && appState.view === "live") url.searchParams.set("gw", appState.gameweek);
  else url.searchParams.delete("gw");
  history.replaceState({}, "", url);
}

function rememberLeague(id, name) {
  const recent = readRecent().filter((item) => item.id !== id);
  recent.unshift({ id, name });
  window.FPLInput.write("fpl-recent-leagues", JSON.stringify(recent.slice(0, 4)));
  renderRecent();
}

function readRecent() {
  try {
    const value = JSON.parse(window.FPLInput.read("fpl-recent-leagues") || "[]");
    return Array.isArray(value)
      ? value.filter((item) => item && parseLeagueId(item.id)).map((item) => ({ id: parseLeagueId(item.id), name: String(item.name || "Classic league") })).slice(0, 4)
      : [];
  } catch {
    return [];
  }
}

function renderRecent() {
  const recent = readRecent();
  elements.recent.classList.toggle("hidden", !recent.length);
  elements.recentList.innerHTML = recent.map((item) => `<button class="recent-button" type="button" data-league="${escapeHtml(item.id)}">${escapeHtml(item.name)} <span>· ${escapeHtml(item.id)}</span></button>`).join("");
  elements.recentList.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => openLeague(button.dataset.league));
  });
}

function openLeague(leagueId) {
  window.FPLAnalytics.cancel();
  appState.leagueId = leagueId;
  appState.gameweek = null;
  appState.view = "live";
  elements.input.value = leagueId;
  loadLive();
}

function scheduleRefresh() {
  clearTimeout(appState.refreshTimer);
  if (appState.view !== "live" || !appState.data?.league.fixtures.live || document.hidden) return;
  appState.refreshTimer = setTimeout(() => loadLive({ background: true }), Math.max(REFRESH_INTERVAL_MS, (appState.data.cache?.ttl_seconds || 90) * 1000));
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

function changeLeague() {
  clearTimeout(appState.refreshTimer);
  appState.requestController?.abort();
  ++appState.generation;
  window.FPLAnalytics.cancel();
  appState.data = null;
  appState.leagueId = "";
  appState.gameweek = null;
  appState.view = "live";
  elements.input.value = "";
  history.replaceState({}, "", window.location.pathname);
  setPageState("landing");
  elements.input.focus();
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const leagueId = parseLeagueId(elements.input.value);
  if (!leagueId) { showError("Enter a numeric classic league ID or paste its FPL standings URL.", false); return; }
  openLeague(leagueId);
});
elements.retry.addEventListener("click", () => (appState.leagueId ? loadLive() : setPageState("landing")));
elements.refresh.addEventListener("click", () => loadLive({ background: true }));
elements.changeLeague.addEventListener("click", changeLeague);
$("#error-change-league").addEventListener("click", changeLeague);
$("#current-gameweek-button").addEventListener("click", () => { appState.gameweek = null; appState.view = "live"; loadLive(); });
elements.gameweekSelect.addEventListener("change", () => {
  const chosen = Number(elements.gameweekSelect.value);
  appState.gameweek = chosen === appState.data.league.current_gameweek ? null : chosen;
  loadLive();
});
elements.tieRule.addEventListener("change", () => {
  appState.tieRule = elements.tieRule.value;
  if (appState.data && appState.view === "live") loadLive();
  else updateUrl();
});
elements.themeToggle.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
document.querySelectorAll("[role='tab']").forEach((tab) => {
  tab.addEventListener("click", () => activateView(tab.dataset.view));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[role='tab']")];
    const next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
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
if (query.has("league") && !queryLeague) {
  showError("The shared link contains an invalid league ID. Choose Change league and enter a numeric ID or FPL classic league URL.", false);
} else if (queryLeague) {
  appState.leagueId = queryLeague;
  elements.input.value = queryLeague;
  loadLive();
} else {
  setPageState("landing");
}
