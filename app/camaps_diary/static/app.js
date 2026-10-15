// Diario CamAPS - interfaccia web (senza dipendenze esterne).
// Tutti gli indirizzi sono relativi: funziona anche dietro l'Ingress di Home Assistant.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const NF1 = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 1 });
const NF0 = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 0 });
const n1 = (v) => (v == null || Number.isNaN(v) ? '–' : NF1.format(v));
const n0 = (v) => (v == null || Number.isNaN(v) ? '–' : NF0.format(v));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const parseNum = (s) => {
  const v = parseFloat(String(s ?? '').replace(',', '.'));
  return Number.isFinite(v) ? v : null;
};
const pad = (n) => String(n).padStart(2, '0');
const debounce = (fn, ms) => {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
};

const MEAL_LABELS = {
  colazione: 'Colazione', spuntino: 'Spuntino', pranzo: 'Pranzo',
  merenda: 'Merenda', cena: 'Cena', fuori_pasto: 'Fuori pasto', ipoglicemia: 'Correzione ipo',
};
const NOTE_LABELS = { sintomo: 'Sintomo', attivita: 'Attività', malattia: 'Malattia', visita: 'Visita', altro: 'Altro' };
const STATUS_LABELS = {
  ok: 'Collegato', errore: 'Aggiornamento non riuscito', in_corso: 'Aggiornamento in corso',
  non_configurato: 'Da configurare', mai: 'In attesa del primo aggiornamento',
};
const AUTO_LIKE = new Set(['automatic', 'ease_off']);
const iaFotoLabel = (o) => {
  if (!o) return '–';
  if (!o.disponibile) return 'non configurata (manca la chiave Anthropic)';
  const costo = (o.costo_usd || 0).toFixed(2);
  return `${o.modello} · ${n0(o.n || 0)} analisi questo mese · ~$${costo} (stima indicativa)`;
};
const OFF_IMPORT_LABEL = (o) => {
  if (!o) return '–';
  if (o.stato === 'in_corso') return 'import in corso (qualche minuto)…';
  if (o.alimenti) return `${NF0.format(o.alimenti)} alimenti italiani`;
  return o.errore || 'non ancora importati';
};
const CIQUAL_LABEL = (c) => {
  if (!c) return '–';
  if (c.stato === 'in_corso') return 'download in corso…';
  if (c.alimenti) return `${NF0.format(c.alimenti)} alimenti${c.versione ? `, versione ${c.versione}` : ''}`;
  return c.errore || 'non ancora scaricata';
};

const state = {
  today: null, day: null, stato: null, payload: null, trendDays: 7, trendRows: null, agpBins: null,
  calYear: null, calMonth: null, showAllFoods: false, reportDays: '14', visitLastDate: null,
  wizardStep: 1,
};

const WIZARD_STEPS = 4;

// ---------------------------------------------------------------- utilita'

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error('Il diario non risponde. Controlla che l\'add-on sia avviato.');
  }
  let data = null;
  try { data = await res.json(); } catch { /* risposta senza JSON */ }
  if (!res.ok) throw new Error((data && data.errore) || `Errore del server (${res.status}).`);
  return data;
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 3200);
}

const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDay = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d, 12); };
const addDays = (s, n) => { const d = parseDay(s); d.setDate(d.getDate() + n); return isoDay(d); };
const minutesOf = (ts) => Number(ts.slice(11, 13)) * 60 + Number(ts.slice(14, 16));
const hhmm = (min) => { const m = Math.round(min); return `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`; };
const localInput = (d) => `${isoDay(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

function dayLabel(s) {
  if (s === state.today) return 'Oggi';
  if (s === addDays(state.today, -1)) return 'Ieri';
  const t = parseDay(s).toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' });
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function whenLocal(ts) {
  // 'AAAA-MM-GG HH:MM:SS' (ora locale)
  const day = ts.slice(0, 10);
  let d;
  if (day === state.today) d = 'oggi';
  else if (day === addDays(state.today, -1)) d = 'ieri';
  else d = parseDay(day).toLocaleDateString('it-IT', { weekday: 'short', day: 'numeric', month: 'short' });
  return `${d} alle ${ts.slice(11, 16)}`;
}

function whenUtc(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  return whenLocal(`${isoDay(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}:00`);
}

function timeAgo(value, isUtc) {
  if (!value) return null;
  const d = isUtc ? new Date(value) : new Date(value.replace(' ', 'T'));
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 45) return 'pochi secondi fa';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min fa`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} ${h === 1 ? 'ora' : 'ore'} fa`;
  const days = Math.round(h / 24);
  if (days < 7) return `${days} ${days === 1 ? 'giorno' : 'giorni'} fa`;
  return isUtc ? whenUtc(value) : whenLocal(value);
}

// ---------------------------------------------------------------- fogli

function showSheet(sel) {
  const s = $(sel);
  s.hidden = false;
  $('.app').style.overflow = 'hidden';
}

function hideSheet(sel) {
  const s = $(sel);
  s.hidden = true;
  if (sel === '#scan-sheet') stopScanner();
  if ($$('.sheet').every((x) => x.hidden)) $('.app').style.overflow = 'auto';
}

// ---------------------------------------------------------------- giorno

function renderFreshness() {
  const el = $('#freshness');
  const s = state.stato;
  el.classList.remove('late');
  if (!s) return;
  if (!s.ultima_glicemia) {
    el.textContent = s.sync_stato === 'errore' || s.sync_stato === 'non_configurato'
      ? 'Collegamento a Glooko da sistemare: apri Stato.'
      : 'Nessun dato ancora scaricato da CamAPS.';
    el.classList.add('late');
    return;
  }
  const last = new Date(s.ultima_glicemia.replace(' ', 'T'));
  const hours = (Date.now() - last) / 36e5;
  let text = `Ultimi dati da CamAPS: ${whenLocal(s.ultima_glicemia)}`;
  if (s.sync_stato === 'errore') text += '. L\'ultimo aggiornamento non è riuscito.';
  el.textContent = text;
  if (hours > 6 || s.sync_stato === 'errore') el.classList.add('late');
}

function syncDayHeaders() {
  const label = dayLabel(state.day);
  const isToday = state.day >= state.today;
  const pairs = [
    ['day-title', 'day-picker', 'day-next'],
    ['pasti-title', 'pasti-day-picker', 'pasti-day-next'],
  ];
  for (const [titleId, pickerId, nextId] of pairs) {
    $(`#${titleId}`).textContent = label;
    $(`#${pickerId}`).value = state.day;
    $(`#${pickerId}`).max = state.today;
    $(`#${nextId}`).disabled = isToday;
  }
}

async function loadDay() {
  syncDayHeaders();
  $('#readout').textContent = '';
  $('#chart').innerHTML = '<div class="skeleton skeleton-chart"></div>';
  $('#figures').innerHTML = '<div class="skeleton skeleton-figures"></div>';
  $('#log').innerHTML = '<div class="skeleton" style="grid-column:1/-1;height:160px"></div>';
  try {
    const p = await api(`api/giorno?data=${state.day}`);
    state.payload = p;
    renderChart(p);
    renderFigures(p);
    renderMealTiles(p);
    renderDayNotes(p.note);
    flipDayContent();
  } catch (err) {
    $('#chart').innerHTML = `<div class="chart-empty">${esc(err.message)}</div>`;
  }
}

function flipDayContent() {
  for (const id of ['#day-content', '#pasti-content']) {
    const el = $(id);
    if (!el) continue;
    el.classList.remove('flip');
    void el.offsetWidth; // forza il reflow cosi' l'animazione riparte anche su cambi rapidi
    el.classList.add('flip');
  }
}

