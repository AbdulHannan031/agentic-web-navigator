// Detect the signed-in Google account so the home page can show the user's
// avatar/email the way google.com does.
//
// Approach: load google.com in a hidden window (same persistent session, so its
// cookies apply) and read the OneGoogle account button — its aria-label is
// exactly "Google Account: Name (email)" and it carries the avatar <img>. This
// is far more stable than the undocumented ListAccounts endpoint. Falls back to
// a cookie check so we can at least show a generic "Signed in" chip.
const { BrowserWindow } = require('electron')

const SCRAPE_JS = `(() => {
  const btn = document.querySelector('a[aria-label^="Google Account"], a[href*="SignOutOptions"], a[href*="accounts.google.com/SignOut"]');
  const label = btn ? (btn.getAttribute('aria-label') || '') : '';
  let img = (btn && btn.querySelector('img')) || document.querySelector('img.gbii, img[src*="googleusercontent"]');
  const avatar = img ? (img.src || '') : '';
  // "Google Account: Abdul Hannan (abdulhannan03086@gmail.com)"
  const m = label.match(/Google Account:\\s*(.*?)\\s*\\(([^)]+@[^)]+)\\)/);
  if (m) return [{ name: m[1].trim(), email: m[2].trim(), avatar }];
  const em = label.match(/[\\w.+-]+@[\\w.-]+/);
  if (em) return [{ name: '', email: em[0], avatar }];
  return [];
})()`

async function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))])
}

async function scrapeAccounts(session) {
  let win
  try {
    win = new BrowserWindow({
      show: false,
      webPreferences: { session, offscreen: false, sandbox: true, contextIsolation: true },
    })
    await withTimeout(win.loadURL('https://www.google.com/'), 8000)
    // Let the OneGoogle bar hydrate.
    await new Promise((r) => setTimeout(r, 800))
    const accounts = await withTimeout(win.webContents.executeJavaScript(SCRAPE_JS, true), 4000)
    return Array.isArray(accounts) ? accounts : []
  } catch (_) {
    return []
  } finally {
    if (win && !win.isDestroyed()) win.destroy()
  }
}

async function hasGoogleAuthCookie(session) {
  try {
    const cookies = await session.cookies.get({ domain: '.google.com' })
    return cookies.some((c) => ['SID', '__Secure-1PSID', '__Secure-3PSID'].includes(c.name))
  } catch (_) {
    return false
  }
}

// Returns an array of { name, email, avatar }. Empty if not signed in.
async function getGoogleAccounts(session) {
  const accounts = await scrapeAccounts(session)
  if (accounts.length) return accounts
  // Fallback: we know they're signed in, but couldn't read details.
  if (await hasGoogleAuthCookie(session)) return [{ name: 'Signed in to Google', email: '', avatar: '' }]
  return []
}

module.exports = { getGoogleAccounts }
