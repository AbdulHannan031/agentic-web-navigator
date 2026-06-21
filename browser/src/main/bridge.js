// WebSocket client to the Python agent service (Python is the server).
// Reconnects automatically and forwards messages to/from the renderer UI.
const WebSocket = require('ws')

class AgentBridge {
  /** @param {string} url  @param {(type:string,payload:any)=>void} onMessage */
  constructor(url, onMessage) {
    this.url = url
    this.onMessage = onMessage
    this.ws = null
    this.connected = false
    this._queue = []
    this._connect()
  }

  _connect() {
    try {
      this.ws = new WebSocket(this.url)
    } catch (e) {
      return this._retry()
    }
    this.ws.on('open', () => {
      this.connected = true
      this.onMessage('bridge:status', { connected: true })
      this.send('hello', { cdpPort: Number(process.env.NAV_CDP_PORT || 9222), version: '0.1.0' })
      for (const m of this._queue.splice(0)) this.ws.send(m)
    })
    this.ws.on('message', (data) => {
      try {
        const msg = JSON.parse(data.toString())
        this.onMessage(msg.type, msg.payload || {})
      } catch (_) {}
    })
    this.ws.on('close', () => { this.connected = false; this.onMessage('bridge:status', { connected: false }); this._retry() })
    this.ws.on('error', () => { /* close handler will retry */ })
  }

  _retry() {
    setTimeout(() => this._connect(), 1500)
  }

  send(type, payload) {
    const data = JSON.stringify({ type, payload: payload || {} })
    if (this.connected && this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(data)
    else this._queue.push(data)
  }
}

module.exports = { AgentBridge }
