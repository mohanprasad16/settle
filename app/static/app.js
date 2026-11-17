// Settle front end. No framework: one state object, small render functions, safe DOM building.
// Text from the files (narrations, names, notes) is written by outsiders, so it is only ever set
// through textContent, never as HTML.
"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
}

// ---- formatting --------------------------------------------------------------------------------

function rupees(paise) {
  if (paise === null || paise === undefined || paise === "") return "";
  const neg = paise < 0;
  let p = Math.abs(Math.round(paise));
  const whole = Math.floor(p / 100), frac = String(p % 100).padStart(2, "0");
  let s = String(whole);
  if (s.length > 3) {
    const head = s.slice(0, -3), tail = s.slice(-3);
    s = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }
  return (neg ? "−" : "") + "₹" + s + "." + frac;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fdate(v) {
  if (!v) return "";
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(v);
  const d = `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}`;
  return m[4] ? `${d}, ${m[4]}:${m[5]}` : d;
}
const KIND = {
  loss: { label: "Money lost", mark: "⊗" },
  customer: { label: "Owed to a customer", mark: "⊘" },
  books: { label: "Fix in your books", mark: "⊙" },
  risk: { label: "Worth a look", mark: "◇" },
};
const ACTION = {
  journal_post: "Post a journal entry",
  razorpay_ticket: "Send a ticket to Razorpay support",
  razorpay_api_call: "Check the status with Razorpay",
  customer_note: "Send a note to the customer",
  webhook_replay: "Replay the payment webhook to your store",
};
const STATUS = {
  needs_approval: "Waiting for your approval",
  auto: "Runs on its own (routine, under ₹500)",
  approved: "Approved",
  executed: "Done",
  blocked: "Blocked",
  rejected: "Rejected",
};
const COLUMN = {
  row_no: "Row", entity_id: "Razorpay id", type: "Type", amount: "Amount", fee: "Fee", tax: "GST on fee",
  order_receipt: "Order no", method: "Method", card_type: "Card", card_network: "Network",
  settlement_id: "Settlement", utr: "UTR", created_at: "Created", txn_date: "Date", narration: "Narration",
  ref_no: "Reference", debit: "Withdrawal", credit: "Deposit", order_no: "Order no", order_date: "Order date",
  customer: "Customer", status: "Status", refund_amount: "Refund",
};
const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
};

// ---- talking to the server ---------------------------------------------------------------------

