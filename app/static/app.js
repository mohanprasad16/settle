// Settle front end. No framework: one state object, small render functions, safe DOM building.
// Text from the files (narrations, names, notes) is written by outsiders, so it is only ever set
// through textContent, never as HTML.
"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

// replaceChildren() prints null/false as text; put() drops them like el() does.
function put(n, ...kids) {
  n.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
}

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

const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) n.setAttribute(k, v);
  for (const k of kids.flat()) if (k) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return n;
}

// ---- formatting --------------------------------------------------------------------------------

function rupees(paise) {
  if (paise === null || paise === undefined || paise === "") return "";
  const neg = paise < 0;
  const p = Math.abs(Math.round(paise));
  const whole = Math.floor(p / 100), frac = String(p % 100).padStart(2, "0");
  let s = String(whole);
  if (s.length > 3) s = s.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + s.slice(-3);
  return (neg ? "−" : "") + "₹" + s + "." + frac;
}
// Short form for chart labels: ₹21.3L, ₹33.4K.
function short(paise) {
  const r = Math.abs(paise) / 100, sign = paise < 0 ? "−" : "";
  if (r >= 1e7) return `${sign}₹${(r / 1e7).toFixed(2)}Cr`;
  if (r >= 1e5) return `${sign}₹${(r / 1e5).toFixed(1)}L`;
  if (r >= 1e3) return `${sign}₹${(r / 1e3).toFixed(1)}K`;
  return `${sign}₹${r.toFixed(0)}`;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
function fdate(v) {
  if (!v) return "";
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(v);
  const d = `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}`;
  return m[4] ? `${d}, ${m[4]}:${m[5]}` : d;
}
const num = (n) => Number(n || 0).toLocaleString("en-IN");

// ---- words -------------------------------------------------------------------------------------

const KIND = {
  loss: { label: "Money lost", why: "Money that left your business and won't come back on its own." },
  customer: { label: "Owed to a customer", why: "A customer is owed money; left alone it becomes a complaint or a chargeback." },
  books: { label: "Fix in your books", why: "The money is fine but your books are wrong, so your sales, GST and P&L are off." },
  risk: { label: "Worth a look", why: "Not missing money, a pattern a finance team usually wants to know about." },
};
// One line on why each problem matters, in plain words.
const WHY = {
  settlement_missing: "Razorpay paid this out, but it never arrived in your bank. Until it's traced, the money is effectively lost.",
  settlement_short: "Your bank received less than Razorpay paid out. The gap is money you should have.",
  settlement_over: "Your bank received more than Razorpay paid out, often a double posting the bank will reverse later.",
  settlement_reversed: "The bank took back money it had credited. It's gone from your account until someone explains why.",
  paid_order_no_payment: "You treated this order as paid and probably shipped it, but no money ever came in.",
  duplicate_refund: "The customer was refunded twice. The second refund is your money.",
  refund_fee: "Razorpay normally doesn't charge to send a refund. This fee is worth questioning.",
  chargeback: "The customer's bank pulled the money back. You can often contest it within a deadline.",
  fee_overcharge: "Small fee differences on every payment add up quietly over a year.",
  rate_misclassified: "Debit cards cost less than credit cards. Charging the credit rate on debit cards leaks margin on every sale.",
  gst_mismatch: "GST on fees should be exactly 18%. Anything else affects your input tax credit.",
  unexplained_adjustment: "Razorpay took money out with no reason given. Ask them what it was for.",
  unexplained_adjustment_credit: "Razorpay added money with no reason. It may be reversed later, so don't count it as yours yet.",
  duplicate_payment: "The customer paid twice for one order. They're owed a refund.",
  books_refund_not_processed: "Your books say this customer was refunded, but no refund went out. They're still waiting.",
  refund_mismatch: "The refund at Razorpay and the refund in your books don't agree, so one of them is wrong.",
  ghost_success: "The customer paid, but your store never got the confirmation, so the order may never be shipped.",
  orphan_payment: "Money came in with no matching order, so this sale is missing from your books.",
  refund_not_in_books: "Razorpay refunded this order but your books still count it as a sale.",
  amount_mismatch: "Razorpay collected a different amount than the order says, often a discount nobody recorded.",
  refund_spike: "Far more refunds than a normal day can mean a product, delivery or fraud problem.",
  repeat_refunder: "One customer getting refund after refund can be a service problem, or abuse.",
  method_refund_rate: "One payment method being refunded far more often is worth checking with Razorpay.",
};
const HELP = {
  "Money lost": "<b>Money lost</b>: money that left your business and won't come back unless someone acts: missing settlements, double refunds, overcharged fees, chargebacks.",
  "Owed to customers": "<b>Owed to customers</b>: refunds your books promised but that never went out, or customers charged twice.",
  "Book corrections": "<b>Book corrections</b>: the money is fine, but your books are wrong, so sales, GST or your P&L need a correcting entry.",
  "Auto-reconciled": "<b>Auto-reconciled</b>: the share of rows in your three files that Settle matched completely on its own, with no issue raised.",
  Unexplained: "<b>Unexplained</b>: money we couldn't account for. <b>₹0.00</b> means every rupee lands on a named line: a fee, a timing difference or an issue listed here.",
  Settlement: "<b>Settlement</b>: one payout from Razorpay to your bank. It bundles a day's payments, minus refunds, fees and chargebacks.",
  UTR: "<b>UTR</b>: the bank's reference number for a transfer. It's how a settlement is matched to a line on your bank statement.",
  Chargeback: "<b>Chargeback</b>: the customer's bank pulled a payment back, usually after a dispute. You can often contest it.",
  "Reconciled by value": "<b>Reconciled by value</b>: rupees of settled money that the bank credited exactly, out of everything Razorpay settled this month. Money still in transit isn't counted until it lands.",
  BRS: "<b>Bank Reconciliation Statement (BRS)</b>: the standard statement that starts from one balance (Razorpay's settlements), lists every difference, and ends at the balance as per your bank.",
  "Settlement status": "<b>Status</b>: Matched means the bank credited exactly the settled amount. Short, Over, Missing and Reversed are problems; In transit will land next month; Needs a person means it fits more than one bank line.",
  Timing: "<b>Timing</b>: normal differences that sort themselves out, like orders at month end that settle next month.",
};
for (const k of Object.keys(KIND)) HELP[`kind-${k}`] = `<b>${KIND[k].label}</b>: ${KIND[k].why}`;
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
const ACTOR = { engine: "Settle (code)", ai: "AI", human: "A person", rule: "Your rule" };
const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
};

// ---- help tooltips -----------------------------------------------------------------------------

const tip = $("#tip");
function helpBtn(term) {
  return el("button", { class: "help", type: "button", "data-help": term, "aria-label": `What does “${term}” mean?` }, "?");
}
function showTip(target) {
  const html = HELP[target.dataset.help];
  if (!html) return;
  tip.innerHTML = html; // our own fixed strings, never file text
  tip.hidden = false;
  const r = target.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
  const top = r.bottom + 8 + h > innerHeight ? r.top - h - 8 : r.bottom + 8;
  Object.assign(tip.style, { left: `${left}px`, top: `${top}px` });
}
function hideTip() { tip.hidden = true; }
document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-help]"); if (t) showTip(t); });
document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-help]")) hideTip(); });
document.addEventListener("focusin", (e) => { const t = e.target.closest?.("[data-help]"); if (t) showTip(t); else hideTip(); });
document.addEventListener("click", (e) => { const t = e.target.closest(".help"); if (t) { e.preventDefault(); tip.hidden ? showTip(t) : hideTip(); } });
addEventListener("scroll", hideTip, true);

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
// A toast at the top right. `fixes` are buttons that take the person straight to the fix.
function notice(kind, title, text = "", retry = null, fixes = []) {
  const box = el("div", { class: `banner ${kind}`, role: kind === "err" ? "alert" : "status" },
    el("div", {}, el("b", { text: title }), text,
      fixes.length || retry ? el("div", { class: "inline", style: "margin-top:8px" },
        retry ? el("button", { class: "btn small", type: "button", onclick: () => { box.remove(); retry(); } }, "Try again") : null,
        ...fixes.map(([label, fn]) => el("button", { class: "btn small", type: "button", onclick: () => { box.remove(); fn(); } }, label))) : null),
    el("button", { class: "btn ghost small", type: "button", "aria-label": "Dismiss", onclick: () => box.remove() }, "✕"));
  put(alerts, box);
  if (kind !== "err") setTimeout(() => box.remove(), 9000);
  return box;
}
function fail(err, retry, fixes = []) {
  notice("err", err.error || "Something went wrong.", err.hint || "", err.network || err.status >= 500 ? retry : null, fixes);
}

let loadingTimer = null;
function loading(steps, sub = "This takes a few seconds.") {
  clearInterval(loadingTimer);
  const box = $("#loading");
  if (!steps) { box.hidden = true; return; }
  const list = Array.isArray(steps) ? steps : [steps];
  let i = 0;
  $("#loading-text").textContent = list[0];
  $("#loading-sub").textContent = sub;
  box.hidden = false;
  if (list.length > 1) loadingTimer = setInterval(() => { i = Math.min(i + 1, list.length - 1); $("#loading-text").textContent = list[i]; }, 2400);
}

// ---- views -------------------------------------------------------------------------------------

const views = ["landing", "upload", "review", "connections", "results"];
function show(name) {
  for (const v of views) $("#" + v).hidden = v !== name;
  window.scrollTo({ top: 0 });
}

const state = { view: null, filter: "all", page: "overview", query: "", asked: [], sets: null, setFilter: "all", setQuery: "", synced: null };

async function loadSample() {
  loading(["Loading the sample month…", "Matching about 3,700 rows across your three files…", "Building the bridge from books to bank…"]);
  try { state.synced = null; state.page = "overview"; render(await api("POST", "/api/sample")); put(alerts); }
  catch (e) { fail(e, loadSample); }
  finally { loading(false); }
}

// Demo of the live loop: the same sample month, presented as pulled from all three connections.
async function demoSync() {
  loading(["Pulling settlements from the payment gateway (sample data)…", "Pulling the bank statement (sample data)…", "Pulling orders from your books (sample data)…", "Reconciling all three…"]);
  try {
    const v = await api("POST", "/api/sample");
    state.synced = new Date(); state.page = "overview"; render(v); put(alerts);
    notice("info", "Demo sync done", "This is the sample month, loaded as if pulled from your gateway, bank and books. Live mode would do this every morning.");
  } catch (e) { fail(e, demoSync); }
  finally { loading(false); }
}

