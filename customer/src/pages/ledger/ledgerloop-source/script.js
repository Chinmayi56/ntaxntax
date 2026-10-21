/* ==========================================================================
   Sample data (in-memory only)

   BACKEND TODO — when the real database is ready (see supabase-schema.sql):
   Every array below (CLIENTS, INVOICES, TASKS, KANBAN_TASKS, etc.) is a
   stand-in for a real `supabase.from('table').select()` call. The render
   functions that read them (renderDashboard, renderClients, renderKanban...)
   don't need to change — just swap what feeds these arrays:
     1. Replace each `const X = [...]` with `let X = []` (still mutable)
     2. On load, fetch and fill it:
          const { data } = await supabase.from('clients').select('*');
          CLIENTS = data;
     3. Anywhere the code does `CLIENTS.unshift(...)` / `.push(...)` to add
        an item locally, also fire the matching insert:
          await supabase.from('clients').insert({...});
     4. Task assignment (openNewTaskModal below) is the one to prioritize —
        that's the "CA assigns work to staff" flow. Its assignee dropdown
        should eventually list real staff profiles (role = 'staff'), and
        the assigned_to field should be scoped by Row Level Security so
        each staff login only ever pulls back their own tasks.
   ========================================================================== */
/* ==========================================================================
   Supabase live-backend wiring
   Fill in your project URL + anon key below (Supabase Dashboard → Project
   Settings → API) to switch this file from demo mode to a real, logged-in,
   role-based app. Leave them as the placeholder text and everything keeps
   working exactly like it does right now — nothing breaks either way.
   ========================================================================== */
const SUPABASE_URL = 'YOUR_SUPABASE_URL';
const SUPABASE_ANON_KEY = 'YOUR_SUPABASE_ANON_KEY';
const LIVE_MODE = SUPABASE_URL !== 'YOUR_SUPABASE_URL' && SUPABASE_ANON_KEY !== 'YOUR_SUPABASE_ANON_KEY';
let supabaseClient = null;
let currentProfile = null; // { id, full_name, role } — set once logged in, live mode only

async function initSupabase(){
  if (!LIVE_MODE) return;
  const { createClient } = await import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm');
  supabaseClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  const { data: { session } } = await supabaseClient.auth.getSession();
  if (session){
    await loadCurrentProfile();
    enterAfterLogin();
  }
}
async function loadCurrentProfile(){
  const { data: { user } } = await supabaseClient.auth.getUser();
  if (!user) return;
  const { data: profile } = await supabaseClient.from('profiles').select('*').eq('id', user.id).single();
  currentProfile = profile || { id: user.id, full_name: user.email, role: 'staff', status: 'pending' };
}
/* Routes a just-authenticated user to the right screen based on approval
   status: active → the workspace, pending → the waiting screen, rejected →
   signed back out with an explanation. Call this instead of showApp()
   directly anywhere a login/session just succeeded. */
async function enterAfterLogin(){
  if (!LIVE_MODE || !currentProfile || currentProfile.status === 'active'){
    showApp();
    return;
  }
  if (currentProfile.status === 'rejected'){
    await supabaseClient.auth.signOut();
    currentProfile = null;
    const loginError = document.getElementById('loginError');
    loginError.textContent = 'Your access request was declined. Contact your CA to request access again.';
    loginError.style.display = 'block';
    showLogin();
    return;
  }
  showPendingApproval();
}
async function signOutUser(){
  if (LIVE_MODE && supabaseClient) await supabaseClient.auth.signOut();
  currentProfile = null;
  showLogin();
}

const CLIENTS = [
  { id: "c1", name: "Aarav Textiles Pvt Ltd", gstin: "27AACCA1234B1Z5" },
  { id: "c2", name: "Kadam & Sons Retail", gstin: "27AABCK5678C1Z2" },
  { id: "c3", name: "Nimbus Digital Studio", gstin: "29AACCN4321D1Z8" },
  { id: "c4", name: "Sundar Freight Co.", gstin: "33AACCS7890E1Z1" },
  { id: "c5", name: "Vantage Realty Group", gstin: "07AACCV2468F1Z4" },
];
const INVOICES = [
  { id: "INV-1042", client: "Aarav Textiles Pvt Ltd", date: "2026-08-05", amount: 84200, status: "paid" },
  { id: "INV-1041", client: "Nimbus Digital Studio", date: "2026-08-03", amount: 32750, status: "pending" },
  { id: "INV-1040", client: "Sundar Freight Co.", date: "2026-07-29", amount: 118400, status: "overdue" },
  { id: "INV-1039", client: "Vantage Realty Group", date: "2026-07-26", amount: 56000, status: "paid" },
  { id: "INV-1038", client: "Kadam & Sons Retail", date: "2026-07-22", amount: 21300, status: "paid" },
  { id: "INV-1037", client: "Nimbus Digital Studio", date: "2026-07-18", amount: 47850, status: "overdue" },
];
const TASKS = [
  { id: "t1", title: "File GSTR-3B — Aarav Textiles", meta: "Due Aug 20", done: false },
  { id: "t2", title: "Reconcile bank statement — July", meta: "Assigned to Priya", done: false },
  { id: "t3", title: "Send payment reminder — Sundar Freight", meta: "3 days overdue", done: false },
  { id: "t4", title: "Prepare TDS working — Q1", meta: "Assigned to Rohit", done: true },
  { id: "t5", title: "Onboard Vantage Realty on portal", meta: "Completed Aug 2", done: true },
];
const DEADLINES = [
  { name: "GSTR-3B Filing", client: "Aarav Textiles", date: "Aug 20", urgency: "warn" },
  { name: "TDS Payment", client: "Nimbus Digital", date: "Aug 07", urgency: "overdue" },
  { name: "GSTR-1 Filing", client: "Kadam & Sons", date: "Aug 11", urgency: "warn" },
  { name: "Advance Tax Q2", client: "Vantage Realty", date: "Sep 15", urgency: "ok" },
];
const REVENUE_BY_MONTH = [
  { m: "Mar", v: 210000 }, { m: "Apr", v: 248000 }, { m: "May", v: 232000 },
  { m: "Jun", v: 279000 }, { m: "Jul", v: 305000 }, { m: "Aug", v: 160000 },
];
const EXPENSE_CATEGORIES = [
  { label: "Salaries", value: 118000, color: "#1F7A5C" },
  { label: "Software & Tools", value: 42000, color: "#7FD9B5" },
  { label: "Rent & Utilities", value: 36000, color: "#C98A2C" },
  { label: "Travel", value: 21000, color: "#B0432E" },
  { label: "Misc.", value: 15000, color: "#8FA3C9" },
];

/* ---- Kanban tasks ---- */
let KANBAN_TASKS = [
  { id:"k1", title:"File GSTR-3B — Aarav Textiles", client:"Aarav Textiles", assignee:"PM", priority:"high", due:"Aug 20", status:"todo" },
  { id:"k2", title:"Collect Q1 TDS challans — Vantage Realty", client:"Vantage Realty", assignee:"RS", priority:"med", due:"Aug 18", status:"todo" },
  { id:"k3", title:"Draft engagement letter — new client", client:"Internal", assignee:"AK", priority:"low", due:"Aug 25", status:"todo" },
  { id:"k4", title:"Reconcile bank statement — July", client:"Kadam & Sons", assignee:"PM", priority:"med", due:"Aug 12", status:"progress" },
  { id:"k5", title:"Prepare TDS working — Q1", client:"Nimbus Digital", assignee:"RS", priority:"high", due:"Aug 10", status:"progress" },
  { id:"k6", title:"Send payment reminder — Sundar Freight", client:"Sundar Freight", assignee:"AK", priority:"high", due:"Overdue", status:"progress" },
  { id:"k7", title:"Onboard Vantage Realty on portal", client:"Vantage Realty", assignee:"PM", priority:"low", due:"Completed Aug 2", status:"done" },
  { id:"k8", title:"File GSTR-1 — Kadam & Sons", client:"Kadam & Sons", assignee:"RS", priority:"med", due:"Completed Aug 1", status:"done" },
];

/* ---- Client directory ---- */
let CLIENT_DIRECTORY = [
  { name:"Aarav Textiles Pvt Ltd", gstin:"27AACCA1234B1Z5", type:"Business", contact:"Aarav Deshmukh", phone:"+91 98200 11122", openInvoices:1, status:"Active" },
  { name:"Kadam & Sons Retail", gstin:"27AABCK5678C1Z2", type:"Business", contact:"Meera Kadam", phone:"+91 98700 44551", openInvoices:0, status:"Active" },
  { name:"Nimbus Digital Studio", gstin:"29AACCN4321D1Z8", type:"Business", contact:"Rehan Fernandes", phone:"+91 90080 33221", openInvoices:2, status:"Active" },
  { name:"Sundar Freight Co.", gstin:"33AACCS7890E1Z1", type:"Business", contact:"Karthik Sundaram", phone:"+91 99400 88712", openInvoices:1, status:"Active" },
  { name:"Vantage Realty Group", gstin:"07AACCV2468F1Z4", type:"Business", contact:"Neha Vantage", phone:"+91 98110 22990", openInvoices:0, status:"Onboarding" },
  { name:"R. Iyer & Associates", gstin:"—", type:"Practice", contact:"Ramesh Iyer", phone:"+91 98450 12376", openInvoices:0, status:"Active" },
];

/* ---- Filing calendar ---- */
const CALENDAR_ITEMS = [
  { month:"August 2026", entries:[
    { date:"07", title:"TDS Payment — Nimbus Digital", sub:"Section 194J · Monthly", urgency:"overdue" },
    { date:"11", title:"GSTR-1 Filing — Kadam & Sons", sub:"Outward supplies · Monthly", urgency:"warn" },
    { date:"20", title:"GSTR-3B Filing — Aarav Textiles", sub:"Summary return · Monthly", urgency:"warn" },
    { date:"30", title:"PT Return — All employees", sub:"Professional tax · Monthly", urgency:"ok" },
  ]},
  { month:"September 2026", entries:[
    { date:"15", title:"Advance Tax Q2 — Vantage Realty", sub:"Installment 2 of 4", urgency:"ok" },
    { date:"20", title:"GSTR-3B Filing — Sundar Freight", sub:"Summary return · Monthly", urgency:"ok" },
    { date:"30", title:"Tax Audit Report — R. Iyer & Associates", sub:"Form 3CD · Annual", urgency:"ok" },
  ]},
];

/* ---- GST filings ---- */
const GST_FILINGS = [
  { client:"Aarav Textiles Pvt Ltd", type:"GSTR-3B", period:"Jul 2026", due:"2026-08-20", status:"pending" },
  { client:"Kadam & Sons Retail", type:"GSTR-1", period:"Jul 2026", due:"2026-08-11", status:"pending" },
  { client:"Nimbus Digital Studio", type:"GSTR-3B", period:"Jul 2026", due:"2026-08-20", status:"filed" },
  { client:"Sundar Freight Co.", type:"GSTR-3B", period:"Jun 2026", due:"2026-07-20", status:"overdue" },
  { client:"Vantage Realty Group", type:"GSTR-1", period:"Jul 2026", due:"2026-08-11", status:"filed" },
  { client:"Kadam & Sons Retail", type:"GSTR-3B", period:"Jun 2026", due:"2026-07-20", status:"filed" },
  { client:"Aarav Textiles Pvt Ltd", type:"GSTR-9", period:"FY 2025-26", due:"2026-12-31", status:"pending" },
];

