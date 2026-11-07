---
tab.sv: Samma spelare
tab.en: Same players
label.sv: Fyll i samma spelare
label.en: Fill in same players
---

# sv

Kopierar laguppställningarna från en tidigare rond till den aktuella ronden i en lagtävling i medlemssystemet, där det är möjligt. Öppna sidan med laguppställningarna och klicka på bokmärket.

Kontrollera spelarna innan du sparar. Ladda om sidan för att ångra osparade ändringar.

# en

Copies team rosters from a previous round to the current round in a team tournament in the member system, where possible. Open the team roster page and click the bookmark.

Check the players before saving. Reload the page to undo unsaved changes.

# Code

```js
javascript:(async () => {
  const locale = 'sv';
  const text = {
    sv: {
      wrongPage: 'Öppna sidan med laguppställningarna i en lagtävling först.',
      apiError: (status, groupId) => `API:et svarade med HTTP ${status} för grupp ${groupId}. Är tävlingen offentlig?`,
      matchMissing: (matchId, groupId) => `Match ${matchId} hittades inte i grupp ${groupId}.`,
      locked: 'Laguppställningen är låst; klicka på "Öppna" först.',
      futureRound: 'Ronddatum är i framtiden, du kommer inte att kunna spara individuella resultaten!\n\nRonddatum kan ändras under "Lottning" i gruppen, inte via "Hantera Ronder".',
      noPrevious: label => `${label}: ingen tidigare match med laguppställning.`,
      copied: (label, round) => `${label}: kopierad från rond ${round}`,
      missing: boards => `; bord ${boards.join(', ')} ändrades inte (spelare saknas i listan).`,
      home: 'Hemmalag',
      away: 'Bortalag',
      review: 'Kontrollera laguppställningarna och klicka sedan på "Spara spelare" / "Spara allt".',
      error: 'Samma spelare: ',
    },
    en: {
      wrongPage: 'Open the team roster page in a team tournament first.',
      apiError: (status, groupId) => `API returned HTTP ${status} for group ${groupId}. Is the tournament public?`,
      matchMissing: (matchId, groupId) => `Match ${matchId} not found in group ${groupId}.`,
      locked: 'Roster is locked; click "Öppna" first.',
      futureRound: 'The round date is in the future; you will not be able to save individual results!\n\nThe round date can be changed under "Lottning" in the group, not via "Hantera Ronder".',
      noPrevious: label => `${label}: no earlier match with a roster.`,
      copied: (label, round) => `${label}: copied from round ${round}`,
      missing: boards => `; board(s) ${boards.join(', ')} not set (player missing from list).`,
      home: 'Home',
      away: 'Away',
      review: 'Check the rosters, then click "Spara spelare" / "Spara allt".',
      error: 'Same players: ',
    },
  }[locale];
  // The public API lives next to the servlets, e.g. /webapp/memdb/public/api/v1.
  const API = new URL('public/api/v1', location.href).href;
  try {
    const form = document.getElementById('updateTournamentRoundResult');
    const matchId = form && +form.querySelector('input[name=id]').value;
    const groupLink = document.querySelector('a[href*="ViewTournamentClassGroupServlet?id="]');
    const groupId = groupLink && +new URL(groupLink.href).searchParams.get('id');
    if (!matchId || !groupId) throw new Error(text.wrongPage);

    // Use the server's warning, including text the browser moves out of tables.
    if (document.body.innerText.replace(/\s+/g, ' ').includes('Ronddatum är i framtiden')) {
      throw new Error(text.futureRound);
    }

    const res = await fetch(`${API}/tournamentresults/team/roundresults/id/${groupId}`);
    if (!res.ok) throw new Error(text.apiError(res.status, groupId));
    const matches = await res.json();

    const current = matches.find(m => m.id === matchId);
    if (!current) throw new Error(text.matchMissing(matchId, groupId));

    const sel = (color, board) => document.getElementById(`${color}memberid${matchId}${board}`);
    let boards = 0;
    while (sel('white', boards)) boards++;

    // Already-saved rosters render with disabled selects, which a form submit
    // skips. Unlock them the same way the page's "Öppna" button does.
    if (sel('white', 0).disabled) {
      if (typeof postopenteamtournamentroundresultform !== 'function') throw new Error(text.locked);
      postopenteamtournamentroundresultform(boards, matchId);
    }

    // Site convention (see updateTeamOrder in memdb.js): on 0-based board i,
    // the home team plays black when i is even and white when i is odd.
    const homeColor = i => (i % 2 === 0 ? 'black' : 'white');
    const awayColor = i => (i % 2 === 0 ? 'white' : 'black');

    // Roster of a team in a past match, indexed by 0-based board.
    const lineup = (match, wasHome) => {
      const ids = [];
      for (const g of match.games) {
        const c = wasHome ? homeColor(g.tableNr) : awayColor(g.tableNr);
        ids[g.tableNr] = c === 'white' ? g.whiteId : g.blackId;
      }
      return ids;
    };

    const report = [];
    const fill = (clubId, teamNr, colorOf, label) => {
      const isTeam = (id, nr) => id === clubId && nr === teamNr;
      const prev = matches
        .filter(m => m.roundNr < current.roundNr && m.games.length &&
          (isTeam(m.homeId, m.homeTeamNumber) || isTeam(m.awayId, m.awayTeamNumber)))
        .sort((a, b) => b.roundNr - a.roundNr)[0];
      if (!prev) { report.push(text.noPrevious(label)); return; }
      const ids = lineup(prev, isTeam(prev.homeId, prev.homeTeamNumber));
      const missing = [];
      for (let i = 0; i < boards; i++) {
        const s = sel(colorOf(i), i);
        const id = ids[i];
        if (id == null || !s.querySelector(`option[value="${id}"]`)) { missing.push(i + 1); continue; }
        s.value = String(id);
        if (window.jQuery) jQuery(s).trigger('chosen:updated');
      }
      report.push(text.copied(label, prev.roundNr) +
        (missing.length ? text.missing(missing) : '.'));
    };

    fill(current.homeId, current.homeTeamNumber, homeColor, text.home);
    fill(current.awayId, current.awayTeamNumber, awayColor, text.away);

    if (typeof updateTeamOrder === 'function') updateTeamOrder(boards, matchId);
    alert(report.join('\n') + '\n\n' + text.review);
  } catch (e) {
    alert(text.error + e.message);
  }
})();
```
