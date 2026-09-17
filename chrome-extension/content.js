// Idempotent injection; no access to other tabs, passwords or cookies.
if (!globalThis.hhAssistantInstalled) {
  globalThis.hhAssistantInstalled = true;
  const visible = e => e && e.getClientRects().length > 0;
  const buttons = root => [...root.querySelectorAll('button, a[data-qa*="response"]')].filter(visible);
  const text = e => (e?.innerText || '').trim();
  const attachButton = root => [...root.querySelectorAll('button,a,[role="button"]')].find(e => visible(e) && /^Приложить письмо$/i.test(text(e)));
  async function attachLetter(payload) {
    const card = cards().find(c => c.id === payload.id);
    if (!card || !card.sent || blocker()) throw Error('Не удалось проверить отправленный отклик текущей вакансии.');
    const add = attachButton(card.node);
    if (!add) return {unavailable: true};
    if (!payload.letter?.trim()) throw Error('Для приложения письма заполните шаблон в приложении.');
    add.click();
    let scope;
    for (let i = 0; i < 30; i++) {
      const dialogs = [...document.querySelectorAll('[role="dialog"],.bloko-modal-content,[data-qa="vacancy-response-popup"]')].filter(visible);
      const candidates = dialogs.filter(d => /сопроводительное|приложить письмо/i.test(text(d)) && visible(d.querySelector('textarea')));
      if (candidates.length === 1) scope = candidates[0];
      if (visible(card.node.querySelector('textarea'))) scope = card.node;
      if (scope) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!scope) throw Error('Не распознано поле приложения письма. Сам отклик уже отправлен; добавьте письмо вручную.');
    const area = scope.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(area, payload.letter);
    area.dispatchEvent(new Event('input', {bubbles: true}));
    area.dispatchEvent(new Event('change', {bubbles: true}));
    await new Promise(resolve => setTimeout(resolve, 100));
    const submit = [...scope.querySelectorAll('button,[role="button"]')].find(b => visible(b) && /^(Отправить|Отправить письмо|Приложить письмо|Добавить письмо)$/i.test(text(b)));
    if (!submit || submit.disabled || area.value !== payload.letter || /captcha|капча|я не робот/i.test(text(scope))) throw Error('Не удалось безопасно отправить письмо. Проверьте форму вручную.');
    submit.click();
    for (let i = 0; i < 30; i++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      if (/письмо отправлено|письмо приложено/i.test(text(card.node)) || (!visible(area) && !attachButton(card.node))) return {attached: true};
    }
    throw Error('Письмо отправлялось, но результат не подтверждён. Проверьте вручную; автоматически оно повторно не отправляется.');
  }
  function visibilityRequest(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node; (node = walker.nextNode());) {
      if (!/поменяйте видимость резюме|измените видимость резюме/i.test(node.textContent)) continue;
      let hidden = false;
      for (let e = node.parentElement; e; e = e.parentElement) {
        const style = getComputedStyle(e);
        if (e.getAttribute('aria-hidden') === 'true' || e.inert || style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) === 0) { hidden = true; break; }
      }
      if (hidden) continue;
      const range = document.createRange(); range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (!(r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth)) continue;
        const hit = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2)));
        if (!hit || !node.parentElement.contains(hit)) continue;
        const phrase = node.textContent.match(/поменяйте видимость резюме|измените видимость резюме/i)[0];
        return `${phrase} (элемент ${node.parentElement.tagName.toLowerCase()})`;
      }
    }
    return false;
  }
  function cards() {
    return [...document.querySelectorAll('[data-qa="vacancy-serp__vacancy"], [data-qa="vacancy-serp__vacancy-card"]')].map(node => {
      const title = node.querySelector('[data-qa="serp-item__title"], a[href*="/vacancy/"]');
      const id = title?.href.match(/\/vacancy\/(\d+)/)?.[1];
      if (!id) return null;
      return {node, id, title: text(title), url: title.href, company: text(node.querySelector('[data-qa="vacancy-serp__vacancy-employer-text"]')),
        sent: /вы откликнулись|отклик отправлен|отозвать отклик/i.test(text(node)),
        canRespond: buttons(node).some(b => text(b) === 'Откликнуться'), canAttachLetter: !!attachButton(node)};
    }).filter(Boolean);
  }
  function modal() {
    const known = [...document.querySelectorAll('[role="dialog"], [data-qa="vacancy-response-popup"], .bloko-modal-content')].find(e => visible(e) && /Отклик/.test(text(e)));
    if (known) return known;
    // New HH layouts can omit the old dialog attributes. Identify the smallest
    // container with the response heading and its own final button, never body.
    for (const button of buttons(document).filter(b => text(b) === 'Откликнуться')) {
      for (let e = button.parentElement; e && e !== document.body; e = e.parentElement) {
        if (e.matches('[data-qa="vacancy-serp__vacancy"], [data-qa="vacancy-serp__vacancy-card"]')) break;
        if ([...e.querySelectorAll('h1,h2,h3,h4,span,div')].some(h => visible(h) && /^Отклик на вакансию$/i.test(text(h)))) return e;
      }
    }
    return null;
  }
  function blocker() {
    const body = text(document.body);
    // A resume's ordinary visibility label is not a request to change it.
    const dialogs = [...document.querySelectorAll('[role="dialog"], [data-qa="vacancy-response-popup"], .bloko-modal-content')].filter(visible);
    const response = modal();
    if (response && !dialogs.includes(response)) dialogs.push(response);
    const request = dialogs.map(visibilityRequest).find(Boolean);
    if (request) return `hh.ru просит изменить видимость резюме. Решите это вручную, затем продолжите.\nДиагностика 0.2.3: ${request}`;
    if (/captcha|капча|я не робот|подтвердите, что вы не робот/i.test(body)) return 'В рабочей вкладке CAPTCHA. Пройдите её вручную.';
    const unknown = [...document.querySelectorAll('[role="dialog"], .bloko-modal-content')].find(e => visible(e) && e !== modal());
    if (unknown && !modal()) return 'Открыто дополнительное окно hh.ru. Выполните действие вручную.';
    return null;
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender?.id !== chrome.runtime.id) return;
    if (message.action === 'attachLetter') {
      attachLetter(message.payload).then(respond).catch(error => respond({error: error.message}));
      return true;
    }
    try {
      const {action, payload} = message;
      if (action === 'snapshot') {
        const m = modal();
        const next = document.querySelector('a[data-qa="pager-next"]');
        const submit = m && buttons(m).find(b => text(b) === 'Откликнуться');
        const area = m?.querySelector('textarea');
        respond({contentVersion: '0.2.3', url: location.href, blocker: blocker(), login: buttons(document).some(b => text(b) === 'Войти') || [...document.querySelectorAll('a')].some(a => visible(a) && text(a) === 'Войти'),
          cards: cards().map(({node, ...data}) => data), empty: /ничего не\s+найдено/i.test(text(document.body)),
          next: next && next.getAttribute('aria-disabled') !== 'true' ? next.href : null,
          modal: m ? {requiredLetter: !!area?.required || /Сопроводительное письмо.*обяз/i.test(text(m)) || !!submit?.disabled,
            wantsLetter: !!area || buttons(m).some(b => /Добавить сопроводительное/i.test(text(b)))} : null});
      } else if (action === 'openResponse') {
        if (blocker()) throw Error(blocker());
        const card = cards().find(c => c.id === payload.id);
        if (!card) throw Error('Вакансия не найдена в рабочей вкладке');
        if (modal()) throw Error('Сначала закройте предыдущую форму отклика');
        const button = buttons(card.node).find(b => text(b) === 'Откликнуться');
        if (!button) throw Error('Кнопка отклика отсутствует');
        button.click(); respond({opened: true});
      } else if (action === 'submit') {
        const m = modal();
        const card = cards().find(c => c.id === payload.id);
        if (blocker() || !m || !card || !text(m).includes(payload.title)) {
          respond({notSubmitted: blocker() || 'Не удалось проверить соответствие формы текущей вакансии.'}); return;
        }
        const add = buttons(m).find(b => /Добавить сопроводительное/i.test(text(b)));
        let area = m.querySelector('textarea');
        if (add && !area) {
          if (!payload.letter?.trim()) { respond({notSubmitted: 'Выбрано добавление письма ко всем откликам. Заполните сопроводительное письмо.'}); return; }
          add.click();
          // Rendering is asynchronous. Do not send until the next snapshot sees
          // the textarea; this response guarantees the final click did not run.
          respond({preparePending: true, notSubmitted: null}); return;
        }
        if (area) {
          if (!area.value.trim() && !payload.letter) { respond({notSubmitted: 'Требуется сопроводительное письмо'}); return; }
          if (payload.letter) {
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(area, payload.letter);
            area.dispatchEvent(new Event('input', {bubbles: true}));
            area.dispatchEvent(new Event('change', {bubbles: true}));
            if (area.value !== payload.letter) { respond({notSubmitted: 'Не удалось проверить вставку письма.'}); return; }
          }
        }
        const submit = buttons(m).find(b => text(b) === 'Откликнуться');
        if (!submit || submit.disabled) { respond({notSubmitted: 'Форма требует дополнительного действия. Заполните её вручную.'}); return; }
        submit.click(); respond({submitted: true});
      } else throw Error('Неизвестная команда');
    } catch (error) { respond({notSubmitted: error.message, error: error.message}); }
  });
}