async function api(method, url, body, form = false) {
  let res;
  try {
    res = await fetch(url, {
      method, credentials: "same-origin",
      headers: form || !body ? {} : { "Content-Type": "application/json" },
      body: form ? body : body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw { network: true, error: "We couldn't reach Settle.", hint: "Check your internet connection and try again." };
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw { status: res.status, error: data?.error || "Something went wrong.", hint: data?.hint || "Try again.", ...(data || {}) };
  return data;
}

const alerts = $("#alerts");
function notice(kind, title, text = "", retry = null) {
  const box = el("div", { class: `banner ${kind}`, role: kind === "err" ? "alert" : "status" },
    el("div", {}, el("b", { text: title }), text),
    el("div", { class: "inline" },
      retry ? el("button", { class: "btn small", type: "button", onclick: () => { box.remove(); retry(); } }, "Try again") : null,
      el("button", { class: "btn quiet small", type: "button", "aria-label": "Dismiss", onclick: () => box.remove() }, "Dismiss")));
  alerts.replaceChildren(box);
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return box;
}
function fail(err, retry) {
  notice("err", err.error || "Something went wrong.", err.hint || "", err.network || err.status >= 500 ? retry : null);
}

let loadingTimer = null;
function loading(steps) {
  clearInterval(loadingTimer);
  const box = $("#loading");
  if (!steps) { box.hidden = true; return; }
  const list = Array.isArray(steps) ? steps : [steps];
  let i = 0;
  $("#loading-text").textContent = list[0];
  box.hidden = false;
  if (list.length > 1) loadingTimer = setInterval(() => { i = Math.min(i + 1, list.length - 1); $("#loading-text").textContent = list[i]; }, 2600);
}

// ---- views -------------------------------------------------------------------------------------

const views = ["landing", "upload", "review", "results"];
function show(name) {
  for (const v of views) $("#" + v).hidden = v !== name;
  $("#topnav").hidden = name !== "results";
  window.scrollTo({ top: 0 });
}

let state = { view: null, filter: "all" };

async function loadSample() {
  loading(["Loading the sample month…", "Matching 3,700 rows across three files…", "Building the bridge…"]);
  try { render(await api("POST", "/api/sample")); alerts.replaceChildren(); }
  catch (e) { fail(e, loadSample); }
  finally { loading(false); }
}
async function loadChaos() {
  loading(["Making a new random month with hidden problems…", "Reconciling it…", "Checking against the answer key…"]);
  try {
    const v = await api("POST", "/api/chaos", {});
    render(v);
    notice("info", `New random month #${v.seed}`, "The answer key is below the problem list. Settle hasn't seen this month before.");
  } catch (e) { fail(e, loadChaos); }
  finally { loading(false); }
}

// ---- results -----------------------------------------------------------------------------------

function render(v) {
  state.view = v;
  const root = $("#results");
  const t = v.totals, auto = t.auto_reconciled || {};
  const tied = t.unexplained === 0;
  const source = v.source === "sample" ? "Sample month" : v.source === "chaos" ? `Random month #${v.seed ?? ""}` : "Your files";

  const head = el("div", { class: "runhead" },
    el("div", {},
      el("h1", { style: "font-size:clamp(1.6rem,3vw,2.2rem)" }, v.merchant || "Your business"),
      el("div", { class: "meta" }, `${source} · ${fdate(v.period.start)} to ${fdate(v.period.end)} · ${(t.rows.razorpay + t.rows.orders + t.rows.bank).toLocaleString("en-IN")} rows checked`)),
    el("div", { class: "inline" },
      el("a", { class: "btn small", href: "/api/report", target: "_blank", rel: "noopener" }, "Audit report"),
      el("a", { class: "btn quiet small", href: "/api/export" }, "Excel pack (.zip)"),
      el("a", { class: "btn quiet small", href: "/api/journal" }, "Tally journal (.csv)")));

  const fig = (cls, label, value, sub) => el("div", { class: cls }, el("span", { class: "label" }, label), el("span", { class: "fig" }, value), sub ? el("span", { class: "small muted" }, sub) : null);
  const statement = el("div", { class: "statement", role: "group", "aria-label": "Summary" },
    fig("loss", "Money lost", rupees(t.loss)),
    fig("customer", "Owed to customers", rupees(t.customer)),
    fig("books", "Book corrections", rupees(t.books)),
    fig("", "Auto-reconciled", `${auto.pct}%`, `${(auto.rows || 0).toLocaleString("en-IN")} of ${(auto.of || 0).toLocaleString("en-IN")} rows`),
    fig(tied ? "tied" : "tied bad", "Unexplained", rupees(t.unexplained), tied ? "✓ Ties out" : "Needs a look"));

  const r = v.roi;
  const roi = el("details", { class: "roi" },
    el("summary", {}, `Found ${rupees(r.leakage_found)} · saves about ${r.hours_saved} hours of ticking by hand · AI cost so far ${rupees(r.ai_cost)}`),
    el("p", { class: "formula" }, "Money found: ", r.formulas.leakage_found),
    el("p", { class: "formula" }, "Hours saved: ", r.formulas.hours_saved),
    el("p", { class: "formula" }, "AI cost: ", r.formulas.ai_cost));

  const banners = [];
  if (!v.ai.available) banners.push(el("div", { class: "banner warn" }, el("div", {}, el("b", { text: "The AI is off right now." }), "Every amount here is still exact. Explanations are written from templates and Ask is unavailable.")));
  if (v.suspicious?.length) banners.push(el("div", { class: "banner warn" }, el("div", {}, el("b", { text: `${v.suspicious.length} record${v.suspicious.length > 1 ? "s" : ""} contain text that tries to instruct an AI.` }), "Settle treats it as data and changed nothing. They're listed under Patterns worth a look.")));

  const problems = v.findings.filter((f) => f.kind !== "risk").sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
  const list = el("ul", { class: "ledger-list", id: "problem-list" });
  const filters = el("div", { class: "filters", role: "group", "aria-label": "Show" },
    ...[["all", `All (${problems.length})`], ["loss", "Money lost"], ["customer", "Owed to customers"], ["books", "Fix in your books"]].map(([k, label]) =>
      el("button", { class: "chip", type: "button", "aria-pressed": String(state.filter === k), onclick: () => { state.filter = k; render(state.view); } }, label)));
  const shown = problems.filter((f) => state.filter === "all" || f.kind === state.filter);
  for (const f of shown) list.append(el("li", {}, entry(f)));
  if (!shown.length) list.append(el("li", { class: "muted", style: "padding:14px" }, problems.length ? "Nothing in this group." : "No problems found this month. Every rupee ties out."));

  const main = el("div", {},
    el("h2", {}, "What to do first"),
    el("p", { class: "muted" }, "Sorted by amount. Open any line to see why, the proof from your files, and the fix."),
    filters, list,
    v.chaos ? chaosPanel(v) : null);

  const side = el("aside", { class: "side", "aria-label": "Ask and download" }, askPanel(v));

  root.replaceChildren(head, statement, roi, ...banners,
    el("div", { class: "grid2" }, main, side),
    bridgesSection(v), patternsSection(v), exceptionsSection(v), handledSection(v));
  show("results");
}

function entry(f) {
  const k = KIND[f.kind] || KIND.books;
  return el("button", { class: "entry", type: "button", onclick: (e) => openFinding(f.id, e.currentTarget), "aria-label": `${k.label}: ${f.title}, ${rupees(f.amount)}` },
    el("span", { class: `mark ${f.kind}`, "aria-hidden": "true" }, k.mark),
    el("span", {},
      el("span", { class: "t" }, el("span", { class: `kind ${f.kind}` }, k.label), f.title, f.count ? ` (${f.count} payments)` : ""),
      f.headline ? el("div", { class: "h" }, f.headline) : null,
      el("div", { class: "who" }, f.contact ? `Contact: ${f.contact}` : "", f.ref ? ` · ${fdate(f.ref)}` : "")),
    el("span", { class: "amt" }, rupees(f.amount)));
}

function bridgeTable(title, steps, caption) {
  const vals = [];
  let run = 0;
  for (const s of steps) {
    if (s.kind === "start") run = s.amount;
    else if (s.kind !== "end" && s.kind !== "residual") run += s.amount;
    vals.push(s.kind === "end" ? s.amount : run);
  }
  const lo = Math.min(...vals) * 0.985, hi = Math.max(...vals) * 1.005, span = hi - lo || 1;
  const pct = (x) => ((x - lo) / span) * 100;
  const rows = [];
  let prev = 0;
  steps.forEach((s, i) => {
    const cur = vals[i];
    let bar = null;
    if (s.kind === "start" || s.kind === "end") bar = el("div", { class: "bar", "aria-hidden": "true" }, el("i", { class: "total", style: `left:0;width:${pct(cur)}%` }));
    else if (s.kind !== "residual" && s.amount) {
      const a = Math.min(prev, cur), b = Math.max(prev, cur);
      bar = el("div", { class: "bar", "aria-hidden": "true" }, el("i", { class: s.amount < 0 ? "down" : "up", style: `left:${pct(a)}%;width:max(2px,${pct(b) - pct(a)}%)` }));
    }
    const cls = s.kind + (s.kind === "residual" && s.amount ? " bad" : "");
    const label = s.kind === "residual" ? (s.amount ? "Unexplained" : "Unexplained ✓ ties out") : s.label;
    rows.push(el("tr", { class: cls }, el("td", {}, label, bar), el("td", { class: "num" }, s.count || ""), el("td", { class: "num" }, rupees(s.amount))));
    prev = cur;
  });
  return el("div", {},
    el("h3", {}, title),
    el("p", { class: "small muted" }, caption),
    el("div", { class: "tablewrap" }, el("table", { class: "book" },
      el("thead", {}, el("tr", {}, el("th", {}, "Line"), el("th", { class: "num" }, "Items"), el("th", { class: "num" }, "Amount"))),
      el("tbody", {}, rows))));
}

function bridgesSection(v) {
  return el("section", { class: "block", "aria-labelledby": "bridges-h" },
    el("h2", { id: "bridges-h" }, "How every rupee is accounted for"),
    el("p", { class: "muted" }, "Two bridges, the way an auditor ties out a month. Red lines are problems, grey lines are timing. Each ends with what's left unexplained."),
    el("div", { class: "bridges" },
      bridgeTable("Your books to Razorpay", v.bridges.books_to_razorpay, "From your net sales to what Razorpay settled."),
      bridgeTable("Razorpay to your bank", v.bridges.razorpay_to_bank, "From what Razorpay settled to what reached your bank.")));
}

function patternsSection(v) {
  const risk = v.findings.filter((f) => f.kind === "risk");
  if (!risk.length && !v.suspicious?.length) return null;
  const list = el("ul", { class: "ledger-list" }, ...risk.map((f) => el("li", {}, entry(f))));
  for (const s of v.suspicious || []) {
    list.append(el("li", {}, el("div", { class: "entry", style: "cursor:default" },
      el("span", { class: "mark risk", "aria-hidden": "true" }, "◇"),
      el("span", {}, el("span", { class: "t" }, el("span", { class: "kind risk" }, "Worth a look"), s.title),
        el("div", { class: "h" }, `${sourceName(s.source)} row ${s.row_no}, ${s.field}: `, el("q", { text: s.text.replace(/<\/?data>/g, "") })),
        el("div", { class: "who" }, "Treated as data. It changed nothing in this reconciliation.")),
      el("span", {}))));
  }
  return el("section", { class: "block", "aria-labelledby": "patterns-h" },
    el("h2", { id: "patterns-h" }, "Patterns worth a look"),
    el("p", { class: "muted" }, "Not missing money: the amounts match. These are patterns a finance team usually wants to know about."), list);
}

function exceptionsSection(v) {
  const items = [
    ...(v.exceptions || []).map((e) => `Settlement ${e.settlement_id} of ${rupees(e.amount)}, settled ${fdate(e.settled_on)}: ${e.why}. Check your bank statement for this credit.`),
    ...(v.unmatched_credits || []).map((u) => `Bank credit of ${rupees(u.credit)} on ${fdate(u.txn_date)} (bank row ${u.row_no}) mentions Razorpay but matches no settlement in this report. Most likely from another month.`),
  ];
  if (!items.length) return null;
  return el("section", { class: "block", "aria-labelledby": "exc-h" },
    el("h2", { id: "exc-h" }, "Couldn't match automatically"),
    el("p", { class: "muted" }, "Settle doesn't guess. These need a person to look; they're kept on their own line so the bridge still ties out."),
    el("ul", { class: "ticks cross" }, ...items.map((x) => el("li", {}, x))));
}

function handledSection(v) {
  const resolved = v.resolved || [];
  return el("section", { class: "block", "aria-labelledby": "handled-h" },
    el("h2", { id: "handled-h" }, "What we checked and left alone"),
    el("p", { class: "muted" }, "These look like problems in a simple match but aren't. Shown so you can see what was checked."),
    el("ul", { class: "ticks" }, ...v.handled.map((h) => el("li", {}, `${h.label}: ${h.count.toLocaleString("en-IN")}`))),
    resolved.length ? el("div", { style: "margin-top:16px" }, el("h3", {}, "Marked as expected by you"),
      el("ul", { class: "ticks" }, ...resolved.map((r) => el("li", {}, `${r.title}, ${rupees(r.amount)}: “${r.note}”`)))) : null);
}

function chaosPanel(v) {
  const key = v.chaos;
  const planted = key.filter((k) => k.found !== null);
  const found = planted.filter((k) => k.found).length;
  const extra = key.filter((k) => k.found === null).length;
  return el("section", { class: "block", id: "answer-key", "aria-labelledby": "key-h" },
    el("h2", { id: "key-h" }, "Answer key for this random month"),
    el("p", {}, el("b", {}, `Found ${found} of ${planted.length} hidden problems`), extra ? `, plus ${extra} it flagged that weren't planted.` : ", and flagged nothing else."),
    el("ul", { class: "answerkey" }, ...key.map((k) =>
      el("li", {},
        el("span", { class: k.found ? "y" : k.found === false ? "n" : "x", "aria-label": k.found ? "found" : k.found === false ? "missed" : "extra" }, k.found ? "✓" : k.found === false ? "✗" : "?"),
        el("span", {}, k.title, el("span", { class: "small muted" }, ` · ${fdate(k.ref)}`),
          k.found === null ? el("div", { class: "small muted" }, "Flagged but not in the answer key") : null),
        el("span", { class: "num" }, rupees(k.amount))))),
    el("div", { class: "cta", style: "margin-top:12px" }, el("button", { class: "btn", type: "button", onclick: loadChaos }, "Try another random month")));
}

function cell(ev, c, v) {
  if (ev.money.includes(c)) return v ? rupees(v) : "";
  if (/date|_at$/.test(c)) return fdate(v);
  return v ?? "";
}

function sourceName(s) { return { rp: "Razorpay report", bank: "Bank statement", orders: "Your orders", finding: "Problem" }[s] || s; }

// ---- Ask -----------------------------------------------------------------------------------------

const SUGGESTED = ["Why did a settlement never reach my bank?", "How much did I lose to chargebacks this month?", "Which day had the most refunds?", "What should I do first?"];

function askPanel(v) {
  const out = el("div", { "aria-live": "polite" });
  const input = el("textarea", { id: "ask-q", placeholder: "Ask about this month, e.g. why was 21 Oct short?", maxlength: "500" });
  const go = el("button", { class: "btn primary", type: "submit" }, "Ask");
  const form = el("form", { class: "ask", onsubmit: (e) => { e.preventDefault(); runAsk(input.value, out, go); } },
    el("label", { for: "ask-q", class: "sr" }, "Your question"), input,
    el("div", { class: "suggest" }, ...SUGGESTED.map((q) => el("button", { type: "button", onclick: () => { input.value = q; runAsk(q, out, go); } }, q))),
    go);
  if (!v.ai.available) { input.disabled = true; go.disabled = true; }
  return el("div", { class: "panel" },
    el("h2", { style: "font-size:1.25rem" }, "Ask about this month"),
    el("p", { class: "small muted" }, v.ai.available ? "Answers come from your files. Every figure is checked before you see it." : "Ask needs the AI, which is off right now."),
    form, out);
}

async function runAsk(q, out, btn) {
  q = (q || "").trim();
  if (!q) { out.replaceChildren(el("p", { class: "small", role: "alert" }, "Type a question first.")); return; }
  btn.disabled = true; btn.textContent = "Working it out…";
  out.replaceChildren(el("p", { class: "small muted" }, "Looking through your files…"));
  try {
    const a = await api("POST", "/api/ask", { question: q });
    const cites = a.citations.map((c) => c.source === "finding"
      ? el("li", {}, el("button", { class: "linkish", type: "button", onclick: (e) => openFinding(c.ref, e.currentTarget) }, `Problem ${c.ref}`))
      : el("li", {}, `${sourceName(c.source)}, row ${c.ref}`));
    out.replaceChildren(el("div", { class: "answer" },
      el("span", { class: a.verified ? "badge ok" : "badge warn" }, a.verified ? "✓ Figures checked against your files" : "Couldn't verify every figure: double-check"),
      el("p", { style: "margin-top:8px" }, a.answer),
      cites.length ? el("div", {}, el("b", { class: "small" }, "Proof"), el("ul", { class: "small" }, ...cites)) : null,
      a.trace?.length ? el("details", {}, el("summary", { class: "small" }, `Show how I worked it out (${a.trace.length} step${a.trace.length > 1 ? "s" : ""})`),
        el("ol", { class: "timeline" }, ...a.trace.map((t) => el("li", {},
          el("b", {}, t.tool === "run_sql" ? "Looked up the data" : "Opened a problem"), ` · ${t.rows_returned ?? 0} rows · ${t.ms} ms`,
          el("pre", {}, t.input || ""))))) : null));
  } catch (e) {
    out.replaceChildren(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint)));
  } finally { btn.disabled = false; btn.textContent = "Ask"; }
}

// ---- finding drawer --------------------------------------------------------------------------

let lastOpener = null;
const drawer = $("#drawer"), sheet = $("#drawer-sheet");
function closeDrawer() { drawer.hidden = true; document.body.style.overflow = ""; lastOpener?.focus?.(); }
drawer.addEventListener("click", (e) => { if (e.target === drawer) closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden) closeDrawer(); });

