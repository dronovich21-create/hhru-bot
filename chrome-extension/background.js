const BASE = 'http://127.0.0.1:8765';
let polling = false;
let clientId;
async function client() {
  if (!clientId) clientId = (async () => {
    const stored = await chrome.storage.session.get('clientId');
    const id = stored.clientId || randomNonce();
    await chrome.storage.session.set({clientId: id});
    return id;
  })();
  return clientId;
}
function randomNonce() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('');
}
async function sign(key, message) {
  const bytes = new TextEncoder();
  const secret = await crypto.subtle.importKey('raw', bytes.encode(key), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return Array.from(new Uint8Array(await crypto.subtle.sign('HMAC', secret, bytes.encode(message))), b => b.toString(16).padStart(2, '0')).join('');
}
function hhUrl(value) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || !(u.hostname === 'hh.ru' || u.hostname.endsWith('.hh.ru'))) throw Error('Разрешены только страницы hh.ru');
  return u.href;
}
async function post(path, data = {}) {
  const {key} = await chrome.storage.local.get('key');
  if (!key) throw Error('Сначала подключите расширение.');
  const body = JSON.stringify(data), nonce = randomNonce(), stamp = String(Math.floor(Date.now() / 1000)), id = await client();
  const signature = await sign(key, `request\n${path}\n${stamp}\n${nonce}\n${id}\n${body}`);
  const response = await fetch(BASE + path, {method: 'POST', headers: {'X-HH-Time': stamp, 'X-HH-Nonce': nonce, 'X-HH-Client': id, 'X-HH-Signature': signature, 'Content-Type': 'application/json'}, body, signal: AbortSignal.timeout(5000), cache: 'no-store', redirect: 'error'});
  const raw = await response.text();
  if (raw.length > 1000000 || response.headers.get('X-HH-Signature') !== await sign(key, `response\n${nonce}\n${response.status}\n${raw}`)) throw Error('Подлинность приложения не подтверждена. Обновите расширение и проверьте подключение.');
  if (!response.ok) throw Error('Не удалось подключиться к приложению: ' + response.status);
  return JSON.parse(raw);
}
async function execute(command) {
  const {action, payload} = command;
  if (action === 'open') {
    const tab = await chrome.tabs.create({url: 'https://hh.ru/', active: true});
    await chrome.storage.session.set({workTab: tab.id});
    return {opened: true};
  }
  const {workTab} = await chrome.storage.session.get('workTab');
  if (!workTab) throw Error('Рабочая вкладка не найдена. Запустите приложение заново.');
  const tab = await chrome.tabs.get(workTab);
  if (tab.status !== 'complete' && action === 'snapshot') return {loading: true};
  hhUrl(tab.url || 'https://hh.ru/');
  if (action === 'navigate') {
    await chrome.tabs.update(workTab, {url: hhUrl(payload.url)});
    return {navigating: true};
  }
  if (tab.status !== 'complete') throw Error('Вкладка ещё загружается. Дождитесь загрузки и продолжите.');
  await chrome.scripting.executeScript({target: {tabId: workTab}, files: ['content.js']});
  const response = await chrome.tabs.sendMessage(workTab, {action, payload});
  if (action === 'snapshot') response.extensionVersion = chrome.runtime.getManifest().version;
  if (response.error) throw Error(response.error);
  return response;
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const {enabled} = await chrome.storage.local.get('enabled');
    if (!enabled) return;
    const {command} = await post('/poll');
    if (command && (await post('/claim', {id: command.id})).active) {
      let result;
      try { result = {value: await execute(command)}; }
      catch (error) { result = {error: error.message}; }
      // Never execute a mutation again if result delivery fails.
      await post('/result', {id: command.id, ...result});
    }
  } catch (_) { /* The app may be closed. Keep reconnecting without touching tabs. */ }
  finally { polling = false; setTimeout(poll, 1000); }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) return;
  if (message.action === 'connect') {
    post('/ping').then(() => { poll(); respond({ok: true}); }).catch(error => respond({error: error.message}));
    return true;
  }
});
chrome.alarms.create('reconnect', {periodInMinutes: 0.5});
chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'});
chrome.alarms.onAlarm.addListener(poll);
poll();
