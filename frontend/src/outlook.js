/* Presentation only: projections, risk, conditions and comparison maths come from Python. */
window.ForfeitOutlook = (() => {
  let baseline = null;
  let modified = null;
  let conditions = [];
  let forecastError = "";
  let scenarioError = "";
  let loading = false;
  let scenarioLoading = false;
  let controller = null;
  let scenarioController = null;
  let revision = 0;
  let fixtureId = null;
  let playerId = null;
  let teamId = null;
  let kind = "goal";
  let resetDraft = false;
  let recordedHistory = null;
  const pct = (value) => value == null ? "Pending" : `${(100 * value).toFixed(1)}%`;
  const signed = (value) => `${value > 0 ? "+" : ""}${Number(value).toFixed(1)}`;
  const shortNames = (names) => names.length > 2 ? `${names.slice(0,2).join(", ")} and ${names.length-2} others` : names.join(", ");
  const h = (value) => escapeHtml(value);
  const rows = () => new Map((baseline?.managers || []).map((r) => [r.entry_id, r]));
  const refresh = () => { if (appState.data && appState.view !== "analytics") renderApplication(); };

  async function read(url, options, abortController) {
    const timer = setTimeout(() => abortController.abort(), 60000);
    try {
      const response = await fetch(url, { ...options, signal: abortController.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the selected football conditions.");
      if (data.source !== appState.data?.league.fetched_at) throw new Error("Scores changed. Refresh before comparing scenarios.");
      return data;
    } finally { clearTimeout(timer); }
  }

  function cancel() {
    controller?.abort(); scenarioController?.abort(); revision++;
  }

  async function start(data, generation) {
    cancel();
    baseline = data.outlook || null; recordedHistory = null;
    modified = null; conditions = []; forecastError = ""; scenarioError = "";
    fixtureId = null; playerId = null; teamId = null; kind = "goal";
    scenarioLoading = false;
    loading = data.league.status !== "complete";
    refresh();
    if (!loading) {
      if (!data.demo?.active) {
        controller = new AbortController();
        try {
          const response = await fetch(detailEndpoint("risk-history") + `&gameweek=${data.league.gameweek}`, {signal:controller.signal});
          const history = await response.json();
          if (response.ok && generation === appState.generation) { recordedHistory = history; refresh(); }
        } catch (_) { /* Final standings stay available when history cannot load. */ }
      }
      return;
    }
    controller = new AbortController();
    try {
      const payload = await read(detailEndpoint("outlook"), {}, controller);
      if (generation !== appState.generation) return;
      baseline = payload;
    } catch (failure) {
      if (generation !== appState.generation) return;
      forecastError = failure.name === "AbortError" ? "Risk estimation timed out. Projections remain available; refresh to retry." : failure.message;
    } finally {
      if (generation === appState.generation) { loading = false; refresh(); }
    }
  }

  function assumptions() {
    if (!baseline) return "";
    const coverage = baseline.coverage;
    return `<details class="forecast-assumptions"><summary>Model assumptions & uncertainty</summary>
      <p>Experimental internal estimates · ${baseline.simulations.toLocaleString()} samples · reproducible seed ${baseline.seed} · ${h(baseline.model_version)}.</p>
      <p>${h(baseline.tie_convention)}</p>
      <p>${coverage.remaining_owned_players_using_priors} remaining owned players use fallback history. ${coverage.live_player_breakdowns_missing_and_frozen} live player breakdowns are missing and frozen. Team history ${coverage.team_history_available ? "available" : "unavailable: fallback scoring rates apply"}.</p>
      <p>Official expected-stat coverage: ${coverage.players_with_expected_stats ?? 0} owned players. Completed appearance history: ${coverage.players_with_appearance_history ?? 0}. Verified external lineup records: ${coverage.confirmed_lineup_players ?? 0}.</p>
      <ul>${baseline.assumptions.map((a) => `<li>${h(a)}</li>`).join("")}</ul>
    </details>`;
  }

  function eventCards(lookup) {
    const events = baseline?.dangerous_events || [];
    return `<section class="section-block next-events"><div class="section-header"><div><h2>What could change the result?</h2><p>Events associated with a material risk swing in the shared forecast.</p></div></div>
      <div class="decisive-grid">${events.map((event,index) => {
        const lead = event.managers[0];
        const names = (ids) => shortNames(ids.map(id=>lookup.get(id)?.manager_name || String(id)));
        return `<article class="event-card"><span class="table-note">${pct(event.occurrence_probability)} modelled occurrence · ${event.matching_samples} matching draws</span><h3>${h(event.label)}</h3>
          <p>${event.benefits_entry_ids.length ? `Helps ${h(names(event.benefits_entry_ids))}. ` : ""}${event.vulnerable_entry_ids.length ? `More vulnerable: ${h(names(event.vulnerable_entry_ids))}.` : ""}</p>
          <strong>${h(lookup.get(lead.entry_id)?.manager_name)}: ${pct(lead.baseline_risk)} → ${pct(lead.event_risk)}</strong><span class="table-note">${signed(100*lead.risk_change)} percentage points · ${signed(lead.expected_points_swing)} expected points</span>
          ${event.projected_loser_changes ? '<p class="event-change">Projected last-place manager changes.</p>' : ""}
          <details><summary>Impact & uncertainty</summary><ul>${event.managers.map(r=>`<li>${h(lookup.get(r.entry_id)?.manager_name)}: ${signed(100*r.risk_change)} pp; sampling band ${r.sampling_band.map(v=>signed(100*v)).join(" to ")} pp; ${signed(r.expected_points_swing)} points.</li>`).join("")}</ul><p>${h(event.uncertainty_note)}</p></details>
          <button type="button" class="text-button" data-next-event="${index}">Explore a forced version</button></article>`;
      }).join("") || `<p class="fine-print">${loading ? "Screening events alongside risk estimates…" : "No screened event exceeds the sampling threshold. Football and model uncertainty remain."}</p>`}</div>
      <p class="fine-print">Conditional associations from existing draws. Goals mean at least one further goal; a substitute event means any cameo. Explorer tests a chosen number of returns or minutes as a forced intervention. Sampling bands exclude model error.</p></section>`;
  }

  function fixtureTimeline(lookup) {
    const forecasts = baseline?.fixture_forecasts || [];
    const timeline = baseline?.fixture_timeline || [];
    if (!timeline.length) return "";
    const time = value => value ? new Date(value).toLocaleString([], {weekday:"short",hour:"2-digit",minute:"2-digit"}) : "Kickoff order unavailable";
    return `<section class="section-block fixture-intelligence"><div class="section-header"><div><h2>The remaining fixture timeline</h2><p>Overlapping matches are grouped. Bottom at a checkpoint is separate from final forfeit risk.</p></div></div>
      <ol class="fixture-timeline">${timeline.map((group,index)=>`<li><span class="table-note">${h(time(group.kickoff_time))} · ${group.samples} shared draws</span><h3>${h(group.label)}</h3>
        <p>${group.remaining_uncertainty_sd.toFixed(1)} points of remaining uncertainty (SD)${group.expected_to_clarify ? " · Expected to clarify the contest" : ""}.</p>
        <details data-card="fixture-group-${index}"><summary>Checkpoint risks & match exposure</summary>
          <div class="table-wrap"><table><thead><tr><th>Manager</th><th>Mean checkpoint score</th><th>Bottom at checkpoint</th><th>Final forfeit chance</th><th>Remaining points: 80% range</th></tr></thead><tbody>${group.managers.map(r=>`<tr><th>${h(lookup.get(r.entry_id)?.manager_name)}</th><td>${r.projected_checkpoint_score.toFixed(1)}</td><td>${pct(r.checkpoint_bottom_probability)}</td><td>${pct(r.final_last_probability)}</td><td>${r.remaining_contribution_interval_80.join(" to ")}</td></tr>`).join("")}</tbody></table></div>
          ${forecasts.filter(f=>group.fixture_ids.includes(f.fixture_id)).map(f=>`<article class="fixture-exposure"><h4>${h(f.label)} · importance #${f.importance_rank}</h4><p>Typical relative contribution spread: ${f.relative_influence.toFixed(1)} points (80% range ${f.relative_swing_interval_80.join(" to ")}). Importance follows association with final risk, then relative points spread.</p>
            <p>${f.players.map(p=>`${h(p.name)}${p.captained_by_entry_ids.length ? ` (captain: ${h(shortNames(p.captained_by_entry_ids.map(id=>lookup.get(id)?.manager_name || id)))})` : ""}`).join(" · ")}</p>
            <ul>${f.managers.map(r=>`<li>${h(lookup.get(r.entry_id)?.manager_name)}: ${signed(r.expected_effective_contribution)} effective points; 80% range ${r.contribution_interval_80.join(" to ")}.</li>`).join("")}</ul>
            <button type="button" class="text-button" data-timeline-fixture="${f.fixture_id}">Explore this match</button><p class="fine-print">${h(f.note)}</p></article>`).join("")}
          <p class="fine-print">${h(group.note)} Remaining uncertainty measures sampled final-minus-checkpoint score dispersion, including captaincy and autosub changes; it is not a calibrated forecast error.</p></details></li>`).join("")}</ol></section>`;
  }

  function historyView(data) {
    const history = recordedHistory || baseline?.risk_history;
    if (!history && loading) return "";
    const lookup = managerMap(data);
    const players = new Map(data.managers.flatMap(m=>m.squad.map(p=>[p.player_id,p.name || p.player_name])));
    const stamp = value => new Date(value).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
    return `<section class="section-block risk-history"><div class="section-header"><div><h2>Recorded risk movement</h2><p>Only forecasts saved at the time appear here.</p></div></div>
      ${baseline?.history_recording_warning ? `<p class="fine-print">${h(baseline.history_recording_warning)}</p>` : ""}
      ${(history?.series || []).map((group,index)=>{
        const series = group.contender_entry_ids.map(id=>({key:String(id),name:lookup.get(id)?.manager_name || String(id)}));
        const observations = group.observations.map(o=>({time:stamp(o.source),...Object.fromEntries(o.managers.map(r=>[String(r.entry_id),100*r.last_probability]))}));
        const latest = group.observations.at(-1);
        return `<details data-card="risk-series-${group.series_id}" ${index===0 ? "open" : ""}><summary>${h(group.configuration.model_version)} · ${group.observations.length} observations · ${group.configuration.tie_rule === "include" ? "ties included" : "outright last"}</summary>
          <p class="fine-print">${group.configuration.simulations} draws · seed ${group.configuration.seed}. Series with different settings, models or members are kept separate. Times use your timezone; checkpoints are spaced by observation, not elapsed time.</p>
          ${window.FPLCharts.line("Estimated last-place probability (%)",observations,series,"time",{prefix:"",unit:"%",axis:"Source time"})}
          <ul>${latest.managers.filter(r=>group.contender_entry_ids.includes(r.entry_id)).map(r=>`<li>${h(lookup.get(r.entry_id)?.manager_name)}: ${pct(r.last_probability)}${r.risk_change == null ? " · first observation" : ` · ${signed(100*r.risk_change)} pp since the preceding compatible observation`}.</li>`).join("")}</ul>
          <p class="fine-print">Latest source: ${h(new Date(latest.source).toLocaleString())}. Recorded: ${h(new Date(latest.recorded_at).toLocaleString())}.</p>
          ${latest.player_changes.length || latest.annotations.length ? `<p>Between the latest two observations: ${latest.player_changes.slice(0,8).map(p=>`${h(players.get(p.player_id) || `Player ${p.player_id}`)} ${signed(p.points_change)} official points, ${signed(p.minutes_change)} minutes`).join("; ")}${latest.annotations.length ? `${latest.player_changes.length ? "; " : ""}${latest.annotations.length} fixture/coverage updates` : ""}. These changes provide context; they do not allocate the risk movement to one player or event.</p>` : ""}</details>`;
      }).join("") || `<p class="fine-print">${h(history?.note || "Recorded risk history is unavailable for this observation.")}</p>`}
      ${history?.series?.length ? `<p class="fine-print">${h(history.note)}</p>` : ""}</section>`;
  }

  function overview(data) {
    const lookup = managerMap(data);
    const names = (ids) => ids.map((id) => lookup.get(id)?.manager_name).filter(Boolean).join(" & ");
    const current = names(data.summary.bottom_entry_ids);
    const projected = names(baseline?.projected_bottom_entry_ids || data.outlook?.projected_bottom_entry_ids || []);
    const risk = (baseline?.managers || []).filter((r) => r.last_probability != null).sort((a,b) => b.last_probability - a.last_probability);
    const high = risk[0];
    const rowLookup = rows();
    const envelope = `${data.model.minimum_remaining_player_contribution}/+${data.model.maximum_remaining_player_contribution}`;
    const ordered = baseline?.managers?.map((r) => lookup.get(r.entry_id)).filter(Boolean) || data.managers;
    const battle = data.managers.filter(m=>m.status!=="safe").sort((a,b)=>
      (rowLookup.get(b.entry_id)?.last_probability || 0) - (rowLookup.get(a.entry_id)?.last_probability || 0)
      || (rowLookup.get(a.entry_id)?.projected_score ?? a.effective_score) - (rowLookup.get(b.entry_id)?.projected_score ?? b.effective_score));
    const decisive = baseline?.decisive_players || [];
    return `<div class="outlook-headlines">
      <article class="outlook-headline"><span>Currently at the bottom</span><strong>${h(current)}</strong><small>Confirmed effective points · ${data.model.ties_count_as_last ? "bottom ties count" : "outright last only"}</small></article>
      <article class="outlook-headline"><span>Projected bottom</span><strong>${h(projected || "Unavailable")}</strong><small>Model mean final score · includes uncertain appearances</small></article>
      <article class="outlook-headline"><span>Highest estimated risk</span><strong>${high ? `${h(lookup.get(high.entry_id)?.manager_name)} · ${pct(high.last_probability)}` : loading ? "Estimating risk…" : "Risk unavailable"}</strong><small>${baseline?.state === "ready" ? `${baseline.simulations.toLocaleString()} shared-fixture samples · uncalibrated model` : "Point projections available while risk loads"}</small></article>
    </div>
    ${forecastError ? `<p class="outlook-message" role="status">${h(forecastError)}</p>` : ""}
    <section class="section-block"><div class="section-header"><div><h2>The forfeit outlook</h2><p>Confirmed points and estimated final scores, side by side. Select a manager for their squad and exposure.</p></div></div>
      <div class="battle-grid">${battle.slice(0,4).map(m=>{
        const r=rowLookup.get(m.entry_id);
        return `<article class="battle-card"><button type="button" class="text-button" data-open-manager="${m.entry_id}">${h(m.manager_name)}</button><strong class="battle-risk">${pct(r?.last_probability)}</strong><span class="table-note">estimated chance of last · ${m.remaining_player_count} still involved</span><div class="battle-scores"><span>Live <strong>${m.effective_score}</strong></span><span>Projected <strong>${r?.projected_score.toFixed(1) || "—"}</strong></span></div><span class="status-badge ${statusClass(m)}">${m.status === "can_finish_last" ? "AT RISK · route found" : m.status === "no_modelled_path" ? "NO MODELLED PATH" : "UNRESOLVED · exact search"}</span></article>`;
      }).join("")}</div>
      <p class="fine-print">Leading modelled risks are shown first. ${battle.length>4 ? `${battle.length-4} further managers remain outside the SAFE group; inspect the full table below. ` : ""}A sampled 0% does not prove safety. SAFE uses the practical ${h(envelope)} total remaining contribution envelope.</p>
      <details class="full-standings" data-card="full-standings"><summary>Detailed projected standings · all ${data.managers.length} managers</summary>
      <div class="table-wrap"><table class="outlook-table"><thead><tr><th>Projected rank / Manager</th><th>Live</th><th>Projected</th><th>Remaining</th><th>Yet to play</th><th>Gap to projected bottom</th><th>Last-place estimate</th><th>Proof / search</th></tr></thead><tbody>
      ${ordered.map((m) => {
        const r = rowLookup.get(m.entry_id);
        const pending = m.squad.filter((p) => p.remaining_fixtures.some((f) => !f.started) && (p.effective_multiplier > 0 || p.autosub_status === "possible")).length;
        return `<tr class="${baseline?.projected_bottom_entry_ids.includes(m.entry_id) ? "projected-bottom" : ""}">
          <th><button type="button" class="text-button" data-open-manager="${m.entry_id}">${r?.projected_rank || "—"}. ${h(m.manager_name)}</button>${m.is_bottom ? '<span class="table-note">Currently at bottom</span>' : ""}</th>
          <td>${m.effective_score}</td><td><strong>${r?.projected_score.toFixed(1) || "—"}</strong>${r ? `<span class="table-note">80% range ${r.score_interval_80.join("–")}</span>` : ""}</td>
          <td>${r ? signed(r.expected_remaining) : "—"}</td><td>${pending}<span class="table-note">${m.remaining_player_count} still involved</span></td><td>${r ? signed(r.gap_from_projected_bottom) : "—"}</td>
          <td>${r?.last_probability != null ? `<strong>${pct(r.last_probability)}</strong><span class="risk-meter" style="--risk:${100*r.last_probability}%"></span><span class="table-note">${r.risk_label} estimated risk</span>` : loading ? "Estimating…" : "Unavailable"}</td>
          <td><span class="status-badge ${statusClass(m)}">${m.status === "safe" ? "SAFE in envelope" : m.status === "can_finish_last" ? "AT RISK · route found" : m.status === "no_modelled_path" ? "NO MODELLED PATH" : "UNRESOLVED"}</span></td></tr>`;
      }).join("")}</tbody></table></div>
      <p class="fine-print">All projections include hits, chips, captaincy and legal autosubs. A sampled 0% does not prove safety. SAFE uses the separate practical ${h(envelope)} total remaining contribution envelope.</p>
      </details>
    </section>
    ${eventCards(lookup)}${fixtureTimeline(lookup)}${historyView(data)}
    <section class="section-block"><div class="section-header"><div><h2>Players to watch</h2><p>Effective exposure across the bottom contest, including captaincy and pending substitutions.</p></div><button type="button" class="secondary-button" data-explore>Explore an outcome</button></div>
      <div class="decisive-grid">${decisive.slice(0,5).map((p) => `<article class="decisive-card"><span class="table-note">${h(p.team_name)} · ${p.status === "live" ? "Fixture live" : "Yet to play"}</span><h3>${h(p.player_name)}</h3><strong>~${p.swing_points} point swing</strong><p>A strong return helps ${h(shortNames(p.strong_performance_helps))}. A smaller return favours ${h(shortNames(p.blank_helps))}.</p><details><summary>Why this matters</summary><p>${h(p.explanation)}</p><p>Higher exposure: ${h(p.strong_performance_helps.join(", "))}. Lower exposure: ${h(p.blank_helps.join(", "))}.</p><p>${h(p.association_note)}</p></details><div class="decisive-actions">${[["goal","Goal"],["blank","Blank"],...(["GK","DEF","MID"].includes(p.position) ? [["clean_sheet","Clean sheet"]] : [])].map(([event,label])=>`<button type="button" class="text-button" data-explore-player="${p.player_id}" data-explore-fixture="${p.fixture_ids[0]}" data-explore-kind="${event}" data-explore-team="${p.team_id}">Test ${label.toLowerCase()}</button>`).join("")}</div></article>`).join("") || '<p class="fine-print">No substantial remaining differential appears in these samples. Pending absences can still affect effective exposures; inspect Managers.</p>'}</div>
      <div class="fixture-strip">${(baseline?.important_fixtures || []).map((f) => `<span>${h(f.label)} · ${h(f.status)}</span>`).join("")}</div>
    </section>${assumptions()}`;
  }

  function managerFacts(manager) {
    const r = rows().get(manager.entry_id);
    if (!r || baseline?.state === "complete") return "";
    const helps = (baseline.decisive_players || []).filter((p) => p.strong_performance_helps_entry_ids?.includes(manager.entry_id));
    const hurts = (baseline.decisive_players || []).filter((p) => p.blank_helps_entry_ids?.includes(manager.entry_id));
    return `<div class="manager-outlook"><div class="manager-facts">
      <div class="manager-fact"><span>Projected final</span><strong>${r.projected_score.toFixed(1)}</strong></div>
      <div class="manager-fact"><span>Estimated last-place chance</span><strong>${pct(r.last_probability)}</strong></div>
      <div class="manager-fact"><span>Outright / tied bottom</span><strong>${pct(r.outright_last_probability)} / ${pct(r.tied_last_probability)}</strong></div>
      <div class="manager-fact"><span>80% sampled score range</span><strong>${r.score_interval_80.join("–")}</strong></div>
      </div><p>${r.probability_interval_95 ? `95% sampling interval for last place: ${r.probability_interval_95.map(pct).join("–")}. ` : ""}Model error is not included.</p>
      <p>${manager.status === "safe" ? h(manager.safety_detail) : `Projected gap to the bottom: ${r.gap_from_projected_bottom.toFixed(1)} points. Remaining returns and absences can change this; the search status and estimated risk are separate.`}</p>
      <p>Strong performances help: ${h(helps.map(p=>p.player_name).join(", ") || "no leading differential")}. Rivals' strong performances hurt: ${h(hurts.map(p=>p.player_name).join(", ") || "no leading differential")}.</p>
      <p>Mean effective captaincy: ${r.captaincy_exposure.map((p)=>`${h(p.player_name)} ×${p.mean_multiplier.toFixed(2)}`).join(" · ")}. Includes absence/handover uncertainty.</p>
      <button type="button" class="secondary-button" data-explore>Explore outcomes</button>
      <button type="button" class="text-button" data-exact-manager="${manager.entry_id}">Inspect exact examples</button></div>`;
  }

  function explorer(data) {
    if (data.league.status === "complete") return '<p class="phase-note">This Gameweek is complete. Hypothetical events cannot change its final results.</p>';
    const catalog = baseline?.catalog || data.outlook?.catalog || [];
    const fixture = catalog.find((f) => f.fixture_id === fixtureId) || catalog[0];
    if (!fixture) return '<p class="phase-note">No unfinished fixtures are available to explore.</p>';
    fixtureId = fixture.fixture_id;
    const player = fixture.players.find((p) => p.player_id === playerId) || fixture.players[0];
    playerId = player?.player_id || null;
    const currentForm = document.querySelector("#what-if-form");
    const sameForm = !resetDraft && currentForm && Number(currentForm.querySelector("#what-if-fixture")?.value) === fixtureId && currentForm.querySelector("#what-if-kind")?.value === kind;
    resetDraft = false;
    const draftValue = (id, fallback) => h(sameForm ? currentForm.querySelector(id)?.value ?? fallback : fallback);
    const teamKind = ["clean_sheet", "lose_clean_sheet"].includes(kind);
    const scoreKind = kind === "score";
    const maxMinutes = kind === "substitute" ? player?.maximum_substitute_minutes ?? 90 : player?.maximum_final_minutes ?? 90;
    const minMinutes = kind === "minutes" ? Math.max(1, player?.minutes || 0) : 1;
    const minutesKind = ["minutes","substitute"].includes(kind);
    const unavailable = minutesKind && maxMinutes < minMinutes;
    const result = modified || baseline;
    const lookup = managerMap(data);
    return `<section class="explorer-panel"><div class="view-heading"><div><p class="kicker">Try the football events that matter</p><h2>Scenario Explorer</h2><p>Combine outcomes and compare the estimated final table with the unmodified baseline.</p></div></div>
      <form id="what-if-form" class="explorer-controls">
        <label>Fixture<select id="what-if-fixture">${catalog.map((f) => `<option value="${f.fixture_id}" ${f.fixture_id === fixtureId ? "selected" : ""}>${h(f.label)}${f.started ? " · Live" : ""}</option>`).join("")}</select></label>
        <label>Outcome<select id="what-if-kind">${[["goal","Scores a goal"],["assist","Gets an assist"],["blank","No further goals / assists"],["dnp","Does not appear"],["clean_sheet","Team clean sheet"],["lose_clean_sheet","Team concedes"],["substitute","Comes on as a substitute"],["minutes","Final playing minutes (advanced)"],["score","Final match score (advanced)"]].map(([k,label]) => `<option value="${k}" ${kind === k ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        ${teamKind ? `<label>Team<select id="what-if-team">${[[fixture.home_team_id,fixture.home_team_name],[fixture.away_team_id,fixture.away_team_name]].map(([id,name])=>`<option value="${id}" ${String(teamId ?? draftValue("#what-if-team",fixture.home_team_id)) === String(id) ? "selected" : ""}>${h(name)}</option>`).join("")}</select></label>` : scoreKind ? `<label>Home goals<input id="what-if-home" type="number" min="0" max="15" value="${draftValue("#what-if-home",2)}" required></label><label>Away goals<input id="what-if-away" type="number" min="0" max="15" value="${draftValue("#what-if-away",1)}" required></label>` : `<label>Player<select id="what-if-player">${fixture.players.map((p)=>`<option value="${p.player_id}" ${p.player_id === playerId ? "selected" : ""}>${h(p.name)}</option>`).join("")}</select></label>`}
        ${["goal","assist","minutes","substitute"].includes(kind) ? `<label>${minutesKind ? "Final fixture minutes" : "Further returns"}<input id="what-if-value" type="number" min="${minutesKind ? minMinutes : 1}" max="${minutesKind ? maxMinutes : 6}" value="${draftValue("#what-if-value",minutesKind ? Math.max(minMinutes,Math.min(20,maxMinutes)) : 1)}" ${unavailable ? "disabled" : ""} required></label>` : ""}
        <button class="primary-button" type="submit" ${conditions.length >= 12 || unavailable ? "disabled" : ""}>Add condition</button>
      </form>
      <p class="fine-print">${unavailable ? "This player has already featured or has no time left for the selected appearance. Choose another player or event. " : ""}A blank still earns appearance and other points. Selected goals/assists are additional from now; the fixture score and team clean sheets update together. Advanced minutes apply to this fixture only, including Double Gameweeks.</p>
      <ul class="condition-list">${conditions.map((c,i) => `<li>${h(c.label || conditionLabel(c,fixture,catalog))}<button type="button" class="text-button" data-remove-condition="${i}" aria-label="Remove condition ${i+1}">Remove</button></li>`).join("")}</ul>
      <div class="inline-actions">${conditions.length ? '<button type="button" class="secondary-button" data-reset-conditions>Reset scenario</button>' : ""}<span role="status" aria-live="polite">${scenarioLoading ? "Recalculating shared outcomes…" : conditions.length && modified ? "Forced-event estimate ready" : "Unmodified baseline"}</span></div>
      ${scenarioError ? `<p class="outlook-message" role="alert">${h(scenarioError)}</p>` : ""}
      ${modified ? `<p class="condition-box">Projected bottom: ${h(modified.projected_bottom_entry_ids.map(id=>lookup.get(id)?.manager_name).join(" & "))}. ${modified.projected_last_entry_ids.length ? "" : "No sole projected last under the strict tie rule. "}${h(modified.comparison_note || "")}</p>` : ""}
      ${result ? `<div class="table-wrap"><table class="outlook-table"><thead><tr><th>Manager</th><th>Live</th><th>${modified ? "Scenario" : "Baseline"} mean final</th><th>Point change</th><th>Last-place estimate</th><th>Risk change</th></tr></thead><tbody>${result.managers.map((r) => `<tr><th>${h(lookup.get(r.entry_id)?.manager_name)}</th><td>${r.current_score}</td><td>${r.projected_score.toFixed(1)}</td><td>${r.score_change == null ? "—" : signed(r.score_change)}</td><td>${pct(r.last_probability)}</td><td>${r.risk_change == null ? "—" : `${signed(100*r.risk_change)} pp`}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <p class="fine-print">Forced-event interventions sample the other players and fixtures. They are not Bayesian conditional probabilities, a fixed final result or a necessary route to last place. Invalid conditions show an explanation and leave the baseline visible.</p>
    </section>`;
  }

  function conditionLabel(c,fixture,catalog) {
    const f = catalog.find(f=>f.fixture_id===c.fixture_id) || fixture;
    const who = f.players.find(p=>p.player_id===c.player_id)?.name || (c.team_id === f.home_team_id ? f.home_team_name : f.away_team_name);
    return c.kind === "score" ? `${f.label}: ${c.home_goals}–${c.away_goals}` : `${who}: ${c.kind.replaceAll("_"," ")}${["goal","assist","minutes","substitute"].includes(c.kind) ? ` (${c.value})` : ""}`;
  }

  async function recalculate() {
    scenarioController?.abort();
    const localRevision = ++revision;
    modified = null; scenarioError = "";
    if (!conditions.length) { scenarioLoading = false; refresh(); return; }
    scenarioLoading = true; refresh();
    scenarioController = new AbortController();
    const generation = appState.generation;
    try {
      const payload = await read(detailEndpoint("what-if"), {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({source: appState.data.league.fetched_at,
          conditions: conditions.map(({label, ...c})=>c)})
      }, scenarioController);
      if (localRevision !== revision || generation !== appState.generation) return;
      modified = payload; conditions = payload.conditions;
    } catch (failure) {
      if (localRevision !== revision || generation !== appState.generation) return;
      scenarioError = failure.name === "AbortError" ? "Scenario calculation timed out. Remove a condition or refresh to retry." : failure.message;
    } finally {
      if (localRevision === revision && generation === appState.generation) { scenarioLoading = false; refresh(); }
    }
  }

  document.addEventListener("submit", (event) => {
    if (event.target.id !== "what-if-form") return;
    event.preventDefault();
    const c = {fixture_id: fixtureId, kind};
    if (["clean_sheet","lose_clean_sheet"].includes(kind)) c.team_id = Number(document.querySelector("#what-if-team").value);
    else if (kind === "score") { c.home_goals = Number(document.querySelector("#what-if-home").value); c.away_goals = Number(document.querySelector("#what-if-away").value); }
    else { c.player_id = Number(document.querySelector("#what-if-player").value); if (["goal","assist","minutes","substitute"].includes(kind)) c.value = Number(document.querySelector("#what-if-value").value); }
    conditions.push(c); recalculate();
  });
  document.addEventListener("change", (event) => {
    if (event.target.id === "what-if-fixture") { fixtureId = Number(event.target.value); playerId = null; teamId = null; refresh(); }
    if (event.target.id === "what-if-team") teamId = Number(event.target.value);
    if (event.target.id === "what-if-kind") { kind = event.target.value; refresh(); }
    if (event.target.id === "what-if-player") { playerId = Number(event.target.value); refresh(); }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-remove-condition")) { conditions.splice(Number(button.dataset.removeCondition),1); recalculate(); }
    if (button.hasAttribute("data-reset-conditions")) { conditions=[]; recalculate(); }
    if (button.hasAttribute("data-next-event") || button.hasAttribute("data-timeline-fixture")) {
      const selected = baseline?.dangerous_events?.[Number(button.dataset.nextEvent)]?.condition;
      fixtureId = selected?.fixture_id || Number(button.dataset.timelineFixture);
      playerId = selected?.player_id || null; teamId = selected?.team_id || null;
      kind = selected?.kind || "goal"; resetDraft = true;
      activateView("scenarios"); refresh(); document.querySelector("#explorer-content").scrollIntoView({block:"start"});
    }
    if (button.hasAttribute("data-explore") || button.hasAttribute("data-explore-player")) {
      if (button.dataset.explorePlayer) { playerId=Number(button.dataset.explorePlayer); fixtureId=Number(button.dataset.exploreFixture); kind=button.dataset.exploreKind || "goal"; teamId=Number(button.dataset.exploreTeam) || null; resetDraft=true; }
      activateView("scenarios"); refresh(); document.querySelector("#explorer-content").scrollIntoView({block:"start"});
    }
    if (button.hasAttribute("data-open-manager")) {
      activateView("managers");
      const card = document.querySelector(`[data-card="manager-${button.dataset.openManager}"]`);
      card.open = true; card.scrollIntoView({block:"start"});
    }
    if (button.hasAttribute("data-exact-manager")) {
      appState.selectedManager=Number(button.dataset.exactManager); activateView("scenarios"); refresh();
      document.querySelector("#exact-heading").scrollIntoView({block:"start"});
    }
  });
  return { start, cancel, overview, explorer, managerFacts, historyView };
})();
