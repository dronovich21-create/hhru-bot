chrome.storage.local.get('key').then(({key}) => { document.querySelector('#key').value = key || ''; });
document.querySelector('#connect').onclick = async () => {
  const key = document.querySelector('#key').value.trim();
  if (!key) return;
  await chrome.storage.local.set({key, enabled: true});
  const result = await chrome.runtime.sendMessage({action: 'connect'});
  document.querySelector('#status').textContent = result.ok ? 'Подключено. Можно запускать приложение.' : result.error;
};
document.querySelector('#disconnect').onclick = async () => {
  await chrome.storage.local.set({enabled: false});
  document.querySelector('#status').textContent = 'Отключено';
};