function openConnections() {
  const when = state.synced ? `Last synced: ${fdate(state.synced.toISOString().slice(0, 10))}, ${state.synced.toTimeString().slice(0, 5)} (demo, sample data)` : "Last synced: never";
  for (const s of $$("#connections .conn-synced")) s.textContent = when;
  for (const s of $$("#connections .conn-status")) s.textContent = state.synced ? "✓ Demo sync done" : "Demo: sample data";
  show("connections");
  $("#conn-h").focus?.();
}
async function loadChaos() {
  loading(["Making a new random month with hidden problems…", "Reconciling it…", "Checking against the answer key…"], "Settle hasn't seen this month before.");
  try {
    const v = await api("POST", "/api/chaos", {});
    state.page = "overview";
    render(v);
    notice("info", `New random month #${v.seed}`, "The answer key is at the top of the overview, so you can check every hidden problem was found.");
  } catch (e) { fail(e, loadChaos); }
  finally { loading(false); }
}

// ---- progress: the Start here checklist --------------------------------------------------------

const STEPS = [
  ["open", "Review the biggest issue", "Open it to see why it happened and the proof from your files."],
  ["decide", "Approve a fix, or mark it as expected", "Fixes wait for a named person. In this demo nothing is sent."],
  ["report", "Download the audit report", "Everything you decided, with the proof, ready for your auditor."],
];
function progressKey() { const v = state.view; return v ? `settle_progress_${v.source}_${v.seed ?? ""}` : "settle_progress"; }
function progress() { try { return JSON.parse(store.get(progressKey()) || "{}"); } catch { return {}; } }
function markStep(k) {
  const p = progress();
  if (p[k]) return;
  p[k] = true;
  store.set(progressKey(), JSON.stringify(p));
  const card = $("#start-here");
  if (card) card.replaceWith(startCard());
}
function startCard() {
  const p = progress();
  const done = STEPS.filter(([k]) => p[k]).length;
  const biggest = issues()[0];
  const actions = {
    open: biggest ? el("button", { class: "btn primary small", type: "button", onclick: (e) => openFinding(biggest.id, e.currentTarget) }, "Open the biggest issue") : null,
    decide: el("button", { class: "btn small", type: "button", onclick: () => (biggest ? openFinding(biggest.id, null, "fix") : go("issues")) }, "Go to the fix"),
    report: el("a", { class: "btn small", href: "/api/report", target: "_blank", rel: "noopener", onclick: () => markStep("report") }, "Open the audit report"),
  };
  return el("section", { class: "card start", id: "start-here", "aria-labelledby": "start-h" },
    el("div", { class: "card-head" },
      el("div", {}, el("h2", { id: "start-h" }, done === STEPS.length ? "You've done the month-end loop" : "Start here"),
        el("p", { class: "small muted" }, done === STEPS.length ? "That's the whole flow. Try a random month to see Settle on data it has never seen." : `Three steps to close the month. ${done} of 3 done.`)),
      el("button", { class: "btn ghost small", id: "tour-start-card", type: "button", onclick: () => startTour() }, "Take the 1-minute tour")),
    el("ol", {}, ...STEPS.map(([k, title, text], i) => el("li", { class: p[k] ? "done" : "", "data-step": k },
      el("span", { class: "tick", "aria-hidden": "true" }, p[k] ? "✓" : String(i + 1)),
      el("div", {}, el("b", {}, title), el("span", { class: "sr" }, p[k] ? " (done)" : " (to do)"), el("p", {}, text), p[k] ? null : actions[k])))));
}

// ---- the app -----------------------------------------------------------------------------------

function issues() {
  return (state.view?.findings || []).filter((f) => f.kind !== "risk").sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
}

const PAGES = [
  ["overview", "Overview", "◧"],
  ["settlements", "Settlements", "⇆"],
  ["issues", "Issues to fix", "!"],
  ["flow", "How the money flows", "⇄"],
  ["patterns", "Patterns & things to check", "◇"],
  ["ask", "Ask a question", "?"],
  ["audit", "Approvals & audit trail", "✓"],
  ["rules", "Saved rules", "≡"],
];

function render(v) {
  state.view = v;
  state.sets = null; // payout list is fetched when the Settlements page is first opened
  const root = $("#results");
  const t = v.totals;
  const risks = v.findings.filter((f) => f.kind === "risk").length + (v.exceptions?.length || 0) + (v.unmatched_credits?.length || 0) + (v.suspicious?.length || 0);
  const counts = { issues: issues().length, patterns: risks, rules: v.rules?.length || 0 };

  const nav = el("aside", { class: "side-nav", "aria-label": "Sections" },
    el("a", { class: "brand", href: "/", id: "home", "aria-label": "Settle home", onclick: (e) => { e.preventDefault(); go("overview"); } }, el("span", { class: "logo", "aria-hidden": "true" }), "Settle"),
    el("nav", {}, ...PAGES.map(([k, label, ico]) =>
      el("button", { class: "navbtn", type: "button", id: `nav-${k}`, "data-page": k, "aria-current": state.page === k ? "page" : null, onclick: () => go(k) },
        el("span", { class: "ico", "aria-hidden": "true" }, ico), label,
        counts[k] ? el("span", { class: k === "issues" ? "count" : "count calm", "aria-label": `${counts[k]} items` }, counts[k]) : null))),
    el("div", { class: "side-foot" },
      el("button", { class: "navbtn", type: "button", id: "nav-connect", onclick: openConnections }, el("span", { class: "ico", "aria-hidden": "true" }, "⚡"), "Connect your sources"),
      el("span", { class: "live" }, v.demo_mode ? "Demo mode: nothing is sent" : "Live")));

  const month = v.period?.start ? `${MONTH_LONG[+String(v.period.start).slice(5, 7) - 1]} ${String(v.period.start).slice(0, 4)}` : "";
  const source = state.synced ? "Demo sync (sample data)" : v.source === "sample" ? "Sample month" : v.source === "chaos" ? `Random month #${v.seed ?? ""}` : "Your files";
  const top = el("header", { class: "topbar" }, el("div", { class: "in" },
    el("span", { class: "period", title: `${fdate(v.period.start)} to ${fdate(v.period.end)}` }, month),
    el("b", {}, v.merchant || "Your business"),
    el("span", { class: "source", id: "source-chip" }, source),
    el("span", { class: "grow" }),
    el("button", { class: "btn ghost small", id: "tour-start", type: "button", "aria-label": "Take the tour", onclick: () => startTour() }, "?", el("span", { class: "long" }, " Take the tour")),
    el("button", { class: "btn small", id: "nav-chaos", type: "button", title: "A new random month with hidden problems and an answer key", onclick: loadChaos }, "Inject chaos"),
    el("details", { class: "menu", id: "export-menu" },
      el("summary", { class: "btn small", role: "button" }, "Export ▾"),
      el("div", { class: "menu-list" },
        el("a", { href: "/api/report", target: "_blank", rel: "noopener", onclick: () => markStep("report") }, "Audit report", el("span", {}, "Opens in a new tab, prints to PDF")),
        el("a", { href: "/api/export", onclick: () => markStep("report") }, "Excel pack (.zip)", el("span", {}, "Issues, bridges and journals as CSV")),
        el("a", { href: "/api/brs", onclick: () => markStep("report") }, "Bank Reconciliation Statement (.csv)", el("span", {}, "Razorpay balance to bank balance, line by line")),
        el("a", { href: "/api/journal", onclick: () => markStep("report") }, "Tally journal (.csv)", el("span", {}, "Balanced entries ready to import")))),
    el("button", { class: "btn primary small", id: "nav-new", type: "button", "aria-label": "New reconciliation", onclick: () => { put(alerts); show("landing"); } }, "New", el("span", { class: "long" }, " reconciliation"))));

  const pages = {
    overview: overviewPage(v, t),
    settlements: settlementsPage(),
    issues: issuesPage(v),
    flow: flowPage(v),
    patterns: patternsPage(v),
    ask: askPage(v),
    audit: auditPage(),
    rules: rulesPage(v),
  };
  for (const [k, p] of Object.entries(pages)) { p.id = `page-${k}`; p.classList.add("page"); p.hidden = k !== state.page; }

  put(root, nav, el("div", { class: "app" }, top, ...Object.values(pages)));
  show("results");
  if (state.page === "audit") refreshAudit();
  if (state.page === "settlements") refreshSettlements();
}

