// One-sheet crossfade player with server-managed config.
// - No localStorage. Always GET /api/config from the proxy on the same host (port 8811).
// - Paged fetch from /api/movies (start/size).
// - Crossfade between two <img> (.poster) elements.
// - Optional auto-dim using canvas average luminance.

(function(){
  const imgA  = document.getElementById('posterA');
  const imgB  = document.getElementById('posterB');
  let front = imgA, back = imgB;

  // Always talk to the proxy running on this host
  function proxyBase(){ return `${location.protocol}//${location.hostname}:8811`; }

  // Display error message to user
  function showError(message, hostname = 'poster-wall.local') {
    const settingsUrl = `http://${hostname}:8088/settings.html`;
    document.body.innerHTML = `
      <div class="error-container">
        <div class="error-message">
          ${message}
        </div>
        <div class="error-settings">
          <div class="error-settings-label">
            Configure settings at:
          </div>
          <div class="error-settings-url">
            ${settingsUrl}
          </div>
        </div>
      </div>
    `;
  }

  async function loadCfg(){
    const r = await fetch(`${proxyBase()}/api/config`, { cache: 'no-store' });
    if (!r.ok) {
      throw new Error(`Configuration service unavailable (${r.status}). Please check if the proxy server is running on port 8811.`);
    }
    const j = await r.json();
    
    // Check if config has required fields for operation (but always return the config with hostname)
    const config = {
      sectionId:   j.sectionId   ?? '1',
      rotateSec:   Math.max(3, Number(j.rotateSec) || 10),
      plexUrl:     j.plexUrl     ?? '',
      plexToken:   j.plexToken   ?? '',
      plexInsecure:!!j.plexInsecure,
      autoDim:     !!j.autoDim,
      hostname:    j.hostname    ?? '<hostname>'
    };
    
    if (!config.plexUrl || !config.plexToken) {
      const error = new Error('Plex configuration incomplete. Please check your Plex URL and token on the settings page.');
      error.config = config; // attach config so we can get hostname
      throw error;
    }
    
    return config;
  }

  function headers(cfg){
    const h = {};
    if (cfg.plexToken)    h['X-Plex-Token'] = cfg.plexToken;
    if (cfg.plexUrl)      h['X-Plex-Url']   = cfg.plexUrl;
    if (cfg.plexInsecure) h['X-Allow-Insecure'] = '1';
    return h;
  }

  // Prefix relative poster URLs so they hit the proxy (not the static server)
  function prox(u){
    if (!u) return u;
    if (/^https?:\/\//i.test(u)) return u;
    return proxyBase() + u;
  }

  // Paged fetch of all items
  async function fetchItems(cfg){
    const out = [];
    const PAGE_SIZE = 500;
    let start = 0;
    const h = headers(cfg);

    while (true) {
      const url = `${proxyBase()}/api/movies?` + new URLSearchParams({
        section: cfg.sectionId || '1',
        start: String(start),
        size:  String(PAGE_SIZE)
      });
      
      try {
        const r = await fetch(url, { cache: 'no-store', headers: h });
        if (!r.ok) {
          if (r.status === 401) {
            throw new Error('Plex authentication failed. Please check your Plex token.');
          } else if (r.status === 404) {
            throw new Error(`Plex section ${cfg.sectionId} not found. Please check your section ID.`);
          } else if (r.status >= 500) {
            throw new Error(`Plex server error (${r.status}). Please check your Plex server.`);
          } else {
            throw new Error(`Movies service error (${r.status}). Please check your configuration.`);
          }
        }
        
        const j = await r.json();
        const batch = j.items || [];
        out.push(...batch);
        if (batch.length < PAGE_SIZE) break;
        start += PAGE_SIZE;
        if (start > 50000) break; // safety guard
      } catch (error) {
        if (error.message.includes('fetch')) {
          throw new Error('Unable to connect to movies service. Please check network connectivity.');
        }
        throw error;
      }
    }
    
    if (out.length === 0) {
      throw new Error('No movies found in the specified Plex section. Please check your library.');
    }
    
    return out;
  }

  // ---- auto-dim brightness helpers ----
  function computeBrightness(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const w = 32, h = 32;
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, w, h);
        const { data } = ctx.getImageData(0, 0, w, h);
        let sum = 0;
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i], g = data[i+1], b = data[i+2];
          sum += 0.299*r + 0.587*g + 0.114*b;
        }
        resolve(sum / (w*h)); // 0..255
      };
      img.onerror = () => resolve(0);
      img.src = src;
    });
  }
  function shouldDim(avgLuma){ return avgLuma >= 200; } // tweak if desired

  // ---- crossfade plumbing ----
  function preload(src){
    return new Promise((res, rej)=>{
      const i = new Image();
      i.onload = ()=>res();
      i.onerror = rej;
      i.src = src;
    });
  }

  async function applyDim(el, enabled, src){
    if (!enabled) { el.classList.remove('dim'); return; }
    try {
      const luma = await computeBrightness(src);
      if (shouldDim(luma)) el.classList.add('dim'); else el.classList.remove('dim');
    } catch {
      el.classList.remove('dim');
    }
  }

  function swap(cfg, src){
    back.classList.remove('visible');
    const psrc = prox(src);
    preload(psrc).then(async ()=>{
      back.src = psrc;
      await applyDim(back, cfg.autoDim, psrc);
      requestAnimationFrame(()=>{
        front.classList.remove('visible');
        back.classList.add('visible');
        const t = front; front = back; back = t;
      });
    }).catch(()=>{/* ignore a single failed image */});
  }

  function startRotation(cfg, list){
    if (!list.length) return;
    // shuffle
    for (let i=list.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1)); [list[i],list[j]]=[list[j],list[i]]; }
    let idx = 0;

    // prime first
    const first = list[idx++ % list.length];
    const firstSrc = prox(first.poster);
    front.src = firstSrc;
    applyDim(front, cfg.autoDim, firstSrc).then(()=>{
      front.classList.add('visible');
    });

    setInterval(()=>{
      const item = list[idx++ % list.length];
      swap(cfg, item.poster);
    }, cfg.rotateSec * 1000);
  }

  // ---- boot ----
  (async function init(){
    try{
      const cfg = await loadCfg();
      const items = await fetchItems(cfg);
      startRotation(cfg, items);
    }catch(e){
      console.error(e);
      // If we have config attached to error (incomplete config), use its hostname
      const hostname = e.config?.hostname || 'poster-wall.local';
      showError(e.message || 'An unexpected error occurred. Please check the console for details.', hostname);
    }
  })();
})();
