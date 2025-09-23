(function () {
  // ---------- helpers ----------
  const has = id => !!document.getElementById(id);
  const el  = id => document.getElementById(id);

  // Always talk to the proxy running on the same host (port 8811)
  function proxyBase() {
    return `${location.protocol}//${location.hostname}:8811`;
  }

  async function loadServerCfg() {
    const r = await fetch(`${proxyBase()}/api/config`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`GET /api/config ${r.status}`);
    return await r.json();
  }

  async function saveServerCfg(obj) {
    const headers = { 'Content-Type': 'application/json' };
    const key = has('adminKey') ? (el('adminKey').value || '').trim() : '';
    if (key) headers['X-Admin-Key'] = key;
    const r = await fetch(`${proxyBase()}/api/config`, {
      method: 'PUT', headers, body: JSON.stringify(obj)
    });
    if (!r.ok) throw new Error(`PUT /api/config ${r.status}`);
  }

  // ---------- init ----------
  (async function init() {
    try {
      const cfg = await loadServerCfg();

      // populate (null-safe; fields may be removed from HTML)
      if (has('sectionId'))   el('sectionId').value    = cfg.sectionId ?? '1';
      if (has('rotateSec'))   el('rotateSec').value    = cfg.rotateSec ?? 10;
      if (has('plexUrl'))     el('plexUrl').value      = cfg.plexUrl   ?? '';
      if (has('plexToken'))   el('plexToken').value    = cfg.plexToken ?? '';
      if (has('plexInsecure'))el('plexInsecure').checked = !!cfg.plexInsecure;
      if (has('autoDim'))     el('autoDim').checked    = !!cfg.autoDim;
      if (has('adminKey'))    el('adminKey').value     = ''; // never persist the key in JSON

      bindHandlers(cfg);
    } catch (e) {
      // minimal inline error
      const pre = document.getElementById('testOut');
      if (pre) pre.textContent = `Failed to load server config: ${e}`;
      console.error(e);
      bindHandlers({}); // still bind so user can try saving
    }
  })();

  function bindHandlers(cfg) {
    // Save button
    if (has('btnSave')) {
      el('btnSave').addEventListener('click', async () => {
        try {
          const next = { ...cfg };
          if (has('sectionId'))    next.sectionId    = (el('sectionId').value || '1').trim();
          if (has('rotateSec'))    next.rotateSec    = Math.max(3, Number(el('rotateSec').value) || 10);
          if (has('autoDim'))      next.autoDim      = !!el('autoDim').checked;
          if (has('plexUrl')) {
            let u = (el('plexUrl').value || '').trim();
            if (u && !/^https?:\/\//i.test(u)) u = 'http://' + u; // normalize
            next.plexUrl = u;
          }
          if (has('plexToken'))    next.plexToken    = (el('plexToken').value || '').trim();
          if (has('plexInsecure')) next.plexInsecure = !!el('plexInsecure').checked;

          await saveServerCfg(next);
          alert('Settings saved to server.');
        } catch (e) {
          alert(`Save failed: ${e}`);
        }
      });
    }

    // Restart button
    if (has('btnRestart')) {
      el('btnRestart').addEventListener('click', async () => {
        if (!confirm('Restart the kiosk service? The display will reload.')) return;
        
        try {
          const headers = { 'Content-Type': 'application/json' };
          const key = has('adminKey') ? (el('adminKey').value || '').trim() : '';
          if (key) headers['X-Admin-Key'] = key;
          
          const r = await fetch(`${proxyBase()}/api/restart-kiosk`, {
            method: 'POST', headers
          });
          
          if (r.ok) {
            alert('Kiosk restart initiated.');
          } else {
            const err = await r.text();
            alert(`Restart failed: ${err}`);
          }
        } catch (e) {
          alert(`Restart error: ${e}`);
        }
      });
    }

    // Consolidated settings form
    if (has('plex-form')) {
      el('plex-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const next = { ...cfg };
        if (has('sectionId'))    next.sectionId    = (el('sectionId').value || '1').trim();
        if (has('rotateSec'))    next.rotateSec    = Math.max(3, Number(el('rotateSec').value) || 10);
        if (has('autoDim'))      next.autoDim      = !!el('autoDim').checked;
        if (has('plexUrl')) {
          let u = (el('plexUrl').value || '').trim();
          if (u && !/^https?:\/\//i.test(u)) u = 'http://' + u; // normalize
          next.plexUrl = u;
        }
        if (has('plexToken'))    next.plexToken    = (el('plexToken').value || '').trim();
        if (has('plexInsecure')) next.plexInsecure = !!el('plexInsecure').checked;

        await saveServerCfg(next);
        alert('Settings saved to server.');
      });
    }

    // Test buttons
    if (has('btnPing')) {
      el('btnPing').addEventListener('click', async () => {
        const out = has('testOut') ? el('testOut') : null;
        try {
          const r = await fetch(`${proxyBase()}/api/ping`);
          if (out) out.textContent = `Proxy OK: ${await r.text()}`;
        } catch (e) {
          if (out) out.textContent = `Proxy error: ${e}`;
        }
      });
    }

    if (has('btnTry')) {
      el('btnTry').addEventListener('click', async () => {
        const out = has('testOut') ? el('testOut') : null;
        if (out) out.textContent = 'Fetching...';
        try {
          // Pull server cfg fresh for test
          const c = await (await fetch(`${proxyBase()}/api/config`, { cache:'no-store' })).json();
          const headers = {};
          if (c.plexToken)    headers['X-Plex-Token'] = c.plexToken;
          if (c.plexUrl)      headers['X-Plex-Url']   = c.plexUrl;
          if (c.plexInsecure) headers['X-Allow-Insecure'] = '1';

          const url = `${proxyBase()}/api/movies?` + new URLSearchParams({
            section: c.sectionId || '1',
            start: '0',
            size:  '1'
          });
          const r = await fetch(url, { cache: 'no-store', headers });
          const text = await r.text();
          if (out) out.textContent = `Status ${r.status}\n` + text.slice(0, 2000);
        } catch (e) {
          if (out) out.textContent = `Error: ${e}`;
        }
      });
    }
  }
})();
