/* Small accessible SVG charts. Rendering only: every statistic comes from Python. */
window.FPLCharts = (() => {
  const esc = (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
  const number = (v) => v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  function line(title, rows, series, labelKey = "gameweek", options = {}) {
    const prefix = options.prefix ?? "GW ", unit = options.unit ?? "points", axis = options.axis ?? "Gameweek";
    const values = rows.flatMap((r) => series.map((s) => r[s.key])).filter((v) => v != null && Number.isFinite(v));
    if (!values.length) return `<p class="fine-print">No chart data available.</p>`;
    const min = options.min ?? Math.min(0, ...values), max = options.max ?? Math.max(1, ...values), range = max - min || 1;
    const x = (i) => rows.length === 1 ? 328 : 48 + i * 560 / (rows.length - 1);
    const y = (v) => 205 - (v - min) / range * 173;
    const ticks = [min, min + range / 2, max];
    const paths = series.map((s, si) => {
      let connected = false;
      const path = rows.map((r, i) => {
        if (r[s.key] == null) { connected = false; return ""; }
        const command = connected ? "L" : "M"; connected = true;
        return `${command}${x(i)},${y(r[s.key])}`;
      }).join(" ");
      const stroke = s.colour ? ` style="stroke:${esc(s.colour)}"` : "";
      const dot = s.colour ? ` style="stroke:${esc(s.colour)};fill:${esc(s.colour)}"` : "";
      return `<path d="${path}" class="chart-series series-${si}"${stroke} fill="none" stroke-width="2.5"/>${rows.map((r, i) => {
        if (r[s.key] == null) return "";
        const tip = `${prefix}${r[options.tipKey || labelKey]} · ${s.name}: ${number(r[s.key])} ${unit}`;
        return `<circle class="chart-dot series-${si}"${dot} cx="${x(i)}" cy="${y(r[s.key])}" r="4.5" tabindex="0" role="button" data-chart-tip="${esc(tip)}" aria-label="${esc(tip)}"><title>${esc(tip)}</title></circle>`;
      }).join("")}`;
    }).join("");
    const legend = series.map((s, i) => {
      const swatch = `<i class="series-${i}"${s.colour ? ` style="background:${esc(s.colour)}"` : ""}></i>${esc(s.name)}`;
      return options.managerLegend ? `<button type="button" data-sim-manager="${esc(s.key)}" aria-pressed="true" aria-label="Hide ${esc(s.name)}">${swatch}</button>` : `<span>${swatch}</span>`;
    }).join("");
    return `<figure class="chart"><figcaption>${esc(title)}</figcaption><div class="chart-legend">${legend}</div><svg viewBox="0 0 640 240" role="group" aria-label="${esc(title)}">${ticks.map((v) => `<line x1="48" y1="${y(v)}" x2="608" y2="${y(v)}" class="chart-grid"/><text x="40" y="${y(v) + 4}" text-anchor="end">${number(v)}</text>`).join("")}${paths}${rows.map((r, i) => i % Math.max(1, Math.ceil(rows.length / 8)) === 0 || i === rows.length - 1 ? `<text x="${x(i)}" y="230" text-anchor="middle">${esc(r[labelKey])}</text>` : "").join("")}</svg><p class="chart-readout" aria-live="polite">${esc(axis)} → · Hover, tap or focus a point for its value.</p><details class="chart-data"><summary>Chart data</summary><div class="table-wrap"><table><thead><tr><th>${esc(axis)}</th>${series.map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r[options.tipKey || labelKey])}</td>${series.map((s) => `<td>${number(r[s.key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details></figure>`;
  }
  function bars(title, rows, options = {}) {
    const max = options.max ?? Math.max(1, ...rows.map((r) => Math.abs(r.value || 0)));
    const unit = options.unit ?? "";
    return `<figure class="chart bar-chart"><figcaption>${esc(title)}</figcaption><div class="chart-rows">${rows.map((r) => {
      const tip = r.tip || `${r.label}: ${number(r.value)}${unit}`;
      return `<button type="button" class="bar-row" data-chart-tip="${esc(tip)}" aria-label="${esc(tip)}"><span>${esc(r.label)}${r.note ? `<small class="table-note">${esc(r.note)}</small>` : ""}</span><span class="bar-track"><i style="width:${r.value == null ? 0 : Math.abs(r.value) / max * 100}%;${r.colour ? `background:${esc(r.colour)};color:${esc(r.colour)}` : ""}" class="${r.value < 0 ? "negative" : options.showZero && r.value === 0 ? "zero-value" : ""}"></i></span><strong>${number(r.value)}${r.value == null ? "" : esc(unit)}</strong></button>`;
    }).join("")}</div><p class="chart-readout" aria-live="polite">Hover, tap or focus a bar for its value. A dash means unavailable.</p></figure>`;
  }
  function intervals(title, rows) {
    const available = rows.filter(r => Number.isFinite(r.value) && r.low != null && r.high != null);
    if (!available.length) return `<p class="fine-print">Simulated score intervals are unavailable.</p>`;
    const min = Math.floor(Math.min(...available.flatMap(r => [r.low, r.value])) - 1);
    const max = Math.ceil(Math.max(...available.flatMap(r => [r.high, r.value])) + 1);
    const position = value => 100 * (value - min) / (max - min);
    return `<figure class="chart interval-chart"><figcaption>${esc(title)}</figcaption><div class="interval-axis"><span>Manager</span><span><b>${number(min)}</b><b>${number(max)}</b></span><span>Mean</span></div><div class="chart-rows">${rows.map(r => {
      const ready = available.includes(r);
      const tip = ready ? `${r.label}: projected ${number(r.value)} points; 10th–90th percentile ${number(r.low)} to ${number(r.high)} points` : `${r.label}: simulated score interval unavailable`;
      return `<button type="button" class="interval-row" data-chart-tip="${esc(tip)}" aria-label="${esc(tip)}"><span>${esc(r.label)}</span><span class="interval-track" style="--manager-colour:${esc(r.colour)}">${ready ? `<i class="interval-range" style="left:${position(r.low)}%;width:${position(r.high)-position(r.low)}%"></i><i class="interval-dot" style="left:${position(r.value)}%"></i>` : ""}</span><strong>${ready ? number(r.value) : "—"}</strong></button>`;
    }).join("")}</div><p class="chart-readout" aria-live="polite">Dot: projected mean. Line: simulated 10th–90th percentile. Hover, tap or focus a row for exact values.</p></figure>`;
  }
  function bind(root) {
    root.querySelectorAll("[data-chart-tip]").forEach((element) => {
      const show = () => { element.closest(".chart").querySelector(".chart-readout").textContent = element.dataset.chartTip; };
      element.onmouseenter = show; element.onfocus = show; element.onclick = show;
      element.onkeydown = (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); show(); } };
    });
  }
  return { line, bars, intervals, bind };
})();
