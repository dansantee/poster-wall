// One-sheet crossfade: two stacked <img> elements fade between posters.
// Reads settings from pw_config (proxy URL, Plex URL/token, insecure flag, rotateSec).
(function(){
  const LS = { get:(k,d=null)=>{ try{return JSON.parse(localStorage.getItem(k))??d;}catch{return d;} } };
  const cfg = LS.get('pw_config', {
    proxyUrl: 'http://localhost:8811',
    sectionId: '1',
    limit: 150,
    plexUrl: '',
    plexToken: '',
    plexInsecure: false,
    rotateSec: 10,
    autoDim: false
  });

  const imgA  = document.getElementById('posterA');
  const imgB  = document.getElementById('posterB');
  let front = imgA, back = imgB;

  function headers(){
    const h = {};
    if (cfg.plexToken) h['X-Plex-Token'] = cfg.plexToken;
    if (cfg.plexUrl)   h['X-Plex-Url']   = cfg.plexUrl;
    if (cfg.plexInsecure) h['X-Allow-Insecure'] = '1';
    return h;
  }

  function prox(u){
    if (!u) return u;
    if (/^https?:\/\//i.test(u)) return u;              // already absolute
    return cfg.proxyUrl.replace(/\/$/,'') + u;           // prefix proxy base (e.g., http://localhost:8811)
  }

  function computeBrightness(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = 'anonymous'; // stays same-origin due to proxy path
      img.onload = () => {
        // Downsample aggressively for speed
        const w = 32, h = 32;
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, w, h);
        const { data } = ctx.getImageData(0, 0, w, h);
        let sum = 0;
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i], g = data[i+1], b = data[i+2];
          sum += 0.299*r + 0.587*g + 0.114*b; // perceived luminance
        }
        const avg = sum / (w*h);
        resolve(avg); // 0..255
      };
      img.onerror = () => resolve(0); // treat failures as dark
      img.src = src;
    });
  }

  function shouldDim(avgLuma) {
    return avgLuma >= 150;
  }

  async function fetchItems(){
    const headers = {};
    if (cfg.plexToken) headers['X-Plex-Token'] = cfg.plexToken;
    if (cfg.plexUrl)   headers['X-Plex-Url']   = cfg.plexUrl;
    if (cfg.plexInsecure) headers['X-Allow-Insecure'] = '1';

    const items = [];
    const PAGE_SIZE = 500;           // good balance; change if you like
    let start = 0;
    const hardCap = 50000;           // safety guard; prevents infinite loops

    while (items.length < hardCap) {
      const url = `${cfg.proxyUrl.replace(/\/$/,'')}/api/movies?` + new URLSearchParams({
        section: cfg.sectionId || '1',
        start: String(start),
        size:  String(PAGE_SIZE)
      });

      const r = await fetch(url, { cache: 'no-store', headers });
      if (!r.ok) throw new Error(`Proxy ${r.status}`);
      const j = await r.json();
      const batch = j.items || [];
      items.push(...batch);

      if (batch.length < PAGE_SIZE) break; // no more pages
      start += PAGE_SIZE;
    }

    return items;
  }

  function preload(src){
    return new Promise((res, rej)=>{
      const i = new Image();
      i.onload = ()=>res();
      i.onerror = rej;
      i.src = src;
    });
  }

  function swap(src){
    src = prox(src);
    back.classList.remove('visible');
    preload(src).then(async ()=>{
      back.src = src;

      // Auto-dim if enabled
      if (cfg.autoDim) {
        try {
          const luma = await computeBrightness(prox(src));
          if (shouldDim(luma)) back.classList.add('dim');
          else back.classList.remove('dim');
        } catch { back.classList.remove('dim'); }
      } else {
        back.classList.remove('dim');
      }

      requestAnimationFrame(()=>{
        front.classList.remove('visible');
        back.classList.add('visible');
        const t = front; front = back; back = t;
      });
    });
  }

  function startRotation(list){
    if (!list.length) return;
    // shuffle
    for (let i=list.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1)); [list[i],list[j]]=[list[j],list[i]]; }
    let idx = 0;

    // prime first image
    const first = list[idx++ % list.length];
    front.src = prox(first.poster);
    if (cfg.autoDim) {
      computeBrightness(prox(first.poster)).then(luma=>{
        if (shouldDim(luma)) front.classList.add('dim');
        else front.classList.remove('dim');
      });
    } else {
      front.classList.remove('dim');
    }
    front.classList.add('visible');
    front.classList.add('visible');

    const sec = Math.max(3, Number(cfg.rotateSec) || 10);
    setInterval(()=>{
      const item = list[idx++ % list.length];
      swap(item.poster);
    }, sec * 1000);
  }

  (async function init(){
    try{
      const items = await fetchItems();
      startRotation(items);
    }catch(e){
      console.error("Failed to fetch posters:", e);
    }
  })();
})();
