"use strict";

const state = {
  games: [],
  current: null,      // game metadata
  symMap: {},         // symbol id -> {icon, kind, name}
  reels: 5,
  rows: 3,
};

const $ = (sel) => document.querySelector(sel);

// API origin for the slot engine. Defaults to same-origin "/api".
// Override with ?api=https://your-backend to point at a separately hosted engine.
const API_BASE = new URLSearchParams(location.search).get("api") || "";

async function api(path, opts) {
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) throw new Error((await res.json()).error || res.statusText);
  return res.json();
}

async function loadGames() {
  state.games = await api("/api/games");
  const sel = $("#game");
  sel.innerHTML = "";
  for (const g of state.games) {
    const o = document.createElement("option");
    o.value = g.game_id;
    o.textContent = `${g.display_name} (${g.reels}x${g.rows})`;
    sel.appendChild(o);
  }
  sel.onchange = () => selectGame(sel.value);
  await selectGame(state.games[0].game_id);
}

async function selectGame(gameId) {
  const meta = await api(`/api/paytable?game=${encodeURIComponent(gameId)}`);
  state.current = meta;
  state.reels = meta.reels;
  state.rows = meta.rows;
  state.symMap = {};
  for (const s of meta.symbols) state.symMap[s.id] = s;

  // bet levels
  const bet = $("#bet");
  bet.innerHTML = "";
  for (const b of meta.bet_levels) {
    const o = document.createElement("option");
    o.value = b; o.textContent = b;
    bet.appendChild(o);
  }
  bet.value = meta.default_bet;

  const nLines = (meta.paylines || []).length || 1;
  $("#lines").max = nLines;
  $("#lines").value = nLines;

  renderSymbols(meta);
  renderMechanics(meta);
  renderGrid(emptyGrid());
  $("#status").textContent = `${meta.display_name} ready`;
}

function emptyGrid() {
  return Array.from({ length: state.rows }, () =>
    Array.from({ length: state.reels }, () => null));
}

// `coinLabels` maps "row,col" -> short text shown under the symbol (coin credits
// for cash-collect titles, tier name for hold & win jackpot coins).
function renderGrid(grid, locked, coinLabels) {
  const el = $("#reels");
  el.style.gridTemplateColumns = `repeat(${state.reels}, 84px)`;
  el.innerHTML = "";
  const lockedSet = new Set((locked || []).map(([r, c]) => `${r},${c}`));
  const labels = coinLabels || {};
  for (let r = 0; r < state.rows; r++) {
    for (let c = 0; c < state.reels; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";
      const sid = grid[r]?.[c];
      if (sid) {
        const sym = state.symMap[sid] || { icon: "?", kind: "normal" };
        cell.textContent = sym.icon;
        if (sym.kind !== "normal") cell.classList.add("special");
      }
      if (lockedSet.has(`${r},${c}`)) cell.classList.add("locked");
      const label = labels[`${r},${c}`];
      if (label !== undefined) {
        const tag = document.createElement("span");
        tag.className = "coinval";
        tag.textContent = label;
        cell.appendChild(tag);
      }
      el.appendChild(cell);
    }
  }
}

function creditLabels(coins) {
  const out = {};
  for (const [key, value] of Object.entries(coins || {})) out[key] = `x${value}`;
  return out;
}

function renderMechanics(meta) {
  const box = $("#mechanics");
  box.innerHTML = "";
  for (const m of meta.mechanics || []) {
    const li = document.createElement("li");
    li.textContent = m.replace(/-/g, " ");
    box.appendChild(li);
  }
}

function renderSymbols(meta) {
  const box = $("#symbols");
  box.innerHTML = "";
  for (const s of meta.symbols) {
    const d = document.createElement("div");
    d.className = "sym";
    const role = s.kind === "wild" ? "WILD"
      : s.kind === "scatter" ? "SCATTER"
      : s.kind === "bonus" ? "BONUS" : "line";
    d.innerHTML = `<span class="ico">${s.icon}</span>
      <span class="meta">${s.name}<br>${role}</span>`;
    box.appendChild(d);
  }
  const bonusTxt = meta.bonus_trigger >= 99
    ? "This title has no pick bonus."
    : `Bonus triggers at ${meta.bonus_trigger}+.`;
  $("#rules").textContent =
    `Paylines: ${(meta.paylines || []).length}. Scatter triggers free spins at ` +
    `${meta.scatter_trigger}+. ${bonusTxt}`;
}