/* ---- Documents ---- */
const DOCUMENTS = [
  { name:"GSTR-3B_Jul2026_AaravTextiles.pdf", client:"Aarav Textiles", category:"GST Filing", uploaded:"2026-08-05", size:"212 KB" },
  { name:"Bank_Statement_July.xlsx", client:"Kadam & Sons", category:"Bank Records", uploaded:"2026-08-03", size:"88 KB" },
  { name:"Engagement_Letter_Signed.pdf", client:"Vantage Realty", category:"Onboarding", uploaded:"2026-08-01", size:"340 KB" },
  { name:"TDS_Challan_Q1.pdf", client:"Nimbus Digital", category:"Tax Payment", uploaded:"2026-07-29", size:"156 KB" },
  { name:"INV-1040_SundarFreight.pdf", client:"Sundar Freight", category:"Invoice", uploaded:"2026-07-29", size:"64 KB" },
  { name:"Form_3CD_Draft.docx", client:"R. Iyer & Associates", category:"Tax Audit", uploaded:"2026-07-22", size:"98 KB" },
];

/* ---- Team chat ---- */
let CHAT_MESSAGES = [
  { name:"Rohit Sharma", initials:"RS", text:"Filed GSTR-1 for Kadam & Sons — all clear.", mine:false },
  { name:"Ayesha Khan", initials:"AK", text:"Sundar Freight invoice is 3 days overdue, sending a reminder today.", mine:false },
  { name:"You", initials:"PM", text:"Thanks — flag me if they don't respond by Friday.", mine:true },
  { name:"Rohit Sharma", initials:"RS", text:"Will do. Also TDS payment for Nimbus is due tomorrow.", mine:false },
];

const fmt = (n) => "₹" + Math.round(n).toLocaleString("en-IN");

/* ==========================================================================
   View / page routing
   ========================================================================== */
function showView(id){
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
function showApp(){
  showView('view-app');
  if (LIVE_MODE){ loadKanbanTasks(); loadClients(); }
  goToPage('dashboard');
}
function showLogin(){ showView('view-login'); }
function showSignup(){ showView('view-signup'); }
function showPendingApproval(){ showView('view-pending'); }
function goToPage(name){
  if (name === 'none') return;
  document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
  let target = document.getElementById('page-' + name);
  if (!target){
    renderGenericPage(name);
    target = document.getElementById('page-generic');
  }
  target.style.display = 'block';
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === name));
  syncSidebarGroups(name);
  
  // Close drawer sidebar on mobile after clicking
  document.querySelector('.shell').classList.remove('mobile-nav-open');
  window.scrollTo(0,0);
}
document.querySelectorAll('[data-page]:not(.nav-parent)').forEach(el => el.addEventListener('click', () => goToPage(el.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(el => el.addEventListener('click', () => goToPage(el.dataset.goto)));
document.getElementById('logoutLink').addEventListener('click', signOutUser);
document.getElementById('pendingLogoutBtn').addEventListener('click', signOutUser);

/* ---- Sidebar accordion groups ---- */
function setGroupExpanded(group, expand){
  const children = group.querySelector('.nav-children');
  group.classList.toggle('expanded', expand);
  children.style.maxHeight = expand ? children.scrollHeight + 'px' : '0px';
}
document.querySelectorAll('.nav-parent-group').forEach(group => {
  const header = group.querySelector('.nav-parent');
  header.addEventListener('click', () => {
    const willExpand = !group.classList.contains('expanded');
    goToPage(header.dataset.page);
    document.querySelectorAll('.nav-parent-group.expanded').forEach(g => { if (g !== group) setGroupExpanded(g, false); });
    setGroupExpanded(group, willExpand);
  });
});
function syncSidebarGroups(activePage){
  const activeChild = document.querySelector('.nav-subitem[data-page="' + activePage + '"]');
  if (activeChild){
    const targetGroup = activeChild.closest('.nav-parent-group');
    document.querySelectorAll('.nav-parent-group.expanded').forEach(g => { if (g !== targetGroup) setGroupExpanded(g, false); });
    setGroupExpanded(targetGroup, true);
  }
}

/* ---- Login ---- */
document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  const pw = document.getElementById('password').value.trim();
  const loginError = document.getElementById('loginError');
  if (!email || !pw){ loginError.textContent = "Enter an email and password to continue."; loginError.style.display = 'block'; return; }
  if (!LIVE_MODE){ showApp(); return; } // demo mode — any credentials work
  const { error } = await supabaseClient.auth.signInWithPassword({ email, password: pw });
  if (error){
    loginError.textContent = error.message;
    loginError.style.display = 'block';
    return;
  }
  loginError.style.display = 'none';
  await loadCurrentProfile();
  enterAfterLogin();
});
document.getElementById('otpBtn').addEventListener('click', showApp);
document.getElementById('goSignup').addEventListener('click', showSignup);
document.getElementById('goLogin').addEventListener('click', showLogin);

/* ---- Login role tabs (visual only, demo) ---- */
document.getElementById('loginRoleTabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.wl-tab');
  if (!btn) return;
  document.querySelectorAll('#loginRoleTabs .wl-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
});

/* ---- Password show/hide ---- */
document.getElementById('togglePw').addEventListener('click', () => {
  const pwInput = document.getElementById('password');
  const isPw = pwInput.type === 'password';
  pwInput.type = isPw ? 'text' : 'password';
});

/* ---- Utility links (demo-only) ---- */
document.getElementById('forgotLink').addEventListener('click', async () => {
  const email = document.getElementById('email').value.trim();
  if (LIVE_MODE && email){
    const { error } = await supabaseClient.auth.resetPasswordForEmail(email);
    showToast(error ? error.message : 'Password reset link sent to your email.');
  } else {
    showToast('Password reset link sent to your email.');
  }
});
document.getElementById('resetSaved').addEventListener('click', () => showToast('Saved form data cleared.'));
document.getElementById('resetCache').addEventListener('click', () => showToast('App cache cleared.'));

/* ---- Sign up ---- */
document.getElementById('signupForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = document.getElementById('suName').value.trim();
  const email = document.getElementById('suEmail').value.trim();
  const pw = document.getElementById('suPassword').value.trim();
  const confirm = document.getElementById('suConfirm').value.trim();
  const firm = document.getElementById('suFirm').value.trim();
  const err = document.getElementById('signupError');

  if (!name || !email || !pw || !firm) {
    err.textContent = "Please fill in your name, email, password, and firm name to continue.";
    err.style.display = 'block';
    return;
  }
  if (pw !== confirm) {
    err.textContent = "Passwords don't match — check and try again.";
    err.style.display = 'block';
    return;
  }
  err.style.display = 'none';

  if (!LIVE_MODE){ showApp(); return; } // demo mode

  const { data, error } = await supabaseClient.auth.signUp({
    email, password: pw,
    options: { data: { full_name: name, firm_name: firm } }
  });
  if (error){
    err.textContent = error.message;
    err.style.display = 'block';
    return;
  }
  if (data.session){
    await loadCurrentProfile();
    enterAfterLogin();
  } else {
    showToast('Account created — check your email to confirm, then log in.');
    showLogin();
  }
});

/* ==========================================================================
   Dashboard
   ========================================================================== */
function renderDashboard(){
  const totalRevenue = INVOICES.filter(i => i.status === "paid").reduce((s,i)=>s+i.amount,0);
  const outstanding = INVOICES.filter(i => i.status !== "paid").reduce((s,i)=>s+i.amount,0);
  const overdueCount = INVOICES.filter(i => i.status === "overdue").length;
  const openTasks = TASKS.filter(t => !t.done).length;

  const stats = [
    { label: "Revenue this month", value: fmt(totalRevenue), delta: "▲ 12% vs July", tone: "up" },
    { label: "Outstanding invoices", value: fmt(outstanding), delta: `${overdueCount} overdue`, tone: "warn" },
    { label: "Open tasks", value: openTasks, delta: "2 due today", tone: "warn" },
    { label: "Active clients", value: CLIENTS.length, delta: "▲ 1 this month", tone: "up" },
  ];
  document.getElementById('statGrid').innerHTML = stats.map((s,idx) => `
    <div class="card stat-card">
      <span class="folio-no">f.${String(idx+1).padStart(2,'0')}</span>
      <div class="stat-label">${s.label}</div>
      <div class="stat-value">${s.value}</div>
      <div class="stat-delta ${s.tone}">${s.delta}</div>
    </div>`).join('');

  const pillClass = { paid: "pill-paid", pending: "pill-pending", overdue: "pill-overdue" };
  document.querySelector('#invoiceTable tbody').innerHTML = INVOICES.slice(0,5).map((inv, i) => `
    <tr><td class="folio-idx">${i+1}</td><td><strong>${inv.id}</strong></td><td>${inv.client}</td>
    <td class="tabular" style="color:var(--ink-soft);">${inv.date}</td>
    <td class="amount tabular">${fmt(inv.amount)}</td><td><span class="pill ${pillClass[inv.status]}">${inv.status}</span></td></tr>`).join('');

  renderAllTasks();
  renderAllDeadlines();
}

const urgencyStyle = {
  overdue: "background:var(--red-soft);color:var(--red);",
  warn: "background:var(--amber-soft);color:var(--amber);",
  ok: "background:var(--green-soft);color:var(--green);",
};

function renderTaskListInto(containerId){
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = TASKS.map(t => `
    <div class="task-row">
      <div class="task-check ${t.done ? 'checked' : ''}" data-id="${t.id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>
      </div>
      <div><div class="task-title ${t.done ? 'checked' : ''}">${t.title}</div><div class="task-meta">${t.meta}</div></div>
    </div>`).join('');
}
function renderAllTasks(){
  renderTaskListInto('taskList');
  renderTaskListInto('myWorkTaskList');
  document.querySelectorAll('.task-check').forEach(el => {
    el.addEventListener('click', () => {
      const task = TASKS.find(t => t.id === el.dataset.id);
      task.done = !task.done;
      renderAllTasks();
    });
  });
}
function renderDeadlineListInto(containerId){
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = DEADLINES.map(d => `
    <div class="deadline"><div><div class="deadline-name">${d.name}</div><div class="deadline-client">${d.client}</div></div>
    <div class="deadline-date" style="${urgencyStyle[d.urgency]}">${d.date}</div></div>`).join('');
}
function renderAllDeadlines(){
  renderDeadlineListInto('deadlineList');
  renderDeadlineListInto('myWorkDeadlineList');
}

/* ---- Dashboard sub-tabs (Getting Started / My Work / Dashboard) ---- */
document.getElementById('dashboardTabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.wb-tab');
  if (!btn) return;
  document.querySelectorAll('#dashboardTabs .wb-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.dtab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('dtab-' + btn.dataset.dtab).classList.add('active');
});

/* ---- Getting Started accordion ---- */
document.getElementById('wsAccordion').addEventListener('click', (e) => {
  const head = e.target.closest('.ws-step-head');
  if (!head) return;
  const step = head.closest('.ws-step');
  const wasActive = step.classList.contains('active');
  document.querySelectorAll('#wsAccordion .ws-step').forEach(s => s.classList.remove('active'));
  if (!wasActive) step.classList.add('active');
});

/* ==========================================================================
   Invoice form
   ========================================================================== */