function go(page) {
  state.page = page;
  for (const p of $$("#results .page")) p.hidden = p.id !== `page-${page}`;
  for (const b of $$("#results .navbtn[data-page]")) {
    if (b.dataset.page === page) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  if (page === "audit") refreshAudit();
  if (page === "settlements") refreshSettlements();
  window.scrollTo({ top: 0 });
}

function pageHead(title, text, ...actions) {
  return el("div", { class: "page-head" },
    el("div", {}, el("h1", {}, title), el("p", {}, text)),
    actions.length ? el("div", { class: "inline" }, ...actions) : null);
}

// ---- overview ----------------------------------------------------------------------------------

function overviewPage(v, t) {
  const auto = t.auto_reconciled || {};
  const val = t.value_reconciled || {};
  const tied = t.unexplained === 0;
  const biggest = issues()[0];
  const byKind = (kind) => v.findings.filter((f) => f.kind === kind);
  const seg = (kind) => {
    const fs = byKind(kind), total = fs.reduce((s, f) => s + Math.abs(f.amount), 0) || 1;
    return el("div", { class: "segbar", "aria-hidden": "true" }, ...fs.map((f) => el("i", { style: `width:${(100 * Math.abs(f.amount)) / total}%`, title: f.title })));
  };
  const tile = (cls, label, value, sub, extra, kpi) => el("div", { class: cls, "data-kpi": kpi },
    el("div", {}, el("span", { class: "label" }, label), helpBtn(label)),
    el("span", { class: "fig" }, value),
    sub ? el("span", { class: "sub" }, sub) : null, extra);
  const statement = el("div", { class: "statement", role: "group", "aria-label": "Summary of the month" },
    tile("loss", "Money lost", rupees(t.loss), `${byKind("loss").length} issues`, seg("loss"), "loss"),
    tile("customer", "Owed to customers", rupees(t.customer), `${byKind("customer").length} customers`, seg("customer"), "customer"),
    tile("books", "Book corrections", rupees(t.books), `${byKind("books").length} entries to fix`, seg("books"), "books"),
    tile("", "Reconciled by value", `${val.pct}%`, `${short(val.amount)} of ${short(val.of)} settled · ${auto.pct}% of rows auto-matched`,
      el("div", { class: "progress", role: "progressbar", "aria-valuenow": val.pct, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": "Reconciled by value" }, el("i", { style: `width:${val.pct}%` })), "value"),
    tile(tied ? "tied" : "tied bad", "Unexplained", rupees(t.unexplained), tied ? "✓ Every rupee is on a named line" : "Needs a look", null, "unexplained"));

  const r = v.roi;
  const roi = el("details", { class: "roi" },
    el("summary", {}, "Found ", el("b", {}, rupees(r.leakage_found)), ` · saves about ${r.hours_saved} hours of ticking by hand · AI cost so far ${rupees(r.ai_cost)}${v.source === "sample" && !r.ai_cost ? " (explanations cached for the sample)" : ""}. How these are worked out`),
    el("p", { class: "formula" }, "Money found: ", r.formulas.leakage_found),
    el("p", { class: "formula" }, "Hours saved: ", r.formulas.hours_saved),
    el("p", { class: "formula" }, "AI cost: ", r.formulas.ai_cost));

  const top5 = issues().slice(0, 5);
  const topCard = el("section", { class: "card flush", "aria-labelledby": "top-h" },
    el("div", { class: "card-head", style: "padding:16px 18px 0" },
      el("div", {}, el("h2", { id: "top-h" }, "Biggest issues"), el("p", { class: "small muted" }, "Sorted by amount. Open one to see why it happened and how to fix it.")),
      el("button", { class: "btn small", type: "button", onclick: () => go("issues") }, `See all ${issues().length} issues`)),
    el("div", { class: "tablewrap" }, el("table", { class: "grid" },
      el("tbody", {}, ...top5.map((f) => el("tr", { class: "link", onclick: (e) => openFinding(f.id, e.currentTarget.querySelector("button")) },
        el("td", {}, el("span", { class: `kind ${f.kind}` }, KIND[f.kind].label)),
        el("td", {}, el("button", { class: "entry-mini linkish", type: "button", style: "text-decoration:none;color:var(--ink);font-weight:500", onclick: (e) => { e.stopPropagation(); openFinding(f.id, e.currentTarget); } }, f.title)),
        el("td", { class: "num" }, rupees(f.amount))))))));

  const banners = [];
  if (!v.ai.available) banners.push(el("div", { class: "banner warn" }, el("div", {}, el("b", { text: "The AI is off right now." }), "Every amount here is still exact. Explanations are written from templates and Ask is unavailable.")));
  if (v.suspicious?.length) banners.push(el("div", { class: "banner warn" }, el("div", {}, el("b", { text: `${v.suspicious.length} record${v.suspicious.length > 1 ? "s contain" : " contains"} text that tries to instruct an AI.` }), "Settle treats it as data and changed nothing. See Patterns & things to check.")));

  const page = el("div", {},
    pageHead("Overview", "Where the month stands: what went missing, what you owe, and whether every rupee ties out.",
      biggest ? el("button", { class: "btn primary", type: "button", onclick: (e) => openFinding(biggest.id, e.currentTarget) }, "Review the biggest issue") : null),
    el("div", { class: "stack" },
      ...banners,
      v.chaos ? chaosPanel(v) : null,
      startCard(),
      statement, roi,
      waterfallCard(v),
      topCard));
  return page;
}

// Books → Razorpay → Bank, grouped so a person can read it: totals, normal costs, timing, problems.
function waterfallCard(v) {
  const a = v.bridges.books_to_razorpay, b = v.bridges.razorpay_to_bank;
  const group = (steps, name, kinds) => {
    const items = steps.filter((s) => kinds.includes(s.kind) && s.amount);
    return items.length ? { name, kind: kinds[0], amount: items.reduce((x, s) => x + s.amount, 0), items } : null;
  };
  const middle = (steps) => [
    group(steps, "Fees, GST and other normal costs", ["normal"]),
    group(steps, "Timing (sorts itself out)", ["timing"]),
    group(steps.filter((s) => s.amount < 0), "Problems: money short", ["finding"]),
    group(steps.filter((s) => s.amount > 0), "Problems: books short", ["finding"]),
    group(steps, "Needs a person / your rules", ["exception", "resolved"]),
  ].filter(Boolean);
  const bars = [
    { name: "Net sales in your books", kind: "total", amount: a[0].amount },
    ...middle(a),
    { name: "Net settled by Razorpay", kind: "total", amount: a[a.length - 1].amount },
    ...middle(b),
    { name: "Received in your bank", kind: "total", amount: b[b.length - 1].amount },
  ];
  let run = 0;
  for (const x of bars) {
    if (x.kind === "total") { x.from = 0; x.to = x.amount; run = x.amount; }
    else { x.from = run; x.to = run + x.amount; run = x.to; }
  }
  const W = 960, H = 300, padL = 8, padR = 8, padT = 28, padB = 64;
  const tops = bars.flatMap((x) => [x.from || x.to, x.to]).filter((x) => x);
  // The totals dwarf the deltas, so the axis starts just under the smallest running total, with a
  // visible break on the total bars so it never pretends the bars start at zero.
  const lo = Math.min(...tops) * 0.965, hi = Math.max(...tops) * 1.004;
  const y = (val) => padT + (1 - (Math.max(val, lo) - lo) / (hi - lo)) * (H - padT - padB);
  const slot = (W - padL - padR) / bars.length, bw = Math.min(64, slot * 0.62);
  const fill = { total: "var(--m-total)", normal: "var(--m-normal)", timing: "var(--m-timing)", finding: "var(--m-loss)", exception: "#f79009" };
  const g = svg("svg", { class: "wf", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Waterfall from net sales ${rupees(bars[0].amount)} to received in bank ${rupees(bars[bars.length - 1].amount)}; unexplained ${rupees(v.totals.unexplained)}` });
  g.append(svg("line", { class: "grid-line", x1: 0, x2: W, y1: H - padB, y2: H - padB }));
  bars.forEach((x, i) => {
    const cx = padL + slot * i + slot / 2, top = y(Math.max(x.from, x.to)), bot = x.kind === "total" ? H - padB : y(Math.min(x.from, x.to));
    const h = Math.max(2, bot - top);
    const grp = svg("g", { class: "bar", tabindex: "-1" },
      svg("rect", { x: cx - bw / 2, y: top, width: bw, height: h, rx: 4, fill: fill[x.kind] || fill.normal }));
    if (x.kind === "total") {
      grp.append(svg("text", { class: "val", x: cx, y: top - 8, "text-anchor": "middle" }, short(x.amount)));
      // axis break marker near the base
      grp.append(svg("path", { d: `M${cx - bw / 2 - 2} ${H - padB - 14} l${bw / 4} -5 l${bw / 4} 5 l${bw / 4} -5 l${bw / 4 + 4} 5`, stroke: "var(--canvas)", "stroke-width": 3, fill: "none" }));
    } else {
      grp.append(svg("text", { x: cx, y: top - 8, "text-anchor": "middle" }, short(x.amount)));
    }
    const words = x.name.split(" "), lines = [];
    for (const w of words) { const l = lines[lines.length - 1]; if (l && (l + " " + w).length <= 14) lines[lines.length - 1] = l + " " + w; else lines.push(w); }
    lines.slice(0, 3).forEach((l, j) => grp.append(svg("text", { x: cx, y: H - padB + 16 + j * 13, "text-anchor": "middle" }, l)));
    const tipText = [x.name, rupees(x.amount), ...(x.items || []).slice(0, 6).map((s) => `${s.label}: ${rupees(s.amount)}`), (x.items || []).length > 6 ? `…and ${x.items.length - 6} more` : ""].filter(Boolean);
    const hit = svg("rect", { class: "hit", x: cx - slot / 2, y: padT - 20, width: slot, height: H - padT });
    hit.addEventListener("mousemove", (e) => { tip.textContent = ""; tipText.forEach((t, k) => tip.append(k ? el("div", {}, t) : el("b", {}, t))); tip.hidden = false; Object.assign(tip.style, { left: `${Math.min(e.clientX + 14, innerWidth - 320)}px`, top: `${e.clientY + 14}px` }); });
    hit.addEventListener("mouseleave", hideTip);
    grp.append(hit);
    g.append(grp);
  });
  const res = v.totals.unexplained;
  return el("section", { class: "card", "aria-labelledby": "wf-h" },
    el("div", { class: "card-head" },
      el("div", {}, el("h2", { id: "wf-h" }, "From your books to your bank"), el("p", { class: "small muted" }, "Each bar explains part of the gap. Hover a bar to see the lines inside it.")),
      el("span", { class: res === 0 ? "badge ok" : "badge warn" }, res === 0 ? `✓ Unexplained ${rupees(res)}` : `Unexplained ${rupees(res)}`, helpBtn("Unexplained"))),
    g,
    el("div", { class: "legend" },
      el("span", {}, el("i", { style: "background:var(--m-total)" }), "Totals"),
      el("span", {}, el("i", { style: "background:var(--m-normal)" }), "Normal costs"),
      el("span", {}, el("i", { style: "background:var(--m-timing)" }), "Timing", helpBtn("Timing")),
      el("span", {}, el("i", { style: "background:var(--m-loss)" }), "Problems found"),
      el("span", { class: "muted" }, `Axis starts at ${short(lo)}, not zero, so the smaller bars are visible.`)),
    el("p", { class: "small", style: "margin:10px 0 0" }, el("button", { class: "linkish", type: "button", onclick: () => go("flow") }, "See every line of both bridges")));
}

function chaosPanel(v) {
  const key = v.chaos;
  const planted = key.filter((k) => k.found !== null);
  const found = planted.filter((k) => k.found).length;
  const extra = key.filter((k) => k.found === null).length;
  return el("section", { class: "card", id: "answer-key", "aria-labelledby": "key-h" },
    el("div", { class: "card-head" },
      el("div", {}, el("h2", { id: "key-h" }, "Answer key for this random month"),
        el("p", { class: "small" }, el("b", {}, `Found ${found} of ${planted.length} hidden problems`), extra ? `, plus ${extra} it flagged that weren't planted.` : ", and flagged nothing else.")),
      el("button", { class: "btn small", type: "button", onclick: loadChaos }, "Try another random month")),
    el("ul", { class: "answerkey" }, ...key.map((k) =>
      el("li", {},
        el("span", { class: k.found ? "y" : k.found === false ? "n" : "x", "aria-label": k.found ? "found" : k.found === false ? "missed" : "extra" }, k.found ? "✓" : k.found === false ? "✗" : "?"),
        el("span", {}, k.title, el("span", { class: "small muted" }, ` ${fdate(k.ref)}`),
          k.found === null ? el("div", { class: "small muted" }, "Flagged but not in the answer key") : null),
        el("span", { class: "num" }, rupees(k.amount))))));
}

// ---- issues ------------------------------------------------------------------------------------

function issuesPage(v) {
  // Patterns ("worth a look") live on their own page, so this list and the sidebar badge agree.
  const all = issues();
  const counts = { all: all.length };
  for (const f of all) counts[f.kind] = (counts[f.kind] || 0) + 1;
  const body = el("tbody");
  const empty = el("tr", { hidden: true }, el("td", { colspan: 4 }, el("div", { class: "empty" }, el("b", {}, "Nothing matches."), "Try a different filter or clear the search.")));
  const apply = () => {
    const q = state.query.trim().toLowerCase();
    let shown = 0;
    for (const tr of $$("tr[data-kind]", body)) {
      const ok = (state.filter === "all" || tr.dataset.kind === state.filter) && (!q || tr.dataset.text.includes(q));
      tr.hidden = !ok; shown += ok;
    }
    empty.hidden = shown > 0;
  };
  for (const f of all) {
    const k = KIND[f.kind] || KIND.books;
    const btn = el("button", { class: "entry", type: "button", "aria-label": `${k.label}: ${f.title}, ${rupees(f.amount)}`, onclick: (e) => { e.stopPropagation(); openFinding(f.id, e.currentTarget); } },
      f.title, f.count ? ` (${f.count} payments)` : "", f.headline ? el("span", { class: "h" }, f.headline) : null);
    body.append(el("tr", { class: "link", "data-kind": f.kind, "data-text": `${f.title} ${f.headline} ${f.ref} ${rupees(f.amount)} ${f.contact}`.toLowerCase(), onclick: () => openFinding(f.id, btn) },
      el("td", {}, el("span", { class: `kind ${f.kind}`, "data-help": `kind-${f.kind}` }, k.label)),
      el("td", {}, btn),
      el("td", { class: "who" }, el("div", { class: "small" }, f.contact || ""), el("div", { class: "ref" }, f.ref && /^\d{4}-\d{2}-\d{2}$/.test(f.ref) ? fdate(f.ref) : f.ref)),
      el("td", { class: "num" }, el("b", {}, rupees(f.amount)))));
  }
  body.append(empty);
  const filters = el("div", { class: "inline", role: "group", "aria-label": "Show" },
    ...[["all", "All"], ["loss", "Money lost"], ["customer", "Owed to customers"], ["books", "Fix in your books"]].map(([k, label]) =>
      el("button", { class: "chip", type: "button", "aria-pressed": String(state.filter === k), onclick: (e) => { state.filter = k; $$(".chip", filters).forEach((c) => c.setAttribute("aria-pressed", String(c === e.currentTarget))); apply(); } },
        label, el("span", { class: "n" }, counts[k] || 0))));
  const search = el("input", { type: "search", id: "issue-search", placeholder: "Search by order no, UTR, amount…", "aria-label": "Search issues", value: state.query, oninput: (e) => { state.query = e.target.value; apply(); } });
  setTimeout(apply);
  const legend = el("div", { class: "legend small", style: "margin:0" }, "What the labels mean:",
    ...["loss", "customer", "books"].map((k) => el("span", {}, el("span", { class: `kind ${k}` }, KIND[k].label), el("button", { class: "help", type: "button", "aria-label": `What does “${KIND[k].label}” mean?`, "data-help": `kind-${k}` }, "?"))));
  return el("div", {},
    pageHead("Issues to fix", "Everything Settle found, biggest first. Open a line to see why it happened, the proof from your files, and the fix waiting for your approval."),
    el("div", { class: "stack" },
      el("div", { class: "card", style: "padding:12px 14px" }, el("div", { class: "inline" }, filters, el("span", { class: "grow" }), el("div", { style: "min-width:260px;flex:1;max-width:360px" }, search)), el("div", { style: "margin-top:10px" }, legend)),
      el("div", { class: "card flush" }, el("div", { class: "tablewrap" }, el("table", { class: "grid issues", id: "problem-list" },
        el("thead", {}, el("tr", {}, el("th", {}, "Type"), el("th", {}, "Issue"), el("th", {}, "Who to contact"), el("th", { class: "num" }, "Amount"))),
        body)))));
}

// ---- settlements ------------------------------------------------------------------------------

const SET_STATUS = {
  matched: ["ok", "✓ Matched"], short: ["loss", "Short"], over: ["books", "Over"], missing: ["loss", "Missing"],
  reversed: ["loss", "Reversed"], in_transit: ["timing", "In transit"], needs_person: ["risk", "Needs a person"],
};
const MATCHED_BY = { utr: "UTR", amount_date: "Amount + date", split: "Split credit" };

function settlementsPage() {
  const body = el("tbody", { id: "settlement-rows" }, el("tr", {}, el("td", { colspan: 9 }, el("div", { class: "skeleton" }), el("div", { class: "skeleton", style: "width:70%" }))));
  return el("div", {},
    pageHead("Settlements", "Every payout from your payment gateway (Razorpay) this month, one line each: what was settled, what reached your bank, and how Settle matched them. Click a line to see inside it."),
    el("div", { class: "stack" },
      el("div", { class: "card", style: "padding:12px 14px" }, el("div", { class: "inline", id: "set-filters" }), el("div", { class: "small muted", style: "margin-top:8px" }, "Status explained ", helpBtn("Settlement status"))),
      el("div", { class: "card flush" }, el("div", { class: "tablewrap" }, el("table", { class: "grid sets", id: "settlement-list" },
        el("thead", {}, el("tr", {}, ...["Settled on", "Settlement", "Items", "Net settled", "Received in bank", "Bank date", "Difference", "How it matched", "Status"].map((h, i) => el("th", { class: [2, 3, 4, 6].includes(i) ? "num" : null }, h)))),
        body, el("tfoot", { id: "settlement-total" }))))));
}

async function refreshSettlements() {
  const body = $("#settlement-rows");
  if (!body) return;
  if (!state.sets) {
    try { state.sets = await api("GET", "/api/settlements"); } catch (e) {
      put(body, el("tr", {}, el("td", { colspan: 9 }, el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint, " "), el("button", { class: "btn small", type: "button", onclick: () => { state.sets = null; refreshSettlements(); } }, "Try again")))));
      return;
    }
  }
  const { settlements: rows, totals } = state.sets;
  const counts = { all: rows.length };
  for (const s of rows) counts[s.status] = (counts[s.status] || 0) + 1;
  const trs = rows.map((s) => {
    const [cls, label] = SET_STATUS[s.status];
    const open = (e) => openSettlement(s, e.currentTarget.closest("tr").querySelector("button"));
    return el("tr", { class: "link", "data-status": s.status, "data-text": `${s.settlement_id} ${s.utr || ""} ${rupees(s.net)} ${rupees(s.received)}`.toLowerCase(), onclick: open },
      el("td", {}, fdate(s.settled_at).split(",")[0]),
      el("td", {}, el("button", { class: "entry-mini linkish mono", type: "button", "aria-label": `Settlement ${s.settlement_id}, ${label}`, onclick: (e) => { e.stopPropagation(); open(e); } }, s.settlement_id), el("div", { class: "ref" }, s.utr ? `UTR ${s.utr}` : "No UTR")),
      el("td", { class: "num" }, num(s.items)),
      el("td", { class: "num" }, rupees(s.net)),
      el("td", { class: "num" }, s.bank_rows.length ? rupees(s.received) : "—"),
      el("td", {}, s.bank_date ? fdate(s.bank_date) : "—"),
      el("td", { class: `num ${s.difference ? "neg" : ""}` }, s.difference ? rupees(s.difference) : "—"),
      el("td", {}, MATCHED_BY[s.matched_by] || "—"),
      el("td", {}, el("span", { class: `kind ${cls}` }, label)));
  });
  const empty = el("tr", { hidden: true }, el("td", { colspan: 9 }, el("div", { class: "empty" }, el("b", {}, "Nothing matches."), "Try a different status or clear the search.")));
  put(body, ...trs, empty);
  put($("#settlement-total"), el("tr", { class: "end" },
    el("td", { colspan: 2 }, el("b", {}, `Total, ${rows.length} settlements`)),
    el("td", { class: "num" }, num(totals.items)), el("td", { class: "num", id: "set-total-net" }, rupees(totals.net)),
    el("td", { class: "num", id: "set-total-received" }, rupees(totals.received)), el("td", {}),
    el("td", { class: "num" }, rupees(totals.difference)), el("td", { colspan: 2 })));
  const apply = () => {
    const q = state.setQuery.trim().toLowerCase();
    let shown = 0;
    for (const tr of $$("tr[data-status]", body)) {
      const ok = (state.setFilter === "all" || tr.dataset.status === state.setFilter) && (!q || tr.dataset.text.includes(q));
      tr.hidden = !ok; shown += ok;
    }
    empty.hidden = shown > 0;
  };
  const chips = ["all", ...Object.keys(SET_STATUS).filter((k) => counts[k])].map((k) =>
    el("button", { class: "chip", type: "button", "data-status": k, "aria-pressed": String(state.setFilter === k), onclick: (e) => { state.setFilter = k; $$("#set-filters .chip").forEach((c) => c.setAttribute("aria-pressed", String(c === e.currentTarget))); apply(); } },
      k === "all" ? "All" : SET_STATUS[k][1].replace("✓ ", ""), el("span", { class: "n" }, counts[k])));
  const search = el("input", { type: "search", id: "set-search", placeholder: "Search settlement, UTR or amount…", "aria-label": "Search settlements", value: state.setQuery, oninput: (e) => { state.setQuery = e.target.value; apply(); } });
  put($("#set-filters"), ...chips, el("span", { class: "grow" }), el("div", { style: "min-width:240px;flex:1;max-width:340px" }, search));
  apply();
}

const TYPE_LABEL = { payment: "Payments", fees: "Razorpay fees + GST", refund: "Refunds", dispute: "Chargebacks", adjustment: "Adjustments", transfer: "Transfers" };

function openSettlement(s, opener) {
  lastOpener = opener || document.activeElement;
  drawer.hidden = false; document.body.style.overflow = "hidden"; sheet.scrollTop = 0;
  const [cls, label] = SET_STATUS[s.status];
  const close = el("button", { class: "btn ghost small", type: "button", onclick: closeDrawer, "aria-label": "Close" }, "Close ✕");
  const order = ["payment", "fees", "refund", "dispute", "adjustment", "transfer"];
  const parts = Object.entries(s.by_type).sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  const credited = s.bank_rows.filter((b) => b.amount > 0);
  const step = (st, title, sub) => el("li", { class: st },
    el("span", { class: "dot", "aria-hidden": "true" }, { ok: "✓", missing: "✗", mismatch: "!", waiting: "…" }[st]),
    el("div", {}, el("div", { class: "st" }, { ok: "In place", missing: "Missing", mismatch: "Doesn't match", waiting: "Next month" }[st]),
      el("b", {}, title), el("div", { class: "small muted" }, sub)));
  const bankStep = s.bank_rows.length
    ? step(s.status === "matched" ? "ok" : "mismatch", "Bank credit", s.bank_rows.map((b) => `Bank row ${b.row_no}, ${fdate(b.date)}: ${rupees(b.amount)}`).join(" · ") + (MATCHED_BY[s.matched_by] ? ` · matched by ${MATCHED_BY[s.matched_by]}` : ""))
    : step(s.status === "in_transit" ? "waiting" : "missing", "Bank credit", s.status === "in_transit" ? "Not in this statement yet: settled on the last day, it lands next month." : s.why ? `Not matched: ${s.why}.` : "No credit for this settlement on your bank statement.");
  put(sheet,
    el("header", {},
      el("div", { class: "hd" },
        el("div", {}, el("span", { class: `kind ${cls}` }, label), el("h2", { id: "drawer-title" }, `Settlement ${s.settlement_id}`), el("div", { class: "bigamt" }, rupees(s.net))),
        close),
      el("p", { class: "why" }, `Settled ${fdate(s.settled_at)} · ${num(s.items)} items · ${s.utr ? `UTR ${s.utr}` : "no UTR on the report"}`)),
    el("div", { class: "body" },
      el("h3", {}, "Where the money went"),
      el("ol", { class: "chain" }, step("ok", "Razorpay settlement", `${rupees(s.net)} settled on ${fdate(s.settled_at)}`), bankStep),
      s.difference ? el("p", {}, el("b", {}, "Difference: "), rupees(s.difference), s.status === "in_transit" ? " (timing, not a loss)" : "") : null,
      el("h3", {}, "What's inside this payout"),
      el("table", { class: "grid book" }, el("tbody", {},
        ...parts.map(([k, amt]) => el("tr", {}, el("td", {}, TYPE_LABEL[k] || k), el("td", { class: "num" }, rupees(amt)))),
        el("tr", { class: "end" }, el("td", {}, "Net settled"), el("td", { class: "num" }, rupees(s.net))))),
      credited.length > 1 ? el("p", { class: "small muted" }, `Your bank paid this in ${credited.length} parts; Settle added them up.`) : null,
      s.finding_id ? el("div", { style: "margin-top:16px" }, el("button", { class: "btn primary", type: "button", onclick: () => openFinding(s.finding_id, lastOpener, "summary") }, "Open the issue for this payout"))
        : el("p", { class: "small muted", style: "margin-top:16px" }, s.status === "matched" ? "Nothing to do: the bank credited exactly what Razorpay settled." : "")));
  close.focus();
}

// ---- how the money flows -----------------------------------------------------------------------

// BRS wording for the Razorpay → bank bridge: the layout every Indian accountant files.
function brsLabel(s, i, steps) {
  if (i === 0) return "Balance as per Razorpay settlements";
  if (i === steps.length - 1) return "Balance as per bank";
  if (s.kind === "residual") return s.label;
  return `${s.amount < 0 ? "Less" : "Add"}: ${s.label}`;
}

function bridgeTable(title, steps, caption, brs = false, help = null) {
  const rows = steps.map((s, i) => {
    const cls = s.kind + (s.kind === "residual" && s.amount ? " bad" : "");
    const label = s.kind === "residual" ? (s.amount ? "Unexplained" : "Unexplained: every rupee is on a line above ✓") : brs ? brsLabel(s, i, steps) : s.label;
    return el("tr", { class: cls }, el("td", {}, label), el("td", { class: "num" }, s.count ? num(s.count) : ""), el("td", { class: "num" }, rupees(s.amount)));
  });
  return el("div", { class: "card flush" },
    el("div", { style: "padding:16px 18px 10px" }, el("h3", { id: brs ? "brs-h" : null }, title, help ? helpBtn(help) : null), el("p", { class: "small muted", style: "margin:2px 0 0" }, caption)),
    el("div", { class: "tablewrap" }, el("table", { class: "grid book" },
      el("thead", {}, el("tr", {}, el("th", {}, "Line"), el("th", { class: "num" }, "Items"), el("th", { class: "num" }, "Amount"))),
      el("tbody", {}, rows))));
}

function flowPage(v) {
  const resolved = v.resolved || [];
  return el("div", {},
    pageHead("How the money flows", "Two bridges, the way an auditor ties out a month: from your books to what your payment gateway (Razorpay) settled, then to what reached your bank. Red lines are problems, grey lines are timing.",
      el("a", { class: "btn primary", href: "/api/report", target: "_blank", rel: "noopener", onclick: () => markStep("report") }, "Download the audit report")),
    el("div", { class: "stack" },
      el("h2", { id: "bridges-h", class: "sr" }, "Both bridges"),
      el("div", { class: "cols", style: "grid-template-columns:1fr 1fr" },
        bridgeTable("Your books → payment gateway", v.bridges.books_to_razorpay, "From your net sales to what Razorpay settled."),
        bridgeTable("Bank Reconciliation Statement (BRS)", v.bridges.razorpay_to_bank, "Razorpay → your bank: from what Razorpay settled to the balance as per your bank.", true, "BRS")),
      el("section", { class: "card", "aria-labelledby": "handled-h" },
        el("h2", { id: "handled-h" }, "What we checked and left alone"),
        el("p", { class: "small muted" }, "These look like problems in a simple match but aren't. Shown so you can see what was checked."),
        el("ul", { class: "ticks" }, ...v.handled.map((h) => el("li", {}, el("span", {}, h.label), el("span", { class: "num" }, num(h.count))))),
        resolved.length ? el("div", { style: "margin-top:14px" }, el("h3", {}, "Marked as expected by you"),
          el("ul", { class: "ticks" }, ...resolved.map((r) => el("li", {}, el("span", {}, `${r.title}: “${r.note}”`), el("span", { class: "num" }, rupees(r.amount)))))) : null)));
}

// ---- patterns ----------------------------------------------------------------------------------

function refundChart(v) {
  const days = v.refunds_by_day || [];
  if (!days.length) return null;
  const spikes = new Set(v.findings.filter((f) => f.check === "refund_spike").map((f) => f.ref));
  const W = 960, H = 200, padB = 26, padT = 18, max = Math.max(...days.map((d) => d[1]));
  const slot = W / days.length, bw = Math.max(3, slot - 3);
  const g = svg("svg", { class: "wf", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Refunds per day; the busiest day had ${max}` });
  g.append(svg("line", { class: "grid-line", x1: 0, x2: W, y1: H - padB, y2: H - padB }));
  days.forEach(([d, n, amt], i) => {
    const h = Math.max(2, ((H - padB - padT) * n) / max), x = i * slot + 1.5;
    const spike = spikes.has(String(d).slice(0, 10));
    const rect = svg("rect", { x, y: H - padB - h, width: bw, height: h, rx: 3, fill: spike ? "var(--m-loss)" : "var(--m-bar)" });
    rect.addEventListener("mousemove", (e) => { tip.textContent = ""; tip.append(el("b", {}, fdate(d)), el("div", {}, `${n} refunds, ${rupees(amt)}`), spike ? el("div", {}, "Flagged as a spike") : ""); tip.hidden = false; Object.assign(tip.style, { left: `${Math.min(e.clientX + 14, innerWidth - 320)}px`, top: `${e.clientY + 14}px` }); });
    rect.addEventListener("mouseleave", hideTip);
    g.append(rect);
    if (spike) g.append(svg("text", { class: "val", x: x + bw / 2, y: H - padB - h - 6, "text-anchor": "middle" }, String(n)));
    if (i % 7 === 0) g.append(svg("text", { x: x + bw / 2, y: H - 8, "text-anchor": "middle" }, fdate(d).split(" ").slice(0, 2).join(" ")));
  });
  return el("section", { class: "card", "aria-labelledby": "rpd-h" },
    el("div", { class: "card-head" }, el("div", {}, el("h2", { id: "rpd-h" }, "Refunds per day"), el("p", { class: "small muted" }, "Red days were flagged as unusual against the month's normal day."))), g);
}

function patternsPage(v) {
  const risk = v.findings.filter((f) => f.kind === "risk");
  const cards = risk.map((f) => el("button", { class: "card pattern", type: "button", style: "text-align:left;cursor:pointer;font:inherit", onclick: (e) => openFinding(f.id, e.currentTarget) },
    el("span", { class: "kind risk" }, "Worth a look"), el("h3", { style: "margin:8px 0 4px" }, f.title),
    el("p", { class: "small muted" }, f.headline && f.headline !== f.title ? f.headline : WHY[f.check] || ""), el("b", {}, rupees(f.amount))));
  const susp = (v.suspicious || []).map((s) => el("li", {},
    el("span", {}, el("b", {}, s.title), el("div", { class: "small muted" }, `${sourceName(s.source)} row ${s.row_no}, ${s.field}: `, el("q", { text: s.text.replace(/<\/?data>/g, "") }), ". Treated as data; it changed nothing."))));
  const exc = [
    ...(v.exceptions || []).map((e) => `Settlement ${e.settlement_id} of ${rupees(e.amount)}, settled ${fdate(e.settled_on)}: ${e.why}. Check your bank statement for this credit.`),
    ...(v.unmatched_credits || []).map((u) => `Bank credit of ${rupees(u.credit)} on ${fdate(u.txn_date)} (bank row ${u.row_no}) mentions Razorpay but matches no settlement in this report. Most likely from another month.`),
  ];
  return el("div", {},
    pageHead("Patterns & things to check", "Not missing money: the amounts here match. These are patterns worth a look, and the few things Settle won't decide on its own."),
    el("div", { class: "stack" },
      refundChart(v),
      cards.length ? el("div", { class: "cols", style: "grid-template-columns:repeat(auto-fill,minmax(260px,1fr))" }, ...cards)
        : el("div", { class: "card empty" }, el("b", {}, "No unusual patterns this month."), "Refund spikes, repeat refunders and odd payment methods would show here."),
      susp.length ? el("section", { class: "card", "aria-labelledby": "susp-h" }, el("h2", { id: "susp-h" }, "Text that tries to instruct an AI"), el("ul", { class: "ticks" }, ...susp)) : null,
      el("section", { class: "card", "aria-labelledby": "exc-h" },
        el("h2", { id: "exc-h" }, "Couldn't match automatically"),
        el("p", { class: "small muted" }, "Settle doesn't guess. These need a person; they keep their own line so the bridge still ties out."),
        exc.length ? el("ul", { class: "ticks" }, ...exc.map((x) => el("li", {}, x)))
          : el("div", { class: "empty" }, el("b", {}, "Nothing needed a person this month."), "If a bank credit could match two settlements, it would wait here instead of being guessed."))));
}

function sourceName(s) { return { rp: "Razorpay report", bank: "Bank statement", orders: "Your orders", finding: "Issue" }[s] || s; }

// ---- ask ---------------------------------------------------------------------------------------

const SUGGESTED = ["Why did a settlement never reach my bank?", "How much did I lose to chargebacks this month?", "Which day had the most refunds?", "What should I do first?"];

function askPage(v) {
  const chat = el("div", { class: "chat", id: "chat", "aria-live": "polite" });
  const input = el("textarea", { id: "ask-q", placeholder: "Ask about this month, e.g. why was 21 Oct short?", maxlength: "500", rows: 2 });
  const go = el("button", { class: "btn primary", type: "submit" }, "Ask");
  const form = el("form", { class: "card", onsubmit: (e) => { e.preventDefault(); runAsk(input.value, chat, go); } },
    el("label", { for: "ask-q", class: "small", style: "font-weight:600" }, "Your question"),
    el("div", { class: "askbox", style: "margin-top:6px" }, input, go),
    el("div", { class: "suggest", "aria-label": "Example questions" }, ...SUGGESTED.map((q) => el("button", { type: "button", onclick: () => { input.value = q; runAsk(q, chat, go); } }, q))));
  if (!v.ai.available) { input.disabled = true; go.disabled = true; }
  const intro = el("div", { class: "card cando" },
    el("div", {}, el("b", {}, "Settle can"), el("ul", {}, el("li", {}, "Answer from your three files, with the rows as proof"), el("li", {}, "Explain an issue in plain words"), el("li", {}, "Show how it worked the answer out"))),
    el("div", {}, el("b", {}, "Settle won't"), el("ul", {}, el("li", {}, "Guess a figure: every number is checked first"), el("li", {}, "Approve, send or change anything"), el("li", {}, "Answer questions outside this month's money"))));
  return el("div", {},
    pageHead("Ask a question", v.ai.available ? "Ask in plain English. Answers come from your files, and every figure is checked against them before you see it." : "Ask needs the AI, which is off right now. Every number elsewhere is still exact."),
    el("div", { class: "stack side", style: "max-width:860px" }, intro, form, chat));
}

async function runAsk(q, chat, btn) {
  q = (q || "").trim();
  $(".ask-hint", chat)?.remove();
  if (!q) { chat.append(el("p", { class: "small ask-hint", role: "alert" }, "Type a question first.")); return; }
  btn.disabled = true; btn.textContent = "Working it out…";
  const pending = el("div", { class: "answer pending" }, el("div", { class: "skeleton", style: "width:40%" }), el("div", { class: "skeleton" }), el("div", { class: "skeleton", style: "width:70%" }), el("p", { class: "small muted" }, "Looking through your files…"));
  chat.append(el("div", { class: "msg-q" }, q), pending);
  pending.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const a = await api("POST", "/api/ask", { question: q });
    const cites = a.citations.map((c) => c.source === "finding"
      ? el("li", {}, el("button", { type: "button", onclick: (e) => openFinding(c.ref, e.currentTarget) }, `Issue ${c.ref}`))
      : el("li", {}, `${sourceName(c.source)}, row ${c.ref}`));
    pending.replaceWith(el("div", { class: "answer" },
      el("span", { class: a.verified ? "badge ok" : "badge warn" }, a.verified ? "✓ Figures checked against your files" : "Couldn't verify every figure: double-check"),
      el("p", {}, a.answer),
      cites.length ? el("div", {}, el("b", { class: "small" }, "Proof"), el("ul", { class: "cites" }, ...cites)) : null,
      a.trace?.length ? el("details", { style: "margin-top:10px" }, el("summary", { class: "small" }, `How I worked it out (${a.trace.length} step${a.trace.length > 1 ? "s" : ""})`),
        el("ol", { class: "timeline" }, ...a.trace.map((t) => el("li", {},
          el("b", {}, t.tool === "run_sql" ? "Looked up the data" : "Opened an issue"), ` · ${t.rows_returned ?? 0} row${(t.rows_returned ?? 0) === 1 ? "" : "s"} · ${t.ms} ms`,
          el("pre", {}, t.input || ""))))) : null));
  } catch (e) {
    pending.replaceWith(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint)));
  } finally { btn.disabled = false; btn.textContent = "Ask"; }
}

// ---- approvals & audit trail -------------------------------------------------------------------

function auditPage() {
  return el("div", {},
    pageHead("Approvals & audit trail", "Every fix Settle proposed and what happened to it, and a log of every decision: what the code found, what the AI wrote, what a person approved.",
      el("a", { class: "btn primary", href: "/api/report", target: "_blank", rel: "noopener", onclick: () => markStep("report") }, "Download the audit report")),
    el("div", { class: "stack", id: "audit-body" }, el("div", { class: "card" }, el("div", { class: "skeleton" }), el("div", { class: "skeleton", style: "width:60%" }))));
}
async function refreshAudit() {
  const box = $("#audit-body");
  if (!box) return;
  try {
    const d = await api("GET", "/api/audit");
    const titles = Object.fromEntries((state.view?.findings || []).map((f) => [f.id, f.title]));
    const waiting = d.actions.filter((a) => a.status === "needs_approval").length;
    put(box,
      el("section", { class: "card flush", "aria-labelledby": "appr-h" },
        el("div", { style: "padding:16px 18px 10px" }, el("h2", { id: "appr-h" }, "Fixes and their status"),
          el("p", { class: "small muted", style: "margin:2px 0 8px" }, waiting ? `${waiting} waiting for a named approver. Open an issue's fix to approve it, or approve month-end entries right here.` : "Nothing is waiting for approval."),
          el("div", { class: "field", style: "max-width:280px;margin:0" }, el("label", { for: "audit-approver" }, "Approve as"), el("input", { type: "text", id: "audit-approver", value: store.get("settle_name"), placeholder: "Your name", autocomplete: "name" }))),
        d.actions.length ? el("div", { class: "tablewrap" }, el("table", { class: "grid" },
          el("thead", {}, el("tr", {}, el("th", {}, "Fix"), el("th", {}, "For issue"), el("th", {}, "Status"), el("th", { class: "num" }, "Amount"))),
          el("tbody", {}, ...d.actions.map((a) => a.finding_id
            ? el("tr", { class: "link", onclick: (e) => openFinding(a.finding_id, e.currentTarget.querySelector("button"), "fix") },
              el("td", {}, ACTION[a.type] || a.type),
              el("td", {}, el("button", { class: "linkish", type: "button", onclick: (e) => { e.stopPropagation(); openFinding(a.finding_id, e.currentTarget, "fix"); } }, titles[a.finding_id] || a.finding_id)),
              el("td", {}, el("span", { class: `status ${a.status}` }, STATUS[a.status] || a.status, a.approved_by ? ` by ${a.approved_by}` : "")),
              el("td", { class: "num" }, a.amount ? rupees(a.amount) : ""))
            : el("tr", {},
              el("td", {}, ACTION[a.type] || a.type, el("div", { class: "small muted" }, a.payload?.narration || "")),
              el("td", { class: "small muted" }, "Month-end entry"),
              el("td", {}, el("span", { class: `status ${a.status}` }, STATUS[a.status] || a.status, a.approved_by ? ` by ${a.approved_by}` : ""),
                a.status === "needs_approval" || a.status === "auto" ? el("div", { class: "inline", style: "margin-top:6px" },
                  el("button", { class: "btn ok small", type: "button", onclick: () => decideInline(a, "approve") }, a.status === "auto" ? "Run it" : "Approve"),
                  el("button", { class: "btn danger small", type: "button", onclick: () => decideInline(a, "reject") }, "Reject")) : null),
              el("td", { class: "num" }, a.amount ? rupees(a.amount) : ""))))))
          : el("div", { class: "empty" }, el("b", {}, "No fixes proposed yet."), "When Settle finds an issue it can act on, the ticket, note or journal entry appears here for approval.")),
      el("section", { class: "card flush", "aria-labelledby": "log-h" },
        el("div", { style: "padding:16px 18px 10px" }, el("h2", { id: "log-h" }, "Decision log"), el("p", { class: "small muted", style: "margin:2px 0 0" }, "Newest first. The same log goes into the audit report.")),
        el("div", { class: "tablewrap" }, el("table", { class: "grid" },
          el("thead", {}, el("tr", {}, el("th", {}, "Time"), el("th", {}, "Who"), el("th", {}, "What"), el("th", {}, "Detail"))),
          el("tbody", {}, ...d.log.slice().reverse().slice(0, 150).map((e) => el("tr", {},
            el("td", { class: "small", style: "white-space:nowrap" }, fdate(e.ts)), el("td", {}, ACTOR[e.actor] || e.actor),
            el("td", {}, e.action), el("td", { class: "small muted" }, e.detail))))))));
  } catch (e) {
    put(box, el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint, el("div", { style: "margin-top:8px" }, el("button", { class: "btn small", type: "button", onclick: refreshAudit }, "Try again")))));
  }
}