function renderChart(p) {
  const box = $('#chart');
  const gs = p.glicemie;
  if (!gs.length && !p.voci.length && !p.pasti.length) {
    box.innerHTML = '<div class="chart-empty">Nessun dato CamAPS per questo giorno.</div>';
    return;
  }
  const W = Math.max(300, Math.round(box.clientWidth || 360));
  const H = 256;
  const m = { l: 34, r: 12, t: 32, b: 54 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const low = p.soglie.bassa;
  const high = p.soglie.alta;
  const maxG = gs.reduce((a, g) => Math.max(a, g[1]), 0);
  const yMin = 40;
  const yMax = Math.min(400, Math.max(300, Math.ceil(maxG / 50) * 50));
  const x = (mm) => m.l + (mm / 1440) * pw;
  const y = (v) => m.t + (1 - (Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * ph;
  const f = (v) => v.toFixed(1);
  const out = [];

  out.push(`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Glicemia del giorno con pasti e boli">`);
  out.push(`<defs><linearGradient id="gluGradient" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="var(--ink)" stop-opacity="0.22"/>
    <stop offset="100%" stop-color="var(--ink)" stop-opacity="0"/>
  </linearGradient></defs>`);
  out.push(`<rect class="band" x="${m.l}" y="${f(y(high))}" width="${pw}" height="${f(y(low) - y(high))}"/>`);
  for (const v of [low, high]) {
    out.push(`<line class="band-edge" x1="${m.l}" x2="${m.l + pw}" y1="${f(y(v))}" y2="${f(y(v))}"/>`);
  }
  for (let h = 0; h <= 24; h += 3) {
    const xx = f(x(h * 60));
    out.push(`<line class="grid" x1="${xx}" x2="${xx}" y1="${m.t}" y2="${m.t + ph}"/>`);
    if (h % 6 === 0 && h < 24) {
      out.push(`<text class="axis" x="${xx}" y="${m.t + ph + 47}" text-anchor="${h === 0 ? 'start' : 'middle'}">${h}:00</text>`);
    }
  }
  for (const v of [low, high, 250].filter((val) => val < yMax)) {
    out.push(`<text class="axis" x="${m.l - 6}" y="${f(y(v) + 4)}" text-anchor="end">${v}</text>`);
  }

  // Area sfumata sotto la curva, per segmenti contigui (si interrompe dove mancano letture)
  const baseY = f(y(yMin));
  let areaD = '';
  let areaPrev = null;
  let areaOpen = false;
  for (const [mm, v] of gs) {
    const newSeg = areaPrev == null || mm - areaPrev > 15;
    if (newSeg) {
      if (areaOpen) areaD += `L${f(x(areaPrev))},${baseY}Z`;
      areaD += `M${f(x(mm))},${baseY}L${f(x(mm))},${f(y(v))}`;
      areaOpen = true;
    } else {
      areaD += `L${f(x(mm))},${f(y(v))}`;
    }
    areaPrev = mm;
  }
  if (areaOpen) areaD += `L${f(x(areaPrev))},${baseY}Z`;
  if (areaD) out.push(`<path class="glu-area" d="${areaD}" fill="url(#gluGradient)" stroke="none"/>`);

  // Modalita' di CamAPS: striscia sotto il grafico
  const sy = m.t + ph + 6;
  for (const md of p.modalita) {
    const cls = md.tipo === 'boost' ? 'mode-boost' : AUTO_LIKE.has(md.tipo) ? 'mode-auto' : 'mode-off';
    const w = Math.max(1.5, x(md.a) - x(md.da));
    out.push(`<rect class="${cls}" x="${f(x(md.da))}" y="${sy}" width="${f(w)}" height="7" rx="1.5"/>`);
  }

  // Insulina basale automatica del microinfusore: a scalini, sotto la striscia di modalita'.
  const basale = p.basale || [];
  const byY0 = sy + 11;
  const byH = 16;
  if (basale.length) {
    const basalMax = Math.max(0.1, ...basale.map((b) => b[2]));
    // scala a radice quadrata, non lineare: un picco di correzione (es. 10 U/h)
    // non deve schiacciare vicino allo zero il resto della giornata, che di
    // solito sta su valori molto più bassi (0,3-1,5 U/h).
    const by = (r) => byY0 + byH * (1 - Math.sqrt(Math.min(r, basalMax)) / Math.sqrt(basalMax));
    let basalD = '';
    for (const [a, b, rate] of basale) {
      if (rate <= 0) continue;
      const xA = f(x(a));
      const xB = f(x(b));
      const yR = f(by(rate));
      basalD += `M${xA},${f(byY0 + byH)} L${xA},${yR} L${xB},${yR} L${xB},${f(byY0 + byH)} Z `;
    }
    out.push(`<line class="basal-base" x1="${m.l}" x2="${m.l + pw}" y1="${f(byY0 + byH)}" y2="${f(byY0 + byH)}"/>`);
    if (basalD) out.push(`<path class="basal-area" d="${basalD}"/>`);
    out.push(`<text class="axis basal-axis" x="${m.l - 6}" y="${f(byY0 + 5)}" text-anchor="end">${basalMax.toFixed(1)}</text>`);
    if (basalMax > 1.3) {
      // riferimento a 1 U/h: la scala e' a radice quadrata, non lineare,
      // quindi un numero intermedio aiuta a leggere le proporzioni giuste.
      out.push(`<line class="basal-ref" x1="${m.l}" x2="${m.l + pw}" y1="${f(by(1))}" y2="${f(by(1))}"/>`);
      out.push(`<text class="axis basal-axis" x="${m.l - 6}" y="${f(by(1) + 3)}" text-anchor="end">1</text>`);
    }
  }

  // Pasti del diario: barre ocra proporzionali ai carboidrati
  for (const meal of p.pasti) {
    const xx = x(minutesOf(meal.ora));
    const c = meal.carbo_g || 0;
    const hgt = Math.max(6, Math.min(c / 150, 1) * ph * 0.45);
    const top = m.t + ph - hgt;
    out.push(`<rect class="meal-bar" x="${f(xx - 5)}" y="${f(top)}" width="10" height="${f(hgt)}" rx="3"/>`);
    out.push(`<line class="meal-top" x1="${f(xx - 5)}" x2="${f(xx + 5)}" y1="${f(top)}" y2="${f(top)}"/>`);
    out.push(`<text class="meal-label" x="${f(xx)}" y="${f(top - 5)}" text-anchor="middle">${n0(c)} g</text>`);
  }

  // Glicemia, interrotta dove mancano letture per piu' di 15 minuti.
  // Sotto e sopra range sono evidenziati con un tratto colorato sovrapposto,
  // cosi' i tre stati (sotto/dentro/sopra) hanno un colore fisso e distinto.
  let d = '';
  let dLow = '';
  let dHigh = '';
  let prev = null;
  let prevLow = null;
  let prevHigh = null;
  for (const [mm, v] of gs) {
    d += `${prev == null || mm - prev > 15 ? 'M' : 'L'}${f(x(mm))},${f(y(v))}`;
    if (v < low) {
      dLow += `${prevLow == null || mm - prevLow > 3 ? 'M' : 'L'}${f(x(mm))},${f(y(v))}`;
      prevLow = mm;
    }
    if (v > high) {
      dHigh += `${prevHigh == null || mm - prevHigh > 3 ? 'M' : 'L'}${f(x(mm))},${f(y(v))}`;
      prevHigh = mm;
    }
    prev = mm;
  }
  out.push(`<path class="glu" d="${d}"/>`);
  if (dLow) out.push(`<path class="glu-low" d="${dLow}"/>`);
  if (dHigh) out.push(`<path class="glu-high" d="${dHigh}"/>`);

  // Voci CamAPS: boli (triangoli), carboidrati senza bolo (punti), ipo (punto rosso).
  // Quelle senza pasto collegato sono toccabili: aprono l'editor pre-impostato su quell'ora
  // ("registra cosa hai mangiato"), unico punto in cui restano visibili e azionabili.
  let lastLabel = -99;
  for (const v of p.voci) {
    const xx = x(v.min);
    const reg = v.pasto_id == null
      ? ` data-register="${v.ora}"${v.tipo === 'ipo' ? ' data-tipo="ipoglicemia"' : ''} tabindex="0"`
      : '';
    if (reg) out.push(`<circle class="voce-hit" cx="${f(xx)}" cy="${m.t - 6}" r="11" fill="transparent"${reg}/>`);
    if (v.tipo === 'bolo') {
      out.push(`<path class="bolus" d="M${f(xx - 5)},${m.t - 11}L${f(xx + 5)},${m.t - 11}L${f(xx)},${m.t - 3}Z"${reg}/>`);
      if (xx - lastLabel > 28) {
        out.push(`<text class="bolus-label" x="${f(xx)}" y="${m.t - 15}" text-anchor="middle">${n1(v.U)}</text>`);
        lastLabel = xx;
      }
    } else if (v.tipo === 'ipo') {
      out.push(`<circle class="ipo-dot" cx="${f(xx)}" cy="${m.t - 6}" r="3.5"${reg}/>`);
    } else {
      for (const mm of v.righe_min) {
        if (mm >= 0 && mm <= 1440) out.push(`<circle class="carb-only" cx="${f(x(mm))}" cy="${m.t - 6}" r="2.5"${reg}/>`);
      }
    }
  }

  out.push(`<line class="cursor" x1="0" x2="0" y1="${m.t}" y2="${m.t + ph}" visibility="hidden"/>`);
  out.push('<circle class="cursor-dot" r="4" visibility="hidden"/>');
  out.push('</svg>');
  box.innerHTML = out.join('');

  const svg = box.querySelector('svg');
  const cur = svg.querySelector('.cursor');
  const dot = svg.querySelector('.cursor-dot');
  const readout = $('#readout');
  const onMove = (ev) => {
    if (!gs.length) return;
    const rect = svg.getBoundingClientRect();
    const px = (ev.clientX - rect.left) * (W / rect.width);
    const mm = ((px - m.l) / pw) * 1440;
    if (mm < 0 || mm > 1440) return;
    let lo = 0;
    let hi = gs.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (gs[mid][0] < mm) lo = mid + 1; else hi = mid;
    }
    let i = lo;
    if (i > 0 && Math.abs(gs[i - 1][0] - mm) < Math.abs(gs[i][0] - mm)) i -= 1;
    const [gm, gv] = gs[i];
    if (Math.abs(gm - mm) > 20) {
      cur.setAttribute('visibility', 'hidden');
      dot.setAttribute('visibility', 'hidden');
      readout.textContent = `Nessuna lettura alle ${hhmm(mm)}`;
      return;
    }
    cur.setAttribute('x1', f(x(gm)));
    cur.setAttribute('x2', f(x(gm)));
    dot.setAttribute('cx', f(x(gm)));
    dot.setAttribute('cy', f(y(gv)));
    cur.setAttribute('visibility', 'visible');
    dot.setAttribute('visibility', 'visible');
    readout.innerHTML = `Alle ${hhmm(gm)}: <b>${gv}</b> mg/dL`;
  };
  svg.addEventListener('pointermove', onMove);
  svg.addEventListener('pointerdown', onMove);
}

function renderFigures(p, sel = '#figures') {
  const r = p.riepilogo;
  const declared = r.carbo_camaps_g || 0;
  const noData = !r.letture_cgm && !r.totale_U && !declared && !r.carbo_ipo_g;
  const range = r.sotto_pct != null ? `sotto ${n0(r.sotto_pct)}%, sopra ${n0(r.sopra_pct)}%` : '';
  let carbSub = '';
  if (!noData) {
    carbSub = `${n0(declared)} g inseriti in CamAPS`;
    if (r.carbo_ipo_g) carbSub += `, ${n0(r.carbo_ipo_g)} g per ipo`;
  }
  $(sel).innerHTML = `
    <div><dt>Media</dt><dd>${n0(r.media_mgdl)}<small>mg/dL</small></dd></div>
    <div><dt>Nel range ${n0(p.soglie.bassa)}–${n0(p.soglie.alta)}</dt>
      <dd class="inrange">${n0(r.tir_pct)}<small>%</small><span class="sub">${range}</span></dd></div>
    <div><dt>Insulina</dt>
      <dd class="ins">${noData ? '–' : n1(r.totale_U)}<small>U</small>
      <span class="sub">${noData ? 'in attesa dei dati CamAPS' : `${n1(r.boli_U)} di boli, ${n1(r.basale_U)} automatica`}</span></dd></div>
    <div><dt>Carboidrati mangiati</dt>
      <dd class="carb">${r.carbo_mangiati_g != null ? n0(r.carbo_mangiati_g) : '–'}<small>g</small>
      <span class="sub">${carbSub}</span></dd></div>
    <div><dt>Passi</dt><dd class="act">${r.passi != null ? n0(r.passi) : '–'}</dd></div>`;
}

function mealEntry(meal) {
  const a = meal.analisi;
  const items = meal.voci.map((v) => esc(v.descrizione) + (v.grammi ? ` ${n0(v.grammi)} g` : '')).join(', ');
  const facts = [];
  const glyc = (parts) => parts.filter(Boolean).join(', ');
  if (a.in_attesa) {
    facts.push(['CamAPS', 'dati di quest\'ora non ancora arrivati su Glooko']);
  } else if (a.ipo) {
    facts.push(['CamAPS', a.voci_ipo.length
      ? `${n0(a.carbo_ipo_camaps_g)} g come trattamento ipo alle ${a.voci_ipo.join(' e ')}`
      : 'nessun trattamento ipo registrato vicino a quest\'ora']);
    const g = glyc([
      a.glicemia_inizio != null && `${a.glicemia_inizio} all'inizio`,
      a.minimo != null && `minimo ${a.minimo}`,
      a.glicemia_30 != null && `${a.glicemia_30} dopo 30 minuti`,
      a.glicemia_60 != null && `${a.glicemia_60} dopo un'ora`,
    ]);
    if (g) facts.push(['Glicemia', g]);
  } else {
    if (a.boli.length) {
      facts.push(['Bolo CamAPS', `<span class="ins">${n1(a.bolo_U)} U</span> per ${n0(a.carbo_bolo_g)} g alle ${a.boli.map((b) => b.ora).join(' e ')}`]);
    } else {
      facts.push(['Bolo CamAPS', 'nessuno vicino a quest\'ora']);
    }
    for (const l of a.lenti) {
      facts.push(['Lenta digestione', `${n0(l.carbo_g)} g in ${l.quote} quote dalle ${l.da} alle ${l.a}, senza boli`]);
    }
    if (a.spuntini.length) facts.push(['Senza bolo', `${n0(a.carbo_spuntino_g)} g con Aggiungi pasto`]);
    if (a.differenza_carbo_g != null && Math.abs(a.differenza_carbo_g) >= 1) {
      const diff = a.differenza_carbo_g;
      facts.push(['Differenza', `<span class="pos">${diff > 0 ? '+' : '−'}${n0(Math.abs(diff))} g</span> ${diff > 0 ? 'in più' : 'in meno'} rispetto a CamAPS`]);
    }
    facts.push(['Automatica', `${n1(a.insulina_auto_U)} U nelle ${n1(a.finestra_h)} ore dopo`]);
    const g = glyc([
      a.glicemia_inizio != null && `${a.glicemia_inizio} prima`,
      a.picco != null && `picco ${a.picco} alle ${a.picco_ora}`,
      a.glicemia_2h != null && `${a.glicemia_2h} a 2 ore`,
    ]);
    if (g) facts.push(['Glicemia', g]);
    if (a.g_per_U_bolo != null) {
      facts.push(['Grammi per U', `${n1(a.g_per_U_bolo)} col bolo, ${n1(a.g_per_U_totale)} con l'automatica`]);
    }
  }
  if (!a.in_attesa && !a.completa) facts.push(['Nota', `dati CamAPS fino alle ${a.dati_fino_a}: valori parziali`]);
  return `
    <li class="entry meal" data-meal="${meal.id}" tabindex="0">
      <div class="time">${meal.ora.slice(11, 16)}</div>
      <div>
        <div class="row"><span class="title">${MEAL_LABELS[meal.tipo] || esc(meal.tipo)}</span>
          <span class="amount carb">${n0(meal.carbo_g)} g</span></div>
        <p class="desc">${items}${meal.note ? `. ${esc(meal.note)}` : ''}</p>
        <dl class="facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>
      </div>
    </li>`;
}

const MEAL_TILE_ORDER = ['colazione', 'spuntino', 'pranzo', 'merenda', 'cena'];

function mealTile(tipo, pastiDiTipo) {
  const lista = pastiDiTipo.slice().sort((a, b) => (b.carbo_g || 0) - (a.carbo_g || 0));
  const main = lista[0];
  if (!main) {
    return `
      <button type="button" class="meal-tile empty" data-add-meal-type="${tipo}" aria-label="Aggiungi ${MEAL_LABELS[tipo]}">
        <svg class="meal-tile-plus" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        <span class="meal-tile-label">${MEAL_LABELS[tipo]}</span>
      </button>`;
  }
  const fotoId = main.foto && main.foto.length ? main.foto[0].id : null;
  return `
    <button type="button" class="meal-tile ${fotoId ? 'has-photo' : ''}" data-meal="${main.id}" aria-label="${MEAL_LABELS[tipo]}, ${n0(main.carbo_g)} grammi">
      ${fotoId ? `<img src="api/foto/${fotoId}" alt="">` : ''}
      <span class="meal-tile-label">${MEAL_LABELS[tipo]}</span>
      <span class="meal-tile-carb">${n0(main.carbo_g)} g</span>
      <span class="meal-tile-time">${main.ora.slice(11, 16)}</span>
      ${lista.length > 1 ? `<span class="meal-tile-extra">+${lista.length - 1}</span>` : ''}
    </button>`;
}

function renderMealTiles(p) {
  const byType = {};
  for (const m of p.pasti) {
    if (!MEAL_TILE_ORDER.includes(m.tipo)) continue;
    (byType[m.tipo] = byType[m.tipo] || []).push(m);
  }
  $('#log').innerHTML = MEAL_TILE_ORDER.map((tipo) => mealTile(tipo, byType[tipo] || [])).join('');

  const extra = p.pasti.filter((m) => !MEAL_TILE_ORDER.includes(m.tipo));
  const extraBox = $('#log-extra');
  if (extra.length) {
    extraBox.hidden = false;
    extraBox.innerHTML = extra.map(mealEntry).join('');
  } else {
    extraBox.hidden = true;
    extraBox.innerHTML = '';
  }
}

function renderDayNotes(note) {
  const ul = $('#day-notes');
  ul.innerHTML = (note || []).length
    ? note.map((n) => `
        <li>
          <span class="note-kind">${esc(NOTE_LABELS[n.tipo] || n.tipo)}</span>
          <span class="note-text">${esc(n.testo)}</span>
          <button type="button" class="icon-btn" data-del-note="${n.id}" aria-label="Elimina nota">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
        </li>`).join('')
    : '<li class="note">Nessuna nota per questo giorno.</li>';
}

async function addDayNote(testo, tipo) {
  try {
    await api('api/note', { method: 'POST', body: { ora: `${state.day}T12:00`, testo, tipo } });
    await loadDay();
  } catch (err) {
    toast(err.message);
  }
}

async function removeDayNote(id) {
  try {
    await api(`api/note/${id}`, { method: 'DELETE' });
    await loadDay();
  } catch (err) {
    toast(err.message);
  }
}

// ---------------------------------------------------------------- editor pasto

const editor = { id: null, tipo: null, voci: [], foto: [] };

function defaultType(date) {
  const h = date.getHours() + date.getMinutes() / 60;
  if (h < 5) return 'fuori_pasto';
  if (h < 10.5) return 'colazione';
  if (h < 12) return 'spuntino';
  if (h < 15) return 'pranzo';
  if (h < 18.5) return 'merenda';
  if (h < 22.5) return 'cena';
  return 'fuori_pasto';
}

function openMealEditor(meal = null, preset = {}) {
  editor.id = meal ? meal.id : null;
  let time;
  if (meal) time = meal.ora.slice(0, 16).replace(' ', 'T');
  else if (preset.ora) time = preset.ora;
  else if (state.day === state.today) time = localInput(new Date());
  else time = `${state.day}T12:00`;
  editor.tipo = meal ? meal.tipo : (preset.tipo || defaultType(new Date(time)));
  editor.voci = meal
    ? meal.voci.map((v) => ({
      alimento_id: v.alimento_id,
      descrizione: v.descrizione,
      grammi: v.grammi,
      carbo_g: v.carbo_g,
      per100: v.grammi ? (v.carbo_g / v.grammi) * 100 : null,
    }))
    : [];
  editor.foto = meal ? (meal.foto || []) : [];
  editor.creatoDaFoto = meal ? meal.creato_da === 'photo' : false;
  editor.ultimaAnalisi = meal ? meal.analisi_foto : null;
  $('#meal-sheet-title').textContent = meal ? 'Modifica pasto' : 'Nuovo pasto';
  $('#meal-time').value = time;
  $('#meal-notes').value = meal?.note || '';
  $('#meal-delete').hidden = !meal;
  $('#meal-error').textContent = '';
  $('#meal-search').value = '';
  $('#meal-off-results').innerHTML = '';
  closeFreeForm();
  renderChips();
  renderItems();
  renderPhotos();
  renderMealReview(meal);
  showSheet('#meal-sheet');
  setAdderTab('frequenti');
  loadMealFrequent();
  searchMealFoods('');
}

function renderMealReview(meal) {
  const box = $('#meal-review');
  if (!meal || !meal.analisi) { box.hidden = true; box.innerHTML = ''; return; }
  const a = meal.analisi;
  const righe = [];
  if (a.in_attesa) {
    righe.push(['CamAPS', "dati di quest'ora non ancora arrivati su Glooko"]);
  } else {
    if (a.differenza_carbo_g != null && Math.abs(a.differenza_carbo_g) >= 1) {
      const diff = a.differenza_carbo_g;
      righe.push(['Dichiarati vs CamAPS',
        `<span class="pos">${diff > 0 ? '+' : '−'}${n0(Math.abs(diff))} g</span> ${diff > 0 ? 'in più' : 'in meno'} rispetto a CamAPS`]);
    }
    if (a.boli && a.boli.length) {
      righe.push(['Bolo collegato', `<span class="ins">${n1(a.bolo_U)} U</span> alle ${a.boli.map((b) => b.ora).join(' e ')}`]);
    }
    if (a.picco != null) righe.push(['Picco glicemico', `${a.picco} mg/dL alle ${a.picco_ora}`]);
  }
  const mm = minutesOf(meal.ora.length > 16 ? meal.ora : `${meal.ora}:00`);
  const glic = ((state.payload && state.payload.glicemie) || []).filter((p) => p[0] >= mm - 30 && p[0] <= mm + 180);
  box.hidden = false;
  const vuoto = !glic.length && !righe.length;
  box.innerHTML = `
    ${glic.length ? '<div class="mini-chart-wrap"><div id="meal-review-chart"></div></div>' : ''}
    ${righe.length ? `<dl class="status-rows meal-review-facts">${righe.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>` : ''}
    ${vuoto ? '<p class="hint">Dati CamAPS non disponibili per questo orario.</p>' : ''}`;
  if (glic.length) renderMiniChart('#meal-review-chart', mm, glic);
}

function renderMiniChart(sel, centerMin, points) {
  const box = $(sel);
  const W = Math.max(280, Math.round(box.clientWidth || 320));
  const H = 110;
  const m = { l: 30, r: 8, t: 8, b: 18 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const low = state.stato?.soglie?.bassa ?? 70;
  const high = state.stato?.soglie?.alta ?? 180;
  const minX = centerMin - 30;
  const maxX = centerMin + 180;
  const vals = points.map((p) => p[1]);
  const yMin = 40;
  const yMax = Math.min(400, Math.max(300, Math.ceil(Math.max(high, ...vals) / 50) * 50));
  const x = (mm) => m.l + ((mm - minX) / (maxX - minX)) * pw;
  const y = (v) => m.t + (1 - (Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * ph;
  const f = (v) => v.toFixed(1);
  const out = [`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Glicemia intorno al pasto">`];
  out.push(`<rect x="${m.l}" y="${f(y(high))}" width="${pw}" height="${f(y(low) - y(high))}" fill="var(--band)"/>`);
  out.push(`<line x1="${f(x(centerMin))}" x2="${f(x(centerMin))}" y1="${m.t}" y2="${m.t + ph}" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="3 3"/>`);
  let d = '';
  let prev = null;
  for (const [mm, v] of points) { d += `${prev == null || mm - prev > 15 ? 'M' : 'L'}${f(x(mm))},${f(y(v))}`; prev = mm; }
  out.push(`<path d="${d}" fill="none" stroke="var(--ink)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`);
  out.push('</svg>');
  box.innerHTML = out.join('');
}

async function openOccurrences(foodId, name) {
  $('#occ-title').textContent = `Le altre volte: ${name || ''}`;
  $('#occ-chart').innerHTML = '<div class="skeleton skeleton-chart"></div>';
  $('#occ-summary').innerHTML = '';
  $('#occ-list').innerHTML = '';
  showSheet('#occ-sheet');
  let data;
  try {
    data = await api(`api/alimenti/${foodId}/occorrenze`);
  } catch (err) {
    $('#occ-chart').innerHTML = `<div class="chart-empty">${esc(err.message)}</div>`;
    return;
  }
  renderOccChart(data.occorrenze);
  const r = data.riepilogo;
  $('#occ-summary').innerHTML = r.n ? `
    <div><dt>Occorrenze</dt><dd>${r.n}</dd></div>
    <div><dt>Picco medio</dt><dd>${r.picco_medio != null ? `${n0(r.picco_medio)} mg/dL` : '–'}</dd></div>
    <div><dt>Tempo al picco</dt><dd>${r.tempo_al_picco_medio != null ? `${n0(r.tempo_al_picco_medio)} min` : '–'}</dd></div>
    <div><dt>Rientro in range</dt><dd>${r.rientro_pct != null ? `${n0(r.rientro_pct)}%` : '–'}</dd></div>`
    : '<div><dt>Nessuna occorrenza</dt><dd>Non hai ancora registrato questo alimento in un pasto.</dd></div>';
  $('#occ-list').innerHTML = data.occorrenze.map((o) => `
    <li>${esc(whenLocal(o.ora))} · ${n0(o.carbo_g)} g${o.bolo_U != null ? ` · bolo ${n1(o.bolo_U)} U` : ''}${o.picco != null ? ` · picco ${o.picco} a +${n0(o.picco_min)} min` : ''}</li>`).join('');
}

function renderOccChart(occorrenze) {
  const box = $('#occ-chart');
  if (!occorrenze.length) { box.innerHTML = '<div class="chart-empty">Nessuna occorrenza da mostrare.</div>'; return; }
  const W = Math.max(280, Math.round(box.clientWidth || 320));
  const H = 180;
  const m = { l: 32, r: 10, t: 10, b: 24 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const low = state.stato?.soglie?.bassa ?? 70;
  const high = state.stato?.soglie?.alta ?? 180;
  const minX = -30;
  const maxX = 180;
  const allVals = occorrenze.flatMap((o) => o.glicemia.map((p) => p[1]));
  const yMin = 40;
  const yMax = Math.min(400, Math.max(300, Math.ceil(Math.max(high, 180, ...allVals) / 50) * 50));
  const x = (mm) => m.l + ((mm - minX) / (maxX - minX)) * pw;
  const y = (v) => m.t + (1 - (Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * ph;
  const f = (v) => v.toFixed(1);
  const out = [`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Curve glicemiche delle ultime occorrenze">`];
  out.push(`<rect x="${m.l}" y="${f(y(high))}" width="${pw}" height="${f(y(low) - y(high))}" fill="var(--band)"/>`);
  for (let mm = -30; mm <= 180; mm += 30) {
    const xx = f(x(mm));
    out.push(`<line x1="${xx}" x2="${xx}" y1="${m.t}" y2="${m.t + ph}" stroke="var(--line)"/>`);
    out.push(`<text x="${xx}" y="${m.t + ph + 16}" font-size="9.5" fill="var(--muted)" text-anchor="middle" font-family="Space Grotesk, sans-serif">${mm}</text>`);
  }
  out.push(`<line x1="${f(x(0))}" x2="${f(x(0))}" y1="${m.t}" y2="${m.t + ph}" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="3 3"/>`);
  occorrenze.forEach((o, i) => {
    const recente = i === 0;
    let d = '';
    let prev = null;
    for (const [mm, v] of o.glicemia) { d += `${prev == null || mm - prev > 15 ? 'M' : 'L'}${f(x(mm))},${f(y(v))}`; prev = mm; }
    out.push(`<path d="${d}" fill="none" stroke="var(--ink)" stroke-width="${recente ? 2.5 : 1.5}" opacity="${recente ? 1 : 0.3}" stroke-linecap="round" stroke-linejoin="round"/>`);
    if (o.picco != null) out.push(`<circle cx="${f(x(o.picco_min))}" cy="${f(y(o.picco))}" r="${recente ? 3.5 : 2}" fill="var(--high)" opacity="${recente ? 1 : 0.5}"/>`);
  });
  out.push('</svg>');
  box.innerHTML = out.join('');
}

function setAdderTab(tab) {
  $$('.adder-tabs [role="tab"]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.adderTab === tab)));
  $$('.adder-panel').forEach((p) => { p.hidden = p.dataset.adderPanel !== tab; });
  if (tab === 'cerca') $('#meal-search').focus();
  if (tab === 'barcode') openScanner((food) => addFoodToMeal(food));
}

function renderChips() {
  $('#meal-type').innerHTML = Object.entries(MEAL_LABELS).map(([k, label]) => `
    <button type="button" role="radio" aria-checked="${k === editor.tipo}" data-type="${k}">${label}</button>`).join('');
}

const itemCarbs = (v) => (v.per100 != null
  ? ((parseNum(v.grammi) || 0) * v.per100) / 100
  : parseNum(v.carbo_g) || 0);

function updateTotal() {
  $('#meal-total').textContent = n0(editor.voci.reduce((s, v) => s + itemCarbs(v), 0));
}

function renderItems() {
  const ul = $('#meal-items');
  if (!editor.voci.length) {
    ul.innerHTML = '<li class="items-empty">Cerca un alimento qui sotto, oppure aggiungi una voce libera.</li>';
  } else {
    ul.innerHTML = editor.voci.map((v, i) => {
      const isFood = v.per100 != null;
      const sub = isFood ? `${n1(v.per100)} g ogni 100 g` : 'voce libera, carboidrati in grammi';
      const value = isFood ? (v.grammi ?? '') : (v.carbo_g ?? '');
      const label = isFood ? `Grammi di ${esc(v.descrizione)}` : `Carboidrati di ${esc(v.descrizione)}`;
      return `
        <li data-i="${i}">
          <span class="iname">${esc(v.descrizione)}<small>${sub}</small></span>
          <span class="stepper">
            <button type="button" class="icon-btn step-btn" data-step="-10" aria-label="Togli 10 g">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button>
            <span class="unit" data-unit="g"><input type="text" inputmode="decimal" value="${esc(value)}"
              data-field="${isFood ? 'grammi' : 'carbo_g'}" aria-label="${label}"></span>
            <button type="button" class="icon-btn step-btn" data-step="10" aria-label="Aggiungi 10 g">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button>
          </span>
          <span class="icarb">${n1(itemCarbs(v))} g</span>
          <button type="button" class="icon-btn" data-remove aria-label="Togli ${esc(v.descrizione)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
          ${v.alimento_id ? `<button type="button" class="link-btn item-occ-row" data-occ="${v.alimento_id}" data-occ-name="${esc(v.descrizione)}">Le altre volte</button>` : ''}
        </li>`;
    }).join('');
  }
  updateTotal();
  $('.dish-zone').hidden = !editor.voci.some((v) => v.per100 != null);
}

function defaultGrams(food) {
  if (food.porzioni && food.porzioni.length) return n0(food.porzioni[0].grammi);
  if (food.porzione_g) return n0(food.porzione_g);
  return '100';
}

function addFoodToMeal(food) {
  editor.voci.push({
    alimento_id: food.id,
    descrizione: food.nome,
    grammi: defaultGrams(food),
    per100: food.carbo_100g,
  });
  $('#meal-search').value = '';
  $('#meal-off-results').innerHTML = '';
  renderItems();
  searchMealFoods('');
  const inputs = $$('#meal-items input');
  const last = inputs[inputs.length - 1];
  if (last) { last.focus(); last.select(); }
}

function foodRow(food, i) {
  const meta = [food.marca, food.porzione_g ? `porzione ${n0(food.porzione_g)} g` : null].filter(Boolean).join(', ');
  return `
    <li data-idx="${i}" tabindex="0">
      <div><div class="fname">${esc(food.nome)}</div>${meta ? `<div class="fmeta">${esc(meta)}</div>` : ''}</div>
      <div class="fcarb">${n1(food.carbo_100g)} g<small>ogni 100 g</small></div>
    </li>`;
}

let mealFrequent = [];

async function loadMealFrequent() {
  const list = $('#meal-frequent');
  try {
    mealFrequent = await api('api/alimenti/frequenti');
  } catch (err) {
    list.innerHTML = `<li class="note">${esc(err.message)}</li>`;
    return;
  }
  list.innerHTML = mealFrequent.length
    ? mealFrequent.map(foodRow).join('')
    : '<li class="note">I pasti che registri più spesso compariranno qui per aggiungerli in un tocco.</li>';
}

let mealResults = [];
let mealOffResults = [];

let mealCiqual = [];

function ciqualRow(r, i) {
  return `
    <li data-cq="${i}" tabindex="0">
      <div><div class="fname">${esc(r.nome)}</div>${r.nome_fr && r.nome_fr !== r.nome ? `<div class="fmeta">${esc(r.nome_fr)}</div>` : ''}</div>
      <div class="fcarb">${n1(r.carbo_100g)} g<small>ogni 100 g</small></div>
    </li>`;
}

async function ciqualInto(q, listEl, headEl, store) {
  if ((q || '').trim().length < 3) {
    listEl.innerHTML = '';
    headEl.hidden = true;
    return;
  }
  try {
    const res = await api(`api/ciqual?q=${encodeURIComponent(q.trim())}`);
    store.splice(0, store.length, ...res.slice(0, 8));
  } catch {
    store.splice(0, store.length);
  }
  headEl.hidden = !store.length;
  listEl.innerHTML = store.map(ciqualRow).join('');
}

async function searchMealFoods(q) {
  const list = $('#meal-results');
  ciqualInto(q, $('#meal-ciqual'), $('#meal-ciqual-head'), mealCiqual);
  try {
    mealResults = (await api(`api/alimenti?q=${encodeURIComponent(q)}`)).slice(0, 8);
  } catch (err) {
    list.innerHTML = `<li class="note">${esc(err.message)}</li>`;
    return;
  }
  if (!mealResults.length) {
    list.innerHTML = q
      ? '<li class="note">Non è tra i tuoi alimenti. Guarda nella tabella Ciqual qui sotto, cercalo su Open Food Facts o aggiungi una voce libera.</li>'
      : '<li class="note">I tuoi alimenti compariranno qui. Scrivi per cercare anche nella tabella Ciqual.</li>';
    return;
  }
  list.innerHTML = mealResults.map(foodRow).join('');
}

async function saveCiqual(r) {
  return api('api/alimenti', { method: 'POST', body: { nome: r.nome, carbo_100g: r.carbo_100g, fonte: 'ciqual', codice: r.codice } });
}

function openDishForm() {
  const old = $('#dish-form');
  if (old) { old.remove(); return; }
  const err = $('#meal-error');
  err.textContent = '';
  if (!editor.voci.length || editor.voci.some((v) => v.per100 == null || !parseNum(v.grammi))) {
    err.textContent = 'Per salvare una ricetta servono alimenti con i grammi: le voci libere non hanno un peso.';
    return;
  }
  const form = document.createElement('form');
  form.className = 'free-form';
  form.id = 'dish-form';
  form.innerHTML = `
    <input name="n" placeholder="Nome della ricetta, es. Pasta al pomodoro" aria-label="Nome della ricetta" required>
    <input name="p" type="number" min="1" value="1" inputmode="numeric" aria-label="Numero di porzioni" title="Porzioni">
    <button class="secondary" type="submit">Salva ricetta</button>`;
  $('.dish-zone').after(form);
  form.n.focus();
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const voci = editor.voci.map((v) => ({ alimento_id: v.alimento_id, grammi: parseNum(v.grammi), descrizione: v.descrizione }));
    try {
      const ricetta = await api('api/ricette', {
        method: 'POST',
        body: { nome: form.n.value, porzioni_n: Number(form.p.value) || 1, voci },
      });
      form.remove();
      toast(`Ricetta salvata: ${n0(ricetta.porzione_g)} g a porzione (${ricetta.porzioni_n} porzioni)`);
    } catch (e) {
      err.textContent = e.message;
    }
  });
}

async function offSearchInto(q, listEl, store) {
  if ((q || '').trim().length < 3) {
    listEl.innerHTML = '<li class="note">Scrivi almeno 3 lettere nella ricerca.</li>';
    return;
  }
  listEl.innerHTML = '<li class="note">Cerco su Open Food Facts…</li>';
  try {
    const res = await api(`api/off/cerca?q=${encodeURIComponent(q.trim())}`);
    store.splice(0, store.length, ...res);
    listEl.innerHTML = res.length
      ? res.map(foodRow).join('')
      : '<li class="note">Nessun prodotto con i carboidrati indicati. Prova un\'altra parola o aggiungilo a mano.</li>';
  } catch (err) {
    listEl.innerHTML = `<li class="note">${esc(err.message)}</li>`;
  }
}

async function saveOffProduct(p) {
  return api('api/alimenti', { method: 'POST', body: { ...p } });
}

function closeFreeForm() {
  const f = $('#free-form');
  if (f) f.remove();
}

function openFreeForm() {
  closeFreeForm();
  const form = document.createElement('form');
  form.className = 'free-form';
  form.id = 'free-form';
  form.innerHTML = `
    <input name="d" placeholder="Cosa hai mangiato" aria-label="Descrizione" required>
    <input name="g" inputmode="decimal" placeholder="Grammi mangiati" aria-label="Grammi mangiati" required>
    <input name="p" inputmode="decimal" placeholder="Carboidrati ogni 100 g" aria-label="Carboidrati ogni 100 g" required>
    <button class="secondary" type="submit">Aggiungi</button>`;
  $('.adder .off-zone').after(form);
  form.d.focus();
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const grammi = parseNum(form.g.value);
    const per100 = parseNum(form.p.value);
    if (!form.d.value.trim() || grammi == null || grammi <= 0 || per100 == null || per100 < 0) {
      $('#meal-error').textContent = 'Per una voce libera servono descrizione, grammi mangiati e carboidrati ogni 100 g: i grammi del pasto li calcola l\'app.';
      return;
    }
    editor.voci.push({ alimento_id: null, descrizione: form.d.value.trim(), grammi: form.g.value, per100 });
    $('#meal-error').textContent = '';
    closeFreeForm();
    renderItems();
  });
}

function mealBody() {
  const voci = editor.voci.map((v) => (v.alimento_id
    ? { alimento_id: v.alimento_id, grammi: parseNum(v.grammi), descrizione: v.descrizione }
    : { descrizione: v.descrizione, grammi: parseNum(v.grammi), carbo_g: Math.round(itemCarbs(v) * 10) / 10 }));
  const body = { ora: $('#meal-time').value, tipo: editor.tipo, note: $('#meal-notes').value, voci };
  if (editor.creatoDaFoto) {
    body.creato_da = 'photo';
    if (editor.ultimaAnalisi) body.analisi_foto = editor.ultimaAnalisi;
  }
  return body;
}

// Salva subito il pasto (anche senza voci) se non ha ancora un id: serve un
// meal_id prima di poter caricare una foto.
async function ensureMealSaved() {
  if (editor.id) return editor.id;
  const saved = await api('api/pasti', { method: 'POST', body: mealBody() });
  editor.id = saved.id;
  $('#meal-sheet-title').textContent = 'Modifica pasto';
  $('#meal-delete').hidden = false;
  return editor.id;
}

function confidenceTier(c) {
  if (c == null) return null;
  if (c < 0.5) return 'bassa';
  if (c < 0.8) return 'media';
  return 'alta';
}

function renderPhotos() {
  const wrap = $('#meal-photos');
  const iaDisponibile = !!state.stato?.ia_foto?.disponibile;
  const notices = $('#meal-ai-notices');
  const allWarnings = [];
  wrap.innerHTML = editor.foto.map((p) => {
    let analisiHtml = '';
    if (p.stato_analisi === 'in_corso') {
      analisiHtml = '<p class="photo-status">Analisi in corso…</p>';
    } else if (p.stato_analisi === 'errore') {
      analisiHtml = `<p class="photo-status error">${esc(p.errore_analisi || 'Analisi non riuscita.')}</p>`;
    } else if (p.analisi) {
      const a = p.analisi;
      const items = a.items || [];
      (a.warnings || []).forEach((w) => allWarnings.push(w));
      const righe = items.map((it, i) => {
        const tier = confidenceTier(it.confidence);
        const checked = it._checked !== false;
        return `
          <li class="ai-item">
            <label class="ai-item-check">
              <input type="checkbox" ${checked ? 'checked' : ''} data-ai-check="${p.id}:${i}">
              <span class="ai-item-name">${esc(it.name_it)}</span>
              <span class="ai-conf conf-${tier}">${tier}</span>
            </label>
            <p class="ai-item-detail">${esc(it.portion_label)} · ${n0(it.grams_estimate)} g
              <small>(${n0(it.grams_min)}–${n0(it.grams_max)} g)</small> ·
              <b class="carb">${n1(it.carbs_g)} g carb</b></p>
            ${it.reason_short ? `<p class="ai-item-reason">${esc(it.reason_short)}</p>` : ''}
          </li>`;
      }).join('');
      analisiHtml = `
        <div class="ai-card">
          ${items.length
            ? `<ul class="ai-items">${righe}</ul>
               <button type="button" class="primary" data-confirm-analysis="${p.id}">Aggiungi al pasto</button>`
            : '<p class="photo-status">Nessun alimento riconosciuto in questa foto.</p>'}
          <p class="ai-disclaimer">Stima approssimativa: controlla le porzioni prima di salvare.</p>
        </div>`;
    }
    return `
      <div class="photo-thumb" data-photo="${p.id}">
        <img src="api/foto/${p.id}" alt="Foto del pasto">
        <button type="button" class="icon-btn photo-remove" data-remove-photo="${p.id}" aria-label="Elimina foto">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
        ${iaDisponibile ? `
          <input type="text" class="ai-hint" placeholder="Cosa stai mangiando? (facoltativo)" data-hint-for="${p.id}">
          <button type="button" class="link-btn photo-analyze-btn" data-analyze-photo="${p.id}" ${p.stato_analisi === 'in_corso' ? 'disabled' : ''}>Stima nutrienti con l'IA</button>
        ` : '<p class="photo-status">Analisi IA non configurata (manca la chiave Anthropic nelle impostazioni dell\'add-on): salvo solo la foto.</p>'}
        ${analisiHtml}
      </div>`;
  }).join('');
  if (allWarnings.length) {
    notices.hidden = false;
    notices.innerHTML = allWarnings.map((w) => `<li>${esc(w)}</li>`).join('');
  } else {
    notices.hidden = true;
    notices.innerHTML = '';
  }
}

async function addMealPhoto(file) {
  $('#meal-error').textContent = '';
  try {
    const id = await ensureMealSaved();
    const fd = new FormData();
    fd.append('foto', file);
    const res = await fetch(`api/pasti/${id}/foto`, { method: 'POST', body: fd });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.errore) || 'Errore nel caricare la foto.');
    editor.foto.push(data);
    renderPhotos();
    toast('Foto aggiunta');
  } catch (err) {
    $('#meal-error').textContent = err.message;
  }
}

async function removeMealPhoto(id) {
  try {
    await api(`api/foto/${id}`, { method: 'DELETE' });
    editor.foto = editor.foto.filter((p) => p.id !== id);
    renderPhotos();
  } catch (err) {
    toast(err.message);
  }
}

async function analyzeMealPhoto(id) {
  const p = editor.foto.find((x) => x.id === id);
  const hintEl = document.querySelector(`[data-hint-for="${id}"]`);
  const hint = hintEl ? hintEl.value.trim() : '';
  if (p) { p.stato_analisi = 'in_corso'; renderPhotos(); }
  try {
    await api(`api/foto/${id}/analizza`, { method: 'POST', body: hint ? { hint } : {} });
  } catch (err) {
    if (p) { p.stato_analisi = 'errore'; p.errore_analisi = err.message; }
    renderPhotos();
    return;
  }
  pollPhotoAnalysis(id);
}

async function pollPhotoAnalysis(id) {
  for (let tentativo = 0; tentativo < 40; tentativo++) {
    await new Promise((r) => setTimeout(r, 1500));
    let stato;
    try {
      stato = await api(`api/foto/${id}/stato`);
    } catch {
      continue; // un errore di rete temporaneo durante il polling non deve interrompere l'attesa
    }
    if (stato.stato_analisi === 'in_corso') continue;
    const p = editor.foto.find((x) => x.id === id);
    if (p) {
      p.stato_analisi = stato.stato_analisi;
      p.analisi = stato.analisi;
      p.errore_analisi = stato.errore_analisi;
      renderPhotos();
    }
    return;
  }
  const p = editor.foto.find((x) => x.id === id);
  if (p) { p.stato_analisi = 'errore'; p.errore_analisi = "L'analisi sta impiegando troppo tempo: riprova."; renderPhotos(); }
}

function toggleAiItem(photoId, itemIdx) {
  const p = editor.foto.find((x) => x.id === photoId);
  const it = p?.analisi?.items?.[itemIdx];
  if (it) it._checked = it._checked === false; // riparte da "true" al primo tocco
}

function confirmAnalysis(photoId) {
  const p = editor.foto.find((x) => x.id === photoId);
  const items = (p?.analisi?.items || []).filter((it) => it._checked !== false);
  if (!items.length) { toast('Nessun alimento selezionato'); return; }
  for (const it of items) {
    const grams = it.grams_estimate || 1;
    editor.voci.push({
      alimento_id: null,
      descrizione: it.name_it,
      grammi: n0(grams),
      per100: grams ? (it.carbs_g / grams) * 100 : 0,
    });
  }
  editor.creatoDaFoto = true;
  editor.ultimaAnalisi = p.analisi;
  renderItems();
  toast(`${items.length} alimenti aggiunti dalla foto: controlla le porzioni`);
}

async function saveMeal() {
  const btn = $('#meal-save');
  $('#meal-error').textContent = '';
  const body = mealBody();
  btn.disabled = true;
  try {
    const saved = await api(editor.id ? `api/pasti/${editor.id}` : 'api/pasti', {
      method: editor.id ? 'PUT' : 'POST', body,
    });
    hideSheet('#meal-sheet');
    toast(editor.id ? 'Pasto aggiornato' : 'Pasto salvato');
    state.day = saved.ora.slice(0, 10);
    await loadDay();
  } catch (err) {
    $('#meal-error').textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function deleteMeal() {
  if (!editor.id || !window.confirm('Eliminare questo pasto dal diario?')) return;
  try {
    await api(`api/pasti/${editor.id}`, { method: 'DELETE' });
    hideSheet('#meal-sheet');
    toast('Pasto eliminato');
    await loadDay();
  } catch (err) {
    $('#meal-error').textContent = err.message;
  }
}

// ---------------------------------------------------------------- codice a barre

const scan = { onFood: null, stream: null, timer: null };

function stopScanner() {
  clearInterval(scan.timer);
  scan.timer = null;
  if (scan.stream) scan.stream.getTracks().forEach((t) => t.stop());
  scan.stream = null;
  const v = $('#scan-video');
  v.hidden = true;
  v.srcObject = null;
}

async function openScanner(onFood) {
  scan.onFood = onFood;
  $('#scan-code').value = '';
  $('#scan-error').textContent = '';
  const hint = $('#scan-hint');
  hint.textContent = 'Scrivi il numero stampato sotto il codice a barre.';
  showSheet('#scan-sheet');
  if (!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia) return;
  try {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const formats = ['ean_13', 'ean_8', 'upc_a', 'upc_e'].filter((f) => supported.includes(f));
    if (!formats.length) return;
    const detector = new window.BarcodeDetector({ formats });
    scan.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const video = $('#scan-video');
    video.srcObject = scan.stream;
    video.hidden = false;
    await video.play();
    hint.textContent = 'Inquadra il codice a barre, oppure scrivi il numero.';
    scan.timer = setInterval(async () => {
      try {
        const codes = await detector.detect(video);
        if (codes.length) {
          $('#scan-code').value = codes[0].rawValue;
          stopScanner();
          lookupBarcode();
        }
      } catch { /* fotogramma non leggibile */ }
    }, 350);
  } catch {
    stopScanner();
    hint.textContent = 'La fotocamera non è disponibile qui: scrivi il numero sotto il codice a barre.';
  }
}

async function lookupBarcode() {
  const code = $('#scan-code').value.replace(/\D/g, '');
  const err = $('#scan-error');
  err.textContent = '';
  if (code.length < 6) {
    err.textContent = 'Il codice deve avere almeno 6 cifre.';
    return;
  }
  const btn = $('#scan-go');
  btn.disabled = true;
  try {
    const res = await api(`api/off/barcode/${code}`);
    const food = res.locale || await saveOffProduct(res.off);
    hideSheet('#scan-sheet');
    if (scan.onFood) scan.onFood(food, !res.locale);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- alimenti

let foods = [];
let foodOffResults = [];
let editingFood = null;

let foodCiqual = [];

async function loadFoods() {
  const q = $('#food-search').value;
  if (!q.trim() && !state.showAllFoods) {
    $('#food-landing').hidden = false;
    $('#food-browse').hidden = true;
    return;
  }
  $('#food-landing').hidden = true;
  $('#food-browse').hidden = false;
  const list = $('#food-list');
  ciqualInto(q, $('#food-ciqual-list'), $('#food-ciqual-head'), foodCiqual);
  try {
    foods = await api(`api/alimenti?q=${encodeURIComponent(q)}`);
  } catch (err) {
    list.innerHTML = `<li class="note">${esc(err.message)}</li>`;
    return;
  }
  if (!foods.length) {
    list.innerHTML = q
      ? '<li class="note">Non è tra i tuoi alimenti. Guarda nella tabella Ciqual qui sotto o aggiungilo con Nuovo alimento.</li>'
      : '<li class="note">L\'elenco è vuoto. Cerca un alimento (per esempio "pasta integrale"): lo trovi nella tabella Ciqual e lo aggiungi con il nome e la porzione che preferisci.</li>';
    return;
  }
  list.innerHTML = foods.map(foodRow).join('');
}

let foodPrefill = null;
const SOURCE_HINTS = {
  openfoodfacts: 'Valori da Open Food Facts: se ti sembrano strani, confrontali con l\'etichetta.',
  ciqual: 'Valori dalla tabella Ciqual (ANSES): carboidrati disponibili, fibra esclusa. Dagli un nome in italiano e la porzione abituale, per esempio 80 g di pasta.',
  piatto: 'Piatto composto da te: i carboidrati per 100 g vengono dagli ingredienti.',
  manuale: 'Per gli alimenti confezionati usa l\'etichetta; per quelli sfusi cerca nella tabella Ciqual.',
};

function openFoodSheet(food = null, prefill = null) {
  editingFood = food;
  foodPrefill = prefill;
  const src = food || prefill || {};
  const form = $('#food-form');
  form.nome.value = src.nome || '';
  form.marca.value = src.marca || '';
  form.carbo_100g.value = src.carbo_100g != null ? n1(src.carbo_100g) : '';
  form.porzione_g.value = src.porzione_g != null ? n0(src.porzione_g) : '';
  form.barcode.value = src.barcode || '';
  $('#food-sheet-title').textContent = food ? 'Modifica alimento' : 'Nuovo alimento';
  $('#food-delete').hidden = !food;
  $('#food-occ').hidden = !food;
  $('#food-error').textContent = '';
  $('#food-source').textContent = SOURCE_HINTS[src.fonte || 'manuale'] || SOURCE_HINTS.manuale;
  showSheet('#food-sheet');
  if (!food) form.nome.focus();
}

async function saveFood() {
  const form = $('#food-form');
  const body = {
    nome: form.nome.value,
    marca: form.marca.value,
    carbo_100g: form.carbo_100g.value,
    porzione_g: form.porzione_g.value,
    barcode: form.barcode.value,
    fonte: editingFood?.fonte || foodPrefill?.fonte || 'manuale',
    codice: editingFood?.codice || foodPrefill?.codice || null,
  };
  try {
    await api(editingFood ? `api/alimenti/${editingFood.id}` : 'api/alimenti', {
      method: editingFood ? 'PUT' : 'POST', body,
    });
    hideSheet('#food-sheet');
    toast(editingFood ? 'Alimento aggiornato' : 'Alimento salvato');
    loadFoods();
  } catch (err) {
    $('#food-error').textContent = err.message;
  }
}

async function deleteFood() {
  if (!editingFood) return;
  if (!window.confirm(`Eliminare ${editingFood.nome}? I pasti già registrati restano invariati.`)) return;
  try {
    await api(`api/alimenti/${editingFood.id}`, { method: 'DELETE' });
    hideSheet('#food-sheet');
    toast('Alimento eliminato');
    loadFoods();
  } catch (err) {
    $('#food-error').textContent = err.message;
  }
}

// ---------------------------------------------------------------- andamento

async function loadWeekInsights() {
  const list = $('#week-frasi');
  const range = $('#week-range');
  list.innerHTML = '<li class="skeleton" style="height:48px"></li>';
  range.textContent = '';
  try {
    const r = await api('api/insights/settimana');
    const f = (s) => parseDay(s).toLocaleDateString('it-IT', { day: 'numeric', month: 'short' });
    range.textContent = `${f(r.settimana_da)} – ${f(r.settimana_a)}`;
    list.innerHTML = r.frasi.map((frase) => `<li>${esc(frase)}</li>`).join('');
  } catch (err) {
    list.innerHTML = `<li class="muted">${esc(err.message)}</li>`;
  }
}

async function loadAgp() {
  const to = state.today;
  const from = addDays(to, -(state.trendDays - 1));
  const box = $('#agp-chart');
  box.innerHTML = '<div class="skeleton skeleton-chart"></div>';
  try {
    state.agpBins = await api(`api/agp?da=${from}&a=${to}`);
    renderAgpChart(state.agpBins);
  } catch (err) {
    box.innerHTML = `<div class="chart-empty">${esc(err.message)}</div>`;
  }
}

function renderAgpChart(bins) {
  const box = $('#agp-chart');
  if (!bins.some((b) => b.n > 0)) {
    box.innerHTML = '<div class="chart-empty">Nessun dato di glicemia nel periodo.</div>';
    return;
  }
  const W = Math.max(300, Math.round(box.clientWidth || 360));
  const H = 200;
  const m = { l: 34, r: 12, t: 14, b: 30 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const low = state.stato?.soglie?.bassa ?? 70;
  const high = state.stato?.soglie?.alta ?? 180;
  const seen = bins.filter((b) => b.n).flatMap((b) => [b.p10, b.p90]);
  const maxV = Math.max(high, 180, ...seen);
  const yMin = 40;
  const yMax = Math.min(400, Math.max(300, Math.ceil(maxV / 50) * 50));
  const x = (mm) => m.l + (mm / 1440) * pw;
  const y = (v) => m.t + (1 - (Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * ph;
  const f = (v) => v.toFixed(1);
  const out = [];

  out.push(`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Profilo glicemico tipico del periodo">`);
  out.push(`<rect class="band" x="${m.l}" y="${f(y(high))}" width="${pw}" height="${f(y(low) - y(high))}"/>`);
  for (const v of [low, high]) {
    out.push(`<line class="band-edge" x1="${m.l}" x2="${m.l + pw}" y1="${f(y(v))}" y2="${f(y(v))}"/>`);
  }
  for (let h = 0; h <= 24; h += 3) {
    const xx = f(x(h * 60));
    out.push(`<line class="grid" x1="${xx}" x2="${xx}" y1="${m.t}" y2="${m.t + ph}"/>`);
    if (h < 24) {
      out.push(`<text class="axis" x="${xx}" y="${m.t + ph + 20}" text-anchor="${h === 0 ? 'start' : 'middle'}">${h}:00</text>`);
    }
  }
  for (const v of [low, high]) {
    out.push(`<text class="axis" x="${m.l - 6}" y="${f(y(v) + 4)}" text-anchor="end">${v}</text>`);
  }

  // Bande 10-90 e 25-75, spezzate dove non ci sono letture per quella fascia oraria
  const band = (loKey, hiKey, opacity) => {
    const segs = [];
    let cur = null;
    for (const b of bins) {
      if (!b.n) { if (cur) { segs.push(cur); cur = null; } continue; }
      if (!cur) cur = [];
      cur.push(b);
    }
    if (cur) segs.push(cur);
    const d = segs.map((seg) => {
      const top = seg.map((b, i) => `${i === 0 ? 'M' : 'L'}${f(x(b.min))},${f(y(b[hiKey]))}`).join('');
      const bot = seg.slice().reverse().map((b) => `L${f(x(b.min))},${f(y(b[loKey]))}`).join('');
      return `${top}${bot}Z`;
    }).join(' ');
    if (d) out.push(`<path d="${d}" fill="var(--ink)" opacity="${opacity}"/>`);
  };
  band('p10', 'p90', 0.1);
  band('p25', 'p75', 0.16);

  // Mediana, interrotta dove non ci sono letture
  let d = '';
  let open = false;
  for (const b of bins) {
    if (!b.n) { open = false; continue; }
    d += `${open ? 'L' : 'M'}${f(x(b.min))},${f(y(b.p50))}`;
    open = true;
  }
  out.push(`<path class="glu" d="${d}"/>`);
  out.push('</svg>');
  box.innerHTML = out.join('');
}

function trendRangeTitle(r) {
  return r.tir_pct == null ? 'Dati insufficienti'
    : `sotto ${n0(r.sotto_pct)}%, nel range ${n0(r.tir_pct)}%, sopra ${n0(r.sopra_pct)}%`;
}

async function loadTrend() {
  const to = state.today;
  const from = addDays(to, -(state.trendDays - 1));
  let rows;
  try {
    rows = await api(`api/periodo?da=${from}&a=${to}`);
  } catch (err) {
    $('#trend-table').innerHTML = `<caption>${esc(err.message)}</caption>`;
    return;
  }
  const full = rows.filter((r) => r.copertura_cgm_pct >= 70);
  const nRead = full.reduce((s, r) => s + r.letture_cgm, 0);
  const wavg = (key) => (nRead ? full.reduce((s, r) => s + (r[key] || 0) * r.letture_cgm, 0) / nRead : null);
  const avg = (list, key) => (list.length ? list.reduce((s, r) => s + (r[key] || 0), 0) / list.length : null);
  const withMeals = rows.filter((r) => r.carbo_mangiati_g != null);
  const withSteps = rows.filter((r) => r.passi != null);
  const declared = full.map((r) => ({ v: r.carbo_camaps_g || 0 }));
  $('#trend-figures').innerHTML = `
    <div><dt>Media</dt><dd>${n0(wavg('media_mgdl'))}<small>mg/dL</small></dd></div>
    <div><dt>Nel range</dt><dd>${n0(wavg('tir_pct'))}<small>%</small></dd></div>
    <div><dt>Insulina al giorno</dt><dd class="ins">${n1(avg(full, 'totale_U'))}<small>U</small>
      <span class="sub">${n1(avg(full, 'basale_U'))} automatica</span></dd></div>
    <div><dt>Carboidrati al giorno</dt><dd class="carb">${withMeals.length ? n0(avg(withMeals, 'carbo_mangiati_g')) : '–'}<small>g</small>
      <span class="sub">${n0(avg(declared, 'v'))} g inseriti in CamAPS</span></dd></div>
    <div><dt>Passi al giorno</dt><dd class="act">${withSteps.length ? n0(avg(withSteps, 'passi')) : '–'}</dd></div>`;
  const dayLabelShort = (r) => parseDay(r.giorno).toLocaleDateString('it-IT', { weekday: 'short', day: 'numeric', month: 'short' });
  const rangeTitle = trendRangeTitle;
  const reversed = rows.slice().reverse();
  state.trendRows = rows;

  const body = reversed.map((r) => {
    const label = dayLabelShort(r);
    const partial = r.copertura_cgm_pct < 70;
    const empty = !r.letture_cgm && !r.totale_U && !r.carbo_camaps_g && !r.carbo_ipo_g;
    return `
      <tr class="${partial ? 'partial' : ''}">
        <td data-day="${r.giorno}" tabindex="0">${label}</td>
        <td>${n0(r.media_mgdl)}</td>
        <td>${r.tir_pct != null ? `${n0(r.tir_pct)}%` : '–'}</td>
        <td class="ins">${empty ? '–' : n1(r.totale_U)}</td>
        <td class="carb">${r.carbo_mangiati_g != null ? n0(r.carbo_mangiati_g) : '–'}</td>
        <td>${empty ? '–' : n0(r.carbo_camaps_g)}</td>
        <td class="act">${r.passi != null ? n0(r.passi) : '–'}</td>
      </tr>`;
  }).join('');
  $('#trend-table').innerHTML = `
    <thead><tr><th>Giorno</th><th>Media</th><th>Range</th><th>Insulina</th>
      <th>Mangiati</th><th>CamAPS</th><th>Passi</th></tr></thead>
    <tbody>${body}</tbody>`;

  $('#trend-cards').innerHTML = reversed.map((r) => {
    const label = dayLabelShort(r);
    const partial = r.copertura_cgm_pct < 70;
    const empty = !r.letture_cgm && !r.totale_U && !r.carbo_camaps_g && !r.carbo_ipo_g;
    const bar = r.tir_pct == null
      ? '<span class="seg nodata" style="width:100%"></span>'
      : `<span class="seg low" style="width:${r.sotto_pct || 0}%"></span>
         <span class="seg inrange" style="width:${r.tir_pct || 0}%"></span>
         <span class="seg high" style="width:${r.sopra_pct || 0}%"></span>`;
    return `
      <li class="trend-card ${partial ? 'partial' : ''}" data-day="${r.giorno}" tabindex="0">
        <div class="tc-head"><span class="tc-day">${label}</span>
          <span class="tc-range">${r.tir_pct != null ? `${n0(r.tir_pct)}% nel range` : 'dati insufficienti'}</span></div>
        <div class="tc-bar" title="${esc(rangeTitle(r))}">${bar}</div>
        <div class="tc-stats">
          <span>${n0(r.media_mgdl)}<small>mg/dL</small></span>
          <span class="ins">${empty ? '–' : n1(r.totale_U)}<small>U</small></span>
          <span class="carb">${r.carbo_mangiati_g != null ? n0(r.carbo_mangiati_g) : '–'}<small>g</small></span>
          ${r.passi != null ? `<span class="act">${n0(r.passi)}<small>passi</small></span>` : ''}
        </div>
      </li>`;
  }).join('');

  renderTrendChart(rows, rangeTitle);
}

function renderTrendChart(rows, rangeTitle) {
  const box = $('#trend-chart');
  if (!rows.length) { box.innerHTML = ''; return; }
  const f = (v) => v.toFixed(1);
  const W = Math.max(280, Math.round(box.clientWidth || 320));
  const H = 88;
  const gap = Math.min(6, 160 / rows.length);
  const bw = Math.max(3, (W - gap * (rows.length - 1)) / rows.length);
  const out = [`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Percentuale nel range per giorno">`];
  rows.forEach((r, i) => {
    const x = i * (bw + gap);
    const partial = r.copertura_cgm_pct < 70;
    const title = `${esc(r.giorno)}: ${esc(rangeTitle(r))}`;
    if (r.tir_pct == null) {
      out.push(`<rect x="${f(x)}" y="${H - 8}" width="${f(bw)}" height="8" rx="2" fill="var(--line)"><title>${title}</title></rect>`);
      return;
    }
    const sopraH = (r.sopra_pct / 100) * H;
    const rangeH = (r.tir_pct / 100) * H;
    const sottoH = (r.sotto_pct / 100) * H;
    const op = partial ? '0.45' : '1';
    let y = 0;
    out.push(`<g opacity="${op}">`);
    out.push(`<rect x="${f(x)}" y="${f(y)}" width="${f(bw)}" height="${f(sopraH)}" fill="var(--high)"/>`);
    y += sopraH;
    out.push(`<rect x="${f(x)}" y="${f(y)}" width="${f(bw)}" height="${f(rangeH)}" fill="var(--inrange)"/>`);
    y += rangeH;
    out.push(`<rect x="${f(x)}" y="${f(y)}" width="${f(bw)}" height="${f(sottoH)}" fill="var(--low)"/>`);
    out.push('</g>');
    out.push(`<rect class="hit" data-day="${r.giorno}" x="${f(x)}" y="0" width="${f(bw)}" height="${H}" fill="transparent" tabindex="0"><title>${title}</title></rect>`);
  });
  out.push('</svg>');
  box.innerHTML = out.join('');
}

// ---------------------------------------------------------------- stato

async function refreshStatus() {
  try {
    state.stato = await api('api/stato');
  } catch (err) {
    $('#freshness').textContent = err.message;
    return;
  }
  state.today = state.stato.oggi || state.today;
  renderFreshness();
  if (!$('#view-stato').hidden) renderStatus();
}

const SYNC_DOT = { ok: 'ok', errore: 'error', in_corso: 'busy', non_configurato: 'warn', mai: 'warn' };

function renderStatus() {
  const s = state.stato;
  if (!s) return;
  const c = s.conteggi;
  const dotClass = SYNC_DOT[s.sync_stato] || 'warn';
  const lastData = timeAgo(s.ultima_glicemia, false);
  const lastSync = timeAgo(s.ultima_sync_utc, true);

  $('#status-top').innerHTML = `
    <div class="status-head">
      <span class="status-dot ${dotClass}"></span>
      <span class="status-headline">${esc(STATUS_LABELS[s.sync_stato] || s.sync_stato)}</span>
    </div>
    <dl class="status-rows">
      <div><dt>Ultimi dati CamAPS</dt><dd>${lastData ? esc(lastData) : 'nessuno'}</dd></div>
      <div><dt>Ultimo aggiornamento</dt><dd>${lastSync ? esc(lastSync) : '–'}</dd></div>
    </dl>
    ${s.sync_errore ? `<p class="status-error">${esc(s.sync_errore)}</p>` : ''}
    ${s.passi_errore ? `<p class="status-error">Passi: ${esc(s.passi_errore)}</p>` : ''}`;

  $('#status-secondary').innerHTML = `
    <dl class="status-list">
      <div><dt>Ultimo tentativo</dt><dd>${esc(timeAgo(s.ultimo_tentativo_utc, true) || '–')}</dd></div>
      <div><dt>Passi (Home Assistant)</dt><dd>${esc(STATUS_LABELS[s.passi_stato] || s.passi_stato)}</dd></div>
      <div><dt>Nel database</dt><dd>${n0(c.glucose)} glicemie, ${n0(c.bolus)} boli, ${n0(c.meal)} pasti, ${n0(c.food)} alimenti${c.meal_photo != null ? `, ${n0(c.meal_photo)} foto` : ''}</dd></div>
      <div><dt>Tabella Ciqual</dt><dd>${esc(CIQUAL_LABEL(s.ciqual))}</dd></div>
      <div><dt>Prodotti italiani (OFF)</dt><dd>${esc(OFF_IMPORT_LABEL(s.off_import))}</dd></div>
      <div><dt>Analisi foto (IA)</dt><dd>${esc(iaFotoLabel(s.ia_foto))}</dd></div>
      <div><dt>Versione</dt><dd>${esc(s.versione)}</dd></div>
    </dl>`;

  $('#ciqual-import').hidden = s.ciqual.stato === 'ok' || s.ciqual.stato === 'in_corso';
  $('#off-import').hidden = s.off_import.stato === 'in_corso';
  $('#off-import').disabled = s.off_import.stato === 'in_corso';
  $('#off-import').textContent = s.off_import.alimenti
    ? 'Aggiorna prodotti italiani (Open Food Facts)' : 'Importa prodotti italiani (Open Food Facts)';
  const btn = $('#sync-now');
  btn.disabled = s.in_corso;
  $('.spinner', btn).hidden = !s.in_corso;
  $('.btn-label', btn).textContent = s.in_corso ? 'Aggiornamento in corso…' : 'Aggiorna ora da Glooko';
}

async function syncNow() {
  try {
    await api('api/sync', { method: 'POST' });
  } catch (err) {
    toast(err.message);
    return;
  }
  toast('Aggiornamento avviato');
  const started = Date.now();
  const poll = async () => {
    await refreshStatus();
    if (state.stato?.in_corso && Date.now() - started < 180000) {
      setTimeout(poll, 3000);
    } else {
      toast(state.stato?.sync_stato === 'errore' ? 'Aggiornamento non riuscito' : 'Dati aggiornati');
      loadDay();
    }
  };
  setTimeout(poll, 1500);
}

// ---------------------------------------------------------------- calendario

const MESI_IT = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
  'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];

function tirBarClass(r) {
  if (r.tir_pct == null) return '';
  if (r.tir_pct >= 70) return 'ok';
  if (r.tir_pct >= 40) return 'mid';
  return 'low';
}

async function loadCalendar() {
  if (state.calYear == null) {
    const d = parseDay(state.today);
    state.calYear = d.getFullYear();
    state.calMonth = d.getMonth() + 1;
  }
  $('#cal-title').textContent = `${MESI_IT[state.calMonth - 1].charAt(0).toUpperCase()}${MESI_IT[state.calMonth - 1].slice(1)} ${state.calYear}`;
  $('#cal-search').value = '';
  $('#cal-search-results').hidden = true;
  $('.cal-weekdays').hidden = false;
  const grid = $('#cal-grid');
  grid.hidden = false;
  grid.innerHTML = '<div class="skeleton" style="grid-column:1/-1;height:280px;"></div>';
  let righe;
  try {
    righe = await api(`api/calendario?anno=${state.calYear}&mese=${state.calMonth}`);
  } catch (err) {
    grid.innerHTML = `<p class="chart-empty" style="grid-column:1/-1">${esc(err.message)}</p>`;
    return;
  }
  const primo = new Date(state.calYear, state.calMonth - 1, 1);
  const offset = (primo.getDay() + 6) % 7; // 0 = lunedi'
  const celle = [];
  for (let i = 0; i < offset; i++) celle.push('<div class="cal-day empty"></div>');
  for (const r of righe) {
    const giornoNum = Number(r.giorno.slice(8, 10));
    const futuro = r.giorno > state.today;
    const hasPhoto = r.foto_id != null;
    celle.push(`
      <button type="button" class="cal-day ${hasPhoto ? 'has-photo' : ''} ${futuro ? 'future' : ''}"
        data-cal-day="${r.giorno}" ${futuro ? 'disabled' : ''} aria-label="${r.giorno}">
        ${hasPhoto ? `<img src="api/foto/${r.foto_id}" alt="" loading="lazy">` : ''}
        <span class="cal-num">${giornoNum}</span>
        ${r.carbo_mangiati_g != null ? `<span class="cal-carb">${n0(r.carbo_mangiati_g)}g</span>` : ''}
        <span class="cal-bar ${tirBarClass(r)}"></span>
      </button>`);
  }
  grid.innerHTML = celle.join('');
}

async function searchDiary(q) {
  const results = $('#cal-search-results');
  const grid = $('#cal-grid');
  const weekdays = $('.cal-weekdays');
  if (!q.trim()) {
    results.hidden = true;
    grid.hidden = false;
    weekdays.hidden = false;
    return;
  }
  grid.hidden = true;
  weekdays.hidden = true;
  results.hidden = false;
  results.innerHTML = '<li class="note">Cerco…</li>';
  try {
    const righe = await api(`api/ricerca?q=${encodeURIComponent(q.trim())}`);
    results.innerHTML = righe.length
      ? righe.map((r) => `
          <li data-cal-search-day="${r.giorno}" tabindex="0">
            <div><div class="fname">${esc(whenLocal(`${r.giorno} ${r.ora}:00`))}</div>
            <div class="fmeta">${esc(MEAL_LABELS[r.tipo] || r.tipo)}</div></div>
          </li>`).join('')
      : '<li class="note">Nessun pasto trovato con questo testo.</li>';
  } catch (err) {
    results.innerHTML = `<li class="note">${esc(err.message)}</li>`;
  }
}

function shiftCalendar(delta) {
  let m = state.calMonth + delta;
  let y = state.calYear;
  if (m < 1) { m = 12; y -= 1; }
  if (m > 12) { m = 1; y += 1; }
  state.calMonth = m;
  state.calYear = y;
  loadCalendar();
}

let dayDetailPayload = null;

async function openDayDetail(day) {
  $('#daydetail-title').textContent = dayLabel(day);
  $('#daydetail-figures').innerHTML = '<div class="skeleton skeleton-figures"></div>';
  $('#daydetail-meals').innerHTML = '';
  dayDetailPayload = null;
  showSheet('#daydetail-sheet');
  try {
    const p = await api(`api/giorno?data=${day}`);
    dayDetailPayload = p;
    renderFigures(p, '#daydetail-figures');
    const pasti = p.pasti.slice().sort((a, b) => a.ora.localeCompare(b.ora));
    $('#daydetail-meals').innerHTML = pasti.length
      ? pasti.map(mealEntry).join('')
      : '<li class="items-empty">Nessun pasto registrato in questo giorno.</li>';
  } catch (err) {
    $('#daydetail-figures').innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
  }
}

function bindSwipe(el, onSwipeLeft, onSwipeRight) {
  let x0 = null;
  let y0 = null;
  el.addEventListener('touchstart', (ev) => {
    x0 = ev.touches[0].clientX;
    y0 = ev.touches[0].clientY;
  }, { passive: true });
  el.addEventListener('touchend', (ev) => {
    if (x0 == null) return;
    const dx = ev.changedTouches[0].clientX - x0;
    const dy = ev.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) onSwipeLeft(); else onSwipeRight();
    }
  }, { passive: true });
}

// ---------------------------------------------------------------- navigazione

function showTab(tab) {
  for (const b of $$('.tabs button')) {
    if (b.dataset.tab === tab) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  }
  for (const v of $$('.view')) v.hidden = v.id !== `view-${tab}`;
  $('.add-bar').hidden = tab !== 'pasti';
  $('.app').scrollTop = 0;
  if (tab === 'giorno' || tab === 'pasti') loadDay();
  if (tab === 'calendario') loadCalendar();
  if (tab === 'andamento') { loadTrend(); loadAgp(); loadWeekInsights(); }
  if (tab === 'stato') setSettingsTab('stato');
}

function setSettingsTab(tab) {
  $$('.settings-tabs [role="tab"]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.settingsTab === tab)));
  $$('[data-settings-panel]').forEach((p) => { p.hidden = p.dataset.settingsPanel !== tab; });
  if (tab === 'stato') { renderStatus(); refreshStatus(); loadVisitSettings(); loadSourceSettings(); }
  if (tab === 'alimenti') { state.showAllFoods = false; $('#food-search').value = ''; loadFoods(); }
}

async function loadVisitSettings() {
  try {
    const s = await api('api/report/impostazioni');
    state.visitLastDate = s.data_ultima_visita || null;
    $('#visit-last-date').value = s.data_ultima_visita || '';
    $('#visit-questions').value = s.domande_visita || '';
  } catch (err) {
    toast(err.message);
  }
}

async function saveVisitSettings() {
  try {
    await api('api/report/impostazioni', { method: 'PUT', body: {
      data_ultima_visita: $('#visit-last-date').value || '',
      domande_visita: $('#visit-questions').value || '',
    } });
    state.visitLastDate = $('#visit-last-date').value || null;
    toast('Salvato');
  } catch (err) {
    toast(err.message);
  }
}

function setSourceType(sorgente) {
  $$('#source-type button').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.source === sorgente)));
  $('#source-nightscout-fields').hidden = sorgente !== 'nightscout';
  $('#source-xdrip-fields').hidden = sorgente !== 'xdrip';
}

async function loadSourceSettings() {
  try {
    const s = await api('api/sorgente_dati');
    setSourceType(s.sorgente || 'glooko');
    $('#source-nightscout-url').value = s.nightscout_url || '';
    $('#source-nightscout-token').value = s.nightscout_token || '';
    $('#source-xdrip-url').value = s.xdrip_url || '';
    $('#source-error').textContent = '';
  } catch (err) {
    toast(err.message);
  }
}

function sourceBody() {
  const sorgente = $('#source-type [aria-checked="true"]')?.dataset.source || 'glooko';
  return {
    sorgente,
    nightscout_url: $('#source-nightscout-url').value.trim(),
    nightscout_token: $('#source-nightscout-token').value.trim(),
    xdrip_url: $('#source-xdrip-url').value.trim(),
  };
}

async function testSourceConnection() {
  const body = sourceBody();
  const err = $('#source-error');
  err.textContent = '';
  if (body.sorgente === 'glooko') {
    err.textContent = "Glooko si testa con \"Aggiorna ora da Glooko\" qui sopra.";
    return;
  }
  try {
    const r = await api('api/sorgente_dati/test', { method: 'POST', body });
    err.textContent = r.ok ? 'Connessione riuscita.' : (r.errore || 'Connessione non riuscita.');
  } catch (e) {
    err.textContent = e.message;
  }
}

async function saveSourceSettings() {
  try {
    await api('api/sorgente_dati', { method: 'PUT', body: sourceBody() });
    toast('Sorgente salvata');
    $('#source-error').textContent = '';
  } catch (err) {
    $('#source-error').textContent = err.message;
  }
}

// ---------------------------------------------------------------- wizard di primo avvio

async function openWizard() {
  state.wizardStep = 1;
  try {
    const soglie = await api('api/soglie');
    $('#wizard-soglia-bassa').value = soglie.bassa;
    $('#wizard-soglia-alta').value = soglie.alta;
  } catch { /* si usano i valori predefiniti del campo */ }
  renderWizardStep();
  showSheet('#wizard-sheet');
}

function renderWizardStep() {
  $$('[data-wizard-step]').forEach((el) => { el.hidden = Number(el.dataset.wizardStep) !== state.wizardStep; });
  $('#wizard-steps').textContent = `Passo ${state.wizardStep} di ${WIZARD_STEPS}`;
  $('#wizard-back').hidden = state.wizardStep === 1;
  $('#wizard-next').textContent = state.wizardStep === WIZARD_STEPS ? 'Inizia' : 'Avanti';
}

function setWizardSourceType(sorgente) {
  $$('#wizard-source-type button').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.wizardSource === sorgente)));
  $('#wizard-nightscout-fields').hidden = sorgente !== 'nightscout';
  $('#wizard-xdrip-fields').hidden = sorgente !== 'xdrip';
}

function wizardSourceBody() {
  const sorgente = $('#wizard-source-type [aria-checked="true"]')?.dataset.wizardSource || 'glooko';
  return {
    sorgente,
    nightscout_url: $('#wizard-nightscout-url').value.trim(),
    nightscout_token: $('#wizard-nightscout-token').value.trim(),
    xdrip_url: $('#wizard-xdrip-url').value.trim(),
  };
}

async function testWizardSourceConnection() {
  const body = wizardSourceBody();
  const err = $('#wizard-source-error');
  err.textContent = '';
  if (body.sorgente === 'glooko') {
    err.textContent = 'Le credenziali Glooko si inseriscono nella Configurazione dell\'add-on.';
    return;
  }
  try {
    const r = await api('api/sorgente_dati/test', { method: 'POST', body });
    err.textContent = r.ok ? 'Connessione riuscita.' : (r.errore || 'Connessione non riuscita.');
  } catch (e) {
    err.textContent = e.message;
  }
}

async function wizardNext() {
  if (state.wizardStep === 2) {
    try { await api('api/sorgente_dati', { method: 'PUT', body: wizardSourceBody() }); }
    catch (err) { $('#wizard-source-error').textContent = err.message; return; }
  }
  if (state.wizardStep === 3) {
    try {
      await api('api/soglie', { method: 'PUT', body: {
        bassa: $('#wizard-soglia-bassa').value, alta: $('#wizard-soglia-alta').value,
      } });
    } catch { /* non blocca il wizard: si puo' correggere dopo in Impostazioni */ }
  }
  if (state.wizardStep === WIZARD_STEPS) {
    await completeWizard();
    return;
  }
  state.wizardStep += 1;
  renderWizardStep();
}

function wizardBack() {
  if (state.wizardStep > 1) { state.wizardStep -= 1; renderWizardStep(); }
}

async function completeWizard() {
  try { await api('api/wizard/completa', { method: 'POST' }); } catch { /* si puo' ignorare */ }
  hideSheet('#wizard-sheet');
  await refreshStatus();
  loadSourceSettings();
}

async function generateReport() {
  const btn = $('#report-genera');
  const body = {};
  if (state.reportDays === 'visita') {
    if (!state.visitLastDate) {
      toast("Imposta prima la data dell'ultima visita, qui sotto in Impostazioni");
      return;
    }
    body.da = state.visitLastDate;
    body.a = state.today;
  } else {
    body.giorni = Number(state.reportDays);
  }
  btn.disabled = true;
  $('.spinner', btn).hidden = false;
  $('.btn-label', btn).textContent = 'Genero il PDF…';
  $('#report-result').hidden = true;
  try {
    const r = await api('api/report', { method: 'POST', body });
    const url = `api/report/${r.id}/pdf`;
    const nomeFile = `diario-camaps-${r.day_from}_${r.day_to}.pdf`;
    const box = $('#report-result');
    box.hidden = false;
    box.innerHTML = `
      <p>Report ${esc(r.day_from)} – ${esc(r.day_to)} pronto.</p>
      <div class="foot-actions">
        <button type="button" class="secondary" id="report-download">Scarica</button>
        <button type="button" class="primary" id="report-share">Condividi</button>
      </div>`;
    $('#report-download').addEventListener('click', () => downloadReport(url, nomeFile));
    $('#report-share').addEventListener('click', () => shareReport(url, nomeFile));
    if ($('#visit-questions').value.trim()) { $('#visit-questions').value = ''; }
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    $('.spinner', btn).hidden = true;
    $('.btn-label', btn).textContent = 'Genera report PDF';
  }
}

// Mai navigare a un nuovo tab/finestra su un URL http(s) di Ingress: l'app di Home
// Assistant lo apre nel Safari di sistema, che non ha la sessione autenticata e
// risponde 401. Si scarica sempre il PDF come blob nella pagina stessa (gia'
// autenticata) e poi si salva/condivide quel blob, senza nessuna nuova richiesta.
async function fetchReportBlob(url, nomeFile) {
  const res = await fetch(url);
  if (!res.ok) throw new Error('Impossibile scaricare il PDF.');
  const blob = await res.blob();
  return new File([blob], nomeFile, { type: 'application/pdf' });
}

async function downloadReport(url, nomeFile) {
  try {
    const file = await fetchReportBlob(url, nomeFile);
    const blobUrl = URL.createObjectURL(file);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = nomeFile;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);
  } catch (err) {
    toast(err.message || 'Scaricamento non riuscito');
  }
}

async function shareReport(url, nomeFile) {
  try {
    const file = await fetchReportBlob(url, nomeFile);
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: nomeFile });
    } else {
      await downloadReport(url, nomeFile);
    }
  } catch (err) {
    if (err.name !== 'AbortError') toast(err.message || 'Condivisione non riuscita');
  }
}

