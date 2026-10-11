/* Presentation only. All probabilities, means, intervals and checkpoints come from the existing API. */
window.FPLSimulations = (() => {
  const h = value => escapeHtml(value);
  const percent = value => value == null ? null : 100 * value;
  const stamp = value => new Date(value).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"});
  let context = "", selected = new Set(), selectedScores = false;
  let currentData, currentResults;

  function render(data, results) {
    currentData = data; currentResults = results;
    const root = document.querySelector("#simulations-content");
    const expanded = new Map([...root.querySelectorAll("details[data-card]")].map(card => [card.dataset.card, card.open]));
    const {forecast, history, loading, error} = results;
    const lookup = new Map(data.managers.map(m => [String(m.entry_id), m]));
    const estimates = new Map((forecast?.managers || []).map(m => [String(m.entry_id), m]));
    const colours = new Map([...lookup.keys()].sort((a,b) => Number(a)-Number(b)).map((id,i) => [id, `var(--sim-colour-${i % 14})`]));
    const ranked = [...data.managers].sort((a,b) =>
      (estimates.get(String(b.entry_id))?.last_probability ?? -1) - (estimates.get(String(a.entry_id))?.last_probability ?? -1)
      || (estimates.get(String(a.entry_id))?.projected_score ?? a.effective_score) - (estimates.get(String(b.entry_id))?.projected_score ?? b.effective_score));
    const topThree = () => new Set(ranked.slice(0,3).map(m => String(m.entry_id)));
    const nextContext = JSON.stringify([data.league.id, data.league.gameweek, data.model.ties_count_as_last, data.demo?.mode, [...lookup.keys()].sort()]);
    if (nextContext !== context) { context = nextContext; selected = topThree(); selectedScores = false; }
    const seriesFor = ids => [...selected].filter(id => ids.has(id)).map(id => ({key:id,name:lookup.get(id)?.manager_name || `Manager ${id}`,colour:colours.get(id)}));
    const empty = text => `<p class="simulation-empty">${h(text)}</p>`;

    function historyChart() {
      const groups = history?.series || [];
      return `<section class="simulation-card simulation-history"><h3>Recorded risk movement</h3><p class="fine-print">Saved last-place estimates throughout this Gameweek. Use the manager colours above or the chart legend to show or hide lines.</p>
        ${groups.map((group,index) => {
          const observations = group.observations || [];
          const config = group.configuration;
          const ids = new Set(observations.flatMap(o => o.managers.map(m => String(m.entry_id))));
          const series = seriesFor(ids);
          const rows = observations.map(o => ({time:new Date(o.source).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}), source:stamp(o.source), ...Object.fromEntries(o.managers.map(m => [String(m.entry_id),percent(m.last_probability)]))}));
          return `<details data-card="simulation-history-${h(group.series_id)}" ${index === 0 ? "open" : ""}><summary>${h(config.model_version)} · ${observations.length} recorded observations</summary>
            <p class="fine-print">${h(config.simulations)} draws · seed ${h(config.seed)} · ${config.tie_rule === "include" ? "ties included" : "outright last"}. Different models, settings and league members remain separate. Source times use your timezone; spacing is by observation, not elapsed time.</p>
            ${observations.length < 2 ? empty(observations.length ? "Only one recorded observation is available. A risk trajectory needs at least two compatible observations; no movement is inferred." : "No recorded observations are available for this series.") : !series.length ? empty("Select a manager with observations in this series to compare recorded risk.") : window.FPLCharts.line("Recorded last-place estimate (%)", rows, series, "time", {prefix:"",unit:"%",axis:"Recorded source time",tipKey:"source",min:0,max:100,managerLegend:true})}
            ${observations.length ? `<p class="fine-print">Latest source: ${h(stamp(observations.at(-1).source))}. Saved: ${h(stamp(observations.at(-1).recorded_at))}.</p>` : ""}</details>`;
        }).join("") || empty(loading ? "Recorded history will appear if the existing forecast response includes it." : history?.note || "Persistent risk history is unavailable for this Gameweek. No historical observations have been invented.")}
        ${groups.length && history.note ? `<p class="fine-print">${h(history.note)}</p>` : ""}
        ${forecast?.history_recording_warning ? `<p class="fine-print">${h(forecast.history_recording_warning)}</p>` : ""}</section>`;
    }

    function checkpointChart() {
      const groups = forecast?.fixture_timeline || [];
      const ids = new Set(groups.flatMap(group => group.managers.map(m => String(m.entry_id))));
      const series = seriesFor(ids);
      const rows = groups.map((group,index) => ({group:String(index+1),fixture:`Group ${index+1}: ${group.label}`, ...Object.fromEntries(group.managers.map(m => [String(m.entry_id),percent(m.checkpoint_bottom_probability)]))}));
      return `<section class="simulation-card simulation-checkpoints"><h3>Projected fixture checkpoint risk</h3><p class="fine-print">Chance of being bottom after each remaining fixture group resolves in the existing simulation. This is a projected checkpoint, separate from recorded history and final forfeit risk.</p>
        ${!groups.length ? empty(loading ? "Fixture checkpoints are loading with the existing risk forecast." : "No remaining fixture checkpoint predictions are available.") : !series.length ? empty("Select a manager covered by the checkpoint predictions to show their risk. The backend only returns the relevant bottom-place contenders.") : window.FPLCharts.line("Projected bottom-at-checkpoint probability (%)", rows, series, "group", {prefix:"",unit:"%",axis:"Fixture group",tipKey:"fixture",min:0,max:100,managerLegend:true})}
        ${groups.length ? `<ol class="simulation-fixtures">${groups.map((group,index) => `<li><strong>Group ${index+1}</strong><span>${h(group.label)}</span><small>${group.kickoff_time ? h(stamp(group.kickoff_time)) : "Kickoff order unavailable"} · ${h(group.samples)} existing draws${group.timing_unknown ? " · timing uncertain" : ""}</small></li>`).join("")}</ol><p class="fine-print">Overlapping fixtures retain the backend's grouping. Missing manager estimates are left unavailable, not treated as zero. ${groups.length === 1 ? "Only one fixture group remains, so points rather than a trajectory are shown." : "Groups are spaced equally, not by elapsed time."}</p>` : ""}</section>`;
    }

    const picker = `<details class="simulation-picker" data-card="simulation-picker" open><summary>Compare managers · ${selected.size} selected</summary><p class="fine-print">Toggle manager colours to compare trajectories. The probability chart always includes everyone.</p><div class="simulation-actions"><button type="button" class="text-button" data-sim-action="top">Top three risks</button><button type="button" class="text-button" data-sim-action="all">Show all</button><button type="button" class="text-button" data-sim-action="none">Hide all</button></div><div class="simulation-managers" role="group" aria-label="Managers to compare">${ranked.map(m => {
      const id = String(m.entry_id);
      return `<button type="button" class="simulation-manager" data-sim-manager="${id}" aria-pressed="${selected.has(id)}" style="--manager-colour:${colours.get(id)}"><i aria-hidden="true"></i>${h(m.manager_name)}</button>`;
    }).join("")}</div></details>`;
    const envelope = `${data.model.minimum_remaining_player_contribution} to +${data.model.maximum_remaining_player_contribution}`;
    const riskRows = ranked.map(m => {
      const estimate = estimates.get(String(m.entry_id));
      const value = percent(estimate?.last_probability);
      const status = m.status === "safe" ? "SAFE in configured envelope" : m.status === "no_modelled_path" ? "NO MODELLED PATH" : m.status === "unresolved" ? "UNRESOLVED" : "AT RISK";
      const note = value === 0 && m.status !== "safe" ? `Sampled 0% · ${status}` : status;
      return {label:m.manager_name,value,colour:colours.get(String(m.entry_id)),note,tip:`${m.manager_name}: ${value == null ? "probability unavailable" : `${value.toFixed(2)}% estimated last-place probability`}. ${note}. A sampled 0% is not a safety proof. SAFE requires the separate ${envelope} remaining-contribution envelope.`};
    });
    const scoreRows = [...data.managers].filter(m => !selectedScores || selected.has(String(m.entry_id))).sort((a,b) => (estimates.get(String(a.entry_id))?.projected_score ?? Infinity) - (estimates.get(String(b.entry_id))?.projected_score ?? Infinity)).map(m => {
      const estimate = estimates.get(String(m.entry_id));
      return {label:m.manager_name,value:estimate?.projected_score,low:estimate?.score_interval_80?.[0],high:estimate?.score_interval_80?.[1],colour:colours.get(String(m.entry_id))};
    });
    root.innerHTML = `<div class="simulation-meta" role="status">${data.league.status === "complete" ? "Gameweek complete · recorded forecasts only" : forecast?.state === "ready" ? `${h(forecast.simulations.toLocaleString())} existing shared draws · ${h(forecast.model_version)}` : loading ? "Risk estimates are loading; existing score projections appear when available." : "Full risk estimates are unavailable; existing projections appear when available."}<span>Gameweek ${h(data.league.gameweek)}</span></div>
      ${error ? `<p class="outlook-message">${h(error)}</p>` : ""}${picker}
      ${data.league.status === "complete" ? empty("This Gameweek is complete. See Overview for final results; saved risk movement is shown below when available.") : `<div class="simulation-grid">
        <section class="simulation-card simulation-probabilities"><h3>Last-place probability</h3><p class="fine-print">All managers · highest risk first · bars use a 0–100% scale.</p>${window.FPLCharts.bars("Simulated chance of finishing last (%)",riskRows,{max:100,unit:"%",showZero:true})}<p class="fine-print">SAFE is proven only within the configured ${h(envelope)} total remaining contribution per player, including double Gameweeks. A sampled 0% is not proof. ${h(forecast?.tie_convention || (data.model.ties_count_as_last ? "Bottom ties count as last." : "Only outright last counts."))}</p></section>
        <section class="simulation-card simulation-scores"><h3>Projected final scores</h3><p class="fine-print">Lowest projected scores first. Compare the existing mean and 10th–90th percentile range.</p><label class="simulation-filter"><input type="checkbox" id="simulation-selected-scores" ${selectedScores ? "checked" : ""}> Show selected managers only</label>${scoreRows.length ? window.FPLCharts.intervals("Projected Gameweek points · 80% range",scoreRows) : empty("Select at least one manager to compare scores.")}<p class="fine-print">Includes the analyser's hits, captaincy, chips and automatic substitutions. Ranges describe simulated outcomes; model uncertainty is additional.</p></section>
        ${checkpointChart()}${historyChart()}</div>`}
      ${data.league.status === "complete" ? historyChart() : ""}
      <p class="simulation-distributions fine-print">Score distributions are unavailable: the existing API exposes means and percentile ranges, but no score bins or individual samples. No distribution curves are inferred.</p>`;
    root.querySelectorAll("details[data-card]").forEach(card => { if (expanded.has(card.dataset.card)) card.open = expanded.get(card.dataset.card); });
    window.FPLCharts.bind(root);
    root.onclick = event => {
      const button = event.target.closest("[data-sim-manager], [data-sim-action]");
      if (!button) return;
      const id = button.dataset.simManager;
      const action = button.dataset.simAction;
      if (id) { if (selected.has(id)) selected.delete(id); else selected.add(id); }
      else selected = action === "top" ? topThree() : action === "all" ? new Set(lookup.keys()) : new Set();
      render(currentData, currentResults);
      root.querySelector(id ? `.simulation-picker [data-sim-manager="${id}"]` : `[data-sim-action="${action}"]`)?.focus({preventScroll:true});
    };
    root.onchange = event => {
      if (event.target.id !== "simulation-selected-scores") return;
      selectedScores = event.target.checked;
      render(currentData, currentResults);
      root.querySelector("#simulation-selected-scores").focus({preventScroll:true});
    };
  }
  return {render};
})();
