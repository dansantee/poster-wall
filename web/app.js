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
    
    // Always return the config with hostname
    return {
      sectionId:     Array.isArray(j.sectionId) ? j.sectionId : [j.sectionId || '1'],
      rotateSec:     Math.max(3, Number(j.rotateSec) || 10),
      plexUrl:       j.plexUrl       ?? '',
      plexToken:     j.plexToken     ?? '',
      plexInsecure:  !!j.plexInsecure,
      autoDim:       !!j.autoDim,
      hostname:      j.hostname      ?? '<hostname>',
      nowShowingText:j.nowShowingText?? 'NOW SHOWING',
      nowShowingFont:j.nowShowingFont?? "'Bebas Neue', sans-serif",
      nowShowingFontSize:j.nowShowingFontSize?? 9,
      nowShowingKerning:j.nowShowingKerning?? 0.1,
      nowShowingFontWeight:j.nowShowingFontWeight?? 700,
      nowShowingColor:j.nowShowingColor?? '#F4E88A',
      progressBarColor:j.progressBarColor?? '#F4E88A',
      progressBarPadding:j.progressBarPadding?? 1.5,
      progressBarHeight:j.progressBarHeight?? 2.5,
      plexDevices:   j.plexDevices   ?? []
    };
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
        section: cfg.sectionId[0] || '1',
        start: String(start),
        size:  String(PAGE_SIZE)
      });
      
      try {
        const r = await fetch(url, { cache: 'no-store', headers: h });
        if (!r.ok) {
          if (r.status === 401) {
            throw new Error('Plex authentication failed. Please check your Plex token.');
          } else if (r.status === 404) {
            throw new Error(`Plex section ${cfg.sectionId.join(', ')} not found. Please check your section ID(s).`);
          } else if (r.status === 400) {
            // Check if this might be due to incomplete config
            if (!cfg.plexUrl || !cfg.plexToken) {
              throw new Error('Plex configuration incomplete. Please check your Plex URL and token on the settings page.');
            }
            throw new Error(`Invalid request (${r.status}). Please check your configuration.`);
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

  // ---- icon mapping helpers ----
  function getContentRatingIcon(rating) {
    if (!rating || rating === 'N/A') return 'info-icons/Rated-NA.png';
    
    const ratingMap = {
      'TV-Y': 'info-icons/Rated-TVY.png',
      'TV-Y7': 'info-icons/Rated-TVY7.png',
      'TV-Y7 FV': 'info-icons/Rated-TVY7.png',
      'G': 'info-icons/Rated-G.png',
      'TV-G': 'info-icons/Rated-TVG.png',
      'PG': 'info-icons/Rated-PG.png',
      'TV-PG': 'info-icons/Rated-TVPG.png',
      'PG-13': 'info-icons/Rated-PG13.png',
      'TV-14': 'info-icons/Rated-TV14.png',
      'R': 'info-icons/Rated-R.png',
      'TV-MA': 'info-icons/Rated-TVMA.png',
      'NC-17': 'info-icons/Rated-NC17.png',
      'XXX': 'info-icons/Rated-XXX.png'
    };
    
    return ratingMap[rating] || 'info-icons/Rated-NA.png';
  }

  function getVideoResolutionIcon(resolution) {
    if (!resolution || resolution === 'N/A') return null;
    
    const resMap = {
      'sd': 'info-icons/Res-SD.png',
      '720': 'info-icons/Res-HD720.png',
      '720p': 'info-icons/Res-HD720.png',
      '1080': 'info-icons/Res-HD1080.png',
      '1080p': 'info-icons/Res-HD1080.png',
      '4k': 'info-icons/Res-UHD4K.png',
      '8k': 'info-icons/Res-UHD8K.png'
    };
    
    return resMap[resolution.toLowerCase()] || null;
  }

  function getAudioChannelIcon(channels) {
    if (!channels || channels === 'N/A') return null;
    
    const channelMap = {
      'mono': 'info-icons/Sound-Mono.png',
      'stereo': 'info-icons/Sound-2.0.png',
      '2.0': 'info-icons/Sound-2.0.png',
      '5.1': 'info-icons/Sound-5.1.png',
      '6.1': 'info-icons/Sound-5.1.png', // Use 5.1 icon for 6.1 as they're visually similar
      '7.1': 'info-icons/Dolby_TRUEHD71.png'
    };
    
    // Handle 5.1 variations (5.1, 5.1(side), etc.)
    if (channels.includes('5.1')) {
      return 'info-icons/Sound-5.1.png';
    }
    
    // Handle 6.1 variations  
    if (channels.includes('6.1')) {
      return 'info-icons/Sound-5.1.png';
    }
    
    return channelMap[channels.toLowerCase()] || null;
  }

  // ---- font and color settings helper ----
  function applyFontSettings(cfg) {
    const titleEl = document.getElementById('nowShowingTitle');
    const progressBarEl = document.getElementById('nowShowingProgressBar');
    
    // Apply font settings
    if (titleEl && cfg.nowShowingFont && cfg.nowShowingFontSize) {
      titleEl.style.fontFamily = cfg.nowShowingFont;
      const fontSize = cfg.nowShowingFontSize;
      const minSize = Math.round(fontSize * 5.33); // maintain ratio: 48px at 9vw
      const maxSize = Math.round(fontSize * 10.67); // maintain ratio: 96px at 9vw
      titleEl.style.fontSize = `clamp(${minSize}px, ${fontSize}vw, ${maxSize}px)`;
      
      // Apply kerning (letter-spacing) from settings
      const kerning = cfg.nowShowingKerning ?? 0.1;
      titleEl.style.letterSpacing = `${kerning}em`;
      
      // Apply font weight from settings
      const fontWeight = cfg.nowShowingFontWeight ?? 700;
      titleEl.style.fontWeight = fontWeight;
      
      // Apply special styling based on font choice
      const fontFamily = cfg.nowShowingFont.toLowerCase();
      
      // Reset all special properties first
      titleEl.style.fontStretch = '';
      titleEl.style.textTransform = '';
      titleEl.style.fontVariant = '';
      
      if (fontFamily.includes('impact') && fontFamily.includes('franklin gothic')) {
        // Original cinematic font combo
        titleEl.style.fontStretch = 'condensed';
        titleEl.style.textTransform = 'uppercase';
      } else if (fontFamily.includes('oswald')) {
        // Oswald: Tall, condensed sans serif - perfect for headers
        titleEl.style.textTransform = 'uppercase';
      } else if (fontFamily.includes('anton')) {
        // Anton: Heavy, bold, condensed - theater block lettering
        titleEl.style.textTransform = 'uppercase';
      } else if (fontFamily.includes('bebas neue')) {
        // Bebas Neue: Very popular tall sans - widely used in posters
        titleEl.style.textTransform = 'uppercase';
      } else if (fontFamily.includes('playfair display')) {
        // Playfair Display: Serif with high contrast - classical look
        titleEl.style.textTransform = 'uppercase';
        titleEl.style.fontVariant = 'small-caps';
      } else if (fontFamily.includes('cinzel')) {
        // Cinzel: Roman inscriptions inspired - dramatic posters
        titleEl.style.textTransform = 'uppercase';
      } else if (fontFamily.includes('raleway')) {
        // Raleway: Clean, modern sans - pairs with dramatic display type
        titleEl.style.textTransform = 'uppercase';
      } else if (fontFamily.includes('libre baskerville')) {
        // Libre Baskerville: Classic serif - refined marquee feel
        titleEl.style.textTransform = 'uppercase';
        titleEl.style.fontVariant = 'small-caps';
      }
    }
    
    // Apply color settings by updating CSS custom properties
    const root = document.documentElement;
    if (cfg.nowShowingColor) {
      root.style.setProperty('--now-showing-color', cfg.nowShowingColor);
    }
    if (cfg.progressBarColor) {
      root.style.setProperty('--progress-bar-color', cfg.progressBarColor);
    }
    if (cfg.progressBarPadding !== undefined) {
      root.style.setProperty('--progress-bar-padding', `${cfg.progressBarPadding}vh`);
    }
    if (cfg.progressBarHeight !== undefined) {
      root.style.setProperty('--progress-bar-height', `${cfg.progressBarHeight}vh`);
    }
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

  // ---- now playing functionality ----
  let rotationInterval = null;
  let nowPlayingInterval = null;
  let currentMode = 'rotation'; // 'rotation' or 'nowplaying'

  async function checkNowPlaying(cfg) {
    if (!cfg.plexDevices || cfg.plexDevices.length === 0) {
      return { playing: false };
    }

    try {
      const h = headers(cfg);
      const r = await fetch(`${proxyBase()}/api/now-playing`, { 
        cache: 'no-store', 
        headers: h 
      });
      
      if (!r.ok) {
        console.warn('Now playing check failed:', r.status);
        return { playing: false };
      }
      
      return await r.json();
    } catch (error) {
      console.warn('Now playing error:', error);
      return { playing: false };
    }
  }

  function showNowPlaying(data, cfg) {
    const stage = document.getElementById('stage');
    const nowShowing = document.getElementById('nowShowing');
    
    if (!nowShowing) return;

    // Hide rotation display
    stage.style.display = 'none';
    
    // Update now showing content
    const titleEl = document.getElementById('nowShowingTitle');
    const progressBar = document.getElementById('nowShowingProgressBar');
    const poster = document.getElementById('nowShowingPoster');
    const iconsContainer = document.getElementById('nowShowingMetadataIcons');

    if (titleEl) {
      titleEl.textContent = cfg.nowShowingText;
      applyFontSettings(cfg);
    }
    if (progressBar) {
      progressBar.style.width = `${data.progress || 0}%`;
    }
    if (poster && data.poster) poster.src = prox(data.poster);
    
    // Clear existing icons
    if (iconsContainer) {
      iconsContainer.innerHTML = '';
      
      // Create and add icons based on available metadata
      const icons = [];
      
      // Video resolution icon
      const videoIcon = getVideoResolutionIcon(data.videoResolution);
      if (videoIcon) {
        icons.push(`<img src="${videoIcon}" class="now-showing-metadata-icon" alt="${data.videoResolution || 'Unknown Resolution'}" />`);
      }
      
      // Audio channels icon
      const audioIcon = getAudioChannelIcon(data.audioChannels);
      if (audioIcon) {
        icons.push(`<img src="${audioIcon}" class="now-showing-metadata-icon" alt="${data.audioChannels || 'Unknown Audio'}" />`);
      }
      
      // Content rating icon (always show, fallback to N/A)
      const ratingIcon = getContentRatingIcon(data.rating);
      if (ratingIcon) {
        icons.push(`<img src="${ratingIcon}" class="now-showing-metadata-icon" alt="${data.rating || 'Not Rated'}" />`);
      }
      
      // Insert all icons
      iconsContainer.innerHTML = icons.join('');
    }

    // Show now playing screen
    nowShowing.classList.add('visible');
    currentMode = 'nowplaying';
  }

  function showRotation() {
    const stage = document.getElementById('stage');
    const nowShowing = document.getElementById('nowShowing');
    
    if (nowShowing) nowShowing.classList.remove('visible');
    if (stage) stage.style.display = 'block';
    currentMode = 'rotation';
  }

  function startRotation(cfg, list){
    if (!list.length) return;
    
    // Clear any existing rotation
    if (rotationInterval) {
      clearInterval(rotationInterval);
      rotationInterval = null;
    }

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

    rotationInterval = setInterval(()=>{
      const item = list[idx++ % list.length];
      swap(cfg, item.poster);
    }, cfg.rotateSec * 1000);
  }

  function startNowPlayingMonitor(cfg) {
    // Clear any existing monitor
    if (nowPlayingInterval) {
      clearInterval(nowPlayingInterval);
      nowPlayingInterval = null;
    }

    // Check every 5 seconds for now playing status
    nowPlayingInterval = setInterval(async () => {
      const nowPlayingData = await checkNowPlaying(cfg);
      
      if (nowPlayingData.playing && currentMode === 'rotation') {
        showNowPlaying(nowPlayingData, cfg);
      } else if (!nowPlayingData.playing && currentMode === 'nowplaying') {
        showRotation();
      } else if (nowPlayingData.playing && currentMode === 'nowplaying') {
        // Update progress bar if still playing
        const progressBar = document.getElementById('nowShowingProgressBar');
        if (progressBar) {
          progressBar.style.width = `${nowPlayingData.progress || 0}%`;
        }
      }
    }, 5000);

    // Initial check
    setTimeout(async () => {
      const nowPlayingData = await checkNowPlaying(cfg);
      if (nowPlayingData.playing) {
        showNowPlaying(nowPlayingData, cfg);
      }
    }, 1000);
  }

  // ---- boot ----
  (async function init(){
    try{
      const cfg = await loadCfg();
      const items = await fetchItems(cfg);
      
      // Apply initial font settings
      applyFontSettings(cfg);
      
      // Start poster rotation
      startRotation(cfg, items);
      
      // Start now playing monitoring if devices are configured
      if (cfg.plexDevices && cfg.plexDevices.length > 0) {
        startNowPlayingMonitor(cfg);
      }
    }catch(e){
      console.error(e);
      // Try to get hostname from config if we managed to load it
      let hostname = 'poster-wall.local';
      try {
        const r = await fetch(`${proxyBase()}/api/config`, { cache: 'no-store' });
        if (r.ok) {
          const j = await r.json();
          hostname = j.hostname || hostname;
        }
      } catch (configError) {
        // Use fallback hostname
      }
      showError(e.message || 'An unexpected error occurred. Please check the console for details.', hostname);
    }
  })();
})();