async function decideInline(a, how) {
  const who = ($("#audit-approver")?.value || "").trim();
  if (!who && how === "approve") { notice("err", "Type your name to approve.", "Approvals are recorded with the approver's name.", null, [["Type my name", () => $("#audit-approver").focus()]]); return; }
  store.set("settle_name", who);
  try {
    await api("POST", `/api/actions/${a.id}/${how}`, { approver: who || "a reviewer" });
    notice("okb", how === "approve" ? "Recorded. Nothing was sent: this is demo mode." : "Rejected. Nothing will be sent.", "It's in the decision log below. Next: download the audit report.");
    markStep("decide");
    refreshAudit();
  } catch (e) { fail(e); }
}

// ---- rules -------------------------------------------------------------------------------------

function rulesPage(v) {
  const rules = v.rules || [], resolved = v.resolved || [];
  return el("div", {},
    pageHead("Saved rules", "When an issue is expected (a coupon at checkout, a known rounding), mark it as expected and Settle remembers it. Matching issues then move off your list but keep their own line in the bridge."),
    rules.length ? el("div", { class: "stack" },
      el("div", { class: "card flush" }, el("div", { class: "tablewrap" }, el("table", { class: "grid" },
        el("thead", {}, el("tr", {}, el("th", {}, "Rule"), el("th", {}, "Why"), el("th", {}, "Applies to"))),
        el("tbody", {}, ...rules.map((r) => el("tr", {}, el("td", {}, (v.findings.find((f) => f.check === r.check)?.title) || r.check.replace(/_/g, " ")), el("td", {}, `“${r.note}”`), el("td", { class: "small muted" }, r.ref ? `Only ${r.ref}` : "All like this"))))))),
      resolved.length ? el("div", { class: "card" }, el("h2", {}, "What your rules resolved"), el("ul", { class: "ticks" }, ...resolved.map((r) => el("li", {}, el("span", {}, `${r.title}: “${r.note}”`), el("span", { class: "num" }, rupees(r.amount)))))) : null)
      : el("div", { class: "card empty" }, el("b", {}, "No saved rules yet."), "Open an issue that isn't really a problem, go to its Fix tab and press ", el("i", {}, "Mark as expected"), " with a short reason. It will show here.",
        el("div", { style: "margin-top:12px" }, el("button", { class: "btn small", type: "button", onclick: () => go("issues") }, "Go to Issues to fix"))));
}