async function openFinding(id, opener) {
  lastOpener = opener || document.activeElement;
  drawer.hidden = false; document.body.style.overflow = "hidden";
  sheet.replaceChildren(el("div", { class: "body" }, el("p", { class: "muted", style: "padding-top:20px" }, "Opening…")));
  try { drawFinding(await api("GET", `/api/finding/${encodeURIComponent(id)}`)); }
  catch (e) {
    sheet.replaceChildren(el("div", { class: "body" }, el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint)),
      el("button", { class: "btn", type: "button", onclick: closeDrawer }, "Close")));
  }
}

function drawFinding(d) {
  const f = d.finding, x = d.explanation, k = KIND[f.kind] || KIND.books;
  const close = el("button", { class: "btn quiet small", type: "button", onclick: closeDrawer }, "Close");
  const header = el("header", {},
    el("div", {}, el("span", { class: `kind ${f.kind}` }, k.label), el("h2", { id: "drawer-title", style: "margin-top:6px" }, f.title),
      el("div", { class: "bigamt" }, rupees(f.amount))), close);
  const body = el("div", { class: "body" });

  if (x) {
    const aiBtn = el("button", { class: "btn small", type: "button" }, "Explain with AI");
    aiBtn.addEventListener("click", async () => {
      aiBtn.disabled = true; aiBtn.textContent = "Writing…";
      try { await api("POST", `/api/finding/${encodeURIComponent(f.id)}/explain`); drawFinding(await api("GET", `/api/finding/${encodeURIComponent(f.id)}`)); }
      catch (e) { aiBtn.replaceWith(el("div", { class: "banner warn", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint))); }
    });
    body.append(
      el("h3", {}, x.headline),
      el("p", {}, x.what_happened),
      el("p", {}, el("b", {}, "Likely cause. "), x.likely_cause),
      el("b", {}, "What to do"),
      el("ol", { class: "steps-todo" }, ...x.what_to_do.map((s) => el("li", {}, s))),
      el("p", { style: "margin-top:10px" }, el("b", {}, "Who to contact: "), x.who_to_contact),
      el("div", { class: "inline" },
        x.source === "model" && x.verified ? el("span", { class: "badge ok" }, "✓ Written by AI, every figure checked") : el("span", { class: "badge warn" }, "Written from a template"),
        x.source !== "model" && state.view?.ai.available ? aiBtn : null));
  }

  if (d.trace?.hops?.length) {
    body.append(el("h3", {}, "Where the money stopped"),
      el("p", { class: "small muted" }, d.trace.stalled_at ? `Stopped at: ${d.trace.stalled_at}` : "Every step is in place."),
      el("ol", { class: "chain" }, ...d.trace.hops.map((h) => el("li", { class: h.status },
        el("div", { class: "st" }, { ok: "✓", missing: "✗", mismatch: "!", waiting: "…" }[h.status] + " " + { ok: "In place", missing: "Missing", mismatch: "Doesn't match", waiting: "Next month" }[h.status]),
        el("div", {}, h.hop), h.amount !== null && h.amount !== undefined ? el("div", { class: "small" }, rupees(h.amount)) : null,
        h.detail ? el("div", { class: "small muted" }, h.detail) : null))));
  }

  if (d.evidence?.length) {
    body.append(el("h3", {}, "Proof from your files"));
    for (const ev of d.evidence) {
      const cols = ev.columns;
      body.append(el("p", { class: "small", style: "margin:10px 0 4px" }, el("b", {}, ev.label), ev.total > ev.rows.length ? ` · showing ${ev.rows.length} of ${ev.total} rows` : ` · ${ev.rows.length} row${ev.rows.length > 1 ? "s" : ""}`),
        el("div", { class: "tablewrap" }, el("table", { class: "ev" },
          el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", { class: ev.money.includes(c) ? "num" : "" }, COLUMN[c] || c)))),
          el("tbody", {}, ...ev.rows.map((r) => el("tr", {}, ...cols.map((c) => el("td", { class: ev.money.includes(c) ? "num" : "" }, cell(ev, c, r[c])))))))));
    }
  }

  const tabs = [...d.drafts.map((dr) => ({ label: dr.kind === "ticket" ? "Ticket to Razorpay" : "Note to customer", text: `Subject: ${dr.subject}\n\n${dr.body}`, ai: dr.source === "model" }))];
  if (d.api_call) tabs.push({ label: "Razorpay API call", text: `${d.api_call.method} ${d.api_call.url}\nAuth: ${d.api_call.auth}\n\n${d.api_call.why}`, ai: false });
  if (tabs.length) {
    const pane = el("div");
    const bar = el("div", { class: "tabs", role: "tablist" });
    const pick = (i) => {
      $$("button", bar).forEach((b, j) => b.setAttribute("aria-selected", String(i === j)));
      const t = tabs[i];
      pane.replaceChildren(el("pre", { tabindex: "0" }, t.text),
        el("div", { class: "inline" }, el("button", { class: "btn small", type: "button", onclick: (e) => copy(t.text, e.currentTarget) }, "Copy"),
          el("span", { class: "small muted" }, t.ai ? "Drafted by AI with every figure checked. Edit before sending." : "Built from your data.")));
    };
    tabs.forEach((t, i) => bar.append(el("button", { type: "button", role: "tab", onclick: () => pick(i) }, t.label)));
    body.append(el("h3", {}, "Drafts"), bar, pane);
    pick(0);
  }

  if (d.actions?.length) body.append(actionsBlock(d.actions, f));
  if (f.kind !== "risk") body.append(expectedBlock(f));

  sheet.replaceChildren(header, body);
  close.focus();
}

