"""Admin web dashboard — a single self-contained HTML page.

Served at GET /dashboard. The page asks for the admin token (stored in the
browser) and calls the existing /admin/* JSON API with the X-Admin-Token header
to show summary, pending payments, users, and accounts, with buttons to verify
payments and activate subscriptions.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Zanzer Admin</title>
<style>
  :root { --bg:#0f1419; --card:#1a212b; --line:#2b3543; --txt:#e6edf3; --muted:#8b98a5; --acc:#2ea043; --warn:#d29922; --bad:#f85149; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--txt); }
  header { padding:16px 20px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; }
  header h1 { font-size:18px; margin:0; }
  .wrap { padding:20px; max-width:1100px; margin:0 auto; }
  .row { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 18px; min-width:150px; }
  .stat .n { font-size:26px; font-weight:700; }
  .stat .l { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:20px; overflow:hidden; }
  .card h2 { font-size:14px; margin:0; padding:12px 16px; border-bottom:1px solid var(--line); }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:9px 16px; border-bottom:1px solid var(--line); font-size:13px; }
  th { color:var(--muted); font-weight:600; }
  tr:last-child td { border-bottom:none; }
  button { background:var(--acc); color:#fff; border:0; border-radius:6px; padding:6px 12px; cursor:pointer; font-size:12px; }
  button.s { background:#30363d; }
  input { background:#0d1117; border:1px solid var(--line); color:var(--txt); border-radius:6px; padding:7px 10px; }
  .pill { padding:2px 8px; border-radius:20px; font-size:11px; }
  .ok{background:rgba(46,160,67,.15);color:#3fb950} .no{background:rgba(248,81,73,.15);color:#f85149}
  .wait{background:rgba(210,153,34,.15);color:#d29922}
  #login { max-width:360px; margin:80px auto; text-align:center; }
  .muted{color:var(--muted)} .err{color:var(--bad)}
</style>
</head>
<body>
<header><h1>🛡️ Zanzer Admin</h1><span id="who" class="muted"></span>
  <span style="margin-left:auto"><button class="s" onclick="logout()">Logout</button></span>
</header>
<div class="wrap">
  <div id="login">
    <h2>Admin sign in</h2>
    <p class="muted">Enter your admin token (ADMIN_TOKEN).</p>
    <p><input id="tok" type="password" placeholder="admin token" style="width:100%"/></p>
    <p><button onclick="login()">Sign in</button></p>
    <p id="lerr" class="err"></p>
  </div>
  <div id="app" style="display:none">
    <div class="row" id="stats"></div>
    <div class="card"><h2>📢 Broadcast</h2>
      <div style="padding:14px 16px">
        <textarea id="bmsg" rows="3" placeholder="Write your announcement…"
          style="width:100%; background:#0d1117; border:1px solid var(--line); color:var(--txt); border-radius:6px; padding:9px"></textarea>
        <div style="margin-top:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted">Send to:</label>
          <select id="baud" style="background:#0d1117;border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:7px">
            <option value="all">All users</option>
            <option value="active">Active subscribers</option>
            <option value="inactive">Inactive / pending</option>
          </select>
          <button onclick="broadcast()">Send broadcast</button>
          <span id="bres" class="muted"></span>
        </div>
      </div>
    </div>
    <div class="card"><h2>🤖 AI Coach</h2>
      <div style="padding:14px 16px; display:grid; gap:10px; max-width:560px">
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted" style="width:90px">Enabled</label>
          <select id="ai_enabled" style="background:#0d1117;border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:7px">
            <option value="true">On</option><option value="false">Off</option>
          </select>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted" style="width:90px">Provider</label>
          <select id="ai_provider" style="background:#0d1117;border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:7px">
            <option value="openai">OpenAI</option><option value="claude">Claude (Anthropic)</option>
          </select>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted" style="width:90px">OpenAI model</label>
          <input id="ai_openai_model" placeholder="gpt-4o-mini" style="flex:1"/>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted" style="width:90px">Claude model</label>
          <input id="ai_anthropic_model" placeholder="claude-sonnet-4-6" style="flex:1"/>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted" style="width:90px">OpenAI key</label>
          <input id="ai_openai_key" type="password" placeholder="(leave blank to keep)" style="flex:1"/>
          <span id="ai_openai_hint" class="muted"></span>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <label class="muted" style="width:90px">Claude key</label>
          <input id="ai_anthropic_key" type="password" placeholder="(leave blank to keep)" style="flex:1"/>
          <span id="ai_anthropic_hint" class="muted"></span>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
          <button onclick="saveAI()">Save AI settings</button>
          <button class="s" onclick="testAI()">Test connection</button>
          <span id="ai_res" class="muted"></span>
        </div>
      </div>
    </div>
    <div class="card"><h2>Pending payments</h2><table id="pay"><thead><tr>
      <th>ID</th><th>User</th><th>Amount</th><th>TX</th><th>Action</th></tr></thead><tbody></tbody></table></div>
    <div class="card"><h2>Users &amp; subscriptions</h2><table id="users"><thead><tr>
      <th>Telegram ID</th><th>Username</th><th>Active</th><th>Plan</th><th>Expires</th><th>Activate</th></tr></thead><tbody></tbody></table></div>
    <div class="card"><h2>MT5 accounts</h2><table id="accts"><thead><tr>
      <th>ID</th><th>User</th><th>Login</th><th>Server</th><th>Status</th></tr></thead><tbody></tbody></table></div>
  </div>
</div>
<script>
const T = () => localStorage.getItem('ztok');
async function api(path, opts={}) {
  opts.headers = Object.assign({'X-Admin-Token': T()}, opts.headers||{});
  const r = await fetch(path, opts);
  if (r.status === 403) throw new Error('unauthorized');
  return r.json();
}
function logout(){ localStorage.removeItem('ztok'); show(false); }
function show(in_){ document.getElementById('app').style.display=in_?'block':'none';
  document.getElementById('login').style.display=in_?'none':'block'; }
async function login(){
  const t=document.getElementById('tok').value.trim();
  localStorage.setItem('ztok', t);
  try { await api('/admin/summary'); document.getElementById('who').textContent='signed in'; show(true); load(); }
  catch(e){ document.getElementById('lerr').textContent='Invalid token'; localStorage.removeItem('ztok'); }
}
function pill(s){ if(s==='active')return '<span class="pill ok">active</span>';
  if(s==='error')return '<span class="pill no">error</span>';
  if(s==='needs_terminal')return '<span class="pill wait">needs terminal</span>';
  return '<span class="pill wait">'+s+'</span>'; }
async function load(){
  const s = await api('/admin/summary');
  document.getElementById('stats').innerHTML =
    stat(s.users,'Users')+stat(s.active_subscriptions,'Active subs')+
    stat(s.pending_payments,'Pending payments')+stat(s.accounts,'Accounts');
  const pays = await api('/admin/payments/pending');
  document.querySelector('#pay tbody').innerHTML = pays.length? pays.map(p=>
    `<tr><td>${p.id}</td><td>${p.user_id}</td><td>${p.amount??''} ${p.currency??''}</td>
     <td class="muted">${(p.tx_hash||'').slice(0,16)}</td>
     <td><button onclick="verify(${p.id})">Verify</button></td></tr>`).join('') :
    '<tr><td colspan=5 class="muted">none</td></tr>';
  const us = await api('/admin/users');
  document.querySelector('#users tbody').innerHTML = us.map(u=>
    `<tr><td>${u.telegram_id}</td><td>${u.username||'—'}</td>
     <td>${u.is_active?'<span class=\\'pill ok\\'>yes</span>':'<span class=\\'pill no\\'>no</span>'}</td>
     <td>${u.subscription?u.subscription.plan:'—'}</td>
     <td>${u.subscription&&u.subscription.expires_at?u.subscription.expires_at.slice(0,10):'—'}</td>
     <td><button onclick="activate(${u.telegram_id})">+30d</button></td></tr>`).join('');
  const ac = await api('/admin/accounts');
  document.querySelector('#accts tbody').innerHTML = ac.length? ac.map(a=>
    `<tr><td>${a.id}</td><td>${a.user_telegram_id}</td><td>${a.login}</td>
     <td>${a.server}</td><td>${pill(a.status)}</td></tr>`).join('') :
    '<tr><td colspan=5 class="muted">none</td></tr>';
  loadAI();
}
async function loadAI(){
  try {
    const c = await api('/admin/ai-settings');
    document.getElementById('ai_enabled').value = c.enabled ? 'true':'false';
    document.getElementById('ai_provider').value = c.provider;
    document.getElementById('ai_openai_model').value = c.openai_model||'';
    document.getElementById('ai_anthropic_model').value = c.anthropic_model||'';
    document.getElementById('ai_openai_hint').textContent = c.openai_key_set?('set '+c.openai_key_hint):'not set';
    document.getElementById('ai_anthropic_hint').textContent = c.anthropic_key_set?('set '+c.anthropic_key_hint):'not set';
    document.getElementById('ai_res').textContent = c.available?'✅ ready':'⚠️ not ready';
  } catch(e){}
}
async function saveAI(){
  const body = {
    enabled: document.getElementById('ai_enabled').value==='true',
    provider: document.getElementById('ai_provider').value,
    openai_model: document.getElementById('ai_openai_model').value.trim(),
    anthropic_model: document.getElementById('ai_anthropic_model').value.trim(),
    openai_api_key: document.getElementById('ai_openai_key').value.trim(),
    anthropic_api_key: document.getElementById('ai_anthropic_key').value.trim(),
  };
  document.getElementById('ai_res').textContent='Saving…';
  await api('/admin/ai-settings',{method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  document.getElementById('ai_openai_key').value='';
  document.getElementById('ai_anthropic_key').value='';
  document.getElementById('ai_res').textContent='Saved.';
  loadAI();
}
async function testAI(){
  document.getElementById('ai_res').textContent='Testing…';
  try {
    const r = await api('/admin/ai-settings/test',{method:'POST'});
    document.getElementById('ai_res').textContent = r.ok?
      `✅ ${r.provider}/${r.model}: ${r.sample}` : `⚠️ ${r.sample}`;
  } catch(e){ document.getElementById('ai_res').textContent='⚠️ test failed (check key/model)'; }
}
function stat(n,l){ return `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`; }
async function broadcast(){
  const message = document.getElementById('bmsg').value.trim();
  const audience = document.getElementById('baud').value;
  if(!message){ document.getElementById('bres').textContent='Write a message first.'; return; }
  document.getElementById('bres').textContent='Sending…';
  const r = await api('/admin/broadcast',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message, audience})});
  document.getElementById('bres').textContent = `Sent ${r.sent}/${r.total} (${r.failed} failed) to ${r.audience}.`;
  document.getElementById('bmsg').value='';
}
async function verify(id){ await api('/admin/payments/'+id+'/verify',{method:'POST'}); load(); }
async function activate(tid){ await api('/admin/users/'+tid+'/activate?days=30&plan=monthly',{method:'POST'}); load(); }
if (T()) { document.getElementById('who').textContent='signed in'; show(true); load().catch(()=>show(false)); }
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return _PAGE
