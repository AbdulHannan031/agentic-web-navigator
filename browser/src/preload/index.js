// Safe IPC bridge exposed to the renderer as window.nav.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('nav', {
  // Browser chrome controls
  navigate: (url) => ipcRenderer.send('ui:navigate', url),
  newTab: () => ipcRenderer.send('ui:newTab'),
  switchTab: (id) => ipcRenderer.send('ui:switchTab', id),
  closeTab: (id) => ipcRenderer.send('ui:closeTab', id),
  back: () => ipcRenderer.send('ui:back'),
  forward: () => ipcRenderer.send('ui:forward'),
  reload: () => ipcRenderer.send('ui:reload'),
  setPanel: (open) => ipcRenderer.send('ui:setPanel', open),

  // Agent
  startTask: (taskId, chatId, instruction) => ipcRenderer.send('task:start', { taskId, chatId, instruction }),

  // Chats
  chatList: () => ipcRenderer.send('chat:list'),
  chatLoad: (chatId) => ipcRenderer.send('chat:load', chatId),
  chatDelete: (chatId) => ipcRenderer.send('chat:delete', chatId),
  cancelTask: (taskId) => ipcRenderer.send('task:cancel', { taskId }),
  resolveHuman: (taskId, note) => ipcRenderer.send('human:resolved', { taskId, note }),
  answerQuestion: (taskId, answer) => ipcRenderer.send('user:answer', { taskId, answer }),
  ping: () => ipcRenderer.send('bridge:ping'),

  // Credential vault
  vaultAdd: (cred) => ipcRenderer.send('vault:add', cred),
  vaultRemove: (domain) => ipcRenderer.send('vault:remove', domain),
  vaultCheck: (domain) => ipcRenderer.send('vault:check', domain),

  // Events from main (tabs/nav/bridge/agent)
  on: (channel, cb) => {
    const allowed = ['tabs:update', 'nav:update', 'agent:event']
    if (allowed.includes(channel)) ipcRenderer.on(channel, (_e, payload) => cb(payload))
  },
})