function actionsBlock(actions, f) {
  const name = el("input", { type: "text", id: "approver", value: store.get("settle_name"), placeholder: "Your name", autocomplete: "name" });
  const wrap = el("div", {}, el("h3", {}, "Fixes waiting for a decision"),
    el("p", { class: "small muted" }, "Settle never sends anything on its own. Money, customers and Razorpay need a named approver; anything over ₹5,000 always does. In this demo, approving records exactly what would be sent."),
    el("div", { class: "field" }, el("label", { for: "approver" }, "Approve as"), name));
  for (const a of actions) wrap.append(actionCard(a, name));
  return wrap;
}

function actionCard(a, nameInput) {
  const card = el("div", { class: "action" });
  const redraw = (a, note) => {
    const can = a.status === "needs_approval" || a.status === "auto";
    card.replaceChildren(
      el("div", { class: "row" }, el("b", {}, ACTION[a.type] || a.type), a.amount ? el("span", { class: "num" }, rupees(a.amount)) : null),
      el("div", { class: `status ${a.status}` }, STATUS[a.status] || a.status, a.reason && a.status !== "executed" ? el("span", { class: "muted", style: "font-weight:400" }, ` · ${a.reason}`) : "", a.approved_by ? ` · by ${a.approved_by}` : ""),
      note ? el("div", { class: "banner okb", role: "status" }, el("div", {}, note)) : null,
      el("details", {}, el("summary", { class: "small" }, a.sent ? "What would have been sent" : "What this will send"), el("pre", {}, JSON.stringify(a.sent || a.payload, null, 2))),
      can ? el("div", { class: "inline" },
        el("button", { class: "btn ok small", type: "button", onclick: () => decide(a, "approve") }, a.status === "auto" ? "Run it" : "Approve"),
        el("button", { class: "btn danger small", type: "button", onclick: () => decide(a, "reject") }, "Reject")) : null);
  };
  const decide = async (a, how) => {
    const who = nameInput.value.trim();
    if (!who && how === "approve") { nameInput.focus(); redraw(a, null); card.append(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: "Type your name to approve." }), "Approvals are recorded with the approver's name."))); return; }
    store.set("settle_name", who);
    try {
      const r = await api("POST", `/api/actions/${a.id}/${how}`, { approver: who || "a reviewer" });
      redraw(r.action, how === "approve" ? (r.demo_mode ? "Recorded. This is a demo, so nothing was sent; below is exactly what would have gone out." : "Done.") : "Rejected. Nothing will be sent.");
    } catch (e) { redraw(a, null); card.append(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint))); }
  };
  redraw(a, null);
  return card;
}