function initInvoiceForm(){
  const clientSel = document.getElementById('client');
  const gstinInput = document.getElementById('gstin');
  clientSel.innerHTML = CLIENTS.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
  function syncGstin(){
    const c = CLIENTS.find(c => c.id === clientSel.value);
    gstinInput.value = c ? c.gstin : "";
  }
  clientSel.addEventListener('change', syncGstin);
  syncGstin();

  const tbody = document.querySelector('#lineItems tbody');
  let rowId = 0;

  function addRow(desc = "", qty = 1, rate = 0, gst = 18){
    const id = `row-${rowId++}`;
    const tr = document.createElement('tr');
    tr.id = id;
    tr.innerHTML = `
      <td><input type="text" class="desc-input" placeholder="e.g. Compliance retainer — July" value="${desc}"></td>
      <td><input type="number" class="qty-input tabular" min="0" value="${qty}"></td>
      <td><input type="number" class="rate-input tabular" min="0" value="${rate}"></td>
      <td><input type="number" class="gst-input tabular" min="0" value="${gst}"></td>
      <td class="amount-cell tabular">₹0</td>
      <td><button type="button" class="remove-row" title="Remove line">✕</button></td>`;
    tbody.appendChild(tr);
    tr.querySelectorAll('input').forEach(inp => inp.addEventListener('input', recalc));
    tr.querySelector('.remove-row').addEventListener('click', () => { tr.remove(); recalc(); });
    recalc();
  }

  function recalc(){
    let subtotal = 0, gstTotal = 0;
    tbody.querySelectorAll('tr').forEach(tr => {
      const qty = parseFloat(tr.querySelector('.qty-input').value) || 0;
      const rate = parseFloat(tr.querySelector('.rate-input').value) || 0;
      const gst = parseFloat(tr.querySelector('.gst-input').value) || 0;
      const base = qty * rate;
      const gstAmt = base * (gst / 100);
      subtotal += base; gstTotal += gstAmt;
      tr.querySelector('.amount-cell').textContent = fmt(base + gstAmt);
    });
    document.getElementById('subtotal').textContent = fmt(subtotal);
    document.getElementById('gstTotal').textContent = fmt(gstTotal);
    document.getElementById('grandTotal').textContent = fmt(subtotal + gstTotal);
  }

  document.getElementById('addRow').addEventListener('click', () => addRow());
  addRow("GST return filing — GSTR-3B", 1, 4500, 18);
  addRow("Bookkeeping — monthly retainer", 1, 12000, 18);

  document.getElementById('saveBtn').addEventListener('click', () => {
    const c = CLIENTS.find(c=>c.id===clientSel.value);
    const subtotal = parseCurrency(document.getElementById('subtotal').textContent);
    const gst = parseCurrency(document.getElementById('gstTotal').textContent);
    const newInv = {
      id: "INV-" + (1043 + INVOICES.filter(i=>i.id.startsWith('INV-104')).length),
      client: c.name,
      date: document.getElementById('invDate').value || "2026-08-08",
      amount: subtotal + gst,
      status: "pending",
    };
    INVOICES.unshift(newInv);
    renderDashboard();
    showToast(`${newInv.id} saved and sent to ${c.name}.`);

    lastSavedInvoiceId = newInv.id;
    const confirmBtn = document.getElementById('simulateConfirmBtn');
    const confirmStatus = document.getElementById('clientConfirmStatus');
    confirmBtn.disabled = false;
    confirmStatus.innerHTML = `<strong style="color:var(--amber);">Awaiting confirmation</strong> — ${newInv.id} was emailed to ${c.name}. No backend is wired up, so use the button to simulate their reply.`;

    goToPage('dashboard');
    const overviewTab = document.querySelector('#dashboardTabs .wb-tab[data-dtab="overview"]');
    if (overviewTab) overviewTab.click();
  });

  document.getElementById('simulateConfirmBtn').addEventListener('click', () => {
    if (!lastSavedInvoiceId) return;
    const inv = INVOICES.find(i => i.id === lastSavedInvoiceId);
    if (inv){
      inv.status = 'paid';
      renderDashboard();
      showToast(`${inv.client} confirmed ${inv.id} — marked paid.`);
    }
    document.getElementById('clientConfirmStatus').innerHTML = `<strong style="color:var(--green);">Confirmed by client</strong> — ${lastSavedInvoiceId} is now marked paid.`;
    document.getElementById('simulateConfirmBtn').disabled = true;
  });

  document.getElementById('downloadPdfBtn').addEventListener('click', () => {
    const c = CLIENTS.find(c => c.id === clientSel.value);
    const rows = Array.from(tbody.querySelectorAll('tr')).map(tr => ({
      desc: tr.querySelector('.desc-input').value || '—',
      qty: tr.querySelector('.qty-input').value || 0,
      rate: tr.querySelector('.rate-input').value || 0,
      gst: tr.querySelector('.gst-input').value || 0,
      amount: tr.querySelector('.amount-cell').textContent,
    }));
    const invId = lastSavedInvoiceId || ("INV-" + (1043 + INVOICES.filter(i=>i.id.startsWith('INV-104')).length));
    const html = `
      <div class="pdf-doc">
        <div class="pdf-head">
          <div>
            <div class="pdf-brand">Ledgerloop Advisory LLP</div>
            <div class="pdf-brand-sub">GSTIN 27AACCL9081Q1Z6 · hello@ledgerloop.app</div>
          </div>
          <div class="pdf-meta">
            <strong>${invId}</strong><br>
            Invoice date: ${document.getElementById('invDate').value}<br>
            Due date: ${document.getElementById('dueDate').value}
          </div>
        </div>
        <div class="pdf-parties">
          <div>
            <div class="pdf-party-label">Billed to</div>
            <div class="pdf-party-name">${c ? c.name : '—'}</div>
            <div class="pdf-party-detail">GSTIN: ${c ? c.gstin : '—'}</div>
          </div>
        </div>
        <table class="pdf-table">
          <thead><tr><th>Description</th><th class="amt">Qty</th><th class="amt">Rate</th><th class="amt">GST %</th><th class="amt">Amount</th></tr></thead>
          <tbody>${rows.map(r => `<tr><td>${r.desc}</td><td class="amt">${r.qty}</td><td class="amt">₹${r.rate}</td><td class="amt">${r.gst}%</td><td class="amt">${r.amount}</td></tr>`).join('')}</tbody>
        </table>
        <div class="pdf-totals">
          <div class="pdf-totals-row"><span>Subtotal</span><span>${document.getElementById('subtotal').textContent}</span></div>
          <div class="pdf-totals-row"><span>GST</span><span>${document.getElementById('gstTotal').textContent}</span></div>
          <div class="pdf-totals-row grand"><span>Total due</span><span>${document.getElementById('grandTotal').textContent}</span></div>
        </div>
        <div class="pdf-notes">${document.getElementById('notes').value}</div>
        <div class="pdf-foot">Generated by Ledgerloop · This is a demo document, not a tax invoice.</div>
      </div>`;
    document.getElementById('invoicePrintArea').innerHTML = html;
    showToast('Opening print dialog — choose "Save as PDF" as the destination.');
    setTimeout(() => window.print(), 200);
  });
}
let lastSavedInvoiceId = null;
function parseCurrency(text){
  return parseInt(String(text).replace(/[^\d]/g, ''), 10) || 0;
}

/* ==========================================================================
   Reports
   ========================================================================== */
