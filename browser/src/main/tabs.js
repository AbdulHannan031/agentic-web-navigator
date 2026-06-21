// Tab manager: each tab is a WebContentsView hosting a real web page.
// The active view is sized to sit below the top chrome bar and left of the
// agent panel. The chrome UI itself lives in the BaseWindow's own web contents.
const { WebContentsView } = require('electron')
const { START_URL } = require('./startpage')

const TOPBAR_H = 88 // address bar + tab strip
const PANEL_W = 380 // agent side panel

class TabManager {
  /** @param {import('electron').BaseWindow} win  @param {(e:string,p:any)=>void} sendUI */
  constructor(win, sendUI) {
    this.win = win
    this.sendUI = sendUI
    /** @type {{id:number, view:import('electron').WebContentsView}[]} */
    this.tabs = []
    this.activeId = -1
    this._seq = 0
    this.panelOpen = true
    this.startUrl = START_URL // overwritten with a profile-aware page at runtime
    win.on('resize', () => this.layout())
  }

  setPanelOpen(open) {
    this.panelOpen = !!open
    this.layout()
  }

  get active() {
    return this.tabs.find((t) => t.id === this.activeId)
  }

  // Active tab's webContents, or null if missing/destroyed.
  _activeWC() {
    const a = this.active
    const wc = a && a.view && a.view.webContents
    return wc && !wc.isDestroyed() ? wc : null
  }

  newTab(url) {
    if (url === undefined) url = this.startUrl
    const view = new WebContentsView({
      webPreferences: { sandbox: true, contextIsolation: true },
    })
    const id = ++this._seq
    const wc = view.webContents
    this.tabs.push({ id, view })
    this.win.contentView.addChildView(view)

    // Links that open a new window/tab (target=_blank, window.open) become a new
    // tab in our browser instead of a native popup — so the agent can follow them.
    wc.setWindowOpenHandler(({ url }) => {
      if (url && /^https?:/i.test(url)) queueMicrotask(() => this.newTab(url))
      return { action: 'deny' }
    })

    const emitState = () => this._emitTabs()
    wc.on('page-title-updated', emitState)
    // If a page closes/destroys itself (window.close, crash), drop its tab so we
    // never keep a stale entry with a dead webContents.
    wc.on('destroyed', () => {
      const i = this.tabs.findIndex((x) => x.id === id)
      if (i === -1) return
      this.tabs.splice(i, 1)
      if (this.activeId === id) {
        if (this.tabs.length) this.switchTo(this.tabs[Math.max(0, i - 1)].id)
        else this.newTab()
      }
      this._emitTabs()
    })
    const hist = () => ({ canGoBack: wc.navigationHistory.canGoBack(), canGoForward: wc.navigationHistory.canGoForward() })
    wc.on('did-navigate', (_e, navUrl) => {
      if (id === this.activeId) this.sendUI('nav:update', { id, url: navUrl, ...hist() })
      emitState()
    })
    wc.on('did-navigate-in-page', (_e, navUrl) => {
      if (id === this.activeId) this.sendUI('nav:update', { id, url: navUrl, ...hist() })
    })

    if (url) wc.loadURL(url)
    this.switchTo(id)
    this._emitTabs()
    return id
  }

  switchTo(id) {
    this.activeId = id
    for (const t of this.tabs) t.view.setVisible(t.id === id)
    this.layout()
    this._emitTabs()
    const a = this.active
    if (a) {
      const wc = a.view.webContents
      this.sendUI('nav:update', {
        id, url: wc.getURL(),
        canGoBack: wc.navigationHistory.canGoBack(),
        canGoForward: wc.navigationHistory.canGoForward(),
      })
    }
  }

  closeTab(id) {
    const idx = this.tabs.findIndex((t) => t.id === id)
    if (idx === -1) return
    const [t] = this.tabs.splice(idx, 1)
    try { this.win.contentView.removeChildView(t.view) } catch (_) {}
    try { if (t.view.webContents && !t.view.webContents.isDestroyed()) t.view.webContents.close() } catch (_) {}
    if (this.activeId === id) {
      if (this.tabs.length) this.switchTo(this.tabs[Math.max(0, idx - 1)].id)
      else this.newTab() // never leave the window with zero tabs
    }
    this._emitTabs()
  }

  navigate(url) {
    const wc = this._activeWC()
    if (!wc) return
    let u = url.trim()
    if (!/^[a-z]+:\/\//i.test(u)) {
      // Treat as search if it doesn't look like a domain.
      u = /\.[a-z]{2,}($|\/|\?)/i.test(u) ? 'https://' + u : 'https://www.google.com/search?q=' + encodeURIComponent(u)
    }
    wc.loadURL(u)
  }

  goBack() { const wc = this._activeWC(); if (wc && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack() }
  goForward() { const wc = this._activeWC(); if (wc && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward() }
  reload() { const wc = this._activeWC(); if (wc) wc.reload() }

  layout() {
    const a = this.active
    if (!a) return
    const { width, height } = this.win.getContentBounds()
    const panel = this.panelOpen ? PANEL_W : 0
    a.view.setBounds({ x: 0, y: TOPBAR_H, width: Math.max(0, width - panel), height: Math.max(0, height - TOPBAR_H) })
  }

  _emitTabs() {
    // Drop any tabs whose webContents was destroyed (e.g. a popup that closed
    // itself), then read each safely so one bad tab can't crash the app.
    this.tabs = this.tabs.filter((t) => t.view && t.view.webContents && !t.view.webContents.isDestroyed())
    this.sendUI('tabs:update', {
      activeId: this.activeId,
      tabs: this.tabs.map((t) => {
        let title = 'New Tab'
        let url = ''
        try {
          title = t.view.webContents.getTitle() || 'New Tab'
          url = t.view.webContents.getURL()
        } catch (_) {}
        return { id: t.id, title, url }
      }),
    })
  }
}

module.exports = { TabManager, TOPBAR_H, PANEL_W }