function expectedBlock(f) {
  const note = el("input", { type: "text", id: "rule-note", placeholder: "Why is this expected? e.g. coupon at checkout", maxlength: "120" });
  const scope = f.kind === "loss" ? null : el("select", { id: "rule-scope", "aria-label": "Apply to" },
    el("option", { value: "this" }, "Just this one"), el("option", { value: "similar" }, `All like this up to ${rupees(Math.abs(f.amount))}`));
  const msg = el("div", { "aria-live": "polite" });
  const go = el("button", { class: "btn small", type: "button" }, "Mark as expected");
  go.addEventListener("click", async () => {
    if (!note.value.trim()) { msg.replaceChildren(el("p", { class: "small", role: "alert" }, "Add a short note saying why.")); note.focus(); return; }
    go.disabled = true;
    try {
      const v = await api("POST", "/api/rules", { finding_id: f.id, note: note.value, scope: scope ? scope.value : "this" });
      closeDrawer(); render(v);
      notice("okb", `Marked as expected: “${note.value.trim()}”.`, `It moved to "What we checked and left alone". The bridge still ties out at ${rupees(v.totals.unexplained)} unexplained.`);
    } catch (e) { msg.replaceChildren(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint))); }
    finally { go.disabled = false; }
  });
  return el("div", {}, el("h3", {}, "Not a problem?"),
    el("p", { class: "small muted" }, "Mark it as expected and Settle will remember it for this session. It keeps its own line in the bridge."),
    el("div", { class: "inline" }, el("label", { class: "sr", for: "rule-note" }, "Reason"), note, scope, go), msg);
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
  catch { btn.textContent = "Select and copy the text above"; }
  setTimeout(() => (btn.textContent = "Copy"), 2500);
}