function initReports(){
  const fClient = document.getElementById('fClient');
  fClient.innerHTML += CLIENTS.map(c => `<option value="${c.name}">${c.name}</option>`).join('');

  const pillClass = { paid: "pill-paid", pending: "pill-pending", overdue: "pill-overdue" };
  function renderTable(rows){
    document.getElementById('rowCount').textContent = `${rows.length} entries`;
    document.querySelector('#txTable tbody').innerHTML = rows.map((inv,i) => `
      <tr><td class="folio-idx">${i+1}</td><td><strong>${inv.id}</strong></td><td>${inv.client}</td>
      <td class="tabular" style="color:var(--ink-soft);">${inv.date}</td>
      <td class="amount tabular">${fmt(inv.amount)}</td><td><span class="pill ${pillClass[inv.status]}">${inv.status}</span></td></tr>`
    ).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--ink-soft);padding:24px 0;">No entries match these filters.</td></tr>`;
  }
  function applyFilters(){
    const client = fClient.value, status = document.getElementById('fStatus').value;
    const from = document.getElementById('fFrom').value, to = document.getElementById('fTo').value;
    renderTable(INVOICES.filter(inv => {
      if (client !== 'all' && inv.client !== client) return false;
      if (status !== 'all' && inv.status !== status) return false;
      if (from && inv.date < from) return false;
      if (to && inv.date > to) return false;
      return true;
    }));
  }
  document.getElementById('applyFilters').addEventListener('click', applyFilters);
  document.getElementById('resetFilters').addEventListener('click', () => {
    fClient.value = 'all'; document.getElementById('fStatus').value = 'all';
    document.getElementById('fFrom').value = '2026-07-01'; document.getElementById('fTo').value = '2026-08-08';
    applyFilters();
  });
  document.getElementById('exportBtn').addEventListener('click', () => {
    const rows = document.querySelectorAll('#txTable tbody tr');
    if (!rows.length || rows[0].children.length < 2) { showToast('No entries to export.'); return; }
    let csv = 'Invoice,Client,Date,Amount,Status\n';
    document.querySelectorAll('#txTable tbody tr').forEach(tr => {
      const cells = tr.querySelectorAll('td');
      if (cells.length < 6) return;
      const inv = cells[1].textContent.trim();
      const client = cells[2].textContent.trim();
      const date = cells[3].textContent.trim();
      const amount = cells[4].textContent.trim().replace(/[^\d.-]/g, '');
      const status = cells[5].textContent.trim();
      csv += `${inv},"${client}",${date},${amount},${status}\n`;
    });
    downloadCsv(csv, 'ledgerloop-transactions.csv');
    showToast('Ledger exported as CSV.');
  });
  renderTable(INVOICES);

  // Bar chart
  const svg = document.getElementById('barChart');
  const max = Math.max(...REVENUE_BY_MONTH.map(d => d.v));
  const chartW = 560, chartH = 220, padL = 40, padB = 26, padT = 10;
  const innerH = chartH - padB - padT;
  const barW = 46, gap = (chartW - padL - barW * REVENUE_BY_MONTH.length) / (REVENUE_BY_MONTH.length + 1);
  let svgContent = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${chartH-padB}" stroke="#D8D2C3"/>
    <line x1="${padL}" y1="${chartH-padB}" x2="${chartW}" y2="${chartH-padB}" stroke="#16233F" stroke-width="1.2"/>`;
  REVENUE_BY_MONTH.forEach((d, i) => {
    const h = (d.v / max) * innerH;
    const x = padL + gap + i * (barW + gap);
    const y = chartH - padB - h;
    const isLast = i === REVENUE_BY_MONTH.length - 1;
    svgContent += `
      <rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="3" fill="${isLast ? '#C98A2C' : '#1F7A5C'}" opacity="${isLast ? '0.9' : '1'}"/>
      <text x="${x + barW/2}" y="${chartH - 8}" text-anchor="middle" font-family="IBM Plex Mono" font-size="11" fill="#4B5670">${d.m}</text>
      <text x="${x + barW/2}" y="${y - 8}" text-anchor="middle" font-family="IBM Plex Mono" font-size="10.5" fill="#16233F">₹${Math.round(d.v/1000)}k</text>`;
  });
  svg.innerHTML = svgContent;

  // Donut chart
  const dsvg = document.getElementById('donutChart');
  const cx = 110, cy = 110, r = 80, strokeW = 26;
  const total = EXPENSE_CATEGORIES.reduce((s,c) => s + c.value, 0);
  let angleStart = -90, content = '';
  EXPENSE_CATEGORIES.forEach(cat => {
    const angle = (cat.value / total) * 360;
    const angleEnd = angleStart + angle;
    const largeArc = angle > 180 ? 1 : 0;
    const toRad = a => (a * Math.PI) / 180;
    const x1 = cx + r * Math.cos(toRad(angleStart)), y1 = cy + r * Math.sin(toRad(angleStart));
    const x2 = cx + r * Math.cos(toRad(angleEnd)), y2 = cy + r * Math.sin(toRad(angleEnd));
    content += `<path d="M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}" fill="none" stroke="${cat.color}" stroke-width="${strokeW}"/>`;
    angleStart = angleEnd;
  });
  content += `<text x="${cx}" y="${cy-2}" text-anchor="middle" font-family="IBM Plex Mono" font-size="13" fill="#16233F" font-weight="600">₹${Math.round(total/1000)}k</text>
              <text x="${cx}" y="${cy+14}" text-anchor="middle" font-family="IBM Plex Sans" font-size="10" fill="#4B5670">total spend</text>`;
  dsvg.innerHTML = content;
  document.getElementById('donutLegend').innerHTML = EXPENSE_CATEGORIES.map(c => `
    <div class="legend-item"><span class="legend-dot" style="background:${c.color};"></span>${c.label}</div>`).join('');
}

/* ==========================================================================
   Tasks (kanban)
   ========================================================================== */
function renderKanban(){
  const cols = [
    { key:"todo", label:"To do" },
    { key:"progress", label:"In progress" },
    { key:"done", label:"Done" },
  ];
  const priorityLabel = { high:"High", med:"Medium", low:"Low" };
  document.getElementById('kanbanBoard').innerHTML = cols.map(col => {
    const items = KANBAN_TASKS.filter(t => t.status === col.key);
    return `
      <div class="kanban-col">
        <h3>${col.label} <span>${items.length}</span></h3>
        ${items.map(t => `
          <div class="kanban-card" data-id="${t.id}" title="Click to move to the next stage">
            <div class="kc-title">${t.title}</div>
            <div class="kc-meta">
              <span class="kc-priority ${t.priority}">${priorityLabel[t.priority]}</span>
              <div style="display:flex;align-items:center;gap:6px;">
                <span>${t.due}</span>
                <span class="kc-avatar">${t.assignee}</span>
              </div>
            </div>
          </div>
        `).join('') || `<div class="kanban-empty">Nothing here</div>`}
      </div>`;
  }).join('');

  document.querySelectorAll('.kanban-card').forEach(card => {
    card.addEventListener('click', async () => {
      const task = KANBAN_TASKS.find(t => t.id === card.dataset.id);
      const order = ["todo","progress","done"];
      const next = order[Math.min(order.indexOf(task.status) + 1, order.length - 1)];
      task.status = next;
      renderKanban();
      if (LIVE_MODE && supabaseClient){
        const { error } = await supabaseClient.from('tasks').update({ status: next }).eq('id', task.id);
        if (error) showToast(error.message);
      }
    });
  });
}
document.getElementById('addTaskBtn').addEventListener('click', openNewTaskModal);

/* ---- Load real tasks from Supabase (live mode only) ---- */
async function loadKanbanTasks(){
  if (!LIVE_MODE || !supabaseClient) return;
  const { data, error } = await supabaseClient
    .from('tasks')
    .select('id, title, priority, status, due_date, clients(name), profiles!tasks_assigned_to_fkey(full_name)')
    .order('created_at', { ascending: false });
  if (error){ showToast(error.message); return; }
  const initials = (name) => (name || '?').trim().split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
  KANBAN_TASKS = data.map(t => ({
    id: t.id,
    title: t.title,
    client: t.clients ? t.clients.name : 'Internal',
    assignee: initials(t.profiles ? t.profiles.full_name : ''),
    priority: t.priority,
    due: t.due_date || 'Unscheduled',
    status: t.status,
  }));
  renderKanban();
}

/* ---- New task modal ---- */
async function openNewTaskModal(){
  let staffOptions = `<option value="PM">Priya Menon</option><option value="RS">Rohit Sharma</option><option value="AK">Ayesha Khan</option>`;
  let staffList = null;

  if (LIVE_MODE && supabaseClient){
    const { data, error } = await supabaseClient.from('profiles').select('id, full_name').eq('role', 'staff').eq('status', 'active');
    if (error){
      showToast(error.message);
    } else if (data && data.length){
      staffList = data;
      staffOptions = data.map(p => `<option value="${p.id}">${p.full_name || 'Unnamed staff'}</option>`).join('');
    } else {
      staffOptions = `<option value="">No approved staff yet — check Users & Roles</option>`;
    }
  }

  openModal('New task', `
    <div class="field"><label>Task title</label><input type="text" id="ntTitle" placeholder="e.g. File GSTR-1 — Kadam & Sons"></div>
    <div class="field-row">
      <div class="field"><label>Client</label>
        <select id="ntClient">
          <option value="Internal">Internal</option>
          ${CLIENTS.map(c => `<option value="${c.name}">${c.name}</option>`).join('')}
        </select>
      </div>
      <div class="field"><label>Assignee</label>
        <select id="ntAssignee">${staffOptions}</select>
      </div>
    </div>
    <div class="field-row">
      <div class="field"><label>Priority</label>
        <select id="ntPriority"><option value="low">Low</option><option value="med" selected>Medium</option><option value="high">High</option></select>
      </div>
      <div class="field"><label>Due</label><input type="text" id="ntDue" placeholder="e.g. Aug 20"></div>
    </div>
    <div class="notes-save-row"><button class="btn btn-primary btn-sm" id="ntSaveBtn">Add task</button></div>
  `);

  document.getElementById('ntSaveBtn').addEventListener('click', async () => {
    const title = document.getElementById('ntTitle').value.trim();
    if (!title){ showToast('Give the task a title first.'); return; }
    const assigneeValue = document.getElementById('ntAssignee').value;
    const priority = document.getElementById('ntPriority').value;
    const due = document.getElementById('ntDue').value.trim() || "Unscheduled";

    if (LIVE_MODE && supabaseClient && staffList){
      if (!assigneeValue){ showToast('Invite a staff member before assigning tasks.'); return; }
      const { error } = await supabaseClient.from('tasks').insert({
        title,
        assigned_to: assigneeValue,
        assigned_by: currentProfile ? currentProfile.id : null,
        priority,
        due_date: /^\d{4}-\d{2}-\d{2}$/.test(due) ? due : null,
        status: 'todo',
      });
      if (error){ showToast(error.message); return; }
      await loadKanbanTasks();
      closeModal();
      showToast('Task assigned — they\'ll see it next time they load their Work Board.');
      return;
    }

    KANBAN_TASKS.unshift({
      id: "k" + Date.now(),
      title,
      client: document.getElementById('ntClient').value,
      assignee: assigneeValue,
      priority,
      due,
      status: "todo",
    });
    renderKanban();
    closeModal();
    showToast('Task added to the Work Board.');
  });
}

/* ==========================================================================
   Clients
   ========================================================================== */
function renderClients(){
  const search = (document.getElementById('clientSearch').value || "").toLowerCase();
  const typeFilter = document.getElementById('clientTypeFilter').value;
  const rows = CLIENT_DIRECTORY.filter(c => {
    if (typeFilter !== 'all' && c.type !== typeFilter) return false;
    if (search && !(c.name.toLowerCase().includes(search) || c.gstin.toLowerCase().includes(search))) return false;
    return true;
  });
  document.getElementById('clientGrid').innerHTML = rows.map(c => `
    <div class="client-card">
      <div class="client-card-top">
        <div>
          <div class="client-name">${c.name}</div>
          <div class="client-gstin tabular">${c.gstin}</div>
        </div>
        <span class="client-type-tag">${c.type}</span>
      </div>
      <div class="client-meta-row"><span>Contact</span><span>${c.contact}</span></div>
      <div class="client-meta-row"><span>Phone</span><span class="tabular">${c.phone}</span></div>
      <div class="client-meta-row"><span>Open invoices</span><span class="tabular">${c.openInvoices}</span></div>
      <div class="client-meta-row"><span>Status</span><span class="pill ${c.status === 'Active' ? 'pill-paid' : 'pill-pending'}">${c.status}</span></div>
    </div>
  `).join('') || `<div style="grid-column:1/-1;text-align:center;color:var(--ink-soft);padding:30px 0;">No clients match your search.</div>`;
}
document.getElementById('clientSearch').addEventListener('input', renderClients);
document.getElementById('clientTypeFilter').addEventListener('change', renderClients);
document.getElementById('addClientBtn').addEventListener('click', openNewClientModal);

async function loadClients(){
  if (!LIVE_MODE || !supabaseClient) return;
  const { data, error } = await supabaseClient.from('clients').select('*').order('created_at', { ascending: false });
  if (error){ showToast(error.message); return; }
  CLIENT_DIRECTORY = data.map(c => ({
    id: c.id,
    name: c.name,
    gstin: c.gstin || '—',
    type: c.type || 'Business',
    contact: c.contact || '—',
    phone: c.phone || '—',
    openInvoices: 0,
    status: c.status || 'Onboarding',
  }));
  renderClients();
}

function openNewClientModal(){
  openModal('Add client', `
    <div class="field"><label>Client / firm name</label><input type="text" id="ncName" placeholder="e.g. Kavya Interiors"></div>
    <div class="field-row">
      <div class="field"><label>GSTIN</label><input type="text" id="ncGstin" placeholder="Optional"></div>
      <div class="field"><label>Type</label>
        <select id="ncType"><option value="Business">Business</option><option value="Practice">Practice / Professional</option></select>
      </div>
    </div>
    <div class="field-row">
      <div class="field"><label>Contact person</label><input type="text" id="ncContact" placeholder="Optional"></div>
      <div class="field"><label>Phone</label><input type="text" id="ncPhone" placeholder="Optional"></div>
    </div>
    <div class="notes-save-row"><button class="btn btn-primary btn-sm" id="ncSaveBtn">Add client</button></div>
  `);
  document.getElementById('ncSaveBtn').addEventListener('click', async () => {
    const name = document.getElementById('ncName').value.trim();
    if (!name){ showToast('Give the client a name first.'); return; }
    const gstin = document.getElementById('ncGstin').value.trim() || null;
    const type = document.getElementById('ncType').value;
    const contact = document.getElementById('ncContact').value.trim() || null;
    const phone = document.getElementById('ncPhone').value.trim() || null;

    if (LIVE_MODE && supabaseClient){
      const { error } = await supabaseClient.from('clients').insert({
        name, gstin, type, contact, phone,
        status: 'Onboarding',
        created_by: currentProfile ? currentProfile.id : null,
      });
      if (error){ showToast(error.message); return; }
      await loadClients();
      closeModal();
      showToast(name + ' added to Clients.');
      return;
    }

    CLIENT_DIRECTORY.unshift({ name, gstin: gstin || "—", type, contact: contact || "—", phone: phone || "—", openInvoices: 0, status: "Onboarding" });
    renderClients();
    closeModal();
    showToast(name + ' added to Clients.');
  });
}

/* ==========================================================================
   Filing calendar
   ========================================================================== */
function renderCalendar(){
  document.getElementById('calendarList').innerHTML = CALENDAR_ITEMS.map(group => `
    <div class="cal-group">
      <div class="cal-group-label">${group.month}</div>
      ${group.entries.map(e => `
        <div class="cal-item ${e.urgency}">
          <div class="cal-date-badge">${e.date}</div>
          <div class="cal-info">
            <div class="cal-title">${e.title}</div>
            <div class="cal-sub">${e.sub}</div>
          </div>
        </div>
      `).join('')}
    </div>
  `).join('');
}

/* ==========================================================================
   GST filings
   ========================================================================== */
function renderGst(){
  const filed = GST_FILINGS.filter(f => f.status === 'filed').length;
  const pending = GST_FILINGS.filter(f => f.status === 'pending').length;
  const overdue = GST_FILINGS.filter(f => f.status === 'overdue').length;
  document.getElementById('gstStatGrid').innerHTML = `
    <div class="card stat-card"><div class="stat-label">Filed this period</div><div class="stat-value">${filed}</div><div class="stat-delta up">On track</div></div>
    <div class="card stat-card"><div class="stat-label">Pending</div><div class="stat-value">${pending}</div><div class="stat-delta warn">Due soon</div></div>
    <div class="card stat-card"><div class="stat-label">Overdue</div><div class="stat-value">${overdue}</div><div class="stat-delta down">Needs action</div></div>
  `;
  const pillClass = { filed:"pill-paid", pending:"pill-pending", overdue:"pill-overdue" };
  document.querySelector('#gstTable tbody').innerHTML = GST_FILINGS.map((f,i) => `
    <tr>
      <td class="folio-idx">${i+1}</td>
      <td>${f.client}</td>
      <td class="tabular">${f.type}</td>
      <td>${f.period}</td>
      <td class="tabular" style="color:var(--ink-soft);">${f.due}</td>
      <td><span class="pill ${pillClass[f.status]}">${f.status}</span></td>
    </tr>
  `).join('');
}

/* ==========================================================================
   Documents
   ========================================================================== */
function renderDocuments(){
  document.querySelector('#docsTable tbody').innerHTML = DOCUMENTS.map((d,i) => `
    <tr>
      <td class="folio-idx">${i+1}</td>
      <td><strong>${d.name}</strong></td>
      <td>${d.client}</td>
      <td><span class="pill" style="background:#E6E9F0;color:var(--ink-soft);">${d.category}</span></td>
      <td class="tabular" style="color:var(--ink-soft);">${d.uploaded}</td>
      <td class="amount tabular">${d.size}</td>
    </tr>
  `).join('');
}

/* ==========================================================================
   Team chat
   ========================================================================== */
function renderChat(){
  const wrap = document.getElementById('chatMessages');
  wrap.innerHTML = CHAT_MESSAGES.map(m => `
    <div class="chat-msg ${m.mine ? 'mine' : ''}">
      <div class="chat-avatar">${m.initials}</div>
      <div>
        <div class="chat-name">${m.mine ? '' : m.name}</div>
        <div class="chat-bubble">${m.text}</div>
      </div>
    </div>
  `).join('');
  wrap.scrollTop = wrap.scrollHeight;
}
document.getElementById('chatForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;
  CHAT_MESSAGES.push({ name:"You", initials:"PM", text, mine:true });
  input.value = "";
  renderChat();
});

/* ==========================================================================
   Toasts & Utilities
   ========================================================================== */
function showToast(msg){
  const stack = document.getElementById('toastStack');
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  stack.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 300);
  }, 2800);
}
function downloadCsv(csv, filename){
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

/* Sidebar collapse */
document.getElementById('sidebarCollapseBtn').addEventListener('click', () => {
  const shell = document.querySelector('.shell');
  if (window.innerWidth <= 768) {
    shell.classList.toggle('mobile-nav-open');
  } else {
    shell.classList.toggle('sidebar-collapsed');
  }
});
document.getElementById('sidebarBackdrop').addEventListener('click', () => {
  document.querySelector('.shell').classList.remove('mobile-nav-open');
});

/* Create dropdown */
function closeAllDropdowns(except){
  document.querySelectorAll('.wb-dropdown.show').forEach(d => { if (d.id !== except) d.classList.remove('show'); });
}
document.getElementById('createBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  closeAllDropdowns('createDropdown');
  document.getElementById('createDropdown').classList.toggle('show');
});
document.getElementById('quickNewTask').addEventListener('click', () => {
  document.getElementById('createDropdown').classList.remove('show');
  goToPage('tasks');
  openNewTaskModal();
});
document.getElementById('quickNewClient').addEventListener('click', () => {
  document.getElementById('createDropdown').classList.remove('show');
  goToPage('clients');
  openNewClientModal();
});

/* Notifications */
function renderNotifications(){
  const urgencyColor = { overdue: "var(--red)", warn: "var(--amber)", ok: "var(--green)" };
  document.getElementById('notifList').innerHTML = DEADLINES.map(d => `
    <div class="wb-notif-item">
      <span class="wb-notif-dot" style="background:${urgencyColor[d.urgency]};"></span>
      <div class="wb-notif-text"><strong>${d.name}</strong> — ${d.client}<span>Due ${d.date}</span></div>
    </div>`).join('');
}
document.getElementById('bellBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  closeAllDropdowns('notifDropdown');
  document.getElementById('notifDropdown').classList.toggle('show');
  document.getElementById('bellDot').style.display = 'none';
});
document.addEventListener('click', () => closeAllDropdowns());

/* Profile dropdown */
document.getElementById('profileBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  closeAllDropdowns('profileDropdown');
  document.getElementById('profileDropdown').classList.toggle('show');
});

function openModal(title, bodyHtml){
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').innerHTML = bodyHtml;
  document.getElementById('modalOverlay').classList.add('show');
}
function closeModal(){
  document.getElementById('modalOverlay').classList.remove('show');
}
document.getElementById('modalCloseBtn').addEventListener('click', closeModal);
document.getElementById('modalOverlay').addEventListener('click', (e) => { if (e.target.id === 'modalOverlay') closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

document.getElementById('menuMyProfile').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  openModal('My Profile', `
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:6px;">
      <div class="wb-profile-avatar" style="width:52px;height:52px;font-size:15px;">PM</div>
      <div>
        <div style="font-size:15.5px;font-weight:600;">Priya Menon</div>
        <div style="font-size:12px;color:var(--ink-soft);">Owner · Aarav Textiles Pvt Ltd</div>
      </div>
    </div>
    <div class="mp-row"><span>Email</span><span>priya.menon@ledgerloop.app</span></div>
    <div class="mp-row"><span>Phone</span><span class="tabular">+91 98200 11122</span></div>
    <div class="mp-row"><span>Role</span><span>Owner · Full access</span></div>
    <div class="mp-row"><span>Branch</span><span>HQ</span></div>
    <div class="mp-row"><span>Member since</span><span>Jan 2026</span></div>
  `);
});

let SAVED_NOTES = "Follow up with Sundar Freight on the overdue invoice.\nAsk Rohit to double-check the TDS working before Friday.";
document.getElementById('menuNotes').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  openModal('Notes', `
    <textarea class="notes-textarea" id="notesArea" placeholder="Write a quick note to yourself…">${SAVED_NOTES}</textarea>
    <div class="notes-save-row"><button class="btn btn-primary btn-sm" id="notesSaveBtn">Save note</button></div>
  `);
  document.getElementById('notesSaveBtn').addEventListener('click', () => {
    SAVED_NOTES = document.getElementById('notesArea').value;
    showToast('Note saved.');
    closeModal();
  });
});

const ANNOUNCEMENTS = [
  { title: "New: Automations page", date: "7 Aug 2026", body: "Turn background rules on or off from Automations in the sidebar — reminders, nudges, and digests." },
  { title: "GST filing window opens", date: "5 Aug 2026", body: "GSTR-3B for July is now open for filing. Deadline is 20 August." },
  { title: "Scheduled maintenance", date: "2 Aug 2026", body: "Brief maintenance this Sunday, 1–2 AM IST. The workspace may be briefly unavailable." },
];
document.getElementById('menuAnnouncements').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  openModal('Announcements', ANNOUNCEMENTS.map(a => `
    <div class="announce-card">
      <div class="announce-title">${a.title}</div>
      <div class="announce-date">${a.date}</div>
      <div class="announce-body">${a.body}</div>
    </div>`).join(''));
});

document.getElementById('menuCalculator').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  openModal('Calculator', `
    <div class="calc-display" id="calcDisplay">0</div>
    <div class="calc-grid" id="calcGrid">
      <button class="calc-key clear" data-k="C">C</button>
      <button class="calc-key op" data-k="(">(</button>
      <button class="calc-key op" data-k=")">)</button>
      <button class="calc-key op" data-k="/">÷</button>
      <button class="calc-key" data-k="7">7</button>
      <button class="calc-key" data-k="8">8</button>
      <button class="calc-key" data-k="9">9</button>
      <button class="calc-key op" data-k="*">×</button>
      <button class="calc-key" data-k="4">4</button>
      <button class="calc-key" data-k="5">5</button>
      <button class="calc-key" data-k="6">6</button>
      <button class="calc-key op" data-k="-">−</button>
      <button class="calc-key" data-k="1">1</button>
      <button class="calc-key" data-k="2">2</button>
      <button class="calc-key" data-k="3">3</button>
      <button class="calc-key op" data-k="+">+</button>
      <button class="calc-key" data-k="0" style="grid-column:span 2;">0</button>
      <button class="calc-key" data-k=".">.</button>
      <button class="calc-key eq" data-k="=">=</button>
    </div>
  `);
  let expr = '';
  const display = document.getElementById('calcDisplay');
  document.getElementById('calcGrid').addEventListener('click', (e) => {
    const btn = e.target.closest('.calc-key');
    if (!btn) return;
    const k = btn.dataset.k;
    if (k === 'C'){ expr = ''; display.textContent = '0'; return; }
    if (k === '='){
      if (!/^[0-9+\-*/().\s]+$/.test(expr)){ display.textContent = 'Error'; expr = ''; return; }
      try { const result = Function('"use strict";return (' + expr + ')')(); display.textContent = String(result); expr = String(result); }
      catch(err){ display.textContent = 'Error'; expr = ''; }
      return;
    }
    expr += k;
    display.textContent = expr;
  });
});

document.getElementById('menuResetSaved').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  showToast('Saved workspace data has been reset.');
});
document.getElementById('menuResetCache').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  showToast('App cache cleared.');
});

document.getElementById('menuSignOut').addEventListener('click', () => {
  document.getElementById('profileDropdown').classList.remove('show');
  signOutUser();
});

/* Lock toggle */
let workspaceLocked = false;
document.getElementById('lockBtn').addEventListener('click', () => {
  workspaceLocked = !workspaceLocked;
  const icon = document.getElementById('lockIcon');
  const btn = document.getElementById('lockBtn');
  if (workspaceLocked){
    icon.innerHTML = '<rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0110 0v1"/>';
    btn.title = 'Workspace locked';
    showToast('Workspace locked.');
  } else {
    icon.innerHTML = '<rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/>';
    btn.title = 'Workspace unlocked';
    showToast('Workspace unlocked.');
  }
});

/* Command palette */
const CMDK_INDEX = [
  { label: "Dashboard", sub: "Workspace setup & overview", page: "dashboard" },
  { label: "Work Board", sub: "Kanban tasks", page: "tasks" },
  { label: "Billing / New invoice", sub: "Create an invoice", page: "invoice" },
  { label: "Clients", sub: "Client directory", page: "clients" },
  { label: "Reports", sub: "Revenue & transaction ledger", page: "reports" },
  { label: "Filing calendar", sub: "Statutory deadlines", page: "calendar" },
  { label: "GST filings", sub: "Return status", page: "gst" },
  { label: "Automations", sub: "Background rules", page: "automations" },
  { label: "Leads", sub: "Pipeline", page: "leads" },
  { label: "Attendance", sub: "Team check-ins", page: "attendance" },
  { label: "Documents", sub: "Client files", page: "documents" },
  { label: "Team chat", sub: "Internal thread", page: "chat" },
];
function openCmdk(){
  document.getElementById('cmdkOverlay').classList.add('show');
  const input = document.getElementById('cmdkInput');
  input.value = '';
  renderCmdkResults('');
  setTimeout(() => input.focus(), 10);
}
function closeCmdk(){
  document.getElementById('cmdkOverlay').classList.remove('show');
}
function renderCmdkResults(query){
  const q = query.trim().toLowerCase();
  let items = CMDK_INDEX;
  let clientHits = [];
  if (q){
    items = CMDK_INDEX.filter(i => i.label.toLowerCase().includes(q) || i.sub.toLowerCase().includes(q));
    clientHits = CLIENT_DIRECTORY.filter(c => c.name.toLowerCase().includes(q)).slice(0,4)
      .map(c => ({ label: c.name, sub: "Client · " + c.gstin, page: "clients" }));
  }
  const all = [...items, ...clientHits];
  const results = document.getElementById('cmdkResults');
  if (!all.length){
    results.innerHTML = `<div class="cmdk-empty">No matches for "${query}"</div>`;
    return;
  }
  results.innerHTML = all.map((i, idx) => `
    <div class="cmdk-result${idx===0?' active':''}" data-page="${i.page}">
      <span>${i.label}</span><span class="cmdk-result-sub">${i.sub}</span>
    </div>`).join('');
  results.querySelectorAll('.cmdk-result').forEach(el => {
    el.addEventListener('click', () => { goToPage(el.dataset.page); closeCmdk(); });
  });
}
document.getElementById('searchBtn').addEventListener('click', openCmdk);
document.getElementById('cmdkOverlay').addEventListener('click', (e) => { if (e.target.id === 'cmdkOverlay') closeCmdk(); });
document.getElementById('cmdkInput').addEventListener('input', (e) => renderCmdkResults(e.target.value));
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k'){ e.preventDefault(); openCmdk(); }
  if (e.key === 'Escape') closeCmdk();
});

/* Attendance */
let attendanceIn = false;
const STAFF_LIVE = [
  { name:"Priya Menon", initials:"PM", checkedInAt: null },
  { name:"Rohit Sharma", initials:"RS", checkedInAt: new Date(Date.now() - 3 * 3600000 - 12 * 60000) },
  { name:"Ayesha Khan", initials:"AK", checkedInAt: new Date(Date.now() - 4 * 3600000 - 40 * 60000) },
];
function formatDuration(ms){
  const mins = Math.floor(ms / 60000);
  const h = Math.floor(mins / 60), m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function renderLiveAttendance(){
  document.getElementById('attClock').textContent = new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
  document.getElementById('attLiveGrid').innerHTML = STAFF_LIVE.map(s => {
    const present = !!s.checkedInAt;
    const duration = present ? formatDuration(Date.now() - s.checkedInAt.getTime()) : '—';
    return `
    <div class="card stat-card">
      <div class="stat-label">${s.name}</div>
      <div class="stat-value" style="font-size:18px;">${present ? 'Checked in' : 'Not checked in'}</div>
      <div class="stat-delta ${present ? 'up' : 'warn'}">${present ? 'Working ' + duration : 'Awaiting check-in'}</div>
    </div>`;
  }).join('');
}
setInterval(renderLiveAttendance, 30000);

document.getElementById('attendanceBtn').addEventListener('click', () => {
  attendanceIn = !attendanceIn;
  const label = document.getElementById('attendanceLabel');
  const now = new Date();
  const nowStr = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  const me = STAFF_LIVE[0];
  if (attendanceIn){
    label.textContent = 'Attendance Out';
    me.checkedInAt = now;
    ATTENDANCE_LOG.unshift({ name: me.name, date: "Today", in: nowStr, out: "—", status: "On time" });
    showToast('Checked in at ' + nowStr + '.');
  } else {
    label.textContent = 'Attendance In';
    const todays = ATTENDANCE_LOG.find(r => r.name === me.name && r.date === "Today");
    if (todays) todays.out = nowStr;
    me.checkedInAt = null;
    showToast('Checked out at ' + nowStr + '.');
  }
  renderLiveAttendance();
  renderAttendancePage();
});

/* Roles */
const TEAM_ROLES = [
  { name: "Priya Menon", role: "Owner · Full access" },
  { name: "Rohit Sharma", role: "Staff · Billing & clients" },
  { name: "Ayesha Khan", role: "Staff · Clients & tasks" },
];
document.getElementById('inviteBtn').addEventListener('click', () => {
  const name = prompt("Team member's name or email to invite:");
  if (!name) return;
  TEAM_ROLES.push({ name, role: "Staff · Pending access setup" });
  document.getElementById('roleList').classList.add('show');
  renderRoleList();
  showToast(name + ' invited to the workspace.');
});
document.getElementById('roleBtn').addEventListener('click', () => {
  const list = document.getElementById('roleList');
  list.classList.toggle('show');
  if (list.classList.contains('show')) renderRoleList();
});
function renderRoleList(){
  document.getElementById('roleList').innerHTML = TEAM_ROLES.map(r => `
    <div class="ws-role-row"><strong>${r.name}</strong><span>${r.role}</span></div>`).join('');
}

/* Automations */
let AUTOMATIONS = [
  { id:"a1", name:"GST due-date reminders", desc:"Notifies clients 3 days before their GSTR-3B deadline.", on:true },
  { id:"a2", name:"Overdue invoice nudges", desc:"Sends a WhatsApp reminder when an invoice is 2+ days overdue.", on:true },
  { id:"a3", name:"New client welcome pack", desc:"Emails onboarding checklist and document requests to new clients.", on:false },
  { id:"a4", name:"Weekly team digest", desc:"Sends a Friday summary of open tasks to the whole team.", on:true },
];
function renderAutomations(){
  document.getElementById('automationList').innerHTML = AUTOMATIONS.map(a => `
    <div class="card panel" style="padding:18px 20px;display:flex;align-items:center;justify-content:space-between;gap:16px;">
      <div>
        <div style="font-weight:600;font-size:14.5px;margin-bottom:4px;">${a.name}</div>
        <div style="font-size:13px;color:var(--ink-soft);">${a.desc}</div>
      </div>
      <button class="btn ${a.on ? 'btn-primary' : 'btn-ghost'} btn-sm" data-id="${a.id}" style="min-width:74px;justify-content:center;">${a.on ? 'On' : 'Off'}</button>
    </div>`).join('');
  document.querySelectorAll('#automationList button[data-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const a = AUTOMATIONS.find(x => x.id === btn.dataset.id);
      a.on = !a.on;
      renderAutomations();
      showToast(a.name + (a.on ? ' turned on.' : ' turned off.'));
    });
  });
}
document.getElementById('addAutomationBtn').addEventListener('click', () => {
  const name = prompt("Automation name:");
  if (!name) return;
  AUTOMATIONS.unshift({ id:"a"+Date.now(), name, desc:"Custom automation — configure trigger and action.", on:true });
  renderAutomations();
  showToast(name + ' created.');
});

/* Leads */
let LEADS = [
  { name:"Sundaram Motors", source:"Referral", stage:"Discovery call", owner:"Priya", value: 60000 },
  { name:"Blue Wave Exports", source:"Website", stage:"Proposal sent", owner:"Rohit", value: 145000 },
  { name:"Kavya Interiors", source:"Referral", stage:"Negotiation", owner:"Ayesha", value: 38000 },
  { name:"Orbit Logistics", source:"Cold outreach", stage:"Discovery call", owner:"Priya", value: 92000 },
];
function renderLeads(){
  document.getElementById('leadCount').textContent = `${LEADS.length} leads`;
  document.querySelector('#leadsTable tbody').innerHTML = LEADS.map((l,i) => `
    <tr><td class="folio-idx">${i+1}</td><td><strong>${l.name}</strong></td><td>${l.source}</td>
    <td><span class="pill pill-pending">${l.stage}</span></td><td>${l.owner}</td>
    <td class="amount tabular">${fmt(l.value)}</td></tr>`).join('');
}
document.getElementById('addLeadBtn').addEventListener('click', () => {
  const name = prompt("Lead / prospect name:");
  if (!name) return;
  LEADS.unshift({ name, source:"Manual entry", stage:"Discovery call", owner:"Priya", value: 0 });
  renderLeads();
  showToast(name + ' added to the pipeline.');
});

/* Attendance */
const ATTENDANCE_LOG = [
  { name:"Priya Menon", date:"Mon, 4 Aug", in:"09:58 AM", out:"06:42 PM", status:"On time" },
  { name:"Rohit Sharma", date:"Mon, 4 Aug", in:"10:22 AM", out:"06:30 PM", status:"Late" },
  { name:"Ayesha Khan", date:"Mon, 4 Aug", in:"09:50 AM", out:"06:15 PM", status:"On time" },
  { name:"Priya Menon", date:"Tue, 5 Aug", in:"09:55 AM", out:"06:48 PM", status:"On time" },
  { name:"Rohit Sharma", date:"Tue, 5 Aug", in:"09:59 AM", out:"06:20 PM", status:"On time" },
  { name:"Ayesha Khan", date:"Tue, 5 Aug", in:"—", out:"—", status:"On leave" },
];
function renderAttendancePage(){
  const pillClass = { "On time":"pill-paid", "Late":"pill-pending", "On leave":"pill-overdue" };
  document.querySelector('#attendanceTable tbody').innerHTML = ATTENDANCE_LOG.map((r,i) => `
    <tr><td class="folio-idx">${i+1}</td><td><strong>${r.name}</strong></td><td>${r.date}</td>
    <td class="tabular">${r.in}</td><td class="tabular">${r.out}</td>
    <td><span class="pill ${pillClass[r.status]}">${r.status}</span></td></tr>`).join('');
}

/* Generic sub-pages */
const GENERIC_PAGES = {
  boards: { title:"Boards", subtitle:"Active work boards across the practice.", type:"cards", items:[
    { title:"Client Onboarding", desc:"Steps for bringing a new client fully onto the platform, from KYC to first invoice.", meta:"6 of 8 cards done" },
    { title:"GST Filing Season", desc:"Every return due this cycle, grouped by client and filing type.", meta:"9 of 14 cards done" },
    { title:"Internal Ops", desc:"Team-facing admin: renewals, licenses, and internal housekeeping.", meta:"2 of 5 cards done" },
  ]},
  communications: { title:"Communications", subtitle:"Recent outbound messages to clients across every channel.", type:"table",
    columns:["Client","Channel","Message","Status"],
    rows:[
      ["Aarav Textiles Pvt Ltd","Email","GSTR-3B due-date reminder","Sent"],
      ["Sundar Freight Co.","WhatsApp","Payment follow-up — INV-1040","Delivered"],
      ["Nimbus Digital Studio","Email","TDS challan requested","Read"],
      ["Kadam & Sons Retail","WhatsApp","Filing confirmation","Sent"],
    ]},
  reminders: { title:"Reminders", subtitle:"Personal and team reminders, separate from client deadlines.", type:"table",
    columns:["Reminder","Client","Due","Status"],
    rows:[
      ["Call back about retainer renewal","Vantage Realty Group","Aug 13","Pending"],
      ["Chase signed engagement letter","R. Iyer & Associates","Aug 14","Pending"],
      ["Review Q1 TDS working before sign-off","Nimbus Digital Studio","Aug 10","Done"],
    ]},
  "users-roles": { title:"Users & Roles", subtitle:"Everyone with access to this workspace and what they can see.", type:"table",
    columns:["Name","Role","Branch","Status"],
    rows:[
      ["Priya Menon","Owner · Full access","HQ","Active"],
      ["Rohit Sharma","Staff · Billing & clients","HQ","Active"],
      ["Ayesha Khan","Staff · Clients & tasks","HQ","Active"],
      ["Karan Mehta","Staff · View only","Branch 2","Invited"],
    ]},
  quotation: { title:"Quotation", subtitle:"Quotes sent to prospective and existing clients.", type:"table",
    columns:["Quote #","Client","Amount","Valid till","Status"],
    rows:[
      ["QTN-0231","Blue Wave Exports","₹1,45,000","Aug 20","Awaiting response"],
      ["QTN-0230","Orbit Logistics","₹92,000","Aug 18","Accepted"],
      ["QTN-0229","Kavya Interiors","₹38,000","Aug 12","Expired"],
    ]},
  receipts: { title:"Receipts", subtitle:"Payments received against issued invoices.", type:"table",
    columns:["Receipt #","Client","Amount","Date","Mode"],
    rows:[
      ["RCT-3311","Aarav Textiles Pvt Ltd","₹84,200","Aug 6","Bank transfer"],
      ["RCT-3310","Vantage Realty Group","₹56,000","Jul 27","UPI"],
      ["RCT-3309","Kadam & Sons Retail","₹21,300","Jul 23","Cheque"],
    ]},
  outstanding: { title:"Outstanding", subtitle:"Invoices not yet marked paid, pulled live from your ledger.", type:"table-dynamic" },
  "billing-settings": { title:"Billing Settings", subtitle:"Defaults applied to every new invoice and quote.", type:"fields", fields:[
    ["Default GST rate","18%"], ["Invoice prefix","INV-"], ["Quotation prefix","QTN-"],
    ["Default payment terms","14 days"], ["Currency","INR (₹)"],
  ]},
  "client-groups": { title:"Groups", subtitle:"Clients organized into groups for faster filtering and reporting.", type:"cards", items:[
    { title:"Retail clients", desc:"Shops and storefronts billed on a monthly retainer.", meta:"12 clients" },
    { title:"Manufacturing", desc:"Production and export-oriented businesses with GST-heavy filings.", meta:"5 clients" },
    { title:"Service businesses", desc:"Consultancies, studios, and professional practices.", meta:"9 clients" },
  ]},
  "client-services": { title:"Services", subtitle:"Services offered to clients and their default billing rate.", type:"table",
    columns:["Service","Category","Default rate"],
    rows:[
      ["GSTR-3B filing","Compliance","₹4,500 / month"],
      ["Bookkeeping — monthly retainer","Accounting","₹12,000 / month"],
      ["TDS working & filing","Compliance","₹3,500 / quarter"],
      ["Tax audit (Form 3CD)","Audit","₹35,000 / year"],
    ]},
  "referred-by": { title:"Referred By", subtitle:"Where each client relationship originated.", type:"table",
    columns:["Client","Referred by","Date"],
    rows:[
      ["Aarav Textiles Pvt Ltd","Existing client — Kadam & Sons","Feb 2025"],
      ["Vantage Realty Group","Website enquiry","Jun 2026"],
      ["Nimbus Digital Studio","Rohit Sharma (staff)","Nov 2025"],
    ]},
  "client-tags": { title:"Tags", subtitle:"Tags used across the client directory.", type:"tags", items:[
    ["Priority",6],["GST only",9],["Retainer",4],["New",3],["Export",2],
  ]},
  "client-settings": { title:"Client Settings", subtitle:"Defaults applied when a new client is added.", type:"fields", fields:[
    ["Default status","Onboarding"], ["Default type","Business"], ["Auto-assign owner","Off"],
  ]},
  "automation-email": { title:"Email Automations", subtitle:"Templated emails your automations can send.", type:"table",
    columns:["Template","Trigger","Last sent"],
    rows:[
      ["GST reminder — 3 days before due","GSTR-3B due-date reminders","Aug 7"],
      ["Overdue invoice nudge","Overdue invoice nudges","Aug 5"],
      ["Welcome & document checklist","New client welcome pack","—"],
    ]},
  "income-tax": { title:"Income Tax", subtitle:"Income tax filings across all clients.", type:"table",
    tutorialUrl:"https://www.youtube.com/results?search_query=income+tax+return+ITR+filing+tutorial",
    columns:["Client","Assessment year","Filing type","Due date","Status"],
    rows:[
      ["Aarav Textiles Pvt Ltd","AY 2026-27","ITR-6","Oct 31, 2026","Pending"],
      ["R. Iyer & Associates","AY 2026-27","ITR-4","Jul 31, 2026","Filed"],
      ["Vantage Realty Group","AY 2026-27","ITR-6","Oct 31, 2026","Pending"],
    ]},
  tds: { title:"TDS", subtitle:"Quarterly TDS returns by client.", type:"table",
    tutorialUrl:"https://www.youtube.com/results?search_query=TDS+return+filing+tutorial",
    columns:["Client","Section","Quarter","Due date","Status"],
    rows:[
      ["Nimbus Digital Studio","194J","Q1 FY26-27","Jul 31, 2026","Filed"],
      ["Sundar Freight Co.","194C","Q1 FY26-27","Jul 31, 2026","Pending"],
      ["Aarav Textiles Pvt Ltd","192","Q1 FY26-27","Jul 31, 2026","Filed"],
    ]},
  "notices-settings": { title:"Notices Settings", subtitle:"How and when compliance notices reach the team.", type:"toggles", items:[
    { label:"Email notifications", desc:"Send filing reminders to the team inbox.", on:true },
    { label:"WhatsApp notifications", desc:"Send urgent overdue notices via WhatsApp.", on:true },
    { label:"Weekly digest", desc:"Roll up all upcoming notices into one Friday email.", on:false },
  ]},
  dsc: { title:"DSC", subtitle:"Digital Signature Certificates on file for clients and staff.", type:"table",
    columns:["Holder","Client","Class","Issued","Expiry","Status"],
    rows:[
      ["Aarav Deshmukh","Aarav Textiles Pvt Ltd","Class 3","Feb 2025","Feb 2028","Active"],
      ["Priya Menon","Internal","Class 3","Jan 2026","Jan 2029","Active"],
      ["Neha Vantage","Vantage Realty Group","Class 3","Aug 2023","Aug 2026","Expiring soon"],
    ]},
  "expiry-management": { title:"Expiry Management", subtitle:"Licenses, certificates, and registrations nearing expiry.", type:"table",
    columns:["Item","Client","Type","Expiry date","Days left"],
    rows:[
      ["Trade license","Kadam & Sons Retail","License","Sep 2, 2026","22"],
      ["DSC — Neha Vantage","Vantage Realty Group","Certificate","Aug 2026","6"],
      ["Shop & Establishment cert.","Sundar Freight Co.","Registration","Nov 2026","93"],
    ]},
  "task-report": { title:"Task Report", subtitle:"How the team is tracking against open work.", type:"stats", items:[
    { label:"Completed this week", value:"18", tone:"up" },
    { label:"Overdue", value:"3", tone:"warn" },
    { label:"Avg. completion time", value:"2.4 days", tone:"ok" },
    { label:"Open right now", value:String(KANBAN_TASKS.filter(t=>t.status!=="done").length), tone:"warn" },
  ]},
  "pricing-plan": { title:"Pricing Plan", subtitle:"Your workspace is currently on the Growth plan.", type:"pricing" },
  "learning-studio": { title:"Learning Studio", subtitle:"Short guides plus video walkthroughs on YouTube for the compliance topics that come up most.", type:"cards", items:[
    { title:"Setting up your first workspace", desc:"Invite your team, add clients, and configure billing defaults in one pass.", meta:"4 min read" },
    { title:"Automations 101", desc:"How triggers, conditions, and templated emails fit together.", meta:"6 min read" },
    { title:"Reading the Reports dashboard", desc:"Understand revenue-by-month and expense-by-category at a glance.", meta:"3 min read" },
    { title:"Filing calendar walkthrough", desc:"Never miss a GST, TDS, or income tax deadline again.", meta:"5 min read" },
    { title:"GST return filing (GSTR-1 & 3B)", desc:"Curated YouTube search results for step-by-step GST filing walkthroughs.", meta:"YouTube ↗", url:"https://www.youtube.com/results?search_query=GST+return+filing+tutorial+GSTR-1+GSTR-3B" },
    { title:"Income Tax Return (ITR) filing", desc:"Curated YouTube search results for ITR filing walkthroughs by assessee type.", meta:"YouTube ↗", url:"https://www.youtube.com/results?search_query=income+tax+return+ITR+filing+tutorial" },
    { title:"TDS return filing basics", desc:"Curated YouTube search results covering TDS sections, quarters, and challans.", meta:"YouTube ↗", url:"https://www.youtube.com/results?search_query=TDS+return+filing+tutorial" },
    { title:"Writing a professional invoice", desc:"Curated YouTube search results on what a compliant, client-ready invoice should include.", meta:"YouTube ↗", url:"https://www.youtube.com/results?search_query=how+to+create+a+professional+invoice+GST" },
  ]},
  "company-profile": { title:"Company Profile", subtitle:"Details shown on invoices and client-facing documents.", type:"fields", fields:[
    ["Firm name","Ledgerloop Advisory LLP"], ["GSTIN","27AACCL9081Q1Z6"],
    ["Registered address","4th Floor, Prestige Towers, Hyderabad, Telangana"],
    ["Phone","+91 87587 66555"], ["Email","hello@ledgerloop.app"],
  ]},
  configuration: { title:"Configuration", subtitle:"Workspace-wide preferences.", type:"toggles", items:[
    { label:"Auto-reminders", desc:"Automatically nudge clients ahead of filing deadlines.", on:true },
    { label:"Two-factor login", desc:"Require a one-time code for every team sign-in.", on:true },
    { label:"Financial year starts in April", desc:"Off uses a January–December calendar instead.", on:true },
    { label:"Allow staff to edit invoices", desc:"When off, only Owners can edit a sent invoice.", on:false },
  ]},
};

async function renderUsersRolesLive(section){
  section.innerHTML = `<div class="topbar"><div><h1>Users & Roles</h1><div class="sub">Loading your team…</div></div></div>`;
  const { data, error } = await supabaseClient.from('profiles').select('*').order('created_at', { ascending: false });
  if (error){
    section.innerHTML = `<div class="topbar"><div><h1>Users & Roles</h1><div class="sub">${error.message}</div></div></div>`;
    return;
  }
  const roleLabel = { owner: 'Owner · Full access', staff: 'Staff', client: 'Client' };
  const pending = data.filter(p => p.status === 'pending');
  const others = data.filter(p => p.status !== 'pending');

  const pendingBody = pending.length ? `
    <div class="card panel" style="padding-bottom:20px;margin-bottom:20px;">
      <div class="panel-head"><h2>Pending approval</h2><span style="font-size:12px;color:var(--ink-soft);">${pending.length} waiting</span></div>
      <div class="table-responsive">
        <table class="ledger">
          <thead><tr><th style="width:32px;">#</th><th>Name</th><th>Email</th><th>Requested</th><th></th></tr></thead>
          <tbody>${pending.map((p, i) => `
            <tr>
              <td class="folio-idx">${i + 1}</td>
              <td><strong>${p.full_name || 'Unnamed'}</strong></td>
              <td>${p.email || '—'}</td>
              <td class="tabular" style="color:var(--ink-soft);">${new Date(p.created_at).toLocaleDateString()}</td>
              <td style="display:flex;gap:8px;justify-content:flex-end;">
                <button class="btn btn-primary btn-sm" data-approve="${p.id}">Accept</button>
                <button class="btn btn-ghost btn-sm" data-reject="${p.id}">Reject</button>
              </td>
            </tr>`).join('')}</tbody>
        </table>
      </div>
    </div>` : `
    <div class="card panel" style="padding:20px;margin-bottom:20px;color:var(--ink-soft);font-size:13.5px;">No pending requests right now.</div>`;

  const teamBody = `
    <div class="card panel" style="padding-bottom:20px;">
      <div class="panel-head"><h2>Team</h2><span style="font-size:12px;color:var(--ink-soft);">${others.length} people</span></div>
      <div class="table-responsive">
        <table class="ledger">
          <thead><tr><th style="width:32px;">#</th><th>Name</th><th>Role</th><th>Status</th></tr></thead>
          <tbody>${others.map((p, i) => `
            <tr>
              <td class="folio-idx">${i + 1}</td>
              <td><strong>${p.full_name || p.email || 'Unnamed'}</strong></td>
              <td>${roleLabel[p.role] || p.role}</td>
              <td><span class="pill ${p.status === 'active' ? 'pill-paid' : 'pill-overdue'}">${p.status}</span></td>
            </tr>`).join('') || `<tr><td colspan="4" style="text-align:center;color:var(--ink-soft);padding:24px 0;">No one here yet.</td></tr>`}</tbody>
        </table>
      </div>
    </div>`;

  section.innerHTML = `<div class="topbar"><div><h1>Users & Roles</h1><div class="sub">Approve staff sign-up requests and see who has access.</div></div></div>${pendingBody}${teamBody}`;

  section.querySelectorAll('[data-approve]').forEach(btn => btn.addEventListener('click', async () => {
    const { error } = await supabaseClient.from('profiles').update({ status: 'active' }).eq('id', btn.dataset.approve);
    if (error){ showToast(error.message); return; }
    showToast('Staff member approved — they can log in now.');
    renderUsersRolesLive(section);
  }));
  section.querySelectorAll('[data-reject]').forEach(btn => btn.addEventListener('click', async () => {
    const { error } = await supabaseClient.from('profiles').update({ status: 'rejected' }).eq('id', btn.dataset.reject);
    if (error){ showToast(error.message); return; }
    showToast('Request rejected.');
    renderUsersRolesLive(section);
  }));
}

function renderGenericPage(key){
  const section = document.getElementById('page-generic');

  if (key === 'users-roles' && LIVE_MODE && supabaseClient){
    renderUsersRolesLive(section);
    return;
  }

  const cfg = GENERIC_PAGES[key];
  if (!cfg){
    section.innerHTML = `<div class="topbar"><div><h1>Not found</h1><div class="sub">This section doesn't exist in the demo yet.</div></div></div>`;
    return;
  }
  let body = '';

  if (key === 'outstanding'){
    const rows = INVOICES.filter(i => i.status !== 'paid');
    const today = new Date('2026-08-08');
    body = `
      <div class="card panel" style="padding-bottom:20px;">
        <div class="panel-head"><h2>Outstanding invoices</h2><span style="font-size:12px;color:var(--ink-soft);">${rows.length} entries</span></div>
        <div class="table-responsive">
          <table class="ledger">
            <thead><tr><th style="width:32px;">#</th><th>Invoice</th><th>Client</th><th class="amount">Amount</th><th>Days overdue</th><th>Status</th></tr></thead>
            <tbody>${rows.map((inv,i) => {
              const days = Math.max(0, Math.round((today - new Date(inv.date)) / 86400000) - 14);
              const pillClass = inv.status === 'overdue' ? 'pill-overdue' : 'pill-pending';
              return `<tr><td class="folio-idx">${i+1}</td><td><strong>${inv.id}</strong></td><td>${inv.client}</td>
                <td class="amount tabular">${fmt(inv.amount)}</td><td class="tabular">${days > 0 ? days : '—'}</td>
                <td><span class="pill ${pillClass}">${inv.status}</span></td></tr>`;
            }).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--ink-soft);padding:24px 0;">Nothing outstanding right now.</td></tr>'}</tbody>
          </table>
        </div>
      </div>`;
  } else if (cfg.type === 'table'){
    body = `
      <div class="card panel" style="padding-bottom:20px;">
        <div class="panel-head"><h2>${cfg.title}</h2><span style="font-size:12px;color:var(--ink-soft);">${cfg.rows.length} entries</span></div>
        <div class="table-responsive">
          <table class="ledger">
            <thead><tr>${cfg.columns.map(c => `<th${/amount|value/i.test(c)?' class="amount"':''}>${c}</th>`).join('')}</tr></thead>
            <tbody>${cfg.rows.map(r => `<tr>${r.map(cell => `<td>${
              /^(Filed|Active|Sent|Accepted|Done|Delivered|Read)$/.test(cell) ? `<span class="pill pill-paid">${cell}</span>` :
              /^(Pending|Awaiting response|Invited|Onboarding|Expiring soon)$/.test(cell) ? `<span class="pill pill-pending">${cell}</span>` :
              /^(Overdue|Expired)$/.test(cell) ? `<span class="pill pill-overdue">${cell}</span>` : cell
            }</td>`).join('')}</tr>`).join('')}</tbody>
          </table>
        </div>
      </div>`;
  } else if (cfg.type === 'cards'){
    body = `<div class="gp-cards">${cfg.items.map(it => {
      const inner = `
        <div class="gp-card-title">${it.url ? '▶ ' : ''}${it.title}</div>
        <div class="gp-card-desc">${it.desc}</div>
        <span class="gp-card-meta">${it.meta}</span>`;
      return it.url
        ? `<a class="gp-card gp-card-link" href="${it.url}" target="_blank" rel="noopener">${inner}</a>`
        : `<div class="gp-card">${inner}</div>`;
    }).join('')}</div>`;
  } else if (cfg.type === 'tags'){
    body = `<div class="gp-tags">${cfg.items.map(([label,count]) => `
      <div class="gp-tag">${label} <b>${count}</b></div>`).join('')}</div>`;
  } else if (cfg.type === 'fields'){
    body = `<div class="gp-fields">${cfg.fields.map(([label,value]) => `
      <div class="mp-row"><span>${label}</span><span>${value}</span></div>`).join('')}</div>`;
  } else if (cfg.type === 'toggles'){
    body = `<div class="gp-toggle-wrap">${cfg.items.map((t,i) => `
      <div class="gp-toggle-row">
        <div><div class="gp-toggle-title">${t.label}</div><div class="gp-toggle-desc">${t.desc}</div></div>
        <button class="gp-switch ${t.on ? 'on' : ''}" data-key="${key}" data-idx="${i}"></button>
      </div>`).join('')}</div>`;
  } else if (cfg.type === 'stats'){
    body = `<div class="stat-grid gp-stat-row">${cfg.items.map(s => `
      <div class="card stat-card">
        <div class="stat-label">${s.label}</div>
        <div class="stat-value">${s.value}</div>
        <div class="stat-delta ${s.tone}">${s.tone === 'up' ? '▲ On track' : s.tone === 'warn' ? 'Needs attention' : 'Steady'}</div>
      </div>`).join('')}</div>`;
  } else if (cfg.type === 'pricing'){
    body = `<div class="gp-pricing">
      <div class="gp-price-card">
        <div class="gp-price-tier">Starter</div>
        <div class="gp-price-amount">₹0<span>/month</span></div>
        <ul class="gp-price-list"><li>Up to 5 clients</li><li>1 team member</li><li>Basic invoicing</li></ul>
      </div>
      <div class="gp-price-card featured">
        <div class="gp-price-tier">Growth</div>
        <div class="gp-price-amount">₹1,499<span>/month</span></div>
        <ul class="gp-price-list"><li>Unlimited clients</li><li>Up to 5 team members</li><li>Automations & GST filings</li><li>Priority support</li></ul>
      </div>
      <div class="gp-price-card">
        <div class="gp-price-tier">Practice</div>
        <div class="gp-price-amount">₹3,999<span>/month</span></div>
        <ul class="gp-price-list"><li>Unlimited everything</li><li>Multi-branch support</li><li>Dedicated onboarding</li></ul>
      </div>
    </div>`;
  }

  const tutorialLink = cfg.tutorialUrl ? `<div class="topbar-actions"><a class="btn btn-ghost" href="${cfg.tutorialUrl}" target="_blank" rel="noopener">▶ Watch tutorial</a></div>` : '';
  section.innerHTML = `<div class="topbar"><div><h1>${cfg.title}</h1><div class="sub">${cfg.subtitle}</div></div>${tutorialLink}</div>${body}`;

  if (cfg.type === 'toggles'){
    section.querySelectorAll('.gp-switch').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = GENERIC_PAGES[btn.dataset.key].items[btn.dataset.idx];
        item.on = !item.on;
        btn.classList.toggle('on', item.on);
        showToast(item.label + (item.on ? ' turned on.' : ' turned off.'));
      });
    });
  }
}

document.getElementById('gstExportBtn').addEventListener('click', () => {
  let csv = 'Client,Return,Period,Due date,Status\n';
  GST_FILINGS.forEach(f => { csv += `"${f.client}",${f.type},${f.period},${f.due},${f.status}\n`; });
  downloadCsv(csv, 'ledgerloop-gst-filings.csv');
  showToast('Filing status report exported.');
});
document.getElementById('uploadDocBtn').addEventListener('click', () => {
  const name = prompt("File name to add (e.g. Invoice_Aug2026.pdf):");
  if (!name) return;
  const client = prompt("Which client is this for?", "Aarav Textiles") || "Unassigned";
  DOCUMENTS.unshift({ name, client, category:"Uploaded", uploaded: new Date().toISOString().slice(0,10), size: "—" });
  renderDocuments();
  showToast(name + ' uploaded.');
});

/* ==========================================================================
   Init everything
   ========================================================================== */
renderDashboard();
initInvoiceForm();
initReports();
renderKanban();
renderClients();
renderCalendar();
renderGst();
renderDocuments();
renderChat();
renderNotifications();
renderAutomations();
renderLeads();
renderAttendancePage();
renderLiveAttendance();
initSupabase();