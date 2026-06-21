// Agentic Web Navigator — Electron browser shell (main process).
//
// Responsibilities:
//   * Launch real Chromium with a CDP port so the Python agent can attach.
//   * Render the browser chrome (tab strip, address bar, agent panel) and host
//     web pages in WebContentsViews.
//   * Bridge the renderer UI <-> the Python agent service over WebSocket.
const path = require('path')
const { app, BrowserWindow, ipcMain, session } = require('electron')
const { TabManager } = require('./tabs')
const { AgentBridge } = require('./bridge')
const { getGoogleAccounts } = require('./profiles')
const { buildStartUrl } = require('./startpage')

const CDP_PORT = process.env.NAV_CDP_PORT || '9222'
const WS_URL = `ws://${process.env.NAV_WS_HOST || '127.0.0.1'}:${process.env.NAV_WS_PORT || '8787'}`

// Expose Chromium over the DevTools Protocol BEFORE the app is ready.
app.commandLine.appendSwitch('remote-debugging-port', CDP_PORT)
// Allow Playwright's CDP websocket to connect (newer Chromium locks this down).
app.commandLine.appendSwitch('remote-allow-origins', '*')

// NOTE: We deliberately do NOT spoof the User-Agent or Client Hints. Faking the UA
// string while navigator.userAgentData (JS) still reports Electron creates an
// inconsistency that Google's sign-in flags as "this browser may not be secure",
// breaking login. The honest (consistent) Electron identity lets Google sign-in
// work. CDP-leak stealth is handled by patchright on the agent side instead.

/** @type {BrowserWindow} */
let win
/** @type {TabManager} */
let tabs
/** @type {AgentBridge} */
let bridge

function sendUI(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload)
  // Tell the agent which tab the user is actually looking at, so it drives the
  // right page when several tabs are open.
  if (channel === 'nav:update' && bridge && payload && payload.url) {
    bridge.send('tab.active', { url: payload.url })
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#15151f',
    title: 'NavGo',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      contextIsolation: true,
      sandbox: false,
    },
  })

  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'))

  tabs = new TabManager(win, sendUI)

  // Forward messages from Python to the renderer. A few are handled in main:
  // `tab.reload` reloads the active page (used to re-init a Cloudflare challenge
  // cleanly after the CDP session is detached, so a human can actually pass it).
  bridge = new AgentBridge(WS_URL, (type, payload) => {
    if (type === 'tab.reload') {
      if (tabs) tabs.reload()
      return
    }
    sendUI('agent:event', { type, payload })
  })

  win.webContents.on('did-finish-load', async () => {
    await refreshStartPage()
    tabs.newTab()
  })
}

// Probe the signed-in Google profile and rebuild the home page so it shows the
// account avatar/email (like google.com). Best-effort; falls back to no chip.
async function refreshStartPage() {
  try {
    const accounts = await getGoogleAccounts(session.defaultSession)
    tabs.startUrl = buildStartUrl(accounts)
  } catch (_) {
    /* keep previous startUrl */
  }
}

// ---- IPC: renderer UI -> main ------------------------------------------
ipcMain.on('ui:navigate', (_e, url) => tabs.navigate(url))
ipcMain.on('ui:newTab', async () => { await refreshStartPage(); tabs.newTab() })
ipcMain.on('ui:switchTab', (_e, id) => tabs.switchTo(id))
ipcMain.on('ui:closeTab', (_e, id) => tabs.closeTab(id))
ipcMain.on('ui:back', () => tabs.goBack())
ipcMain.on('ui:forward', () => tabs.goForward())
ipcMain.on('ui:reload', () => tabs.reload())
ipcMain.on('ui:setPanel', (_e, open) => tabs.setPanelOpen(open))

// ---- IPC: agent panel -> Python ----------------------------------------
ipcMain.on('task:start', (_e, { taskId, chatId, instruction }) => {
  bridge.send('task.start', { taskId, chatId, instruction })
})
ipcMain.on('chat:list', () => bridge.send('chat.list', {}))
ipcMain.on('chat:load', (_e, chatId) => bridge.send('chat.load', { chatId }))
ipcMain.on('chat:delete', (_e, chatId) => bridge.send('chat.delete', { chatId }))
ipcMain.on('task:cancel', (_e, { taskId }) => bridge.send('task.cancel', { taskId }))
ipcMain.on('human:resolved', (_e, { taskId, note }) => bridge.send('human.help.resolved', { taskId, note: note || '' }))
ipcMain.on('user:answer', (_e, { taskId, answer }) => bridge.send('user.answer', { taskId, answer: answer || '' }))
ipcMain.on('bridge:ping', () => bridge.send('ping', {}))

// ---- IPC: credential vault ---------------------------------------------
ipcMain.on('vault:add', (_e, cred) => bridge.send('vault.add', cred))
ipcMain.on('vault:remove', (_e, domain) => bridge.send('vault.remove', { domain }))
ipcMain.on('vault:check', (_e, domain) => bridge.send('vault.check', { domain }))

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