// ---- upload ------------------------------------------------------------------------------------

const form = $("#upload-form");
for (const slot of $$(".slot", form)) {
  const input = $("input[type=file]", slot), label = $(".file", slot);
  const sync = () => {
    slot.classList.remove("error");
    const f = input.files[0];
    slot.classList.toggle("filled", !!f);
    label.textContent = f ? `${f.name} (${Math.max(1, Math.round(f.size / 1024))} KB)` : "";
    if (slot.dataset.slot === "bank") $("#pw-field").hidden = !(f && /\.pdf$/i.test(f.name)) && $("#pw-field").hidden;
  };
  input.addEventListener("change", sync);
  slot.addEventListener("dragover", (e) => { e.preventDefault(); slot.classList.add("drag"); });
  slot.addEventListener("dragleave", () => slot.classList.remove("drag"));
  slot.addEventListener("drop", (e) => {
    e.preventDefault(); slot.classList.remove("drag");
    if (e.dataTransfer.files.length) { const dt = new DataTransfer(); dt.items.add(e.dataTransfer.files[0]); input.files = dt.files; sync(); }
  });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const missing = $$(".slot", form).filter((s) => !$("input[type=file]", s).files.length);
  missing.forEach((s) => s.classList.add("error"));
  if (missing.length) { notice("err", "Add all three files first.", `Missing: ${missing.map((s) => $("h3", s).textContent).join(", ")}.`); return; }
  const bank = $("#f-bank").files[0];
  const pdf = /\.pdf$/i.test(bank.name);
  loading(pdf ? ["Unlocking the statement…", "Reading each page with AI…", "Checking every running balance adds up…", "Almost there…"] : ["Reading your files…", "Matching every row…", "Building the bridge…"]);
  try {
    const v = await api("POST", "/api/upload", new FormData(form), true);
    alerts.replaceChildren();
    if (v.needs_review) showReview(v); else render(v);
  } catch (err) {
    if (err.needs_password) { $("#pw-field").hidden = false; $("#f-password").focus(); }
    if (err.slot) $(`.slot[data-slot=${err.slot}]`, form)?.classList.add("error");
    fail(err, () => form.requestSubmit());
  } finally { loading(false); }
});

