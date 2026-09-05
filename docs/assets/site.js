/* Benham Manual — nav + search. The ONE place the menu is edited.
   No fetch() anywhere: everything loads via <script src>, so file:// works. */

const PAGES = {
  inner: [
    { f: "chokepoint.html",       t: "The chokepoint",      k: "bot.py outbox inbox file bus single process gateway queue",            d: "One Discord process; everything else queues files." },
    { f: "tiers.html",            t: "The four tiers",      k: "capability tier owner gate permissions levels",                        d: "Every capability sits in a tier; the owner gate sits in front." },
    { f: "confirm-flow.html",     t: "Confirmation flow",   k: "destructive token dry-run ttl three gates self-confirm delete purge",  d: "Why the model can never say yes to itself." },
    { f: "taint.html",            t: "The taint model",     k: "tainted turn stranger text approval injection prompt",                 d: "Reading stranger text gates outward actions." },
    { f: "pc-access.html",        t: "PC access",           k: "removed phase b codesession pc_task pc.. machine wall",          d: "Read freely, ask before changing." },
    { f: "conversations.html",   t: "Conversations",       k: "ask nudge bank close tell reverse channel binding reply outreach loop collaborator queue slot priority blocking whenever numbered batch uncollected", d: "An ask that outlives the session that made it." },
    { f: "self-record.html",     t: "Answering from record", k: "what_i_did log evidence memory gaslighting covered proof",              d: "What it DID is a matter of record, not memory." },
    { f: "guests.html",           t: "The guest system",    k: "guest invariants capabilities workspace grants friends whitelist",     d: "Three invariants that never bend." },
    { f: "web-search.html",       t: "Web search",          k: "search tainted queries owner guest logs internet",                     d: "A web page is text a stranger wrote." },
    { f: "exaroton.html",         t: "Exaroton",            k: "minecraft server slash command watchdog start stop",                   d: "Minecraft servers via slash commands, per-guild gated." },
    { f: "attachments.html",      t: "Attachments",         k: "downloads quarantine runnable files exe",                              d: "Quarantined downloads, never Windows Downloads." },
    { f: "identity-persona.html", t: "Identity & persona",  k: "persona benham not tyler impersonation guest_persona",                 d: "Benham is Benham — never Tyler." },
    { f: "webhook-identity.html", t: "Webhook identity",    k: "webhook.py webhooks.json faces outside chokepoint avatar name",        d: "Posting under webhook faces, outside the chokepoint." }
  ],
  outer: [
    { f: "daily-driving.html",    t: "Daily driving",             k: "dm confirm refusal approval prefix mention plain language",           d: "DM it in plain language; what the prompts mean." },
    { f: "agents.html",           t: "The agents",                k: "owner agent guest agent pc codesession which when cost",              d: "Owner agent, guest agent, PC session — which fires when." },
    { f: "cli.html",              t: "CLI shorthands",            k: "do.py send draft dm delete fetch catchup read_history find_user speak listen status webhook webhooks.json", d: "The full cheat-sheet, grouped." },
    { f: "guest-admin.html",      t: "Guest admin",               k: "add remove guest kill switch caps forget status workspace flip restart", d: "Add, remove, cut off, curate." },
    { f: "tray-restart.html",     t: "Tray, restart & supervision", k: "tray icon scheduled task supervise boot restart stop start",        d: "The tray icon, the Scheduled Task, the boot logs." },
    { f: "health-logs-cost.html", t: "Health, logs & cost",       k: "status usage inbox restart logs cost spend up cazzy-mac",                      d: "Is it up, what's it costing, what's it doing." },
    { f: "config-reference.html", t: "Config reference",          k: "control.json knobs restart defaults owner_ids destructive_guilds agent_guilds post_guilds", d: "Every knob, and the restart-after-edit rule." }
  ]
};

const GROUPS = [
  ["inner", "Inner — why it's built this way"],
  ["outer", "Outer — how to drive it"]
];

const ROOT = document.body.dataset.root || "";
const PAGE = document.body.dataset.page || "";

function badge(p) {
  if (!p.badge) return "";
  const label = { shipped: "SHIPPED", planned: "PLANNED", off: "OFF BY DEFAULT" }[p.badge];
  return ` <span class="badge ${p.badge}">${label}</span>`;
}

function buildSidebar() {
  const sb = document.getElementById("sidebar");
  if (!sb) return;
  let h = `<a class="brand" href="${ROOT}index.html">Benham Manual</a>
    <input id="nav-search" type="search" placeholder="search pages…" autocomplete="off">
    <div id="no-hits" hidden>no matches</div>`;
  for (const [sec, label] of GROUPS) {
    h += `<div class="nav-group" data-sec="${sec}">
      <a class="group-head${PAGE === sec + "/index.html" ? " active" : ""}" href="${ROOT}${sec}/index.html">${label}</a>`;
    for (const p of PAGES[sec]) {
      const active = PAGE === sec + "/" + p.f ? " active" : "";
      h += `<a class="nav-link${active}" data-kw="${(p.t + " " + p.k).toLowerCase()}" href="${ROOT}${sec}/${p.f}">${p.t}${badge(p)}</a>`;
    }
    h += `</div>`;
  }
  sb.innerHTML = h;
  sb.querySelector("#nav-search").addEventListener("input", e => filterNav(e.target.value));
}

function filterNav(q) {
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  document.querySelectorAll(".nav-group").forEach(g => {
    let vis = 0;
    g.querySelectorAll(".nav-link").forEach(a => {
      const hit = terms.every(t => a.dataset.kw.includes(t));
      a.hidden = !hit;
      if (hit) vis++;
    });
    g.hidden = vis === 0 && terms.length > 0;
    total += vis;
  });
  document.getElementById("no-hits").hidden = !(terms.length && total === 0);
}

/* Section index pages: <main data-index="inner"> renders its card list. */
function buildIndexCards() {
  const m = document.querySelector("main[data-index]");
  if (!m) return;
  const sec = m.dataset.index;
  const wrap = document.createElement("div");
  wrap.className = "cards";
  wrap.innerHTML = PAGES[sec].map(p =>
    `<a class="card" href="${p.f}"><strong>${p.t}</strong>${badge(p)}<p>${p.d}</p></a>`
  ).join("");
  m.appendChild(wrap);
}

/* Home page: fill page counts into the door cards. */
function fillCounts() {
  document.querySelectorAll("[data-count]").forEach(el => {
    el.textContent = PAGES[el.dataset.count].length;
  });
}

/* Collapsible sidebar: toggle button, state remembered across pages.
   localStorage works on file:// in Chrome/Edge/Firefox; try/catch in case
   a hardened browser says no — then it just defaults to open each page. */
function navState() {
  try { return localStorage.getItem("benham-manual-nav"); } catch (e) { return null; }
}
function saveNavState(s) {
  try { localStorage.setItem("benham-manual-nav", s); } catch (e) {}
}
function buildToggle() {
  const btn = document.createElement("button");
  btn.id = "nav-toggle";
  btn.textContent = "☰";
  btn.title = "menu";
  const saved = navState();
  const closed = saved ? saved === "closed" : window.innerWidth <= 760;
  document.body.classList.toggle("nav-closed", closed);
  btn.setAttribute("aria-expanded", String(!closed));
  btn.addEventListener("click", () => {
    const nowClosed = document.body.classList.toggle("nav-closed");
    btn.setAttribute("aria-expanded", String(!nowClosed));
    saveNavState(nowClosed ? "closed" : "open");
  });
  document.body.prepend(btn);
}

buildSidebar();
buildToggle();
buildIndexCards();
fillCounts();
