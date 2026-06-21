// Renderer: browser chrome + assistant panel (Agent / Logins) + takeover overlay.
const $ = (id) => document.getElementById(id)
let activeTaskId = null
let running = false

// ---------- Chats (persistent conversation per chat) ----------
function newChatId() { return 'chat_' + Date.now() + '_' + Math.floor(Math.random() * 1e6) }
let currentChatId = localStorage.getItem('navgo.chatId') || newChatId()
localStorage.setItem('navgo.chatId', currentChatId)

function setChat(chatId) {
  currentChatId = chatId
  localStorage.setItem('navgo.chatId', chatId)
}

function startNewChat() {
  setChat(newChatId())
  $('log').innerHTML = '<div class="empty"><p>New chat started. Give me a task.</p></div>'
  setStatus('idle', 'muted')
  $('chat-list').classList.add('hidden')
}

function renderChatList(chats) {
  const box = $('chat-list')
  if (!chats || !chats.length) { box.innerHTML = '<div class="chat-empty muted small">No saved chats yet.</div>'; return }
  box.innerHTML = ''
  for (const c of chats) {
    const row = document.createElement('div')
    row.className = 'chat-row' + (c.chatId === currentChatId ? ' active' : '')
    row.innerHTML = `<span class="chat-title">${escapeHtml(c.title || 'New chat')}</span>`
    row.onclick = () => { setChat(c.chatId); window.nav.chatLoad(c.chatId); $('chat-list').classList.add('hidden') }
    const del = document.createElement('span')
    del.className = 'chat-del'; del.textContent = '×'
    del.onclick = (e) => { e.stopPropagation(); window.nav.chatDelete(c.chatId); if (c.chatId === currentChatId) startNewChat() }
    row.appendChild(del)
    box.appendChild(row)
  }
}

function replayLog(entries) {
  $('log').innerHTML = ''
  if (!entries || !entries.length) { $('log').innerHTML = '<div class="empty"><p>No history in this chat yet.</p></div>'; return }
  for (const e of entries) addEntry(e.tool, e.text, e.cls)
}

// ---------- Browser chrome ----------
$('btn-back').onclick = () => window.nav.back()
$('btn-forward').onclick = () => window.nav.forward()
$('btn-reload').onclick = () => window.nav.reload()
$('btn-newtab').onclick = () => window.nav.newTab()

// ---------- Collapsible assistant panel ----------
let panelOpen = true
function togglePanel(force) {
  panelOpen = typeof force === 'boolean' ? force : !panelOpen
  document.body.classList.toggle('panel-collapsed', !panelOpen)
  $('btn-panel').classList.toggle('active', panelOpen)
  window.nav.setPanel(panelOpen)
}
$('btn-panel').onclick = () => togglePanel()
$('panel-fab').onclick = () => togglePanel(true)
window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === '\\') { e.preventDefault(); togglePanel() }
})
$('address').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') window.nav.navigate($('address').value)
})

function isStartPage(url) {
  return !url || url === 'about:blank' || url.startsWith('data:text/html')
}

window.nav.on('tabs:update', ({ activeId, tabs }) => {
  const strip = $('tabstrip')
  strip.innerHTML = ''
  for (const t of tabs) {
    const el = document.createElement('div')
    el.className = 'tab' + (t.id === activeId ? ' active' : '')
    el.title = t.url
    el.innerHTML =
      `<span class="favico"></span><span class="label">${escapeHtml(isStartPage(t.url) ? 'New Tab' : t.title || 'Loading…')}</span>`
    const close = document.createElement('span')
    close.className = 'close'
    close.textContent = '×'
    close.onclick = (ev) => { ev.stopPropagation(); window.nav.closeTab(t.id) }
    el.appendChild(close)
    el.onclick = () => window.nav.switchTab(t.id)
    strip.appendChild(el)
  }
})

window.nav.on('nav:update', ({ url }) => {
  if (document.activeElement !== $('address')) $('address').value = isStartPage(url) ? '' : url
})

// ---------- Segmented view toggle ----------
document.querySelectorAll('.seg').forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll('.seg').forEach((b) => b.classList.toggle('active', b === btn))
    const view = btn.dataset.view
    $('view-agent').classList.toggle('hidden', view !== 'agent')
    $('view-logins').classList.toggle('hidden', view !== 'logins')
  }
})

// ---------- Agent panel ----------
function setStatus(text, cls) { $('status-line').innerHTML = `<span class="${cls || ''}">${escapeHtml(text)}</span>` }
function clearEmpty() { const e = $('log').querySelector('.empty'); if (e) e.remove() }

function addEntry(tool, body, cls, args) {
  clearEmpty()
  const log = $('log')
  const el = document.createElement('div')
  el.className = 'entry ' + (cls || '')
  const argStr = args && Object.keys(args).length
    ? `<div class="args">${escapeHtml(Object.entries(args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join('  '))}</div>` : ''
  el.innerHTML = `<span class="tool">${escapeHtml(tool)}</span><div class="body">${escapeHtml(body)}</div>${argStr}`
  log.appendChild(el)
  log.scrollTop = log.scrollHeight
}

function setRunning(on) {
  running = on
  $('btn-run').disabled = on
  $('btn-cancel').disabled = !on
  $('btn-run').textContent = on ? 'Running' : 'Run'
}

function runTask() {
  const instruction = $('task-input').value.trim()
  if (!instruction || running) return
  activeTaskId = 't' + Date.now()
  $('log').innerHTML = ''
  $('task-input').value = ''
  setRunning(true)
  setStatus('starting', 'working')
  addEntry('task', instruction, 'think')
  window.nav.startTask(activeTaskId, currentChatId, instruction)
}