// ---- PDF review --------------------------------------------------------------------------------

const FIELDS = [["txn_date", "Date"], ["narration", "Narration"], ["ref_no", "Reference"], ["debit", "Withdrawal"], ["credit", "Deposit"], ["balance", "Balance"]];
const LIKELY = { withdrawal: "debit", deposit: "credit", balance: "balance" };
const AMOUNT = new Set(["debit", "credit", "balance"]);
// Amounts read as ₹ with Indian grouping; the server accepts that or plain numbers back.
function fmtAmount(x) {
  const n = Number(String(x ?? "").replace(/[₹,\s]/g, ""));
  return x === "" || x == null || Number.isNaN(n) ? (x ?? "") : rupees(Math.round(n * 100));
}

function showReview(v) {
  const breaks = new Map(v.breaks.map((b) => [b.row, b]));
  $("#review-summary").replaceChildren(v.verified
    ? el("div", { class: "banner okb" }, el("div", {}, el("b", { text: `All ${v.rows.length} rows read from ${v.pages} page${v.pages > 1 ? "s" : ""}, and every running balance adds up.` }), "Check anything you like, then continue."))
    : el("div", { class: "banner warn" }, el("div", {}, el("b", { text: `${v.breaks.length} row${v.breaks.length > 1 ? "s don't" : " doesn't"} add up to the printed balance.` }),
        "They're highlighted, with the box most likely misread outlined. Fix them from your PDF, then continue.")));
  fillReview(v.rows, breaks);
  show("review");
}

