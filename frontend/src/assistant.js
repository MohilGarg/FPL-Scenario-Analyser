/* Private presentation only: forecasts, squad validation and optimisation live in Python. */
(() => {
  const $ = selector => document.querySelector(selector);
  const h = value => String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");
  const number = value => value == null ? "—" : Number(value).toLocaleString(undefined,{maximumFractionDigits:1});
  const money = value => value == null ? "Unknown" : `£${(value/10).toFixed(1)}m`;
  const signed = value => value == null ? "—" : `${value > 0 ? "+" : ""}${number(value)}`;
  const time = value => new Date(value).toLocaleString();
  const state = {csrf:null,predictions:null,squad:null,lineup:null,baseline:null,swap:null,view:"team",sort:"total_expected_points",direction:-1,page:0,predictionRevision:0,moves:[],bank:"",free:"",confirmed:false};
  const controllers = new Set();
  let busy = 0;
  const broadcast = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("fpl-assistant-session") : null;

  function message(text,error=false) {
    const element = $("#assistant-message");
    element.textContent = text;
    element.classList.toggle("hidden",!text);
    element.classList.toggle("error",error);
  }
  function clearPrivate() {
    controllers.forEach(controller => controller.abort()); controllers.clear();
    Object.assign(state,{csrf:null,predictions:null,squad:null,lineup:null,baseline:null,swap:null,page:0,moves:[],bank:"",free:"",confirmed:false,sort:"total_expected_points",direction:-1});
    state.predictionRevision++;
    $("#assistant-workspace").classList.add("hidden");
    $("#assistant-locked").classList.remove("hidden");
    $("#assistant-logout").classList.add("hidden");
    $("#assistant-team-id").value = "";
    $("#assistant-password").value = "";
    for (const selector of ["#assistant-search","#assistant-position","#assistant-min-price","#assistant-max-price"]) $(selector).value = "";
    $("#assistant-club").innerHTML = '<option value="">All clubs</option>';
    $("#assistant-horizon").value = "3";
    $("#assistant-source").textContent = "";
    activate("team");
    for (const selector of ["#assistant-squad-content","#assistant-prediction-content","#assistant-transfer-content","#assistant-transfer-result","#assistant-chip-content","#assistant-player-detail"]) $(selector).replaceChildren();
    $("#assistant-model > div").replaceChildren();
    $("#assistant-player-dialog").close();
  }
  async function api(path,method="GET",body) {
    const controller = new AbortController(); controllers.add(controller);
    try {
      const headers = method === "GET" ? {} : {"Content-Type":"application/json",...(state.csrf ? {"X-CSRF-Token":state.csrf} : {})};
      const response = await fetch(`/api/assistant/${path}`,{method,headers,credentials:"same-origin",cache:"no-store",signal:controller.signal,...(body === undefined ? {} : {body:JSON.stringify(body)})});
      const data = await response.json();
      if (response.status === 401 && path !== "login") {
        clearPrivate();
        $("#assistant-login-form").classList.remove("hidden");
        $("#assistant-access-note").textContent = "Your session ended. Sign in again.";
      }
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the entered values and try again.");
      return data;
    } finally {controllers.delete(controller);}
  }
  async function run(action,loading="Loading…",interrupt=false) {
    if(busy && !interrupt)return;
    if(interrupt)controllers.forEach(controller=>controller.abort());
    busy++;
    const controls=[...document.querySelectorAll('#assistant-workspace button,#assistant-workspace input,#assistant-workspace select')].filter(control=>!control.disabled);
    controls.forEach(control=>control.disabled=true);
    $("#assistant-workspace").setAttribute('aria-busy','true');
    message(loading);
    try {await action(); message("");}
    catch(error) {if (error.name !== "AbortError") message(error.message || "Unable to contact the private backend. Try again shortly.",true);}
    finally {busy--;controls.forEach(control=>control.disabled=false);$("#assistant-workspace").setAttribute('aria-busy',String(busy>0));}
  }
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try {localStorage.setItem("fpl-theme",theme);} catch (_) {}
    $("#assistant-theme").textContent = theme === "dark" ? "Light" : "Dark";
  }
  try {setTheme(localStorage.getItem("fpl-theme") === "dark" ? "dark" : "light");} catch (_) {setTheme("light");}
  $("#assistant-theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  broadcast && (broadcast.onmessage = event => {if (event.data === "logout") {clearPrivate();$("#assistant-login-form").classList.remove("hidden");$("#assistant-access-note").textContent="Signed out in another tab.";}});

  async function loadPredictions() {
    const revision = ++state.predictionRevision;
    const data = await api(`predictions?horizon=${$("#assistant-horizon").value}`);
    if (revision !== state.predictionRevision) return;
    state.predictions = data; state.page = 0;
    const selectedClub = $("#assistant-club").value;
    const clubs = [...new Map(data.players.map(p => [p.team_id,p.team_name]))].sort((a,b) => a[1].localeCompare(b[1]));
    $("#assistant-club").innerHTML = '<option value="">All clubs</option>'+clubs.map(([id,name]) => `<option value="${id}">${h(name)}</option>`).join("");
    $("#assistant-club").value = selectedClub;
    $("#assistant-source").textContent = `Forecast data: ${time(data.data_timestamp)} · ${data.samples} shared draws · ${data.model_version}`;
    $("#assistant-model > div").innerHTML = `<ul>${data.assumptions.map(note => `<li>${h(note)}</li>`).join("")}</ul><p>${data.provenance.map(p => `${h(p.name)}: ${h(p.detail)}`).join("; ")}</p>`;
    renderPredictions();
    if (state.squad) renderTransfers();
  }
  async function unlock(session) {
    state.csrf = session.csrf;
    $("#assistant-locked").classList.add("hidden");
    $("#assistant-workspace").classList.remove("hidden");
    $("#assistant-logout").classList.remove("hidden");
    $("#assistant-squad-content").innerHTML = '<p class="assistant-empty">Load your public deadline squad to optimise a future lineup. Your FPL account is never modified.</p>';
    await loadPredictions();
  }
  function clearAccountPlans() {
    state.moves=[];state.bank="";state.free="";state.confirmed=false;state.swap=null;
    $("#assistant-transfer-result").replaceChildren();
    $("#assistant-chip-content").innerHTML='<p class="assistant-empty">Compare chip values for the loaded public squad.</p>';
  }
  $("#assistant-login-form").onsubmit = event => {
    event.preventDefault();
    const password = $("#assistant-password").value;
    $("#assistant-password").value = "";
    const button = event.target.querySelector("button"); button.disabled = true;
    run(async () => {try {await unlock(await api("login","POST",{password}));} finally {button.disabled=false;}},"Signing in securely…");
  };
  $("#assistant-logout").onclick = () => run(async () => {
    await api("logout","POST",{}); clearPrivate(); broadcast?.postMessage("logout");
    $("#assistant-access-note").textContent = "Signed out. Private data has been cleared from this page.";
    $("#assistant-login-form").classList.remove("hidden");
  },"Signing out…",true);
  $("#assistant-refresh").onclick = () => run(async () => {
    await api("refresh","POST",{}); await loadPredictions();
    if (state.squad) {state.squad=await api("squad","POST",{entry_id:state.squad.entry_id});clearAccountPlans();await loadLineup();}
  },"Refreshing the shared forecast…");
  function activate(view) {
    state.view = view;
    document.querySelectorAll("[data-assistant-view]").forEach(button => {button.setAttribute("aria-selected",String(button.dataset.assistantView===view));button.tabIndex=button.dataset.assistantView===view ? 0 : -1;});
    document.querySelectorAll(".assistant-panel").forEach(panel => panel.classList.toggle("hidden",panel.id!==`assistant-${view}`));
  }
  document.querySelectorAll("[data-assistant-view]").forEach(button => {
    button.onclick = () => activate(button.dataset.assistantView);
    button.onkeydown = event => {if (!["ArrowLeft","ArrowRight"].includes(event.key)) return;event.preventDefault();const tabs=[...document.querySelectorAll("[data-assistant-view]")];const next=tabs[(tabs.indexOf(button)+(event.key==="ArrowRight" ? 1 : -1)+tabs.length)%tabs.length];next.focus();activate(next.dataset.assistantView);};
  });
  $("#assistant-horizon").onchange = () => run(loadPredictions,"Loading the cached horizon…");
  for (const selector of ["#assistant-search","#assistant-position","#assistant-club","#assistant-min-price","#assistant-max-price"]) $(selector).addEventListener("input",() => {state.page=0;renderPredictions();});
  function renderPredictions() {
    const data = state.predictions; if (!data) return;
    const search = $("#assistant-search").value.trim().toLowerCase(), position=$("#assistant-position").value, club=$("#assistant-club").value;
    const minimum=$("#assistant-min-price").value, maximum=$("#assistant-max-price").value;
    const rows = data.players.filter(p => (!search || `${p.name} ${p.team_name}`.toLowerCase().includes(search)) && (!position || p.position===position) && (!club || String(p.team_id)===club) && (!minimum || p.price_tenths!=null && p.price_tenths>=10*Number(minimum)) && (!maximum || p.price_tenths!=null && p.price_tenths<=10*Number(maximum)));
    const value = (p,key) => key.startsWith("gw-") ? p.gameweeks.find(g => g.gameweek===Number(key.slice(3)))?.expected_points : p[key];
    rows.sort((a,b) => {const av=value(a,state.sort),bv=value(b,state.sort);if (av==null) return bv==null ? 0 : 1;if (bv==null) return -1;return state.direction*(typeof av==="string" ? av.localeCompare(bv) : av-bv);});
    const pageCount=Math.max(1,Math.ceil(rows.length/50));state.page=Math.min(state.page,pageCount-1);
    const headings=[['name','Player'],['team_name','Club'],['position','Pos'],['price_tenths','Price'],...data.gameweeks.map(gw=>[`gw-${gw}`,`GW${gw}`]),['total_expected_points','Total']];
    const next=data.gameweeks[0];
    $("#assistant-prediction-content").innerHTML = `${data.horizon_note ? `<p class="fine-print">${h(data.horizon_note)}</p>` : ""}<div class="table-wrap"><table class="assistant-table"><thead><tr>${headings.map(([key,label])=>`<th aria-sort="${state.sort===key ? state.direction===1 ? 'ascending' : 'descending' : 'none'}"><button type="button" data-assistant-sort="${key}">${h(label)}${state.sort===key ? state.direction===1 ? ' ↑' : ' ↓' : ''}</button></th>`).join("")}<th>GW${next} minutes</th><th>Appearance</th><th>xG</th><th>xA</th><th>xCS</th><th>Def pts</th><th>GW${next} 80% range</th></tr></thead><tbody>${rows.slice(state.page*50,(state.page+1)*50).map(p => {
      const first=p.gameweeks[0];
      return `<tr><td><button type="button" class="player-name" data-assistant-player="${p.player_id}">${h(p.name)}</button></td><td>${h(p.team_name)}</td><td>${h(p.position)}</td><td>${money(p.price_tenths)}</td>${p.gameweeks.map(g=>`<td class="${g.expected_points==null ? 'unknown' : ''}" title="${h(g.coverage)}; ${g.fixture_ids.length} published fixtures">${number(g.expected_points)}</td>`).join("")}<td class="forecast-total">${number(p.total_expected_points)}</td><td>${number(first.expected_minutes)}</td><td>${first.appearance_probability==null ? '—' : `${number(100*first.appearance_probability)}%`}</td><td>${number(first.expected_goals)}</td><td>${number(first.expected_assists)}</td><td>${number(first.clean_sheet_expectation)}</td><td>${number(first.expected_defensive_points)}</td><td>${first.points_interval_80?.join('–') || '—'}</td></tr>`;
    }).join("") || `<tr><td colspan="${headings.length+7}">No players match these filters.</td></tr>`}</tbody></table></div><div class="assistant-pagination"><span>${rows.length ? state.page*50+1 : 0}–${Math.min((state.page+1)*50,rows.length)} of ${rows.length} players</span><div><button type="button" class="icon-button" data-assistant-page="-1" ${state.page===0 ? 'disabled' : ''}>Previous</button><button type="button" class="icon-button" data-assistant-page="1" ${state.page===pageCount-1 ? 'disabled' : ''}>Next</button></div></div><p class="fine-print">A dash means unknown schedule coverage. Confirmed blanks are zero; doubles aggregate the actual published fixtures. xCS is the expected number of qualifying clean sheets, which can exceed one in a Double Gameweek. Metrics after Total describe GW${next}. Click a player for fixture-level probabilities, provenance and limitations.</p>`;
  }
  async function loadLineup(gameweek) {
    state.lineup=null;state.baseline=null;renderTeam();
    state.lineup = await api(`lineup${gameweek ? `?gameweek=${gameweek}` : ''}`);
    state.baseline = state.lineup.current;state.swap=null;
    renderTeam();renderTransfers();
  }
  $("#assistant-squad-form").onsubmit = event => {event.preventDefault();run(async () => {state.squad=await api("squad","POST",{entry_id:Number($("#assistant-team-id").value)});clearAccountPlans();await loadLineup();},"Loading your public squad and comparing legal lineups…");};
  function playerCard(id,lineup,editable=false,benchIndex=null) {
    const p=state.squad.players.find(p=>p.player_id===id);
    const forecast = state.lineup.player_projections[id];
    const content=`<strong>${h(p?.name || id)}${id===lineup.captain ? '<span class="captain-marker"> C</span>' : id===lineup.vice_captain ? '<span class="captain-marker"> V</span>' : ''}</strong><small>${h(p?.position)}${benchIndex==null ? '' : ` · ${benchIndex===0 ? 'Reserve GK' : `Sub ${benchIndex}`}`}</small><small>${forecast ? `${number(forecast.expected_points)} xPts` : 'Forecast in selected GW'}</small>`;
    return editable ? `<button type="button" class="assistant-player ${state.swap===id ? 'selected' : ''}" data-assistant-swap="${id}" aria-pressed="${state.swap===id}">${content}</button>` : `<div class="assistant-player">${content}</div>`;
  }
  function pitch(lineup,editable) {
    return `<div class="assistant-pitch">${['GK','DEF','MID','FWD'].map(position=>`<div class="assistant-pitch-row">${lineup.starting_ids.filter(id=>state.squad.players.find(p=>p.player_id===id)?.position===position).map(id=>playerCard(id,lineup,editable)).join('')}</div>`).join('')}</div><div class="assistant-bench">${lineup.bench_ids.map((id,index)=>playerCard(id,lineup,editable,index)).join('')}</div>`;
  }
  function metric(label,value,note) {return `<article><span>${h(label)}</span><strong>${number(value)}</strong><small>${h(note)}</small></article>`;}
  function renderTeam() {
    const squad=state.squad;if(!squad)return;
    const result=state.lineup;
    const info=`<h3>${h(squad.name)}</h3><p class="fine-print">Public squad: GW${squad.snapshot_gameweek} · loaded ${h(time(squad.updated_at))}. Planning deadline: ${h(time(result?.gameweek_deadlines[result.gameweek] || squad.deadline))}. Last-deadline bank ${money(squad.snapshot_bank_tenths)}; value ${money(squad.snapshot_value_tenths)}.</p><ul class="assistant-notes">${squad.notes.map(note=>`<li>${h(note)}</li>`).join('')}</ul>`;
    if(!result){$("#assistant-squad-content").innerHTML=info+'<p class="assistant-empty">A recommended lineup will appear when complete published fixture coverage and the shared forecast are available.</p>';return;}
    const current=result.current,optimal=result.recommended;
    const options=current.starting_ids.map(id=>{const p=squad.players.find(p=>p.player_id===id);return `<option value="${id}">${h(p.name)}</option>`;}).join('');
    $("#assistant-squad-content").innerHTML=`${info}<div class="assistant-team-controls"><label>Planning Gameweek<select id="assistant-lineup-gameweek">${Object.keys(result.gameweek_deadlines).map(Number).map(gw=>`<option value="${gw}" ${gw===result.gameweek ? 'selected' : ''}>GW${gw}</option>`).join('')}</select></label></div><div class="assistant-metrics">${metric('Public lineup',state.baseline.expected_points,'Projected for the selected GW')}${metric('Recommended',optimal.expected_points,`${optimal.formation} · ${optimal.points_interval_80.join('–')} simulated 80% range`)}${metric('Your draft',current.expected_points,`${current.formation} · ${current.points_interval_80.join('–')} simulated 80% range`)}${metric('Gain vs draft',result.expected_gain,'Same shared draws and FPL rules')}</div><div class="assistant-lineup-layout"><article class="assistant-team-card"><h3>Your draft · ${h(current.formation)}</h3><p class="fine-print">Select two players to swap a starter with a sub, or reorder two substitutes. Illegal changes are rejected by the server.</p>${pitch(current,true)}<div class="assistant-team-controls"><label>Captain<select id="assistant-captain">${options}</select></label><label>Vice-captain<select id="assistant-vice">${options}</select></label><button class="secondary-button" type="button" data-assistant-update-captain>Compare captaincy</button></div><button class="text-button" type="button" data-assistant-use-optimal>Use recommended as draft</button></article><article class="assistant-team-card"><h3>Recommended XI · ${h(optimal.formation)}</h3>${pitch(optimal,false)}<p class="fine-print">Expected additional captaincy: ${number(optimal.expected_captaincy_extra)} points. Captain appearance ${number(100*optimal.captain_appearance_probability)}%; vice takeover ${number(100*optimal.vice_takeover_probability)}%.</p><p class="fine-print">${h(optimal.method)}</p><details><summary>Captaincy alternatives</summary><ul class="assistant-notes">${optimal.captain_alternatives.map(pair=>`<li>${h(squad.players.find(p=>p.player_id===pair.captain).name)} / vice ${h(squad.players.find(p=>p.player_id===pair.vice_captain).name)}: ${number(pair.expected_extra_points)} extra xPts.</li>`).join('')}</ul></details></article></div><p class="fine-print">${h(result.comparison_note)} Sample uncertainty and model limitations still apply. Drafts are private comparisons and are never submitted to FPL.</p>`;
    $("#assistant-captain").value=String(current.captain);$("#assistant-vice").value=String(current.vice_captain);
    $("#assistant-lineup-gameweek").onchange=event=>run(()=>loadLineup(Number(event.target.value)),"Comparing the selected future Gameweek…");
  }
  async function evaluateDraft(draft) {
    const data=await api('lineup','POST',{gameweek:state.lineup.gameweek,starting_ids:draft.starting_ids,bench_ids:draft.bench_ids,captain:draft.captain,vice_captain:draft.vice_captain});
    state.lineup=data;state.swap=null;renderTeam();
  }
  function renderTransfers() {
    if(!state.squad || !state.predictions)return;
    const owned=new Set(state.squad.players.map(p=>p.player_id));
    const outgoingOptions=id=>'<option value="">Select outgoing player</option>'+state.squad.players.map(p=>`<option value="${p.player_id}" ${String(p.player_id)===String(id) ? 'selected' : ''}>${h(p.name)} · ${p.position}</option>`).join('');
    $("#assistant-transfer-content").innerHTML=`<form id="assistant-transfer-form" class="assistant-transfer-form"><p class="fine-print">Compare a manual plan, or find single-transfer alternatives for one outgoing player. Enter current account values; the public squad may exclude unpublished transfers.</p><div class="assistant-finances"><label>Confirmed bank (£m)<input id="assistant-bank" type="number" min="0" max="200" step="0.1" value="${h(state.bank)}" required placeholder="Last snapshot: ${squadBank()}"></label><label>Available free transfers<input id="assistant-free" type="number" min="0" max="5" step="1" value="${h(state.free)}" required placeholder="Not publicly available"></label></div><div>${state.moves.map((move,index)=>{
      const position=state.squad.players.find(p=>String(p.player_id)===String(move.outgoing))?.position;
      const incoming=state.predictions.players.filter(p=>!owned.has(p.player_id) && (!position || p.position===position)).sort((a,b)=>a.name.localeCompare(b.name));
      return `<div class="assistant-transfer-row"><label>Player out<select data-assistant-move="${index}" data-move-field="outgoing" required>${outgoingOptions(move.outgoing)}</select></label><label>Player in<select data-assistant-move="${index}" data-move-field="incoming" required><option value="">Select incoming player</option>${incoming.map(p=>`<option value="${p.player_id}" ${String(p.player_id)===String(move.incoming) ? 'selected' : ''}>${h(p.name)} · ${h(p.team_name)} · ${money(p.price_tenths)}</option>`).join('')}</select></label><label>Selling price (£m)<input data-assistant-move="${index}" data-move-field="selling" type="number" min="0" max="20" step="0.1" value="${h(move.selling)}" required></label><button type="button" class="icon-button" data-assistant-remove-move="${index}" aria-label="Remove transfer ${index+1}">×</button></div>`;
    }).join('')}</div><label class="assistant-check"><input id="assistant-confirm-account" type="checkbox" ${state.confirmed ? 'checked' : ''} required><span>I confirm that this squad, bank, free transfers and selling prices match my current FPL account.</span></label><div class="assistant-transfer-actions"><button class="secondary-button" type="button" data-assistant-add-move ${state.moves.length>=5 ? 'disabled' : ''}>Add transfer</button><button class="primary-button" type="submit">Compare with no transfers</button><button class="secondary-button" type="button" data-assistant-search-transfer ${state.moves.length!==1 ? 'disabled' : ''}>Find single-transfer alternatives</button></div></form>`;
    $("#assistant-bank").oninput=event=>state.bank=event.target.value;$("#assistant-free").oninput=event=>state.free=event.target.value;$("#assistant-confirm-account").onchange=event=>state.confirmed=event.target.checked;
    $("#assistant-transfer-form").onsubmit=event=>{event.preventDefault();run(async()=>{
      const prices=Object.fromEntries(state.moves.map(move=>[String(move.outgoing),Math.round(10*Number(move.selling))]));
      const result=await api('transfers','POST',{moves:state.moves.map(move=>({outgoing:Number(move.outgoing),incoming:Number(move.incoming)})),bank_tenths:Math.round(10*Number(state.bank)),free_transfers:Number(state.free),selling_prices:prices,account_confirmed:state.confirmed});
      $("#assistant-transfer-result").innerHTML=`<div class="assistant-metrics">${metric('Next GW net gain',result.gain_next,`${result.hit} point prospective hit`)}${metric('Three-GW gain',result.gain_3,'Same two fixed squads; hit once')}${metric('Five-GW gain',result.gain_5,'No future transfers or price changes')}<article><span>Remaining bank</span><strong>${money(result.bank_after_tenths)}</strong><small>Uses actual entered selling prices</small></article></div><div class="table-wrap"><table class="assistant-table"><thead><tr><th>GW</th><th>No-transfer XI</th><th>Proposed XI</th><th>Hit</th><th>Net gain</th></tr></thead><tbody>${result.gameweeks.map(g=>`<tr><th>${g.gameweek}</th><td>${number(g.baseline)}</td><td>${number(g.proposed)}</td><td>${number(g.hit)}</td><td title="${h(g.note || '')}">${signed(g.net_gain)}</td></tr>`).join('')}</tbody></table></div><ul class="assistant-notes">${result.assumptions.map(note=>`<li>${h(note)}</li>`).join('')}</ul>`;
    },"Evaluating the plan with reused shared outcomes…");};
  }
  async function searchTransfer(button) {
    const move=state.moves[0];
    if(state.moves.length!==1 || !move.outgoing || move.selling==="" || state.bank==="" || state.free==="" || !state.confirmed)throw new Error('Select one outgoing player and confirm its selling price, bank and free transfers first.');
    button.disabled=true;
    try {
      const data=await api('transfers/search','POST',{outgoing:Number(move.outgoing),horizon:Number($("#assistant-horizon").value),bank_tenths:Math.round(10*Number(state.bank)),free_transfers:Number(state.free),selling_prices:{[move.outgoing]:Math.round(10*Number(move.selling))},account_confirmed:true});
      $("#assistant-transfer-result").innerHTML=`<h3>Single-transfer shortlist · ${data.gameweeks.length} Gameweeks</h3><p class="fine-print">${data.evaluated_candidates} of ${data.eligible_candidates} eligible replacements evaluated with shared joint draws. Keeping your squad: 0 incremental points.</p><div class="table-wrap"><table class="assistant-table"><thead><tr><th>Player out</th><th>Player in</th><th>Horizon gain</th><th>Next GW</th><th>3 GWs</th><th>5 GWs</th><th>Hit</th><th>Bank left</th><th>Plan</th></tr></thead><tbody>${data.recommendations.map(plan=>`<tr><td>${h(plan.moves[0].out.name)}</td><td>${h(plan.moves[0].in.name)}</td><td>${signed(plan.gain_horizon)}</td><td>${signed(plan.gain_next)}</td><td>${signed(plan.gain_3)}</td><td>${signed(plan.gain_5)}</td><td>${plan.hit}</td><td>${money(plan.bank_after_tenths)}</td><td><button class="text-button" type="button" data-assistant-select-transfer="${plan.moves[0].in.player_id}">Use in draft</button></td></tr>`).join('') || '<tr><td colspan="9">No affordable legal replacement has complete published coverage in this horizon.</td></tr>'}</tbody></table></div><ul class="assistant-notes">${data.notes.map(note=>`<li>${h(note)}</li>`).join('')}</ul>`;
    } finally {button.disabled=false;}
  }
  function squadBank() {return state.squad.snapshot_bank_tenths==null ? 'unknown' : (state.squad.snapshot_bank_tenths/10).toFixed(1);}
  $("#assistant-transfer-content").onchange=event=>{
    if(!event.target.hasAttribute('data-assistant-move'))return;
    const move=state.moves[Number(event.target.dataset.assistantMove)];move[event.target.dataset.moveField]=event.target.value;
    if(event.target.dataset.moveField==='outgoing'){move.incoming='';const player=state.squad.players.find(p=>String(p.player_id)===String(move.outgoing));move.selling=player?.selling_price_tenths==null ? '' : String(player.selling_price_tenths/10);renderTransfers();}
  };
  $("#assistant-evaluate-chips").onclick=()=>run(async()=>{
    if(!state.squad)throw new Error('Load your squad in My Team first.');
    const data=await api('chips');
    $("#assistant-chip-content").innerHTML=`<div class="assistant-chip-grid">${[['bboost','Bench Boost'],['3xc','Triple Captain']].map(([chip,name])=>`<article class="assistant-team-card"><h3>${name}</h3><div class="table-wrap"><table class="assistant-table"><thead><tr><th>GW</th><th>Conditional extra xPts</th><th>Availability</th></tr></thead><tbody>${data.comparisons.filter(row=>row.chip===chip).map(row=>`<tr><th>${row.gameweek}</th><td title="${h(row.note || '')}">${signed(row.expected_gain)}</td><td>${row.availability==='unused_at_public_snapshot' ? 'Unused in public snapshot' : row.availability==='unknown' ? 'Unknown' : 'Used / outside window'}</td></tr>`).join('')}</tbody></table></div></article>`).join('')}</div><ul class="assistant-notes">${data.notes.map(note=>`<li>${h(note)}</li>`).join('')}</ul>`;
  },"Comparing fixed-squad chip values across the cached window…");
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.assistantSort){state.direction=state.sort===button.dataset.assistantSort ? -state.direction : -1;state.sort=button.dataset.assistantSort;renderPredictions();}
    if(button.dataset.assistantPage){state.page+=Number(button.dataset.assistantPage);renderPredictions();}
    if(button.dataset.assistantPlayer)run(async()=>{
      const player=await api(`players/${button.dataset.assistantPlayer}`);
      $("#assistant-player-detail").innerHTML=`<h2>${h(player.name)}</h2><p>${h(player.team_name)} · ${h(player.position)} · ${money(player.price_tenths)}</p><p class="fine-print">${h(player.news)} · ${h(player.model_version)} · ${h(time(player.data_timestamp))}</p><div class="table-wrap"><table class="assistant-table"><thead><tr><th>GW / fixture</th><th>xPts</th><th>Minutes</th><th>Start</th><th>Appear</th><th>xG</th><th>xA</th><th>CS</th><th>Def contribution</th><th>Def pts</th><th>80% range</th></tr></thead><tbody>${player.fixtures.map(f=>`<tr><th>GW${f.gameweek} / ${f.fixture_id}</th><td>${number(f.expected_points)}</td><td>${number(f.expected_minutes)}</td><td>${number(f.starting_probability==null ? null : 100*f.starting_probability)}%</td><td>${number(f.appearance_probability==null ? null : 100*f.appearance_probability)}%</td><td>${number(f.expected_goals)}</td><td>${number(f.expected_assists)}</td><td>${number(f.clean_sheet_probability==null ? null : 100*f.clean_sheet_probability)}%</td><td>${number(f.expected_defensive_contribution)}</td><td>${number(f.expected_defensive_points)}</td><td>${f.points_interval_80?.join('–') || '—'}</td></tr>`).join('') || '<tr><td colspan="11">No published fixtures in this window. Check Gameweek coverage below.</td></tr>'}</tbody></table></div><ul class="assistant-notes">${player.gameweeks.map(g=>`<li>GW${g.gameweek}: ${h(g.coverage)} · ${number(g.expected_points)} xPts · variance ${number(g.points_variance)}.</li>`).join('')}</ul><p class="fine-print">${player.provenance.map(p=>`${h(p.name)}: ${h(p.detail)}`).join('; ')}</p><ul class="assistant-notes">${player.assumptions.map(note=>`<li>${h(note)}</li>`).join('')}</ul>`;
      $("#assistant-player-dialog").showModal();
    },'Loading cached fixture details…');
    if(button.dataset.assistantSwap){
      const id=Number(button.dataset.assistantSwap);
      if(state.swap==null){state.swap=id;renderTeam();return;}
      if(state.swap===id){state.swap=null;renderTeam();return;}
      const first=state.swap,current=state.lineup.current;
      const draft={...current,starting_ids:current.starting_ids.map(pid=>pid===first ? id : pid===id ? first : pid),bench_ids:current.bench_ids.map(pid=>pid===first ? id : pid===id ? first : pid)};
      state.swap=null;renderTeam();run(()=>evaluateDraft(draft),'Checking the legal draft and comparing expected points…');
    }
    if(button.hasAttribute('data-assistant-update-captain'))run(()=>evaluateDraft({...state.lineup.current,captain:Number($("#assistant-captain").value),vice_captain:Number($("#assistant-vice").value)}),'Comparing captain and vice on the shared outcomes…');
    if(button.hasAttribute('data-assistant-use-optimal'))run(()=>evaluateDraft(state.lineup.recommended),'Comparing the recommended draft…');
    if(button.hasAttribute('data-assistant-search-transfer'))run(()=>searchTransfer(button),'Screening replacements, then comparing up to eight whole squads on cached outcomes…');
    if(button.dataset.assistantSelectTransfer){state.moves[0].incoming=button.dataset.assistantSelectTransfer;renderTransfers();message('Replacement added to your draft. Compare the plan to see each Gameweek.');}
    if(button.hasAttribute('data-assistant-add-move')){state.moves.push({outgoing:'',incoming:'',selling:''});renderTransfers();}
    if(button.hasAttribute('data-assistant-remove-move')){state.moves.splice(Number(button.dataset.assistantRemoveMove),1);renderTransfers();}
  });
  $("#assistant-close-dialog").onclick=()=>$("#assistant-player-dialog").close();
  window.addEventListener('pageshow',event=>{if(event.persisted){clearPrivate();run(checkAccess,'Checking private access…');}});
  setInterval(async()=>{
    if(!state.csrf)return;
    try {
      const session=await api('session');
      if(!session.authenticated){clearPrivate();$("#assistant-login-form").classList.toggle('hidden',!session.enabled);$("#assistant-access-note").textContent='Your session ended. Sign in again.';}
    } catch (_) { /* A transient network failure does not grant additional access. */ }
  },60000);
  async function checkAccess() {
    const session=await api('session');
    $("#assistant-access-note").textContent=session.enabled ? 'Sign in with your private assistant password.' : session.note;
    $("#assistant-login-form").classList.toggle('hidden',!session.enabled);
    if(session.authenticated)await unlock(session);
  }
  activate('team');run(checkAccess,'Checking private access…');
})();
