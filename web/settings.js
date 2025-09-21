(function(){
  const LS = {
    get: (k, d=null) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set: (k, v) => localStorage.setItem(k, JSON.stringify(v))
  };

  // Always-proxy config (no useProxy flag)
  const cfg = LS.get('pw_config', {
    proxyUrl: 'http://localhost:8811',
    sectionId: '1',
    plexUrl: '',
    plexToken: '',
    plexInsecure: false,
    rotateSec: 120,
    autoDim: false
  });

  const el = id => document.getElementById(id);

  // Init fields
  el('sectionId').value = cfg.sectionId;
  el('proxyUrl').value = cfg.proxyUrl;
  el('plexUrl').value = cfg.plexUrl;
  el('plexToken').value = cfg.plexToken;
  el('plexInsecure').checked = !!cfg.plexInsecure;
  el('rotateSec').value = cfg.rotateSec;
  el('autoDim').checked = !!cfg.autoDim;

  // Save display settings  ⟵ now also saves autoDim here
  document.getElementById('display-form').addEventListener('submit', e => {
    e.preventDefault();
    cfg.sectionId = (el('sectionId').value || '1').trim();
    cfg.proxyUrl = (el('proxyUrl').value || 'http://localhost:8811').trim();
    cfg.rotateSec = Math.max(3, Number(el('rotateSec').value) || 120);
    cfg.autoDim = !!el('autoDim').checked;             // <- moved here
    LS.set('pw_config', cfg);
    alert('Display settings saved.');
  });

  // Save Plex settings (no autoDim here anymore)
  document.getElementById('plex-form').addEventListener('submit', e => {
    e.preventDefault();
    let u = (el('plexUrl').value || '').trim();
    if (u && !/^https?:\/\//i.test(u)) u = 'http://' + u; // normalize scheme
    cfg.plexUrl = u;
    cfg.plexToken = (el('plexToken').value || '').trim();
    cfg.plexInsecure = !!el('plexInsecure').checked;
    LS.set('pw_config', cfg);
    alert('Plex settings saved.');
  });

  // Ping Proxy
  document.getElementById('btnPing').addEventListener('click', async ()=>{
    const out = el('testOut');
    try {
      const r = await fetch(`${cfg.proxyUrl.replace(/\/$/,'')}/api/ping`);
      out.textContent = `Proxy OK: ${await r.text()}`;
    } catch (e) {
      out.textContent = `Proxy error: ${e}`;
    }
  });

  // Fetch Sample (always via proxy)
  document.getElementById('btnTry').addEventListener('click', async ()=>{
    const out = el('testOut');
    out.textContent = 'Fetching...';
    try {
      const url = `${cfg.proxyUrl.replace(/\/$/,'')}/api/movies?` + new URLSearchParams({
        section: cfg.sectionId || '1',
      });
      const headers = {};
      if (cfg.plexToken) headers['X-Plex-Token'] = cfg.plexToken;
      if (cfg.plexUrl)   headers['X-Plex-Url']   = cfg.plexUrl;
      if (cfg.plexInsecure) headers['X-Allow-Insecure'] = '1';

      const r = await fetch(url, { cache: 'no-store', headers });
      const text = await r.text();
      out.textContent = `Status ${r.status}\n` + text.slice(0, 2000);
    } catch (e) {
      out.textContent = `Error: ${e}`;
    }
  });
})();