function fillReview(rows, breaks) {
  const table = $("#review-table");
  table.replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "#"), ...FIELDS.map(([, l]) => el("th", {}, l)))),
    el("tbody", {}, ...rows.map((r, i) => {
      const b = breaks.get(i + 1);
      return el("tr", { class: b ? "broken" : "", title: b ? `Expected balance ${rupees(b.expected_balance)}, printed ${rupees(b.printed_balance)}` : null },
        el("td", {}, String(i + 1)),
        ...FIELDS.map(([k, l]) => {
          const attrs = { "data-k": k, class: b && LIKELY[b.likely_field] === k ? "likely" : "", "aria-label": `Row ${i + 1} ${l}` };
          // The narration wraps so the whole line is readable; it is how a person recognises the row.
          if (k === "narration") return el("td", {}, el("textarea", { ...attrs, rows: 2, title: r[k] ?? "" }, r[k] ?? ""));
          return el("td", {}, el("input", { ...attrs, value: AMOUNT.has(k) ? fmtAmount(r[k]) : r[k] ?? "" }));
        }));
    })));
}

$("#review-go").addEventListener("click", async () => {
  const rows = $$("#review-table tbody tr").map((tr) => Object.fromEntries($$("input, textarea", tr).map((i) => [i.dataset.k, i.value])));
  loading(["Checking the balances again…", "Reconciling…"]);
  try { render(await api("POST", "/api/bank-preview/confirm", { rows })); alerts.replaceChildren(); }
  catch (e) {
    if (e.breaks) {
      fillReview(rows, new Map(e.breaks.map((b) => [b.row, b])));
      $("#review-summary").replaceChildren(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint)));
    } else fail(e);
  } finally { loading(false); }
});

// ---- wiring ------------------------------------------------------------------------------------

$("#try-sample").addEventListener("click", loadSample);
$("#upload-sample").addEventListener("click", loadSample);
$("#go-upload").addEventListener("click", () => show("upload"));
$("#upload-back").addEventListener("click", () => show("landing"));
$("#review-back").addEventListener("click", () => show("upload"));
$("#nav-chaos").addEventListener("click", loadChaos);
$("#nav-new").addEventListener("click", () => { alerts.replaceChildren(); show("landing"); });
$("#home").addEventListener("click", (e) => { if (state.view) { e.preventDefault(); show("results"); } });
const connect = $("#connect");
$("#open-connect").addEventListener("click", () => connect.showModal());
$("#connect-close").addEventListener("click", () => connect.close());

// Pick up where this browser left off (sessions last an hour).
(async () => {
  try {
    const v = await api("GET", "/api/result");
    if (v.loaded) render(v); else show("landing");
  } catch { show("landing"); }
})();
