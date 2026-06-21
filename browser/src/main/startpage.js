// The "Nav Go" start / new-tab page: centered yellow location pin, the wordmark,
// a search box, and — top-right — the signed-in Google profile (like google.com).
// Served as a data: URL (NOT file://) so the Python agent treats it as a real
// browsing tab rather than the Electron UI shell.

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

function profileChip(accounts) {
  if (!accounts || !accounts.length) {
    return `<a class="acct signin" href="https://accounts.google.com/">Sign in</a>`
  }
  const a = accounts[0]
  const initial = (a.name || a.email || '?').trim().charAt(0).toUpperCase()
  const more = accounts.length > 1 ? `<span class="acct-more">+${accounts.length - 1}</span>` : ''
  const avatar = a.avatar
    ? `<img class="avatar" src="${esc(a.avatar)}" referrerpolicy="no-referrer" alt="">`
    : `<span class="avatar fallback">${esc(initial)}</span>`
  return `<div class="acct" title="${esc(a.email)}">
      ${avatar}${more}
      <div class="acct-meta"><div class="acct-name">${esc(a.name || a.email)}</div>
      <div class="acct-mail">${esc(a.email)}</div></div>
    </div>`
}

function buildStartUrl(accounts = []) {
  const HTML = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Nav Go</title>
<style>
  :root{--bg:#15151f;--fg:#e6e8f0;--muted:#8b8fa3;--gold:#f9c74f;--border:#262635;--card:#1e1e2b}
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:radial-gradient(1200px 600px at 50% -8%,#23233a,var(--bg));
    color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px}
  /* top-right account chip */
  .topbar{position:fixed;top:16px;right:20px;display:flex;align-items:center;gap:10px}
  .acct{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--border);
    border-radius:24px;padding:6px 14px 6px 6px;text-decoration:none;color:var(--fg);transition:border .15s}
  .acct:hover{border-color:var(--gold)}
  .acct.signin{padding:9px 18px;color:var(--gold);font-weight:600}
  .avatar{width:34px;height:34px;border-radius:50%;object-fit:cover;flex:none}
  .avatar.fallback{display:grid;place-items:center;background:linear-gradient(135deg,#89b4fa,#b4befe);
    color:#11111b;font-weight:700}
  .acct-meta{line-height:1.2}.acct-name{font-size:13px;font-weight:600}
  .acct-mail{font-size:11px;color:var(--muted)}
  .acct-more{font-size:11px;color:var(--muted);margin-left:-4px}
  /* center */
  .pin{filter:drop-shadow(0 8px 18px rgba(249,199,79,.35));animation:bob 2.6s ease-in-out infinite}
  @keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
  .brand{font-size:44px;font-weight:800;letter-spacing:.5px}.brand .go{color:var(--gold)}
  .tag{color:var(--muted);margin-top:-14px;font-size:13px}
  form{width:min(580px,86vw);display:flex;margin-top:8px}
  input{flex:1;height:52px;padding:0 20px;border-radius:28px;border:1px solid var(--border);
    background:var(--card);color:var(--fg);font-size:16px;outline:none;box-shadow:0 8px 30px rgba(0,0,0,.3)}
  input:focus{border-color:var(--gold)}
  .hint{color:var(--muted);font-size:12px;margin-top:2px}
</style></head>
<body>
  <div class="topbar">${profileChip(accounts)}</div>
  <svg class="pin" width="88" height="88" viewBox="0 0 24 24">
    <path d="M12 2C7.6 2 4 5.6 4 10c0 5.2 6.6 11.2 7.3 11.8.4.3.9.3 1.3 0C13.4 21.2 20 15.2 20 10c0-4.4-3.6-8-8-8z" fill="#f9c74f"/>
    <circle cx="12" cy="10" r="3.1" fill="#15151f"/>
  </svg>
  <div class="brand">Nav<span class="go">Go</span></div>
  <div class="tag">Agentic Web Navigator</div>
  <form onsubmit="go(event)">
    <input id="q" autofocus placeholder="Search the web or enter an address" autocomplete="off" spellcheck="false">
  </form>
  <div class="hint">Tip: give the agent a task in the panel on the right →</div>
  <script>
    function go(e){e.preventDefault();var v=document.getElementById('q').value.trim();if(!v)return;
      if(/^[a-z][a-z0-9+.-]*:\\/\\//i.test(v)){location.href=v;}
      else if(/\\.[a-z]{2,}($|\\/|\\?)/i.test(v)){location.href='https://'+v;}
      else{location.href='https://www.google.com/search?q='+encodeURIComponent(v);}}
  </script>
</body></html>`
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(HTML)
}

const START_URL = buildStartUrl([])

module.exports = { buildStartUrl, START_URL }
