/* Small accessible SVG charts. Rendering only: every statistic comes from Python. */
window.FPLCharts = (() => {
  const esc = (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
  const number = (v) => v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  function line(title, rows, series, labelKey = "gameweek") {
    const values = rows.flatMap((r) => series.map((s) => r[s.key])).filter((v) => v != null && Number.isFinite(v));
    if (!values.length) return `<p class="fine-print">No chart data available.</p>`;
    const min = Math.min(0, ...values), max = Math.max(1, ...values), range = max - min || 1;
    const x = (i) => 48 + i * 560 / Math.max(1, rows.length - 1);
    const y = (v) => 205 - (v - min) / range * 173;
    const ticks = [min, min + range / 2, max];
    const paths = series.map((s, si) => {
      let connected = false;
      const path = rows.map((r, i) => {
        if (r[s.key] == null) { connected = false; return ""; }
        const command = connected ? "L" : "M"; connected = true;
        return `${command}${x(i)},${y(r[s.key])}`;
      }).join(" ");
      return `<path d="${path}" class="chart-series series-${si}" fill="none" stroke-width="2.5"/>${rows.map((r, i) => r[s.key] == null ? "" : `<circle class="chart-dot series-${si}" cx="${x(i)}" cy="${y(r[s.key])}" r="4.5" tabindex="0" role="button" data-chart-tip="GW ${esc(r[labelKey])} · ${esc(s.name)}: ${number(r[s.key])}" aria-label="GW ${esc(r[labelKey])}, ${esc(s.name)}, ${number(r[s.key])} points"><title>GW ${esc(r[labelKey])}: ${number(r[s.key])}</title></circle>`).join("")}`;
    }).join("");
    return `<figure class="chart"><figcaption>${esc(title)}</figcaption><div class="chart-legend">${series.map((s, i) => `<span><i class="series-${i}"></i>${esc(s.name)}</span>`).join("")}</div><svg viewBox="0 0 640 240" role="group" aria-label="${esc(title)}">${ticks.map((v) => `<line x1="48" y1="${y(v)}" x2="608" y2="${y(v)}" class="chart-grid"/><text x="40" y="${y(v) + 4}" text-anchor="end">${number(v)}</text>`).join("")}${paths}${rows.map((r, i) => i % Math.max(1, Math.ceil(rows.length / 8)) === 0 || i === rows.length - 1 ? `<text x="${x(i)}" y="230" text-anchor="middle">${esc(r[labelKey])}</text>` : "").join("")}</svg><p class="chart-readout" aria-live="polite">Gameweek → · Hover, tap or focus a point for its score.</p><details class="chart-data"><summary>Chart data</summary><div class="table-wrap"><table><thead><tr><th>GW</th>${series.map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r[labelKey])}</td>${series.map((s) => `<td>${number(r[s.key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details></figure>`;
  }
  function bars(title, rows) {
    const max = Math.max(1, ...rows.map((r) => Math.abs(r.value || 0)));
    return `<figure class="chart bar-chart"><figcaption>${esc(title)}</figcaption>${rows.map((r) => `<button type="button" class="bar-row" data-chart-tip="${esc(r.label)}: ${number(r.value)}" aria-label="${esc(r.label)}: ${number(r.value)}"><span>${esc(r.label)}</span><span class="bar-track"><i style="width:${r.value == null ? 0 : Math.abs(r.value) / max * 100}%" class="${r.value < 0 ? "negative" : ""}"></i></span><strong>${number(r.value)}</strong></button>`).join("")}<p class="chart-readout" aria-live="polite">Tap a bar for its value. A dash means unavailable.</p></figure>`;
  }
  function bind(root) {
    root.querySelectorAll("[data-chart-tip]").forEach((element) => {
      const show = () => { element.closest(".chart").querySelector(".chart-readout").textContent = element.dataset.chartTip; };
      element.onmouseenter = show; element.onfocus = show; element.onclick = show;
      element.onkeydown = (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); show(); } };
    });
  }
  return { line, bars, bind };
})();
