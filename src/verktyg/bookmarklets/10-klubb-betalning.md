---
tab.sv: Klubbetalning
tab.en: Club payment
label.sv: Markera klubb som betald
label.en: Mark club as paid
---

# sv

Markerar alla obetalda deltagare från en vald klubb som betalda på deltagarsidan. Öppna tävlingens deltagarsida och klicka på bokmärket. Välj klubb och klicka på *Markera som betalda*. Redan betalda deltagare hoppas över. Avbryt med *Stoppa och stäng*. Ladda om sidan efteråt och kontrollera att betalningarna sparats.

# en

Marks every unpaid participant from a chosen club as paid on the participants page. Open the tournament's participants page and click the bookmark. Choose a club and click *Mark as paid*. Participants who have already paid are skipped. Cancel with *Stop and close*. Reload the page afterwards and check that the payments were saved.

# Code

```js
javascript:(() => {
  const locale = 'sv';
  const text = {
    sv: {
      wrongPage: 'Kör bokmärket på deltagarsidan där Betalt-rutorna finns.',
      noPlayers: 'Hittade inga deltagare med klubb och Betalt-ruta på sidan.',
      title: 'Markera klubb som betald',
      club: 'Klubb',
      close: 'Stäng',
      start: 'Markera som betalda',
      choose: 'Välj klubb…',
      summary: (members, club, pending) => `${members} deltagare i ${club}. ${pending} obetalda med redigerbar Betalt-ruta.`,
      chooseHint: 'Välj en klubb. Endast deltagare på den här sidan ingår.',
      offline: 'Du är offline. Anslut till internet för att fortsätta.',
      running: 'Markering pågår…',
      chooseFirst: 'Välj en klubb först.',
      nothingPending: 'Inga obetalda deltagare kan markeras för denna klubb.',
      stop: 'Stoppa och stäng',
      done: (online, marked) => `${online ? 'Klart' : 'Stoppat: offline'}. ${marked} Betalt-rutor klickade. Kontrollera sidans kvittens och ladda sedan om för att verifiera att betalningarna sparats.`,
      progress: (index, count, marked) => `Bearbetar ${index} av ${count}. ${marked} Betalt-rutor klickade.`,
    },
    en: {
      wrongPage: 'Run the bookmark on the participants page that has the Paid boxes.',
      noPlayers: 'Found no participants with a club and a Paid box on the page.',
      title: 'Mark club as paid',
      club: 'Club',
      close: 'Close',
      start: 'Mark as paid',
      choose: 'Choose club…',
      summary: (members, club, pending) => `${members} participants in ${club}. ${pending} unpaid with an editable Paid box.`,
      chooseHint: 'Choose a club. Only participants on this page are included.',
      offline: 'You are offline. Connect to the internet to continue.',
      running: 'Marking in progress…',
      chooseFirst: 'Choose a club first.',
      nothingPending: 'No unpaid participants can be marked for this club.',
      stop: 'Stop and close',
      done: (online, marked) => `${online ? 'Done' : 'Stopped: offline'}. ${marked} Paid boxes clicked. Check the page's receipt, then reload to verify that the payments were saved.`,
      progress: (index, count, marked) => `Processing ${index} of ${count}. ${marked} Paid boxes clicked.`,
    },
  }[locale];
  const dialogId = '__clubPaymentDialog';
  const existing = document.getElementById(dialogId);
  if (existing) {
    existing.focus();
    return;
  }
  if (typeof window.sendPaid !== 'function') {
    alert(text.wrongPage);
    return;
  }

  const players = [];
  for (const table of document.querySelectorAll('table.js-sort-table')) {
    const clubColumn = Array.from(table.rows[0]?.cells || []).findIndex(
      (cell) => cell.textContent.trim().toUpperCase() === 'KLUBB'
    );
    if (clubColumn < 0) continue;
    for (const row of table.rows) {
      const paid = row.querySelector('input[type="checkbox"][id^="paid"]');
      const club = row.cells[clubColumn]?.textContent.trim();
      if (paid && club) players.push({ club, paid });
    }
  }
  if (!players.length) {
    alert(text.noPlayers);
    return;
  }

  const dialog = document.createElement('dialog');
  dialog.id = dialogId;
  dialog.style.cssText = 'max-width:440px;width:90%;box-sizing:border-box;padding:24px;border:1px solid #888;border-radius:8px;background:white;color:#111;font:16px sans-serif;';
  dialog.innerHTML = `<h2 style="margin-top:0">${text.title}</h2><label>${text.club} <select style="display:block;width:100%;margin:12px 0;padding:8px"></select></label><p data-summary></p><p data-status role="status" aria-live="polite"></p><div style="display:flex;justify-content:space-between;align-items:center;gap:16px"><button type="button" data-close>${text.close}</button><button type="button" data-start>${text.start}</button></div>`;
  const select = dialog.querySelector('select');
  const summary = dialog.querySelector('[data-summary]');
  const status = dialog.querySelector('[data-status]');
  const start = dialog.querySelector('[data-start]');
  const close = dialog.querySelector('[data-close]');
  select.add(new Option(text.choose, ''));
  const clubs = [...new Set(players.map((player) => player.club))].sort(
    (a, b) => a.localeCompare(b, 'sv')
  );
  for (const club of clubs) select.add(new Option(club, club));

  let running = false;
  let timer;
  function eligible(player) {
    return player.paid.isConnected && !player.paid.checked && !player.paid.matches(':disabled');
  }
  function refresh() {
    const members = players.filter((player) => player.club === select.value);
    const pending = members.filter(eligible);
    summary.textContent = select.value
      ? text.summary(members.length, select.value, pending.length)
      : text.chooseHint;
    start.disabled = running || !pending.length || !navigator.onLine;
    start.title = '';
    if (!navigator.onLine) start.title = text.offline;
    else if (running) start.title = text.running;
    else if (!select.value) start.title = text.chooseFirst;
    else if (!pending.length) start.title = text.nothingPending;
    if (start.disabled) summary.textContent += ` ${start.title}`;
  }
  function cleanup() {
    running = false;
    clearTimeout(timer);
    window.removeEventListener('online', refresh);
    window.removeEventListener('offline', refresh);
    dialog.remove();
  }
  select.addEventListener('change', () => {
    status.textContent = '';
    refresh();
  });
  close.addEventListener('click', cleanup);
  dialog.addEventListener('cancel', cleanup);
  window.addEventListener('online', refresh);
  window.addEventListener('offline', refresh);
  start.addEventListener('click', () => {
    if (running || !select.value || !navigator.onLine) return;
    const pending = players.filter((player) => player.club === select.value && eligible(player));
    if (!pending.length) { refresh(); return; }
    running = true;
    select.disabled = true;
    close.textContent = text.stop;
    refresh();
    let index = 0;
    let marked = 0;
    function next() {
      if (!running) return;
      if (index >= pending.length || !navigator.onLine) {
        running = false;
        select.disabled = false;
        close.textContent = text.close;
        status.textContent = text.done(navigator.onLine, marked);
        refresh();
        return;
      }
      const player = pending[index++];
      if (eligible(player)) {
        player.paid.click();
        marked++;
      }
      status.textContent = text.progress(index, pending.length, marked);
      timer = window.setTimeout(next, 100);
    }
    next();
  });
  document.body.append(dialog);
  refresh();
  dialog.showModal();
})();
```