function bind() {
  $$('.tabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  $('#day-prev').addEventListener('click', () => { state.day = addDays(state.day, -1); loadDay(); });
  $('#day-next').addEventListener('click', () => {
    if (state.day < state.today) { state.day = addDays(state.day, 1); loadDay(); }
  });
  $('#day-picker').addEventListener('change', (ev) => {
    if (ev.target.value) { state.day = ev.target.value; loadDay(); }
  });
  $('#pasti-day-prev').addEventListener('click', () => { state.day = addDays(state.day, -1); loadDay(); });
  $('#pasti-day-next').addEventListener('click', () => {
    if (state.day < state.today) { state.day = addDays(state.day, 1); loadDay(); }
  });
  $('#pasti-day-picker').addEventListener('change', (ev) => {
    if (ev.target.value) { state.day = ev.target.value; loadDay(); }
  });
  bindSwipe($('#view-pasti'),
    () => { if (state.day < state.today) { state.day = addDays(state.day, 1); loadDay(); } },
    () => { state.day = addDays(state.day, -1); loadDay(); });
  $('#add-meal').addEventListener('click', () => openMealEditor());
  $('#note-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const testo = $('#note-text').value.trim();
    if (!testo) return;
    addDayNote(testo, $('#note-kind').value);
    $('#note-text').value = '';
  });
  $('#day-notes').addEventListener('click', (ev) => {
    const del = ev.target.closest('[data-del-note]');
    if (del) removeDayNote(Number(del.dataset.delNote));
  });
  bindSwipe($('#view-giorno'),
    () => { if (state.day < state.today) { state.day = addDays(state.day, 1); loadDay(); } },
    () => { state.day = addDays(state.day, -1); loadDay(); });

  // Calendario
  $('#cal-prev').addEventListener('click', () => shiftCalendar(-1));
  $('#cal-next').addEventListener('click', () => shiftCalendar(1));
  bindSwipe($('#view-calendario'), () => shiftCalendar(1), () => shiftCalendar(-1));
  $('#cal-grid').addEventListener('click', (ev) => {
    const cell = ev.target.closest('[data-cal-day]');
    if (!cell || cell.disabled) return;
    openDayDetail(cell.dataset.calDay);
  });
  $('#cal-search').addEventListener('input', debounce((ev) => searchDiary(ev.target.value), 250));
  $('#cal-search-results').addEventListener('click', (ev) => {
    const li = ev.target.closest('[data-cal-search-day]');
    if (!li) return;
    openDayDetail(li.dataset.calSearchDay);
  });

  $('#log').addEventListener('click', (ev) => {
    const addType = ev.target.closest('[data-add-meal-type]');
    if (addType) { openMealEditor(null, { tipo: addType.dataset.addMealType }); return; }
    const row = ev.target.closest('[data-meal]');
    if (row) {
      const meal = state.payload.pasti.find((m) => String(m.id) === row.dataset.meal);
      if (meal) openMealEditor(meal);
    }
  });
  $('#log').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && ev.target.matches('[data-meal]')) ev.target.click();
  });
  $('#chart').addEventListener('click', (ev) => {
    const reg = ev.target.closest('[data-register]');
    if (reg) openMealEditor(null, { ora: `${state.day}T${reg.dataset.register}`, tipo: reg.dataset.tipo });
  });

  // Fogli: chiusura
  $$('.sheet').forEach((s) => {
    s.addEventListener('click', (ev) => {
      if (ev.target === s || ev.target.closest('[data-close]')) hideSheet(`#${s.id}`);
    });
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    const open = $$('.sheet').filter((s) => !s.hidden).pop();
    if (open) hideSheet(`#${open.id}`);
  });

  // Editor pasto
  $('#meal-type').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-type]');
    if (!b) return;
    editor.tipo = b.dataset.type;
    renderChips();
  });
  $('#meal-items').addEventListener('input', (ev) => {
    const li = ev.target.closest('[data-i]');
    if (!li) return;
    const v = editor.voci[Number(li.dataset.i)];
    v[ev.target.dataset.field] = ev.target.value;
    li.querySelector('.icarb').textContent = `${n1(itemCarbs(v))} g`;
    updateTotal();
  });
  $('#meal-items').addEventListener('click', (ev) => {
    const step = ev.target.closest('[data-step]');
    if (step) {
      const input = step.closest('.stepper').querySelector('input');
      const next = Math.max(0, (parseNum(input.value) || 0) + Number(step.dataset.step));
      input.value = String(Math.round(next * 10) / 10);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    const occ = ev.target.closest('[data-occ]');
    if (occ) { openOccurrences(Number(occ.dataset.occ), occ.dataset.occName); return; }
    const rm = ev.target.closest('[data-remove]');
    if (!rm) return;
    editor.voci.splice(Number(rm.closest('[data-i]').dataset.i), 1);
    renderItems();
  });
  $('#meal-search').addEventListener('input', debounce((ev) => searchMealFoods(ev.target.value), 200));
  $('#meal-search').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      if (mealResults[0] && ev.target.value.trim()) addFoodToMeal(mealResults[0]);
    }
  });
  $('#meal-frequent').addEventListener('click', (ev) => {
    const li = ev.target.closest('[data-idx]');
    if (li) addFoodToMeal(mealFrequent[Number(li.dataset.idx)]);
  });
  $$('.adder-tabs [role="tab"]').forEach((b) => b.addEventListener('click', () => setAdderTab(b.dataset.adderTab)));
  $('#meal-results').addEventListener('click', (ev) => {
    const li = ev.target.closest('[data-idx]');
    if (li) addFoodToMeal(mealResults[Number(li.dataset.idx)]);
  });
  $('#meal-ciqual').addEventListener('click', async (ev) => {
    const li = ev.target.closest('[data-cq]');
    if (!li) return;
    try {
      addFoodToMeal(await saveCiqual(mealCiqual[Number(li.dataset.cq)]));
      toast('Aggiunto ai tuoi alimenti: puoi rinominarlo in Alimenti');
    } catch (err) {
      $('#meal-error').textContent = err.message;
    }
  });
  $('#meal-photo-add').addEventListener('click', () => $('#meal-photo-input').click());
  $('#meal-photo-input').addEventListener('change', (ev) => {
    const file = ev.target.files[0];
    ev.target.value = '';
    if (file) addMealPhoto(file);
  });
  $('#meal-photos').addEventListener('click', (ev) => {
    const rm = ev.target.closest('[data-remove-photo]');
    if (rm) { removeMealPhoto(Number(rm.dataset.removePhoto)); return; }
    const an = ev.target.closest('[data-analyze-photo]');
    if (an) { analyzeMealPhoto(Number(an.dataset.analyzePhoto)); return; }
    const confirm = ev.target.closest('[data-confirm-analysis]');
    if (confirm) confirmAnalysis(Number(confirm.dataset.confirmAnalysis));
  });
  $('#meal-photos').addEventListener('change', (ev) => {
    const chk = ev.target.closest('[data-ai-check]');
    if (!chk) return;
    const [photoId, idx] = chk.dataset.aiCheck.split(':').map(Number);
    toggleAiItem(photoId, idx);
  });
  $('#meal-dish').addEventListener('click', openDishForm);
  $('#meal-off').addEventListener('click', () => offSearchInto($('#meal-search').value, $('#meal-off-results'), mealOffResults));
  $('#meal-off-results').addEventListener('click', async (ev) => {
    const li = ev.target.closest('[data-idx]');
    if (!li) return;
    try {
      addFoodToMeal(await saveOffProduct(mealOffResults[Number(li.dataset.idx)]));
      toast('Aggiunto anche ai tuoi alimenti');
    } catch (err) {
      $('#meal-error').textContent = err.message;
    }
  });
  $('#meal-barcode').addEventListener('click', () => openScanner((food) => addFoodToMeal(food)));
  $('#meal-free').addEventListener('click', openFreeForm);
  $('#meal-save').addEventListener('click', saveMeal);
  $('#meal-delete').addEventListener('click', deleteMeal);

  // Alimenti
  $('#food-search').addEventListener('input', debounce(loadFoods, 200));
  $('#food-browse-all').addEventListener('click', () => { state.showAllFoods = true; loadFoods(); });
  $('#food-list').addEventListener('click', (ev) => {
    const li = ev.target.closest('[data-idx]');
    if (li) openFoodSheet(foods[Number(li.dataset.idx)]);
  });
  $('#food-ciqual-list').addEventListener('click', (ev) => {
    const li = ev.target.closest('[data-cq]');
    if (!li) return;
    const r = foodCiqual[Number(li.dataset.cq)];
    openFoodSheet(null, { nome: r.nome, carbo_100g: r.carbo_100g, fonte: 'ciqual', codice: r.codice });
  });
  $('#food-new').addEventListener('click', () => openFoodSheet(null, { nome: $('#food-search').value }));
  $('#food-off').addEventListener('click', () => offSearchInto($('#food-search').value, $('#food-off-list'), foodOffResults));
  $('#food-off-list').addEventListener('click', async (ev) => {
    const li = ev.target.closest('[data-idx]');
    if (!li) return;
    try {
      await saveOffProduct(foodOffResults[Number(li.dataset.idx)]);
      toast('Aggiunto ai tuoi alimenti');
      loadFoods();
    } catch (err) {
      toast(err.message);
    }
  });
  $('#food-barcode').addEventListener('click', () => openScanner((food, isNew) => {
    toast(isNew ? 'Aggiunto ai tuoi alimenti' : 'È già tra i tuoi alimenti');
    loadFoods();
    openFoodSheet(food);
  }));
  $('#food-form').addEventListener('submit', (ev) => { ev.preventDefault(); saveFood(); });
  $('#food-save').addEventListener('click', saveFood);
  $('#food-delete').addEventListener('click', deleteFood);
  $('#food-occ').addEventListener('click', () => {
    if (editingFood) openOccurrences(editingFood.id, editingFood.nome);
  });

  // Scanner
  $('#scan-go').addEventListener('click', lookupBarcode);
  $('#scan-code').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') lookupBarcode(); });

  // Andamento
  $$('.seg button').forEach((b) => b.addEventListener('click', () => {
    state.trendDays = Number(b.dataset.days);
    $$('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    loadTrend();
    loadAgp();
  }));

  // Report per il diabetologo
  $$('.seg-report button').forEach((b) => b.addEventListener('click', () => {
    state.reportDays = b.dataset.reportDays;
    $$('.seg-report button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  }));
  $('#report-genera').addEventListener('click', generateReport);
  $('#visit-save').addEventListener('click', saveVisitSettings);
  $$('#source-type button').forEach((b) => b.addEventListener('click', () => setSourceType(b.dataset.source)));
  $('#source-test').addEventListener('click', testSourceConnection);
  $('#source-save').addEventListener('click', saveSourceSettings);

  // Wizard di primo avvio
  $$('#wizard-source-type button').forEach((b) => b.addEventListener('click', () => setWizardSourceType(b.dataset.wizardSource)));
  $('#wizard-source-test').addEventListener('click', testWizardSourceConnection);
  $('#wizard-ciqual-import').addEventListener('click', async () => {
    try {
      await api('api/ciqual/importa', { method: 'POST' });
      $('#wizard-ciqual-hint').textContent = 'Download avviato: continua in background, lo trovi anche in Impostazioni.';
    } catch (err) {
      $('#wizard-ciqual-hint').textContent = err.message;
    }
  });
  $('#wizard-next').addEventListener('click', wizardNext);
  $('#wizard-back').addEventListener('click', wizardBack);
  $('#wizard-skip').addEventListener('click', completeWizard);
  const goToTrendDay = (ev) => {
    const el = ev.target.closest('[data-day]');
    if (!el) return;
    state.day = el.dataset.day;
    showTab('giorno');
  };
  const goToTrendDayKey = (ev) => {
    if (ev.key === 'Enter' && ev.target.matches('[data-day]')) goToTrendDay(ev);
  };
  for (const sel of ['#trend-table', '#trend-cards', '#trend-chart']) {
    $(sel).addEventListener('click', goToTrendDay);
    $(sel).addEventListener('keydown', goToTrendDayKey);
  }

  // Stato
  $$('.settings-tabs [role="tab"]').forEach((b) => b.addEventListener('click', () => setSettingsTab(b.dataset.settingsTab)));
  $('#sync-now').addEventListener('click', syncNow);
  $('#ciqual-import').addEventListener('click', async () => {
    try {
      await api('api/ciqual/importa', { method: 'POST' });
      toast('Download della tabella Ciqual avviato');
      setTimeout(refreshStatus, 2000);
    } catch (err) {
      toast(err.message);
    }
  });
  $('#off-import').addEventListener('click', async () => {
    try {
      const r = await api('api/alimenti/importa_italia', { method: 'POST' });
      toast(r.gia_in_corso ? 'Import già in corso' : 'Import avviato: richiede qualche minuto, puoi continuare a usare l\'app');
      setTimeout(refreshStatus, 3000);
    } catch (err) {
      toast(err.message);
    }
  });

  window.addEventListener('resize', debounce(() => {
    if (state.payload && !$('#view-giorno').hidden) renderChart(state.payload);
    if (state.trendRows && !$('#view-andamento').hidden) renderTrendChart(state.trendRows, trendRangeTitle);
    if (state.agpBins && !$('#view-andamento').hidden) renderAgpChart(state.agpBins);
  }, 150));
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshStatus();
  });
}

async function init() {
  bind();
  state.today = isoDay(new Date());
  await refreshStatus();
  state.day = state.today;
  await loadDay();
  setInterval(refreshStatus, 5 * 60 * 1000);
  if (state.stato && !state.stato.wizard_completato) openWizard();
}

init();
