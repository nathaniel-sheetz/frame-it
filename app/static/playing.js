// The strip under the header that shows the playlist that is running, with its controls.
// Shared by the gallery, the library and the playlists page. Each time what is playing
// changes, a "playing" event on the document carries the news to the page.
(() => {
  const POLL = 5000;
  const strip = document.createElement('div');
  strip.id = 'playing';
  strip.hidden = true;
  document.querySelector('header').after(strip);

  let last = '';
  let failure = '';
  let working = false;

  function el(tag, text = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }

  // Time left on the picture, to the minute: it is only asked for every few seconds.
  function left(seconds) {
    if (seconds < 60) return 'under a minute';
    const minutes = Math.ceil(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
  }

  function when(p) {
    if (p.state === 'sending') {
      const sent = `Sending its pictures to the TV: ${p.on_tv} of ${p.total} are there.`;
      return p.problem ? `${sent} Waiting for the TV. ${p.problem}` : sent;
    }
    const next = p.next ? `Next: ${p.next.name}` : p.playlist.shuffle && p.total > 1 ? 'Next: a new shuffle' : '';
    let clock;
    if (p.paused) clock = 'Paused';
    else if (p.problem) clock = `Waiting for the TV. ${p.problem}`;
    else if (!p.counting) clock = 'Waiting for Art Mode: the clock only runs while the picture is on screen';
    else if (p.total < 2) clock = 'The only picture in the playlist';
    else clock = `Changes in ${left(p.remaining)}`;
    return [clock, next].filter(Boolean).join(' · ');
  }

  function button(text, action, className = '') {
    const b = el('button', text, className);
    b.disabled = working;
    b.onclick = () => act(action);
    return b;
  }

  function render({ playing: p, ended }) {
    strip.replaceChildren();
    if (!p) {
      // A playlist that stopped by itself says why, until the note is dismissed.
      let dismissed = null;
      try { dismissed = sessionStorage.getItem('ended'); } catch { /* private browsing */ }
      strip.hidden = !ended || dismissed === ended;
      if (strip.hidden) return;
      const what = el('div', ended, 'what');
      const ok = el('button', 'Dismiss');
      ok.onclick = () => {
        try { sessionStorage.setItem('ended', ended); } catch { /* private browsing */ }
        strip.hidden = true;
      };
      strip.append(what, ok);
      return;
    }
    try { sessionStorage.removeItem('ended'); } catch { /* private browsing */ }
    strip.hidden = false;

    if (p.current) {
      const img = el('img');
      img.alt = '';
      img.src = `/api/library/${p.current.id}/thumb`;
      strip.append(img);
    }
    const what = el('div', '', 'what');
    const name = el('a', p.playlist.name);
    name.href = '/playlists';
    what.append(name, p.current ? ` · ${p.current.name}` : '');
    what.append(el('div', failure || when(p), failure ? 'when err' : 'when'));

    const controls = el('div', '', 'controls');
    if (p.state === 'playing') {
      controls.append(
        button('Back', 'back'),
        p.paused ? button('Resume', 'resume', 'primary') : button('Pause', 'pause'),
        button('Next', 'next'));
    }
    controls.append(button('Stop', 'stop', 'danger'));
    strip.append(what, controls);
  }

  function show(data) {
    render(data);
    // The seconds left change every time; the page is only told about the rest.
    const news = JSON.stringify({ ...data, playing: data.playing && { ...data.playing, remaining: 0 } });
    if (news !== last) {
      last = news;
      document.dispatchEvent(new CustomEvent('playing', { detail: data }));
    }
  }

  async function ask(path, options) {
    const res = await fetch(path, options);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${res.status})`);
    return body;
  }

  async function act(action) {
    if (working) return;
    if (action === 'stop' && !confirm('Stop the playlist? Its pictures stay on the TV.')) return;
    working = true;
    strip.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      failure = '';
      const data = await ask(`/api/playing/${action}`, { method: 'POST' });
      working = false;
      show(data);
    } catch (e) {
      working = false;
      failure = e.message;
      await refresh();
    }
  }

  async function refresh() {
    try {
      const data = await ask('/api/playing');
      if (!working) show(data);
    } catch {
      // The server is restarting or out of reach. The strip keeps what it last knew.
    }
    failure = '';
  }

  setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, POLL);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
  // For a page that has just started or stopped a playlist itself.
  window.playingChanged = refresh;
  refresh();
})();
