(function(){
  // ---------- utilities ----------
  const has = id => !!document.getElementById(id);
  const el  = id => document.getElementById(id);
  const LS  = {
    get(k, d=null){ try{ return JSON.parse(localStorage.getItem(k)) ?? d; } catch{ return d; } },
    set(k, v){ localStorage.setItem(k, JSON.stringify(v)); }
  };

  // ---------- defaults ----------
  const defaults = {
    proxyUrl: 'http://localhost:8811',
    sectionId: '1',
    rotateSec: 10,
    plexUrl: '',
    plexToken: '',
    plexInsecure: false,
    autoDim: false,
    adminKey: ''        // optional; if you add an <input id="adminKey"> we'll read it
  };

  // Load local to bootstrap proxyUrl quickly
  let cfg = { ...defaults, ...(LS.get('pw_config') || {}) };

  // ---------- server config I/O ----------
  async function loadServerCfg(proxyUrl){
    try{
      const r = await fetch(`${proxyUrl.replace(/\/$/,'')}/api/config`, { cache: 'no-store' });
      if (r.ok) return await r.json();
    }catch{}
    return null;
  }

  async function saveServerCfg(obj){
    try{
      const headers = { 'Content-Type': 'application/json' };
      const key = has('adminKey') ? (el('adminKey').value || '').trim() : (obj.adminKey || '');
      if (key) headers['X-Admin-Key'] = key;
      const r = await fetch(`${obj.proxyUrl.replace(/\/$/,'')}/api/config`, {
        method: 'PUT', headers, body: JSON.stringify(obj)
      });
      if (!r.ok) throw new Error(`PUT /api/config ${r.status}`);
      return true;
    }catch(e){
      console.warn('Server config save failed, falling back to localStorage:', e);
      return false;
    }
  }

  // ---------- init (merge: server > local > defaults) ----------
  (async function init(){
    // First, if there's a Proxy URL field on the page, prefill from local
    if (has('proxyUrl')) el('proxyUrl').value = cfg.proxyUrl;

    // Try server config using whatever proxyUrl we currently have (field or local)
    const probeProxy = has('proxyUrl') ? (el('proxyUrl').value || cfg.proxyUrl) : cfg.proxyUrl;
    const serverCfg = await loadServerCfg(probeProxy);

    // Merge order: defaults < local < server (server wins)
    if (serverCfg) {
      cfg = { ...defaults, ...(LS.get('pw_config')||{}), ...serverCfg, proxyUrl: probeProxy };
    } else {
      cfg = { ...defaults, ...(LS.get('pw_config')||{}) };
    }

    // Populate fields (null-safe so you can remove inputs freely)
    if (has('limit'))       el('limit').value = cfg.limit ?? 150; // optional legacy
    if (has('rotateSec'))   el('rotateSec').value = cfg.rotateSec;
    if (has('sectionId'))   el('sectionId').value = cfg.sectionId;
    if (has('proxyUrl'))    el('proxyUrl').value = cfg.proxyUrl;
    if (has('plexUrl'))     el('plexUrl').value = cfg.plexUrl;
    if (has('plexToken'))   el('plexToken').value = cfg.plexToken;
    if (has('plexInsecure'))el('plexInsecure').checked = !!cfg.plexInsecure;
    if (has('autoDim'))     el('autoDim').checked = !!cfg.autoDim;
    if (has('adminKey'))    el('adminKey').value = cfg.adminKey || '';

    // Bind save handlers
    bindHandlers();
  })();

  function bindHandlers(){
    // Display form
    if (has('display-form')) {
      el('display-form').addEventListener('submit', async (e)=>{
        e.preventDefault();
        if (has('sectionId'))   cfg.sectionId   = (el('sectionId').value || '1').trim();
        if (has('proxyUrl'))    cfg.proxyUrl    = (el('proxyUrl').value || 'http://localhost:8811').trim();
        if (has('rotateSec'))   cfg.rotateSec   = Math.max(3, Number(el('rotateSec').value) || cfg.rotateSec || 10);
        if (has('autoDim'))     cfg.autoDim     = !!el('autoDim').checked;
        if (has('limit'))       cfg.limit       = Number(el('limit').value) || cfg.limit || 150; // legacy optional

        const ok = await saveServerCfg(cfg);
        if (!ok) LS.set('pw_config', cfg);
        alert('Display settings saved.');
      });
    }

    // Plex form
    if (has('plex-form')) {
      el('plex-form').addEventListener('submit', async (e)=>{
        e.preventDefault();
        if (has('plexUrl')) {
          let u = (el('plexUrl').value || '').trim();
          if (u && !/^https?:\/\//i.test(u)) u = 'http://' + u; // normalize
          cfg.plexUrl = u;
        }
        if (has('plexToken'))   cfg.plexToken   = (el('plexToken').value || '').trim();
        if (has('plexInsecure'))cfg.plexInsecure= !!el('plexInsecure').checked;
        if (has('adminKey'))    cfg.adminKey    = (el('adminKey').value || '').trim();

        const ok = await saveServerCfg(cfg);
        if (!ok) LS.set('pw_config', cfg);
        alert('Plex settings saved.');
      });
    }

    // Test buttons
    if (has('btnPing')) {
      el('btnPing').addEventListener('click', async ()=>{
        const out = has('testOut') ? el('testOut') : null;
        try {
          const r = await fetch(`${cfg.proxyUrl.replace(/\/$/,'')}/api/ping`);
          if (out) out.textContent = `Proxy OK: ${await r.text()}`;
        } catch (e) {
          if (out) out.textContent = `Proxy error: ${e}`;
        }
      });
    }

    if (has('btnTry')) {
      el('btnTry').addEventListener('click', async ()=>{
        const out = has('testOut') ? el('testOut') : null;
        if (out) out.textContent = 'Fetching...';
        try {
          const url = `${cfg.proxyUrl.replace(/\/$/,'')}/api/movies?` + new URLSearchParams({
            section: cfg.sectionId || '1',
            start: '0',
            size:  '1'
          });
          const headers = {};
          if (cfg.plexToken) headers['X-Plex-Token'] = cfg.plexToken;
          if (cfg.plexUrl)   headers['X-Plex-Url']   = cfg.plexUrl;
          if (cfg.plexInsecure) headers['X-Allow-Insecure'] = '1';

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
