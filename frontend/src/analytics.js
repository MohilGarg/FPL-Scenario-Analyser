/* Descriptive analytics UI. All ranking, scoring and aggregation lives in Python. */
window.FPLAnalytics = (() => {
  const SECTIONS = new Set(["summary", "gameweeks", "managers", "players", "head-to-head"]);
  const state = { league: "", api: "", summary: null, section: "summary", token: 0, viewToken: 0, controller: null, timer: null, cache: new Map(), manager: null, a: null, b: null, gameweek: null, playerSearch: "", playerFilter: "all", data: null, onSummary: null };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const num = (v) => v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  const chipName = (c) => ({ bboost: "Bench Boost", "3xc": "Triple Captain", wildcard: "Wildcard", freehit: "Free Hit" })[c] || c;
  const card = (title, body, note = "") => `<article class="analytics-card"><h3>${esc(title)}</h3>${note ? `<p class="fine-print">${esc(note)}</p>` : ""}${body}</article>`;
  const disclosure = (title, body) => `<details class="analytics-disclosure"><summary>${esc(title)}</summary><div>${body}</div></details>`;
  const facts = (pairs) => `<dl class="analytics-facts">${pairs.map(([label, value, tip]) => `<div${tip ? ` title="${esc(tip)}"` : ""}><dt>${esc(label)}</dt><dd>${esc(value ?? "—")}</dd></div>`).join("")}</dl>`;
  const managerOptions = (selected) => (state.summary?.managers || []).map((m) => `<option value="${m.entry_id}" ${m.entry_id === Number(selected) ? "selected" : ""}>${esc(m.name)}</option>`).join("");
  const selectManager = (id, selected, label = "Manager") => `<label>${esc(label)}<select id="${id}">${managerOptions(selected)}</select></label>`;
  const managerLink = (m) => `<button type="button" class="table-link" data-analytics-manager="${m.entry_id}">${esc(m.name)}</button>`;
  const detailNote = (m) => `Scoring detail: ${m.detail_coverage ?? 0}/${m.scores_analysed} recorded Gameweeks. Totals below cover only available, reconciled squads.`;

  function table(columns, rows, label = "Analytics table") {
    return `<div class="table-wrap"><table class="analytics-table" aria-label="${esc(label)}"><thead><tr>${columns.map((c, i) => `<th scope="col" aria-sort="none"><button type="button" data-sort="${i}" title="Sort by ${esc(c.label)}">${esc(c.label)} <span aria-hidden="true">↕</span></button></th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${columns.map((c) => {
      const value = typeof c.value === "function" ? c.value(r) : r[c.value];
      return `<td data-sort-value="${esc(value ?? "")}">${c.html ? c.html(r) : typeof value === "number" ? num(value) : esc(value ?? "—")}</td>`;
    }).join("")}</tr>`).join("") || `<tr><td colspan="${columns.length}">No data available yet.</td></tr>`}</tbody></table></div>`;
  }
  function bind(root = $("analytics-content")) {
    window.FPLCharts.bind(root);
    root.querySelectorAll("[data-sort]").forEach((button) => {
      button.onclick = () => {
        const tbl = button.closest("table"), index = Number(button.dataset.sort), th = button.closest("th");
        const descending = th.getAttribute("aria-sort") !== "descending";
        tbl.querySelectorAll("th").forEach((head) => head.setAttribute("aria-sort", "none"));
        th.setAttribute("aria-sort", descending ? "descending" : "ascending");
        [...tbl.tBodies[0].rows].sort((a, b) => {
          const va = a.cells[index]?.dataset.sortValue || "", vb = b.cells[index]?.dataset.sortValue || "";
          if (!va || !vb) return !va ? (!vb ? 0 : 1) : -1;
          const result = Number.isFinite(Number(va)) && Number.isFinite(Number(vb)) ? Number(va) - Number(vb) : va.localeCompare(vb);
          return result * (descending ? -1 : 1);
        }).forEach((row) => tbl.tBodies[0].append(row));
      };
    });
    root.querySelectorAll("[data-analytics-manager]").forEach((button) => {
      button.onclick = () => { state.manager = Number(button.dataset.analyticsManager); selectSection("managers"); };
    });
  }
  async function request(path, fresh = false) {
    const key = `${state.league}/${path}`, cached = state.cache.get(key);
    if (!fresh && cached && Date.now() - cached.time < 90_000) return cached.data;
    if (!state.api) throw new Error("The website backend has not been configured.");
    const signal = state.controller.signal;
    const response = await fetch(`${state.api}/api/league/${state.league}/analytics/${path}`, { signal: AbortSignal.any([signal, AbortSignal.timeout(90_000)]), headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : response.status === 422 ? "Choose a valid classic league ID." : "FPL history is unavailable. Please try again shortly.");
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    if (!path.startsWith("details")) state.cache.set(key, { time: Date.now(), data });
    // Bound browser memory when inspecting several leagues.
    if (state.cache.size > 40) state.cache.delete(state.cache.keys().next().value);
    return data;
  }
  function cancel() {
    ++state.token; ++state.viewToken;
    state.controller?.abort(); clearTimeout(state.timer);
  }
  async function open(options) {
    const changed = state.league !== options.leagueId;
    cancel();
    const token = state.token;
    state.controller = new AbortController();
    state.api = options.apiBaseUrl; state.league = options.leagueId; state.onSummary = options.onSummary;
    if (changed) { state.summary = null; state.manager = null; state.a = null; state.b = null; state.gameweek = null; state.playerSearch = ""; state.playerFilter = "all"; }
    const requested = new URLSearchParams(location.search).get("section");
    state.section = SECTIONS.has(requested) ? requested : "summary";
    $("analytics-nav").classList.remove("hidden"); $("analytics-definitions").classList.remove("hidden");
    $("analytics-content").innerHTML = `<div class="analytics-loading" role="status">Loading league history… Scores load first; detailed squads follow.</div>`;
    $("analytics-coverage").textContent = ""; $("analytics-progress").classList.add("hidden");
    try {
      state.summary = await request("summary", options.refresh);
      if (token !== state.token) return;
      state.onSummary?.(state.summary); renderCoverage();
      await selectSection(state.section, false);
      if (token === state.token) pollDetails(token, Date.now());
    } catch (error) { if (token === state.token) showError(error, () => open(options)); }
  }
  function showError(error, retry) {
    $("analytics-content").innerHTML = card("History could not be loaded", `<p>${esc(error.name === "TimeoutError" ? "The backend may be waking up. Please try again." : error.message)}</p><button type="button" class="secondary-button" id="analytics-retry">Try again</button>`);
    $("analytics-retry").onclick = retry;
  }
  function renderCoverage() {
    const s = state.summary, c = s.coverage;
    $("analytics-coverage").textContent = `${s.league.manager_count} managers · ${s.league.completed_gameweeks.length} completed GWs · ${c.scores}/${c.expected} manager scores · ${c.ranked_gameweeks} fully covered weeks`;
    $("analytics-definitions").querySelector("div").innerHTML = s.definitions.map((d) => `<p>${esc(d)}</p>`).join("") + `<p>Score history is cached for 15 minutes; final squads for 24 hours. Current squads use the live cache. Caches reset when the free backend restarts.</p>` + (s.warnings.length ? `<h4>Unavailable data</h4><ul>${s.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "");
  }
  async function pollDetails(token, started) {
    if (token !== state.token) return;
    const progress = $("analytics-progress");
    progress.classList.remove("hidden");
    try {
      const status = await request("details", true);
      if (token !== state.token) return;
      if (status.status === "ready") {
        progress.innerHTML = `<span>Scoring detail: ${status.coverage.available}/${status.coverage.expected} final squads verified.${status.coverage.available < status.coverage.expected ? " Some data is unavailable; detailed totals cover verified squads only." : ""}</span>`;
        state.cache.clear();
        const summary = await request("summary", true);
        if (token !== state.token) return;
        state.summary = summary; renderCoverage();
        const focusedId = document.activeElement?.id;
        const cursor = document.activeElement?.selectionStart;
        await selectSection(state.section, false);
        if (focusedId && $(focusedId)) {
          $(focusedId).focus({ preventScroll: true });
          if (cursor != null && $(focusedId).setSelectionRange) $(focusedId).setSelectionRange(cursor, cursor);
        }
        return;
      }
      if (Date.now() - started > 300_000) {
        progress.innerHTML = `History is taking longer than expected. Scores remain available. <button type="button" class="text-button" id="analytics-recheck">Check progress</button>`;
        $("analytics-recheck").onclick = () => pollDetails(token, Date.now());
        return;
      }
      const p = status.progress;
      progress.innerHTML = p ? `<span>Verifying ${p.managers} managers across ${p.gameweeks} completed Gameweeks · ${p.done}/${p.total} squads checked</span><progress max="${Math.max(1, p.total)}" value="${p.done}" aria-label="Historical squads checked"></progress>` : esc(status.message);
      state.timer = setTimeout(() => pollDetails(token, started), 4000);
    } catch (error) {
      if (token !== state.token) return;
      progress.innerHTML = `Detailed history is temporarily unavailable. Scores are still shown. <button type="button" class="text-button" id="analytics-recheck">Retry detail</button>`;
      $("analytics-recheck").onclick = () => pollDetails(token, Date.now());
    }
  }
  async function selectSection(section, update = true) {
    state.section = SECTIONS.has(section) ? section : "summary";
    const viewToken = ++state.viewToken;
    $("analytics-nav").querySelectorAll("button").forEach((button) => button.setAttribute("aria-current", button.dataset.section === state.section ? "page" : "false"));
    const activeButton = $("analytics-nav").querySelector("[aria-current='page']");
    if (activeButton) $("analytics-nav").scrollLeft = Math.max(0, activeButton.offsetLeft - $("analytics-nav").offsetLeft + activeButton.offsetWidth - $("analytics-nav").clientWidth);
    if (update) { const url = new URL(location.href); url.searchParams.set("section", state.section); history.replaceState({}, "", url); }
    if (!state.summary) return;
    $("analytics-content").innerHTML = `<p class="analytics-loading" role="status">Loading ${esc(state.section)}…</p>`;
    try {
      if (state.section === "summary") renderSummary();
      else if (state.section === "gameweeks") renderGameweeks();
      else if (state.section === "managers") {
        state.manager ||= state.summary.managers[0]?.entry_id;
        const data = await request(`manager/${state.manager}`);
        if (viewToken !== state.viewToken) return;
        renderManager(data);
      } else if (state.section === "players") {
        const data = await request("players");
        if (viewToken !== state.viewToken) return;
        renderPlayers(data);
      } else {
        state.a ||= state.summary.managers[0]?.entry_id; state.b ||= state.summary.managers[1]?.entry_id;
        if (!state.b) { $("analytics-content").innerHTML = card("Head-to-head", "At least two managers are needed."); return; }
        const data = await request(`head-to-head?a=${state.a}&b=${state.b}`);
        if (viewToken !== state.viewToken) return;
        renderHeadToHead(data);
      }
      bind();
    } catch (error) { if (viewToken === state.viewToken) showError(error, () => selectSection(state.section)); }
  }
  function renderSummary() {
    const s = state.summary, t = s.totals, lookup = (gw) => s.gameweeks.find((w) => w.gameweek === gw);
    const finish = (gw) => gw ? `GW${gw} · ${num(lookup(gw)?.margin)} pts` : "—";
    const extreme = (row) => row ? `${row.score} · ${row.name} · GW${row.gameweek}` : "—";
    $("analytics-content").innerHTML = `
      ${!s.coverage.expected ? '<p class="phase-note">No completed Gameweeks yet. Current ownership is available under Players once squads are public.</p>' : ""}
      ${s.coverage.scores < s.coverage.expected ? `<p class="phase-note">${s.coverage.expected - s.coverage.scores} manager scores are unavailable. Weekly finishes use only ${s.coverage.ranked_gameweeks} fully covered weeks. Open Gameweeks or Definitions &amp; data coverage for the missing records.</p>` : ""}
      ${card("Season at a glance", facts([["Average manager GW", num(t.average)], ["Transfer deductions", num(t.hits)], ["Unused bench · verified squads", num(t.unused_bench)], ["Chips recorded", num(t.chips)]]) + `<div class="season-extremes"><p><span>Highest GW</span>${esc(extreme(t.highest))}</p><p><span>Lowest GW</span>${esc(extreme(t.lowest))}</p><p><span>Closest last-place finish</span>${finish(t.closest_finish)}</p><p><span>Largest last-place margin</span>${finish(t.largest_margin)}</p></div>`)}
      ${window.FPLCharts.line("The weekly score line", s.gameweeks, [{ key: "average", name: "League average" }, { key: "bottom", name: "Lowest score" }])}
      ${card("Forfeit leaderboard", table([
        { label: "Manager", value: "name", html: managerLink }, { label: "Times last", value: "times_last" },
        { label: "Bottom 3", value: "bottom_three" }, { label: "Avg position", value: "average_rank" },
        { label: "Lowest GW", value: "worst" }, { label: "Closest escape", value: "closest_escape" },
        { label: "Largest last margin", value: "largest_last_margin" },
      ], [...s.managers].sort((a, b) => (b.times_last ?? -1) - (a.times_last ?? -1)), "Forfeit leaderboard"), "All tied lowest scores count as last. Bottom three includes cutoff ties. Only fully covered league weeks are ranked. Tap a manager for their score trends.")}
      <div class="analytics-grid">${window.FPLCharts.bars("Unused bench points · verified squads", s.managers.map((m) => ({ label: m.name, value: m.bench?.total })))}${window.FPLCharts.bars("Distribution of recorded GW scores", s.distribution.bins)}</div>
      ${card("Score distribution", facts([["Median", num(s.distribution.median)], ["Lower quartile", num(s.distribution.q1)], ["Upper quartile", num(s.distribution.q3)]]), "Quartiles use inclusive interpolation across all recorded manager–Gameweek scores.")}
      ${disclosure("Chip history", card("Chips recorded", chipTable(s.managers.flatMap((m) => m.chips.map((c) => ({ ...c, chip: c.name, name: m.name })))), "Every recorded use is shown, including repeat chips. Only Bench Boost and Triple Captain have directly measured contribution; no Wildcard or Free Hit uplift is inferred."))}
      ${s.captaincy ? disclosure("League captaincy", captaincy(s.captaincy)) : '<p class="fine-print">Captaincy and exact bench totals appear when final squads have been verified.</p>'}
      ${s.detail_coverage ? disclosure("Points by position · league comparison", card("Counted positional points", table([{ label: "Manager", value: "name", html: managerLink }, ...["GK", "DEF", "MID", "FWD"].map((position) => ({ label: position, value: (m) => m.positions?.find((p) => p.position === position)?.points })), { label: "Verified GWs", value: "position_coverage" }], s.managers), "Final player contributions include captaincy, autosubs and Bench Boost. Transfer deductions are excluded; manager pages also show percentages.")) : ""}`;
  }
  function chipTable(chips) {
    return table([{ label: "Manager", value: "name" }, { label: "GW", value: "event" },
      { label: "Chip", value: (c) => chipName(c.chip || c._chip || c.name) },
      { label: "Direct contribution", value: "contribution" }], chips.map((c) => ({ ...c, _chip: c._chip || c.chip || c.name })), "Chip history");
  }
  function captaincy(data) {
    return card("Captaincy across the league", facts([["Multiplied captain points", num(data.total)], ["Additional multiplier points", num(data.additional)]]) + `<h4>Most-captained players · verified weeks</h4>` + table([{ label: "Player", value: "name" }, { label: "Times chosen", value: "chosen" }], data.players || []) + `<h4>Captaincy by Gameweek</h4>` + table([
      { label: "GW", value: "gameweek" }, { label: "Squads verified", value: "coverage" },
      { label: "Most chosen", value: (r) => r.most_chosen?.join(" & ") || null },
      { label: "Concentration", value: "concentration_pct", html: (r) => r.concentration_pct == null ? "—" : `${r.concentration_pct}%` },
      { label: "Unique choices", value: "unique_choices" },
      { label: "All choices", value: (r) => r.choices.map((c) => `${c.name} (${c.count})`).join(", ") },
    ], data.gameweeks), "Chosen captains use the deadline selection. Point totals follow the effective captain, including vice-captain takeover. Concentration = most chosen captain / verified squads that GW; unique choices have one manager.");
  }
  function renderGameweeks() {
    $("analytics-content").innerHTML = card("Completed Gameweeks", table([
      { label: "GW", value: "gameweek", html: (w) => `<button type="button" class="table-link" data-analytics-gw="${w.gameweek}">GW ${w.gameweek}</button>` },
      { label: "Scores", value: "coverage", html: (w) => `${w.coverage}/${state.summary.league.manager_count}` },
      { label: "Last", value: (w) => w.complete ? w.last_names.join(" & ") : "Incomplete coverage" },
      { label: "Lowest", value: "bottom" }, { label: "Second-lowest", value: "second_last" }, { label: "Margin", value: "margin" },
      { label: "Highest scorer", value: (w) => w.top_names.join(" & ") || null }, { label: "Highest", value: "top" },
      { label: "Available mean", value: "average" }, { label: "Hits", value: "hits" },
      { label: "Chips", value: (w) => w.chips.map((c) => chipName(c.name)).join(", ") || "None recorded" },
    ], state.summary.gameweeks, "Gameweek history"), "Select a Gameweek for the full ordering. These are final results, not reconstructed historical live states.") + `<div id="analytics-gw-detail"></div>`;
    $("analytics-content").querySelectorAll("[data-analytics-gw]").forEach((button) => { button.onclick = () => loadGameweek(Number(button.dataset.analyticsGw)); });
    if (state.gameweek) loadGameweek(state.gameweek);
  }
  async function loadGameweek(gw) {
    state.gameweek = gw;
    const viewToken = state.viewToken;
    $("analytics-gw-detail").innerHTML = '<p role="status">Loading final Gameweek results…</p>';
    try {
      const data = await request(`gameweeks?gameweek=${gw}`);
      if (viewToken !== state.viewToken || state.gameweek !== gw) return;
      const week = data.gameweeks[0];
      $("analytics-gw-detail").innerHTML = card(`Gameweek ${gw} · final results`, table([
        { label: "Position", value: "rank" }, { label: "Manager", value: "name", html: managerLink },
        { label: "Effective", value: "score" }, { label: "Raw", value: "raw" }, { label: "Hit", value: "hits" },
        { label: "Chip", value: (r) => r.chips ? r.chips.map(chipName).join(", ") || "None recorded" : "Unavailable" },
        { label: "Chosen captain", value: (r) => r.detail?.captain_name },
        { label: "Captain total", value: (r) => r.detail?.captain_points },
        { label: "Captain additional", value: (r) => r.detail?.captain_additional }, { label: "Unused bench", value: "unused_bench" },
      ], [...week.rows, ...(week.unavailable_members || [])]), `${week.coverage}/${data.league.manager_count} manager scores. Missing records remain unavailable below the ordering; no points are invented.`);
      bind($("analytics-gw-detail"));
    } catch (error) { if (viewToken === state.viewToken && $("analytics-gw-detail")) $("analytics-gw-detail").textContent = error.message; }
  }
  function renderManager(data) {
    const m = data.managers[0];
    const rows = m.history.map((r) => ({ ...r, ...Object.fromEntries(Object.entries(data.trends.find((w) => w.gameweek === r.gameweek) || {}).filter(([k]) => k !== "gameweek")) }));
    const chips = m.chips.map((c) => ({ ...c, chip: c.name, name: m.name }));
    $("analytics-content").innerHTML = `<div class="analytics-controls">${selectManager("analytics-manager", m.entry_id)}</div>
      ${card(m.name, facts([["Average GW", num(m.average)], ["Median", num(m.median)], ["Best / lowest", `${num(m.best)} / ${num(m.worst)}`], ["Score standard deviation", num(m.standard_deviation), "Population standard deviation of recorded weekly scores; lower is more consistent."], ["Last-place finishes", num(m.times_last)], ["Bottom-three finishes", num(m.bottom_three)], ["Weekly wins", num(m.wins)], ["Average weekly position", num(m.average_rank)]]), `${m.scores_analysed} recorded completed Gameweeks; ${m.ranked_weeks} with full league coverage.`)}
      ${window.FPLCharts.line("Weekly performance", rows, [{ key: "score", name: m.name }, { key: "average", name: "League average" }, { key: "bottom", name: "Lowest score" }])}
      ${window.FPLCharts.line("Cumulative effective points · recorded weeks", m.history, [{ key: "cumulative", name: m.name }])}
      ${disclosure("Cumulative transfer deductions", window.FPLCharts.line("Points deducted over the season", m.history, [{ key: "cumulative_hits", name: "Transfer deductions" }]))}
      <div class="analytics-grid">${card("Transfer deductions", facts([["Transfers recorded", num(m.hits.transfers)], ["Points deducted", num(m.hits.total)], ["Hit Gameweeks", num(m.hits.weeks)], ["No-hit Gameweeks", num(m.hits.no_hit)], ["−4 / −8 / larger weeks", `${m.hits.four} / ${m.hits.eight} / ${m.hits.larger}`], ["Average / largest deduction", `${num(m.hits.average)} / ${num(m.hits.maximum)}`]]), "Descriptive totals, not an assessment of transfer success.")}
      ${card("Consistency", facts([["Score range", num(m.range)], ["Above league average", m.above_average_pct == null ? "—" : `${m.above_average_pct}%`], ["Below league average", m.below_average_pct == null ? "—" : `${m.below_average_pct}%`], ["Mean distance from average", num(m.average_distance)]]), "League comparisons use fully covered weeks. Equal-to-average weeks are in neither percentage.")}</div>
      <p class="analytics-coverage">${esc(detailNote(m))}</p>
      <div class="analytics-grid">${card("Bench points", facts([["Unused points", num(m.bench?.total)], ["Average unused", num(m.bench?.average)], ["Highest unused GW", num(m.bench?.highest)], ["Bench Boost counted", num(m.bench?.boost)]]), "Autosubbed players are not unused. Bench Boost is counted separately, not added to unused points.")}
      ${card("Captaincy", facts([["Total multiplied points", num(m.captaincy?.total)], ["Additional multiplier points", num(m.captaincy?.additional)], ["Different captains chosen", num(m.captaincy?.different)], ["Average captain total", num(m.captaincy?.average)], ["Highest captain week", m.captaincy?.best_gameweek ? `GW${m.captaincy.best_gameweek} · ${m.captaincy.best_points} pts` : "—"]]), "An 8-point captain contributes 16 total points, of which 8 are additional captaincy points.")}</div>
      ${card("Chips", chipTable(chips), "Bench Boost contribution = counted bench points. Triple Captain contribution = the full extra multiplier above ordinary ownership. Other chip uplift is not inferred.")}
      ${m.positions?.length ? card("Counted points by position", table([{ label: "Position", value: "position" }, { label: "Points", value: "points" }, { label: "Share (%)", value: "percentage" }], m.positions), `${m.position_coverage} verified GWs. Includes final captaincy, autosubs and Bench Boost; excludes transfer deductions.`) : ""}
      ${card("Gameweek record", table([{ label: "GW", value: "gameweek" }, { label: "Score", value: "score" }, { label: "Position", value: "rank" }, { label: "Hits", value: "hits" }, { label: "Chosen captain", value: (r) => r.detail?.captain_name }, { label: "Captain total", value: (r) => r.detail?.captain_points }, { label: "Additional", value: (r) => r.detail?.captain_additional }, { label: "Unused bench", value: "unused_bench" }], m.history))}`;
    $("analytics-manager").onchange = (event) => { state.manager = Number(event.target.value); selectSection("managers"); };
  }
  function renderPlayers(data) {
    state.data = data;
    const current = data.current;
    $("analytics-content").innerHTML = `${card("Current squad ownership", current.available ? `<p class="fine-print">GW${current.gameweek} · ${current.coverage}/${current.manager_count} squads · ${esc(new Date(current.fetched_at).toLocaleTimeString())}</p><div class="analytics-controls"><label>Find a player<input type="search" id="analytics-player-search" placeholder="Player name" value="${esc(state.playerSearch)}"></label><label>Ownership group<select id="analytics-player-filter"><option value="all">All players</option><option value="one">Exactly one owner</option><option value="two">Two owners</option><option value="shared">At least half the league</option><option value="captain">Unique captains</option></select></label></div><div id="analytics-ownership"></div><p class="fine-print">${esc(current.definition)}</p>` : `<p>${esc(current.message)}</p>`)}
      ${current.available ? card("Current squad similarity", `<p class="fine-print">Shared players out of 15. Choose a pair to inspect overlap and captain choices.</p><div id="analytics-similarity"></div><div id="analytics-pair"></div>`) : ""}
      ${card("Counted player contribution to this league", data.contributions ? `<div id="analytics-contributions"></div>` : '<p>Available after historical squad verification.</p>', `Season · ${data.detail_coverage?.available ?? 0}/${data.coverage.scores} recorded manager–Gameweek squads verified. These are contributions across this league, not global player season totals.`)}
      ${data.contributions?.length ? card("Ownership over the season", `<div class="analytics-controls"><label>Player<select id="analytics-history-player">${data.contributions.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label></div><div id="analytics-ownership-trend"></div>` + disclosure("Most-owned players by Gameweek", table([{ label: "GW", value: "gameweek" }, { label: "Verified squads", value: "coverage" }, { label: "Most owned", value: (w) => w.most_owned?.join(", ") || null }, { label: "Owners", value: "most_owned_count" }], data.ownership_history || [])), "Counts cover verified squads, not assumed missing squads. Coverage can differ between weeks.") : ""}
      ${data.captaincy ? disclosure("League captaincy", captaincy(data.captaincy)) : ""}`;
    if (data.contributions) renderContributions(data.contributions, 20);
    if (data.contributions?.length) {
      $("analytics-history-player").onchange = () => loadOwnershipTrend();
      loadOwnershipTrend();
    }
    if (current.available) {
      $("analytics-player-filter").value = state.playerFilter;
      $("analytics-player-search").oninput = (event) => { state.playerSearch = event.target.value; renderOwnership(); };
      $("analytics-player-filter").onchange = (event) => { state.playerFilter = event.target.value; renderOwnership(); };
      renderOwnership(); renderSimilarity(current);
    }
  }
  function renderContributions(rows, count) {
    $("analytics-contributions").innerHTML = table([{ label: "Player", value: "name" }, { label: "Counted points", value: "points" }, { label: "Scoring appearances", value: "scoring_appearances" }, { label: "Chosen captaincies", value: "captaincies" }, { label: "Additional captain points", value: "additional" }], rows.slice(0, count)) + (count < rows.length ? `<button type="button" class="secondary-button" id="analytics-more-players">Show all ${rows.length} players</button>` : "");
    if ($("analytics-more-players")) $("analytics-more-players").onclick = () => renderContributions(rows, rows.length);
    bind($("analytics-contributions"));
  }
  async function loadOwnershipTrend() {
    const player = $("analytics-history-player").value, viewToken = state.viewToken;
    $("analytics-ownership-trend").textContent = "Loading recorded ownership…";
    try {
      const data = await request(`player/${player}`);
      if (viewToken !== state.viewToken || $("analytics-history-player")?.value !== player) return;
      $("analytics-ownership-trend").innerHTML = window.FPLCharts.line(data.player.name, data.history, [{ key: "owners", name: "Owners · verified squads" }, { key: "captains", name: "Chosen captains" }, { key: "coverage", name: "Squads verified" }]);
      bind($("analytics-ownership-trend"));
    } catch (error) { if (viewToken === state.viewToken) $("analytics-ownership-trend").textContent = error.message; }
  }
  function renderOwnership() {
    const c = state.data.current;
    const rows = c.players.filter((p) => p.name.toLowerCase().includes(state.playerSearch.toLowerCase()) && ({ all: true, one: p.owned === 1, two: p.owned === 2, shared: p.owned >= c.manager_count / 2, captain: p.captains === 1 })[state.playerFilter]);
    $("analytics-ownership").innerHTML = table([
      { label: "Player", value: "name" }, { label: "Position", value: "position" },
      { label: "Ownership", value: "owned", html: (p) => `${p.owned}/${c.manager_count} · ${num(p.ownership_pct)}%` },
      { label: "Starting", value: "starting" }, { label: "Benched", value: "bench" }, { label: "Captain", value: "captains" },
      { label: "Vice", value: "vice_captains" }, { label: "Effective (%)", value: "effective_pct" },
      { label: "Owned by", value: (p) => p.owners.join(", ") },
    ], rows, "Current league ownership");
    bind($("analytics-ownership"));
  }
  function renderSimilarity(current) {
    const managers = state.summary.managers;
    $("analytics-similarity").innerHTML = `<div class="table-wrap"><table class="similarity-table"><thead><tr><th>Shared / 15</th>${managers.map((m, i) => `<th title="${esc(m.name)}">${i + 1}</th>`).join("")}</tr></thead><tbody>${managers.map((a, i) => `<tr><th>${i + 1}. ${esc(a.name)}</th>${managers.map((b) => {
      const pair = current.pairs.find((p) => (p.a === a.entry_id && p.b === b.entry_id) || (p.b === a.entry_id && p.a === b.entry_id));
      return `<td>${a.entry_id === b.entry_id ? "—" : pair ? `<button type="button" data-pair-a="${pair.a}" data-pair-b="${pair.b}" style="--overlap:${pair.percentage}%" aria-label="${esc(a.name)} and ${esc(b.name)}: ${pair.shared_count} shared players">${pair.shared_count}</button>` : "—"}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table></div>`;
    $("analytics-similarity").querySelectorAll("button").forEach((button) => { button.onclick = () => {
      const pair = current.pairs.find((p) => p.a === Number(button.dataset.pairA) && p.b === Number(button.dataset.pairB));
      $("analytics-pair").innerHTML = pairDetail(pair);
    }; });
  }
  function pairDetail(pair) {
    if (!pair) return '<p class="fine-print">Current overlap is unavailable.</p>';
    const names = (players) => esc(players.map((p) => p.name).join(", ") || "None");
    return `<div class="pair-detail"><h4>${esc(pair.a_name)} &amp; ${esc(pair.b_name)} · ${pair.shared_count}/15 shared</h4><p><strong>Shared:</strong> ${names(pair.shared)}</p><p><strong>Only ${esc(pair.a_name)}:</strong> ${names(pair.only_a)}</p><p><strong>Only ${esc(pair.b_name)}:</strong> ${names(pair.only_b)}</p><p><strong>Captains:</strong> ${names(pair.captain_a)} / ${names(pair.captain_b)}</p></div>`;
  }
  function renderHeadToHead(data) {
    const a = data.a, b = data.b;
    $("analytics-content").innerHTML = `<div class="analytics-controls">${selectManager("analytics-h2h-a", a.entry_id, "Manager A")}${selectManager("analytics-h2h-b", b.entry_id, "Manager B")}</div>
      ${card(`${a.name} vs ${b.name}`, facts([[`${a.name} ahead`, num(data.a_wins)], [`${b.name} ahead`, num(data.b_wins)], ["Tied weeks", num(data.ties)], ["Average A − B", num(data.average_difference)], ["Largest A win", num(data.largest_a_win)], ["Largest B win", num(data.largest_b_win)]]), `${data.common_gameweeks} common completed Gameweeks. A finished below B in ${data.b_wins} weeks; B below A in ${data.a_wins}.`)}
      ${window.FPLCharts.line("Gameweek score comparison", data.rows, [{ key: "a", name: a.name }, { key: "b", name: b.name }])}
      ${card("Season comparison", table([{ label: "Metric", value: "metric" }, { label: a.name, value: "a" }, { label: b.name, value: "b" }], [
        { metric: "Effective points · common weeks", a: data.a_common_points, b: data.b_common_points },
        { metric: "Effective points · all recorded weeks", a: a.total_points, b: b.total_points },
        { metric: "Recorded weeks", a: a.scores_analysed, b: b.scores_analysed },
        { metric: "Times last · fully covered league weeks", a: a.times_last, b: b.times_last },
        { metric: "Transfer deductions · recorded weeks", a: a.hits.total, b: b.hits.total },
        { metric: "Unused bench · verified weeks", a: a.bench?.total, b: b.bench?.total },
        { metric: "Verified squads", a: a.detail_coverage, b: b.detail_coverage },
      ]))}
      ${card("Current squad overlap", '<button type="button" class="secondary-button" id="analytics-load-overlap">Show current overlap</button><div id="analytics-h2h-overlap"></div>', "Current squads are separate from historical results.")}`;
    const change = () => {
      const nextA = Number($("analytics-h2h-a").value), nextB = Number($("analytics-h2h-b").value);
      if (nextA === nextB) { $("analytics-h2h-b").setCustomValidity("Choose two different managers"); $("analytics-h2h-b").reportValidity(); return; }
      $("analytics-h2h-b").setCustomValidity(""); state.a = nextA; state.b = nextB; selectSection("head-to-head");
    };
    $("analytics-h2h-a").onchange = change; $("analytics-h2h-b").onchange = change;
    $("analytics-load-overlap").onclick = async () => {
      const viewToken = state.viewToken;
      $("analytics-load-overlap").disabled = true;
      $("analytics-h2h-overlap").textContent = "Loading current squads…";
      try {
        const players = await request("players");
        if (viewToken !== state.viewToken) return;
        const pair = players.current.pairs?.find((p) => (p.a === a.entry_id && p.b === b.entry_id) || (p.b === a.entry_id && p.a === b.entry_id));
        $("analytics-h2h-overlap").innerHTML = pairDetail(pair);
      } catch (error) { if (viewToken === state.viewToken) $("analytics-h2h-overlap").textContent = error.message; }
      finally { if (viewToken === state.viewToken) $("analytics-load-overlap").disabled = false; }
    };
  }
  // Scripts load before the deferred app but after their HTML containers.
  $("analytics-nav").querySelectorAll("button").forEach((button) => { button.onclick = () => selectSection(button.dataset.section); });
  return { open, cancel };
})();