// ---- issue slide-over --------------------------------------------------------------------------

let lastOpener = null;
const drawer = $("#drawer"), sheet = $("#drawer-sheet");
function closeDrawer() { drawer.hidden = true; document.body.style.overflow = ""; lastOpener?.focus?.(); }
drawer.addEventListener("click", (e) => { if (e.target === drawer) closeDrawer(); });
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#tour").hidden) { endTour(); return; }
  if (!drawer.hidden) closeDrawer();
});

async function openFinding(id, opener, tab = "summary") {
  lastOpener = opener || document.activeElement;
  drawer.hidden = false; document.body.style.overflow = "hidden";
  sheet.scrollTop = 0;
  put(sheet, el("div", { class: "body" }, el("div", { class: "skeleton", style: "width:30%;margin-top:20px" }), el("div", { class: "skeleton", style: "height:28px" }), el("div", { class: "skeleton" }), el("div", { class: "skeleton", style: "width:80%" })));
  try {
    drawFinding(await api("GET", `/api/finding/${encodeURIComponent(id)}`), tab);
    markStep("open");
  } catch (e) {
    put(sheet, el("div", { class: "body" }, el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint)),
      el("button", { class: "btn", type: "button", onclick: closeDrawer }, "Close")));
  }
}

function drawFinding(d, tab = "summary") {
  const f = d.finding, x = d.explanation, k = KIND[f.kind] || KIND.books;
  const close = el("button", { class: "btn ghost small", type: "button", onclick: closeDrawer, "aria-label": "Close" }, "Close ✕");
  const tabs = [["summary", "Summary"], ["evidence", "Proof"], ["trace", "Money trail"], ["fix", "Fix"]];
  const bar = el("div", { class: "tabs", role: "tablist", id: "drawer-tabs", "aria-label": "Issue details" });
  const panels = {};
  const pick = (key) => {
    for (const [kk] of tabs) { panels[kk].hidden = kk !== key; $(`#t-${kk}`, bar).setAttribute("aria-selected", String(kk === key)); $(`#t-${kk}`, bar).tabIndex = kk === key ? 0 : -1; }
    sheet.scrollTop = 0;
  };
  for (const [key, label] of tabs) {
    bar.append(el("button", { type: "button", role: "tab", id: `t-${key}`, "aria-controls": `tab-${key}`, onclick: () => pick(key) }, label));
    panels[key] = el("div", { class: "panel-tab", role: "tabpanel", id: `tab-${key}`, "aria-labelledby": `t-${key}` });
  }
  bar.addEventListener("keydown", (e) => {
    const i = tabs.findIndex(([kk]) => !panels[kk].hidden);
    const j = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i + tabs.length - 1) % tabs.length : -1;
    if (j >= 0) { pick(tabs[j][0]); $(`#t-${tabs[j][0]}`, bar).focus(); }
  });

  const header = el("header", {},
    el("div", { class: "hd" },
      el("div", {}, el("span", { class: `kind ${f.kind}` }, k.label), el("h2", { id: "drawer-title" }, f.title), el("div", { class: "bigamt" }, rupees(f.amount))),
      close),
    el("p", { class: "why" }, el("b", {}, "Why it matters: "), WHY[f.check] || k.why),
    bar);

  // Summary
  const S = panels.summary;
  if (x) {
    const aiBtn = el("button", { class: "btn small", type: "button" }, "Explain with AI");
    aiBtn.addEventListener("click", async () => {
      aiBtn.disabled = true; aiBtn.textContent = "Writing…";
      try { await api("POST", `/api/finding/${encodeURIComponent(f.id)}/explain`); drawFinding(await api("GET", `/api/finding/${encodeURIComponent(f.id)}`)); }
      catch (e) { aiBtn.replaceWith(el("div", { class: "banner warn", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint))); }
    });
    S.append(
      el("h3", {}, x.headline),
      el("div", { class: "inline", style: "margin-bottom:10px" },
        x.source === "model" && x.verified ? el("span", { class: "badge ok" }, "✓ Written by AI, every figure checked") : el("span", { class: "badge warn" }, "Written from a template"),
        x.source !== "model" && state.view?.ai.available ? aiBtn : null),
      el("p", {}, x.what_happened),
      el("p", {}, el("b", {}, "Likely cause. "), x.likely_cause),
      el("b", {}, "What to do"),
      el("ol", { class: "todo" }, ...x.what_to_do.map((s) => el("li", {}, s))),
      el("dl", { class: "kv" }, el("dt", {}, "Who to contact"), el("dd", {}, x.who_to_contact)));
  }
  if (f.kind !== "risk") S.append(el("div", { class: "inline", style: "margin-top:14px" },
    el("button", { class: "btn primary small", type: "button", onclick: () => pick("fix") }, "Next: see the fix"),
    el("button", { class: "btn small", type: "button", onclick: () => pick("evidence") }, "See the proof")));

  // Proof
  const E = panels.evidence;
  if (d.evidence?.length) {
    E.append(el("p", { class: "small muted" }, "The exact rows from your files that show this issue."));
    for (const ev of d.evidence) {
      const cols = ev.columns;
      E.append(el("h3", {}, ev.label, el("span", { class: "small muted", style: "font-weight:400" }, ev.total > ev.rows.length ? `  showing ${ev.rows.length} of ${ev.total} rows` : `  ${ev.rows.length} row${ev.rows.length > 1 ? "s" : ""}`)),
        el("div", { class: "card flush" }, el("div", { class: "tablewrap" }, el("table", { class: "grid ev" },
          el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", { class: ev.money.includes(c) ? "num" : "" }, COLUMN[c] || c)))),
          el("tbody", {}, ...ev.rows.map((r) => el("tr", {}, ...cols.map((c) => el("td", { class: ev.money.includes(c) ? "num" : "" }, cell(ev, c, r[c]))))))))));
    }
  } else E.append(el("div", { class: "empty" }, el("b", {}, "No rows to show."), "This is a pattern across many rows rather than one line."));

  // Money trail
  const T = panels.trace;
  if (d.trace?.hops?.length) {
    T.append(el("p", { class: "small muted" }, "Follow the money from the order to your bank. ", d.trace.stalled_at ? el("b", {}, `It stopped at: ${d.trace.stalled_at}.`) : "Every step is in place."),
      el("ol", { class: "chain" }, ...d.trace.hops.map((h) => el("li", { class: h.status },
        el("span", { class: "dot", "aria-hidden": "true" }, { ok: "✓", missing: "✗", mismatch: "!", waiting: "…" }[h.status]),
        el("div", {},
          el("div", { class: "st" }, { ok: "In place", missing: "Missing", mismatch: "Doesn't match", waiting: "Next month" }[h.status]),
          el("b", {}, h.hop), h.amount !== null && h.amount !== undefined ? el("div", { class: "small" }, rupees(h.amount)) : null,
          h.detail ? el("div", { class: "small muted" }, h.detail) : null)))),
      el("p", { class: "small muted" }, el("b", {}, "Terms: "), "a ", el("button", { "data-help": "Settlement", class: "linkish", type: "button" }, "settlement"), " is one Razorpay payout; the ", el("button", { "data-help": "UTR", class: "linkish", type: "button" }, "UTR"), " is the bank's reference that links it to your statement."));
  } else T.append(el("div", { class: "empty" }, el("b", {}, "No single money trail."), f.kind === "risk" ? "This is a pattern across many payments; the Proof tab lists them." : "This issue spans several payments; the Proof tab lists them."));

  // Fix
  const F = panels.fix;
  const drafts = [...d.drafts.map((dr) => ({ label: dr.kind === "ticket" ? "Ticket to Razorpay" : "Note to customer", text: `Subject: ${dr.subject}\n\n${dr.body}`, ai: dr.source === "model" }))];
  if (d.api_call) drafts.push({ label: "Razorpay API call", text: `${d.api_call.method} ${d.api_call.url}\nAuth: ${d.api_call.auth}\n\n${d.api_call.why}`, ai: false });
  if (drafts.length) {
    const pane = el("div", { class: "draft-pane" });
    const dbar = el("div", { class: "drafts-bar", role: "tablist", "aria-label": "Drafts" });
    const pickDraft = (i) => {
      $$("button", dbar).forEach((b, j) => b.setAttribute("aria-selected", String(i === j)));
      const t = drafts[i];
      put(pane, el("pre", { tabindex: "0" }, t.text),
        el("div", { class: "inline", style: "margin-top:8px" }, el("button", { class: "btn small", type: "button", onclick: (e) => copy(t.text, e.currentTarget) }, "Copy"),
          el("span", { class: "small muted" }, t.ai ? "Drafted by AI with every figure checked. Edit before sending." : "Built from your data.")));
    };
    drafts.forEach((t, i) => dbar.append(el("button", { type: "button", role: "tab", class: "draft-tab", onclick: () => pickDraft(i) }, t.label)));
    F.append(el("h3", {}, "Drafts ready to send"), dbar, pane);
    pickDraft(0);
  }
  if (d.actions?.length) F.append(actionsBlock(d.actions));
  if (f.kind !== "risk") F.append(expectedBlock(f));
  if (!F.children.length) F.append(el("div", { class: "empty" }, el("b", {}, "Nothing to approve."), "This is a pattern to look into, not a fix to send."));

  put(sheet, header, el("div", { class: "body" }, ...tabs.map(([kk]) => panels[kk])));
  pick(tab);
  close.focus();
}

