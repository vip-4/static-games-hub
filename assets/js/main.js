"use strict";

/* OpenArc UI — progressive enhancement only.
   The game list is pre-rendered into static HTML by scripts/build.py so that
   crawlers (and visitors without JavaScript) see the full catalog. This script
   merely adds filtering / search on top of those existing <li> elements.
   If no static items are present (i.e. viewing the un-built source over HTTP)
   it falls back to loading data/games.json and rendering the list. */

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function playHref(g) {
  if (g.playable && g.local) return `/play/${g.id}.html`;
  return g.url || "#";
}

function buildItem(g) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.source = g.source || "other";
  li.dataset.tags = (g.tags || []).join(" ");
  const ext = g.playable && g.local ? "" : ' target="_blank" rel="noopener noreferrer"';
  li.innerHTML = `
    <div class="thumb" aria-hidden="true">${esc(g.icon || "\u{1F3AE}")}</div>
    <h3>${esc(g.title)}</h3>
    <p>${esc(g.description || "")}</p>
    <div class="meta">
      <span class="tag src">${esc(g.source)}</span>
      ${g.license ? `<span class="tag lic">${esc(g.license)}</span>` : ""}
      ${(g.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
    </div>
    <a class="btn play" href="${playHref(g)}"${ext}>${g.playable ? "Play" : "View source"}</a>`;
  return li;
}

async function init() {
  const grid = document.getElementById("games-grid");
  if (!grid) return;

  let items = Array.from(grid.querySelectorAll("li"));

  if (!items.length) {
    // Un-built source: fall back to the JSON catalog.
    try {
      const res = await fetch("/data/games.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(String(res.status));
      const games = await res.json();
      const frag = document.createDocumentFragment();
      games.forEach((g) => frag.appendChild(buildItem(g)));
      grid.appendChild(frag);
      items = Array.from(grid.querySelectorAll("li"));
    } catch (e) {
      const stats = document.getElementById("stats");
      if (stats) stats.textContent = "Catalog unavailable — serve over HTTP or run scripts/build.py.";
      return;
    }
  }

  items.forEach((li) => { if (!li.dataset.source) li.dataset.source = "other"; });

  const stats = document.getElementById("stats");
  const filters = document.getElementById("filters");
  const search = document.getElementById("search");
  let active = "all";
  let q = "";

  function apply() {
    let shown = 0;
    items.forEach((li) => {
      const src = li.dataset.source || "";
      const hay = `${li.textContent} ${li.dataset.tags || ""}`.toLowerCase();
      const ok = (active === "all" || src === active) && (!q || hay.includes(q.toLowerCase()));
      li.hidden = !ok;
      if (ok) shown++;
    });
    if (!stats) return;
    if (filters) {
      stats.textContent = `${shown} game${shown === 1 ? "" : "s"} shown`;
    } else {
      const srcs = new Set(items.map((li) => li.dataset.source)).size;
      stats.textContent = `${items.length} open-source games · ${srcs} sources · static & tracker-free`;
    }
  }

  if (filters) {
    const srcs = ["all", ...Array.from(new Set(items.map((li) => li.dataset.source)))];
    srcs.forEach((s, i) => {
      const b = document.createElement("button");
      b.textContent = s === "all" ? "All" : s;
      b.setAttribute("aria-pressed", i === 0 ? "true" : "false");
      b.addEventListener("click", () => {
        active = s;
        filters.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        apply();
      });
      filters.appendChild(b);
    });
  }

  if (search) search.addEventListener("input", (e) => { q = e.target.value; apply(); });

  apply();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