function highlightWinList(wins) {
  const cells = $("#reels").children;
  const idx = (r, c) => r * state.reels + c;
  (wins || []).forEach((w) => {
    const line = state.current.paylines[w.line_index] || [];
    for (let c = 0; c < w.count; c++) {
      const r = line[c];
      if (r !== undefined) cells[idx(r, c)]?.classList.add("win");
    }
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Replay avalanche steps (cascading games) before settling on the final grid.
async function playCascades(result) {
  const steps = result.cascades || [];
  if (steps.length <= 1) {
    renderGrid(result.grid);
    highlightWinList(result.line_wins);
    return;
  }
  for (const c of steps) {
    renderGrid(c.grid);
    highlightWinList(c.line_wins);
    const feats = $("#features");
    feats.innerHTML = "";
    feats.appendChild(badge("cascade", `CASCADE x${c.multiplier}`));
    $("#status").textContent = `Cascade ${c.step + 1} · x${c.multiplier}`;
    await sleep(700);
  }
  renderGrid(result.grid);
}

// Replay sticky-wild respins: wild cells stay locked (glowing) while the rest
// of the grid is re-drawn and paid again.
async function playRespins(result) {
  const respins = result.respins || [];
  if (!respins.length) return;
  for (const r of respins) {
    renderGrid(r.grid, r.locked);
    highlightWinList(r.line_wins);
    const feats = $("#features");
    feats.innerHTML = "";
    feats.appendChild(badge("respin", `RESPIN ${r.step + 1}`));
    $("#status").textContent = `Respin ${r.step + 1} · win ${r.win}`;
    await sleep(700);
  }
  renderGrid(result.grid, (result.features || {}).sticky_cells || []);
}

// Replay Hold & Win: the feature grid only holds coins and blanks, every coin
// shows its credit value (or jackpot tier) and stays locked between respins.
async function playHoldWin(result) {
  const hw = result.hold_win || {};
  const steps = hw.steps || [];
  if (!steps.length) return;
  for (const st of steps) {
    const labels = {};
    for (const [r, c, value, tier] of st.coins) {
      labels[`${r},${c}`] = tier ? tier.toUpperCase() : `x${value}`;
    }
    renderGrid(st.grid, st.locked, labels);
    const feats = $("#features");
    feats.innerHTML = "";
    feats.appendChild(badge("hold", `HOLD & WIN ${st.coins.length}/15`));
    $("#status").textContent =
      `Hold & Win · ${st.coins.length} coins · respins left ${st.respins_left}`;
    await sleep(700);
  }
  $("#status").textContent = `Hold & Win complete · won ${hw.win}`;
}

async function doSpin() {
  const btn = $("#spin");
  btn.disabled = true;
  $("#status").textContent = "spinning...";
  try {
    const body = {
      game_id: state.current.game_id,
      bet: Number($("#bet").value),
      lines: Number($("#lines").value),
    };
    const result = await api("/api/spin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await playCascades(result);
    await playRespins(result);
    await playHoldWin(result);
    if (Object.keys(result.coins || {}).length) {
      renderGrid(result.grid, (result.features || {}).sticky_cells || [],
                 creditLabels(result.coins));
    }
    const feats = $("#features");
    feats.innerHTML = "";
    if ((result.cascades || []).length > 1) {
      feats.appendChild(badge("cascade", `${result.cascades.length} CASCADES`));
    }
    if ((result.respins || []).length) {
      feats.appendChild(badge("respin", `${result.respins.length} RESPINS`));
    }
    if (result.free_spins_triggered) {
      feats.appendChild(badge("free", `FREE SPINS x${result.free_spins_awarded}`));
    }
    if (result.bonus_triggered) {
      feats.appendChild(badge("bonus", "BONUS GAME"));
    }
    if (result.jackpot && result.jackpot.tier) {
      const jp = result.jackpot;
      feats.appendChild(badge("jackpot", `JACKPOT ${jp.tier.toUpperCase()} x${jp.multiplier}`));
    }
    for (const jp of (result.hold_win || {}).jackpots || []) {
      feats.appendChild(badge("jackpot", `COIN ${jp.tier.toUpperCase()} x${jp.multiplier}`));
    }
    if (result.hold_win) {
      feats.appendChild(badge("hold", `HOLD & WIN +${result.hold_win.win}`));
    }
    if ((result.features?.collect || {}).win) {
      feats.appendChild(badge("collect", `COLLECTED +${result.features.collect.win}`));
    }
    let msg = `Win: ${result.total_win}`;
    if (result.scatter_count) msg += `  | Scatters: ${result.scatter_count}`;
    if (result.jackpot && result.jackpot.tier) msg += `  | Jackpot: ${result.jackpot.win}`;
    $("#status").textContent = msg;
  } catch (e) {
    $("#status").textContent = "Error: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

function badge(cls, text) {
  const b = document.createElement("span");
  b.className = `badge ${cls}`;
  b.textContent = text;
  return b;
}

$("#spin").addEventListener("click", doSpin);
loadGames().catch((e) => ($("#status").textContent = "Error: " + e.message));