function cell(ev, c, v) {
  if (ev.money.includes(c)) return v ? rupees(v) : "";
  if (/date|_at$/.test(c)) return fdate(v);
  return v ?? "";
}

function actionsBlock(actions) {
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
    put(card,
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
    if (!who && how === "approve") {
      nameInput.focus(); redraw(a, null);
      card.append(el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: "Type your name to approve." }), "Approvals are recorded with the approver's name. Type it in “Approve as” above.")));
      return;
    }
    store.set("settle_name", who);
    try {
      const r = await api("POST", `/api/actions/${a.id}/${how}`, { approver: who || "a reviewer" });
      redraw(r.action, how === "approve"
        ? (r.demo_mode ? "Recorded. This is a demo, so nothing was sent; below is exactly what would have gone out. Next: download the audit report from Export." : "Done.")
        : "Rejected. Nothing will be sent.");
      markStep("decide");
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
    if (!note.value.trim()) { put(msg, el("p", { class: "small", role: "alert" }, "Add a short note saying why.")); note.focus(); return; }
    go.disabled = true;
    try {
      const v = await api("POST", "/api/rules", { finding_id: f.id, note: note.value, scope: scope ? scope.value : "this" });
      closeDrawer(); render(v); markStep("decide");
      notice("okb", `Marked as expected: “${note.value.trim()}”.`, `It moved to Saved rules and “What we checked and left alone”. The bridge still ties out at ${rupees(v.totals.unexplained)} unexplained.`);
    } catch (e) { put(msg, el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint))); }
    finally { go.disabled = false; }
  });
  return el("div", { style: "margin-top:18px;padding-top:14px;border-top:1px solid var(--line)" }, el("h3", {}, "Not a problem?"),
    el("p", { class: "small muted" }, "Mark it as expected and Settle will remember it for this session. It keeps its own line in the bridge."),
    el("div", { class: "inline" }, el("label", { class: "sr", for: "rule-note" }, "Reason"), note, scope, go), msg);
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
  catch { btn.textContent = "Select and copy the text above"; }
  setTimeout(() => (btn.textContent = "Copy"), 2500);
}