// New chat + history controls
$('btn-newchat').onclick = startNewChat
$('btn-history').onclick = () => {
  const list = $('chat-list')
  const show = list.classList.contains('hidden')
  list.classList.toggle('hidden', !show)
  if (show) window.nav.chatList()
}

$('btn-run').onclick = runTask
$('btn-cancel').onclick = () => { if (activeTaskId) window.nav.cancelTask(activeTaskId); setRunning(false); setStatus('stopped', 'muted') }
$('task-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runTask() }
})
document.querySelectorAll('.chip').forEach((c) => { c.onclick = () => { $('task-input').value = c.textContent; runTask() } })

function showHelp(reason) {
  $('help-text').textContent = reason
  $('help-card').classList.remove('hidden')
  togglePanel(true) // make sure the panel is visible so the user sees the ask
  setStatus('waiting for you — see the help box above', 'muted')
}
function hideHelp() { $('help-card').classList.add('hidden') }

$('btn-continue').onclick = () => {
  hideHelp()
  if (activeTaskId) window.nav.resolveHuman(activeTaskId, '')
  setStatus('resuming', 'working')
}
$('btn-help-stop').onclick = () => {
  hideHelp()
  if (activeTaskId) window.nav.cancelTask(activeTaskId)
  setRunning(false)
  setStatus('stopped', 'muted')
}

// ---------- Agent asks the user a question ----------
function showQuestion(question) {
  $('question-text').textContent = question
  $('question-input').value = ''
  $('question-card').classList.remove('hidden')
  togglePanel(true)
  setStatus('waiting for your answer — see above', 'muted')
  $('question-input').focus()
}
function hideQuestion() { $('question-card').classList.add('hidden') }
function sendAnswer() {
  const answer = $('question-input').value.trim()
  if (!answer) { $('question-input').focus(); return }
  hideQuestion()
  if (activeTaskId) window.nav.answerQuestion(activeTaskId, answer)
  addEntry('you', answer, 'think')
  setStatus('continuing', 'working')
}
$('btn-answer').onclick = sendAnswer
$('question-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAnswer() }
})

// ---------- Logins ----------
$('v-save').onclick = () => {
  const domain = $('v-domain').value.trim()
  const username = $('v-user').value.trim()
  const password = $('v-pass').value
  if (!domain || !username || !password) { vaultStatus('Fill website, username and password.', 'err'); return }
  window.nav.vaultAdd({ domain, username, password, totp: $('v-totp').value.trim() })
  vaultStatus('Saving…', '')
}
$('v-check').onclick = () => {
  const domain = $('v-domain').value.trim()
  if (!domain) { vaultStatus('Enter a website to check.', 'err'); return }
  window.nav.vaultCheck(domain)
}
function vaultStatus(text, cls) { const el = $('v-status'); el.className = 'vault-status small ' + (cls || ''); el.textContent = text }

// ---------- Event stream from Python (via main) ----------
const STATE_CLS = { thinking: 'working', acting: 'working', waiting_human: 'muted', done: 'muted', error: 'err' }

window.nav.on('agent:event', ({ type, payload }) => {
  switch (type) {
    case 'bridge:status':
    case 'ready': {
      const on = type === 'ready' || payload.connected
      $('bridge-pill').className = 'pill ' + (on ? 'on' : 'off')
      $('bridge-text').textContent = on ? 'connected' : 'offline'
      break
    }
    case 'agent.status':
      setStatus(payload.message || payload.state, STATE_CLS[payload.state] || '')
      break
    case 'agent.action':
      if (payload.tool === 'thinking') addEntry('thinking', payload.summary, 'think')
      else if (payload.summary) addEntry(payload.tool, payload.summary, 'ok', payload.args)
      break
    case 'chat.list':
      renderChatList(payload.chats)
      break
    case 'chat.log':
      if (payload.chatId === currentChatId) replayLog(payload.log)
      break
    case 'agent.question':
      showQuestion(payload.question || 'Could you provide more detail?')
      addEntry('question', payload.question, 'human')
      break
    case 'human.help.request': {
      const reason = payload.reason === 'captcha'
        ? 'Please solve the CAPTCHA / "verify you are human" challenge in the page on the left.'
        : (payload.reason || 'The agent is blocked — please take over in the page on the left.')
      showHelp(reason)
      addEntry('help needed', reason, 'human')
      break
    }
    case 'agent.result':
      setRunning(false)
      activeTaskId = null
      hideHelp()
      hideQuestion()
      addEntry('result', payload.result || '(done)', payload.ok ? 'ok' : 'err')
      setStatus(payload.ok ? 'done' : 'failed', payload.ok ? 'muted' : 'err')
      break
    case 'vault.result':
      if (payload.action === 'add') vaultStatus(payload.ok ? `Saved login for ${payload.domain}.` : `Error: ${payload.error}`, payload.ok ? 'ok' : 'err')
      else if (payload.action === 'check') vaultStatus(payload.exists ? `Saved: ${payload.username} @ ${payload.domain}` : `No saved login for ${payload.domain}.`, payload.exists ? 'ok' : 'muted')
      else if (payload.action === 'remove') vaultStatus(`Removed login for ${payload.domain}.`, 'muted')
      break
  }
})

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

setTimeout(() => {
  window.nav.ping()
  window.nav.chatLoad(currentChatId) // restore this chat's history
}, 600)