// ---- guided tour -------------------------------------------------------------------------------

const TOUR = [
  { page: "overview", sel: ".statement", title: "The month in five numbers", text: "Money lost, what you owe customers, and what your books need fixing. Hover the small ? next to any label for a plain explanation." },
  { page: "overview", sel: "[data-kpi=unexplained]", title: "Unexplained ₹0.00 means it ties out", text: "Every rupee between your books and your bank is on a named line: a fee, a timing difference or an issue. If anything couldn't be placed, it would show here." },
  { page: "overview", sel: "#start-here", title: "Your three steps", text: "Review the biggest issue, approve its fix, download the audit report. The ticks fill in as you go." },
  { page: "settlements", sel: "#settlement-list", title: "Every payout, one line each", text: "Each settlement from your payment gateway, what reached your bank, and how Settle matched them. This is the view an accountant ticks against the bank statement." },
  { page: "issues", sel: "#problem-list", title: "Issues to fix", text: "Everything Settle found, biggest first. Click any line to open it: why it happened, the proof rows from your files, the money trail and the fix." },
  { page: "audit", sel: "#nav-audit", title: "Nothing moves without you", text: "Each fix (a Razorpay ticket, a customer note, a journal entry) waits for a named approver. Approvals and every decision are logged here." },
  { page: "ask", sel: "#ask-q", title: "Ask in plain English", text: "Ask about the month, like “why was 14 Oct short?”. Answers cite the rows they came from, and every figure is checked." },
  { page: "overview", sel: "#export-menu", title: "Hand it to your auditor", text: "Export the audit report, an Excel pack, or a Tally journal with balanced entries. That's the whole loop." },
];
let tourAt = -1, tourOpener = null;
function startTour() { tourOpener = document.activeElement; closeDrawer(); tourAt = 0; $("#tour").hidden = false; tourStep(); }
function endTour() { $("#tour").hidden = true; tourAt = -1; store.set("settle_toured", "1"); go("overview"); tourOpener?.focus?.(); }
function tourStep() {
  const s = TOUR[tourAt];
  if (state.page !== s.page) go(s.page);
  const target = $(s.sel);
  if (!target) { tourAt < TOUR.length - 1 ? (tourAt++, tourStep()) : endTour(); return; }
  target.scrollIntoView({ block: "center" });
  const r = target.getBoundingClientRect(), pad = 6;
  Object.assign($("#tour-hole").style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${Math.min(r.height, innerHeight - 40) + 2 * pad}px` });
  $("#tour-count").textContent = `Step ${tourAt + 1} of ${TOUR.length}`;
  $("#tour-title").textContent = s.title;
  $("#tour-text").textContent = s.text;
  $("#tour-back").disabled = tourAt === 0;
  $("#tour-next").textContent = tourAt === TOUR.length - 1 ? "Finish" : "Next";
  const pop = $("#tour-pop"), pw = pop.offsetWidth, ph = pop.offsetHeight;
  const below = r.bottom + pad + 12 + ph < innerHeight;
  const top = below ? r.bottom + pad + 12 : Math.max(12, r.top - pad - 12 - ph);
  const left = Math.min(Math.max(12, r.left), innerWidth - pw - 12);
  Object.assign(pop.style, { top: `${Math.min(top, innerHeight - ph - 12)}px`, left: `${left}px` });
  $("#tour-next").focus();
}
$("#tour-next").addEventListener("click", () => (tourAt < TOUR.length - 1 ? (tourAt++, tourStep()) : endTour()));
$("#tour-back").addEventListener("click", () => { if (tourAt > 0) { tourAt--; tourStep(); } });
$("#tour-skip").addEventListener("click", endTour);
// Keep focus inside the tour while it's open.
$("#tour").addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  const f = $$("#tour-pop button:not(:disabled)");
  const i = f.indexOf(document.activeElement);
  e.preventDefault();
  f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
});
addEventListener("resize", () => { if (tourAt >= 0) tourStep(); });

// ---- upload ------------------------------------------------------------------------------------

const form = $("#upload-form");
const SLOT_NAME = { razorpay: "Razorpay settlement report", bank: "bank statement", orders: "orders export" };
for (const slot of $$(".slot", form)) {
  const input = $("input[type=file]", slot), label = $(".file", slot);
  const sync = () => {
    slot.classList.remove("error");
    const f = input.files[0];
    slot.classList.toggle("filled", !!f);
    label.textContent = f ? `✓ ${f.name} (${Math.max(1, Math.round(f.size / 1024))} KB)` : "or drop the file here";
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
const chooseAgain = (slot) => () => { $(`#f-${slot}`).click(); };

async function rowsIn(file) {
  try { return Math.max(0, (await file.text()).split("\n").filter((l) => l.trim()).length - 1); } catch { return 0; }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const missing = $$(".slot", form).filter((s) => !$("input[type=file]", s).files.length);
  missing.forEach((s) => s.classList.add("error"));
  if (missing.length) {
    const first = missing[0].dataset.slot;
    notice("err", "Add all three files first.", `Missing: ${missing.map((s) => $("h3", s).textContent).join(", ")}.`, null, [[`Choose the ${SLOT_NAME[first]}`, chooseAgain(first)]]);
    return;
  }
  const bank = $("#f-bank").files[0];
  const pdf = /\.pdf$/i.test(bank.name);
  put(alerts); // a retry starts clean: the last attempt's error no longer applies
  loading("Reading your files…");
  const rp = await rowsIn($("#f-razorpay").files[0]), od = await rowsIn($("#f-orders").files[0]);
  loading(pdf ? ["Unlocking the statement…", "Reading each page with AI…", "Checking every running balance adds up…", "Almost there…"]
    : [`Reading ${num(rp)} Razorpay rows and ${num(od)} orders…`, "Matching every payment, refund and settlement…", "Building the bridge from books to bank…"],
    pdf ? "Reading a PDF takes 10 to 20 seconds." : "This takes a few seconds.");
  try {
    const v = await api("POST", "/api/upload", new FormData(form), true);
    put(alerts);
    if (v.needs_review) showReview(v); else { state.page = "overview"; render(v); }
  } catch (err) {
    const fixes = [];
    if (err.needs_password) { $("#pw-field").hidden = false; fixes.push(["Enter the PDF password", () => $("#f-password").focus()]); }
    if (err.slot) { $(`.slot[data-slot=${err.slot}]`, form)?.classList.add("error"); fixes.push([`Choose a different ${SLOT_NAME[err.slot] || "file"}`, chooseAgain(err.slot)]); }
    fail(err, () => form.requestSubmit(), fixes);
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
  put($("#review-summary"), v.verified
    ? el("div", { class: "banner okb" }, el("div", {}, el("b", { text: `All ${v.rows.length} rows read from ${v.pages} page${v.pages > 1 ? "s" : ""}, and every running balance adds up.` }), "Check anything you like, then press Continue to reconcile."))
    : el("div", { class: "banner warn" }, el("div", {}, el("b", { text: `${v.breaks.length} row${v.breaks.length > 1 ? "s don't" : " doesn't"} add up to the printed balance.` }),
        "They're highlighted in red, with the box most likely misread outlined. Correct them from your PDF, then continue.")));
  fillReview(v.rows, breaks);
  show("review");
}

function fillReview(rows, breaks) {
  const table = $("#review-table");
  put(table,
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
  loading(["Checking every balance again…", "Reconciling…"]);
  try { state.page = "overview"; render(await api("POST", "/api/bank-preview/confirm", { rows })); put(alerts); }
  catch (e) {
    if (e.breaks) {
      fillReview(rows, new Map(e.breaks.map((b) => [b.row, b])));
      put($("#review-summary"), el("div", { class: "banner err", role: "alert" }, el("div", {}, el("b", { text: e.error }), e.hint,
        el("div", { style: "margin-top:8px" }, el("button", { class: "btn small", type: "button", onclick: () => { const r = $("#review-table tr.broken"); r?.scrollIntoView({ block: "center" }); $("input.likely, textarea.likely", r || document)?.focus(); } }, "Go to the row to fix")))));
    } else fail(e);
  } finally { loading(false); }
});

// ---- wiring ------------------------------------------------------------------------------------

// If the tour can't load, say so in its place instead of showing a broken player.
const tourVideo = $("#tour-video");
tourVideo?.querySelector("source:last-of-type")?.addEventListener("error", () => { tourVideo.hidden = true; $("#tour-frame").hidden = false; });

$("#try-sample").addEventListener("click", loadSample);
$("#upload-sample").addEventListener("click", loadSample);
$$("[data-sample]").forEach((b) => b.addEventListener("click", loadSample));
$("#go-upload").addEventListener("click", () => show("upload"));
$("#upload-back").addEventListener("click", () => show("landing"));
$("#review-back").addEventListener("click", () => show("upload"));
$("#open-connect").addEventListener("click", openConnections);
$("#conn-back").addEventListener("click", () => show(state.view ? "results" : "landing"));
$("#demo-sync").addEventListener("click", demoSync);
document.addEventListener("click", (e) => { for (const m of $$("details.menu[open]")) if (!m.contains(e.target)) m.open = false; });

// Pick up where this browser left off (sessions last an hour).
(async () => {
  try {
    const v = await api("GET", "/api/result");
    if (v.loaded) render(v); else show("landing");
  } catch { show("landing"); }
})();
