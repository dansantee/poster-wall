// One-sheet crossfade player with server-managed config.
// - No localStorage. Always GET /api/config from the proxy on the same host (port 8811).
// - Paged fetch from /api/movies (start/size).
// - Crossfade between two <img> (.poster) elements.
// - Optional auto-dim using canvas average luminance.

(function(){
  const imgA  = document.getElementById('posterA');
  const imgB  = document.getElementById('posterB');
  let front = imgA, back = imgB;
  const previewMode = new URLSearchParams(location.search).get('preview');

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
    
    console.log('Raw config from server:', j);
    
    // Always return the config with hostname
    const config = {
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
      progressTrackColor:j.progressTrackColor?? '#788496',
      progressTrackOpacity:Math.min(1, Math.max(0.1, Number(j.progressTrackOpacity) || 0.92)),
      progressBarPadding:j.progressBarPadding?? 1.5,
      progressBarHeight:j.progressBarHeight?? 2.5,
      autoDimStrength:Math.min(1, Math.max(0.2, Number(j.autoDimStrength) || 0.5)),
      posterTransitions:!!j.posterTransitions,
      transitionTypes:(j.transitionTypes && Array.isArray(j.transitionTypes)) ? j.transitionTypes : ['crossfade'],
      musicVideoSectionId:Array.isArray(j.musicVideoSectionId) ? j.musicVideoSectionId : (j.musicVideoSectionId ? [String(j.musicVideoSectionId)] : []),
      plexDevices:   j.plexDevices   ?? []
    };
    
    console.log('Processed config:', {
      posterTransitions: config.posterTransitions,
      transitionType: config.transitionType
    });
    
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
      // Don't specify section - let the backend use all configured sections from server config
      const url = `${proxyBase()}/api/movies?` + new URLSearchParams({
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

  function hexToRgb(hex) {
    const normalized = (hex || '').replace('#', '');
    if (!/^[0-9a-f]{6}$/i.test(normalized)) return null;
    return {
      r: parseInt(normalized.slice(0, 2), 16),
      g: parseInt(normalized.slice(2, 4), 16),
      b: parseInt(normalized.slice(4, 6), 16)
    };
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
    if (cfg.progressTrackColor) {
      const rgb = hexToRgb(cfg.progressTrackColor);
      if (rgb) {
        root.style.setProperty('--progress-track-color', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${cfg.progressTrackOpacity ?? 0.92})`);
      }
    }
    if (cfg.progressBarPadding !== undefined) {
      root.style.setProperty('--progress-bar-padding', `${cfg.progressBarPadding}vh`);
    }
    if (cfg.progressBarHeight !== undefined) {
      root.style.setProperty('--progress-bar-height', `${cfg.progressBarHeight}vh`);
    }
    if (cfg.autoDimStrength !== undefined) {
      root.style.setProperty('--poster-dim-brightness', String(cfg.autoDimStrength));
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

  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
    if (max === min) return { h: 0, s: 0, l };
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return { h: h * 60, s, l };
  }

  // Music-video colours from the art, on a 16x16 sample:
  // - backdrop: the average colour, darkened so white text stays readable
  // - accent: the most vivid pixels' colour, lifted to a bright, saturated tone that stands out
  //   on the dark backdrop (progress bar, "Up next" label); null for near-greyscale art,
  //   which keeps the white default
  function computeArtColors(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const c = document.createElement('canvas');
        c.width = 16; c.height = 16;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, 16, 16);
        const { data } = ctx.getImageData(0, 0, 16, 16);
        let r = 0, g = 0, b = 0;
        const pixels = [];
        for (let i = 0; i < data.length; i += 4) {
          r += data[i]; g += data[i+1]; b += data[i+2];
          const hsl = rgbToHsl(data[i], data[i+1], data[i+2]);
          // Vivid = saturated and neither near-black nor near-white
          pixels.push({ rgb: [data[i], data[i+1], data[i+2]], score: hsl.s * (1 - Math.abs(hsl.l - 0.5) * 2) });
        }
        const n = data.length / 4, darken = 0.55;
        const backdrop = `rgb(${Math.round(r / n * darken)}, ${Math.round(g / n * darken)}, ${Math.round(b / n * darken)})`;

        const top = Math.max(...pixels.map(p => p.score));
        let accent = null;
        if (top >= 0.15) {
          const vivid = pixels.filter(p => p.score >= top * 0.6);
          const avg = [0, 1, 2].map(k => vivid.reduce((sum, p) => sum + p.rgb[k], 0) / vivid.length);
          const hsl = rgbToHsl(avg[0], avg[1], avg[2]);
          accent = `hsl(${Math.round(hsl.h)}, ${Math.round(Math.max(hsl.s, 0.55) * 100)}%, 66%)`;
        }
        resolve({ backdrop, accent });
      };
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

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

  function makePreviewNowPlaying(items, musicVideo) {
    const item = (items && items.length > 0) ? items[0] : null;
    // ?state=paused previews the pause treatment
    const state = new URLSearchParams(location.search).get('state') || 'playing';
    const preview = {
      playing: true,
      state,
      duration: 240000,
      viewOffset: 100800,
      offsetAt: Date.now(),
      progress: 42,
      poster: item ? item.poster : '',
      rating: item ? item.rating : 'PG-13',
      videoResolution: '4k',
      audioChannels: '5.1'
    };
    if (musicVideo) {
      preview.mediaType = 'musicvideo';
      preview.artist = 'Weezer';
      preview.trackTitle = 'Buddy Holly';
      preview.upNext = (items || []).slice(1, 4).map((it, i) => ({
        title: it.title, artist: ['Fiona Apple', 'Duran Duran', 'Sublime'][i], trackTitle: it.title, poster: it.poster
      }));
    }
    return preview;
  }

  function swap(cfg, src){
    // Debug: Log configuration values and element states
    console.log('Swap called with config:', {
      posterTransitions: cfg.posterTransitions,
      transitionTypes: cfg.transitionTypes
    });
    console.log('Element states before swap:', {
      frontVisible: front.classList.contains('visible'),
      backVisible: back.classList.contains('visible'),
      frontSrc: front.src ? front.src.split('/').pop() : 'no-src',
      backSrc: back.src ? back.src.split('/').pop() : 'no-src'
    });
    
    // Skip transitions if disabled
    if (!cfg.posterTransitions) {
      console.log('Transitions disabled, using basic crossfade');
      // Original crossfade behavior
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
      return;
    }

    // Randomly select a transition type from the available options
    const availableTransitions = cfg.transitionTypes || ['crossfade'];
    const randomTransitionType = availableTransitions[Math.floor(Math.random() * availableTransitions.length)];
    console.log(`Randomly selected transition: ${randomTransitionType} from [${availableTransitions.join(', ')}]`);

    // Handle crossfade as a proper transition when transitions are enabled
    if (randomTransitionType === 'crossfade') {
      console.log('Using enhanced crossfade transition');
      const psrc = prox(src);
      
      preload(psrc).then(async ()=>{
        back.src = psrc;
        await applyDim(back, cfg.autoDim, psrc);
        
        requestAnimationFrame(()=>{
          // Clear any existing transition classes
          front.className = front.className.replace(/transition-\S+/g, '').trim();
          back.className = back.className.replace(/transition-\S+/g, '').trim();
          
          // Add base poster class and crossfade transition class
          front.classList.add('poster', 'transition-crossfade');
          back.classList.add('poster', 'transition-crossfade');
          
          // Start crossfade: hide front, show back
          front.classList.remove('visible');
          back.classList.add('visible');
          
          // Clean up and swap references after transition
          setTimeout(() => {
            front.classList.remove('transition-crossfade');
            back.classList.remove('transition-crossfade');
            // Swap references for next transition
            const t = front; front = back; back = t;
          }, 1000); // Allow full transition time
        });
      }).catch(()=>{/* ignore a single failed image */});
      return;
    }

    // Advanced transitions
    const transitionClass = `transition-${randomTransitionType}`;
    console.log('Using advanced transition:', transitionClass);
    const psrc = prox(src);
    
    preload(psrc).then(async ()=>{
      back.src = psrc;
      await applyDim(back, cfg.autoDim, psrc);
      
      requestAnimationFrame(()=>{
        // Ensure clean starting state
        front.className = 'poster';
        back.className = 'poster';
        front.style.opacity = '';
        back.style.opacity = '';
        
        // Add the specific transition class
        front.classList.add(transitionClass);
        back.classList.add(transitionClass);
        
        // Set initial states - back element starts in "entering" position
        back.classList.add('entering');
        
        console.log('Starting transition with classes:', {
          front: front.className,
          back: back.className
        });
        
        // Use a short delay to ensure the entering state is applied before transitioning
        requestAnimationFrame(() => {
          // Start the transition: front exits, back becomes visible
          front.classList.remove('visible');
          front.classList.add('exiting');
          
          back.classList.remove('entering');
          back.classList.add('visible');
          
          console.log('Mid-transition classes:', {
            front: front.className,
            back: back.className
          });
          
          // Clean up after transition completes
          setTimeout(() => {
            console.log('Cleaning up transition');
            
            // Completely reset both elements
            front.className = 'poster';
            back.className = 'poster visible';
            front.style.opacity = '';
            back.style.opacity = '';
            
            console.log('Post-cleanup classes:', {
              front: front.className,
              back: back.className
            });
            
            // Swap references
            const t = front; front = back; back = t;
            
            console.log('After swap - front visible:', front.classList.contains('visible'));
          }, 1000); // Allow full transition time
        });
      });
    }).catch(()=>{/* ignore a single failed image */});
  }

  // ---- now playing functionality ----
  let rotationInterval = null;
  let nowPlayingTimer = null;
  // The proxy answers /api/now-playing from a cache that Plex's websocket keeps fresh, so a
  // 1 s poll costs nothing upstream and stops/changes reach the wall within about a second.
  const POLL_MS = 1000;
  // Plex drops the old session before a playlist's next item appears; don't flash the poster
  // rotation in between. Nothing queued: 3 s. More queued: up to 8 s (slow loads measured at
  // 3.9-4.2 s), showing the loading look once it has been more than a normal 0.1-0.5 s gap.
  const STOP_GRACE_MS = 3000;
  const QUEUE_GRACE_MS = 8000;
  const LOADING_LOOK_AFTER_MS = 1000;
  const PROGRESS_TICK_MS = 250;
  let currentMode = 'rotation'; // 'rotation' or 'nowplaying'
  let currentItemKey = null;    // what is on screen in nowplaying mode, to spot a track change

  function nowPlayingKey(data) {
    return data.ratingKey || data.poster || data.title || null;
  }

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
    const backdrop = document.getElementById('nowShowingBackdrop');
    const artistEl = document.getElementById('nowShowingArtist');
    const songEl = document.getElementById('nowShowingSong');
    const isMusicVideo = data.mediaType === 'musicvideo';

    nowShowing.classList.toggle('music', isMusicVideo);
    if (titleEl) {
      titleEl.textContent = cfg.nowShowingText; // hidden by CSS in music-video mode
      applyFontSettings(cfg);
    }
    if (poster && data.poster) {
      poster.onload = placePauseBadge;
      poster.src = prox(data.poster);
    }
    setScrollingText(artistEl, isMusicVideo ? (data.artist || '') : '');
    setScrollingText(songEl, isMusicVideo ? (data.trackTitle || data.title || '') : '');
    if (backdrop && isMusicVideo && data.poster) {
      const colorsFor = nowPlayingKey(data);
      computeArtColors(prox(data.poster)).then(colors => {
        // A slow result for an earlier item must not recolour the current one (Astra pass 1 #4)
        if (!colors || currentItemKey !== colorsFor) return;
        backdrop.style.backgroundColor = colors.backdrop;
        if (colors.accent) nowShowing.style.setProperty('--music-accent', colors.accent);
        else nowShowing.style.removeProperty('--music-accent');
      });
    }
    upNextShown = null; // force the up-next row to redraw for the new item
    currentItemKey = nowPlayingKey(data);

    // Clear existing icons (music videos show artist and song instead)
    if (iconsContainer && isMusicVideo) {
      iconsContainer.innerHTML = '';
    } else if (iconsContainer) {
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
    applyPlayback(data, true);
  }

  function showRotation() {
    const stage = document.getElementById('stage');
    const nowShowing = document.getElementById('nowShowing');

    if (nowShowing) nowShowing.classList.remove('visible', 'music', 'paused', 'loading');
    if (stage) stage.style.display = 'block';
    currentMode = 'rotation';
    currentItemKey = null;
    playback = null;
    queueHasMore = false;
    if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  }

  // ---- up next and the gap between queue items ----
  // Plex play queues say what's coming (the proxy's upNext). They also answer the one question
  // the session data can't: when a session vanishes, is another item coming? A slow-loading
  // next item leaves no session for up to ~4.2 s, exactly like a real Stop (measured
  // 2026-09-26), so with more queued the screen is held longer, in a "loading" look.
  let queueHasMore = false;
  let upNextShown = null;    // JSON of the items currently drawn, to redraw only on change

  // One line of text that ping-pongs when it's wider than its line (song and artist in the
  // music layout): pause, slide to the end, pause, slide back. Measured once the web fonts
  // have loaded, because Montserrat is wider than the fallback it replaces. Web Animations
  // rather than CSS keyframes, so the pauses stay a fixed length whatever the title length.
  const SCROLL_PX_PER_SECOND = 70;
  const SCROLL_PAUSE_MS = 2000;

  function setScrollingText(el, text) {
    if (!el) return;
    el.classList.remove('scrolling');
    el.textContent = '';
    const span = document.createElement('span');
    span.className = 'now-showing-scroll';
    span.textContent = text;
    el.appendChild(span);
    const measure = () => {
      if (span.parentNode !== el) return; // replaced by a newer title meanwhile
      span.getAnimations().forEach(a => a.cancel());
      const overflow = span.scrollWidth - el.clientWidth;
      if (overflow <= 2) { el.classList.remove('scrolling'); return; }
      const slide = Math.max(1500, overflow / SCROLL_PX_PER_SECOND * 1000);
      const total = 2 * (SCROLL_PAUSE_MS + slide);
      const end = `translateX(${-overflow}px)`;
      span.animate([
        { transform: 'translateX(0)', offset: 0 },
        { transform: 'translateX(0)', offset: SCROLL_PAUSE_MS / total, easing: 'ease-in-out' },
        { transform: end, offset: (SCROLL_PAUSE_MS + slide) / total },
        { transform: end, offset: (2 * SCROLL_PAUSE_MS + slide) / total, easing: 'ease-in-out' },
        { transform: 'translateX(0)', offset: 1 }
      ], { duration: total, iterations: Infinity });
      el.classList.add('scrolling');
    };
    requestAnimationFrame(measure);
    // Explicitly load this line's own font and measure again when it's there.
    // `document.fonts.ready` alone can already be settled here, because the web font only
    // starts loading once music text is shown (Astra pass 1 #1).
    if (document.fonts && document.fonts.load) {
      const cs = getComputedStyle(el);
      document.fonts.load(`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`, text)
        .then(() => requestAnimationFrame(measure), () => {});
    }
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function renderUpNext(items) {
    const el = document.getElementById('nowShowingUpNext');
    const list = (items || []).slice(0, 3);
    const json = JSON.stringify(list);
    if (!el || json === upNextShown) return;
    upNextShown = json;
    el.innerHTML = !list.length ? '' :
      `<div class="now-showing-upnext-label">Up next</div><div class="now-showing-upnext-row">` +
      list.map(item =>
        `<div class="now-showing-upnext-item">` +
          (item.poster ? `<img src="${escapeHtml(prox(item.poster))}" alt="" />` : '<div class="now-showing-upnext-blank"></div>') +
          `<div class="now-showing-upnext-song">${escapeHtml(item.trackTitle || item.title)}</div>` +
          `<div class="now-showing-upnext-artist">${escapeHtml(item.artist)}</div>` +
        `</div>`).join('') +
      `</div>`;
  }

  // The session vanished: stop the bar where it is and, if more is queued, show that the next
  // item is on its way.
  function holdForNextItem(showLoading) {
    if (playback && playback.state !== 'stopped') {
      playback.offset = positionMs();
      playback.at = Date.now();
      playback.state = 'stopped';
      renderProgress();
    }
    const nowShowing = document.getElementById('nowShowing');
    if (nowShowing && showLoading && !nowShowing.classList.contains('loading')) {
      nowShowing.classList.add('loading');
      placePauseBadge();
    }
  }

  // ---- progress bar and pause state ----
  // Plex only learns the position from the player's ~10 s reports, while play/pause/seek/stop
  // arrive within ~1 s (measured 2026-09-26). So the bar runs locally from the last report
  // (viewOffset observed at offsetAt) and only moves while the state is "playing".
  let playback = null;       // { key, offset, at, duration, state, progress }
  let progressTimer = null;

  function applyPlayback(data, newItem) {
    const offset = Number(data.viewOffset) || 0;
    const state = data.state || 'playing';
    const key = nowPlayingKey(data);
    // Compare with the last offset Plex *reported*, not the anchor: after a freeze the anchor is
    // the on-screen position, and a repeat of the same report must not look like a new one.
    const sameOffset = !newItem && playback && playback.key === key && playback.reported === offset;
    let anchorOffset = offset;
    let anchorAt = Number(data.offsetAt) || Date.now();
    if (sameOffset && playback.state === state) {
      // A repeated report keeps the existing anchor so the bar never snaps back to a stale
      // value (the proxy's monitor already does this; this covers its direct, uncached path).
      anchorOffset = playback.offset;
      anchorAt = playback.at;
    } else if (sameOffset) {
      // The state changed (pause, buffering, resume) but Plex has not sent a new position:
      // continue from what is on screen rather than jumping back to the stale offset.
      anchorOffset = positionMs();
      anchorAt = Date.now();
    }
    playback = {
      key, state,
      offset: anchorOffset,
      reported: offset,
      at: anchorAt,
      duration: Number(data.duration) || 0,
      progress: Number(data.progress) || 0
    };
    queueHasMore = Array.isArray(data.upNext) && data.upNext.length > 0;
    renderUpNext(data.upNext);
    const nowShowing = document.getElementById('nowShowing');
    if (nowShowing) {
      nowShowing.classList.remove('loading');
      nowShowing.classList.toggle('paused', state === 'paused');
      if (state === 'paused') placePauseBadge();
    }
    renderProgress();
    if (!progressTimer) progressTimer = setInterval(renderProgress, PROGRESS_TICK_MS);
  }

  // Where playback is now, in ms: the anchor, advanced by wall time only while playing.
  function positionMs() {
    if (!playback) return 0;
    let position = playback.offset;
    if (playback.state === 'playing') position += Date.now() - playback.at;
    return playback.duration ? Math.min(playback.duration, position) : position;
  }

  function progressPercent() {
    if (!playback) return 0;
    if (!playback.duration) return playback.progress;
    return Math.min(100, Math.max(0, positionMs() / playback.duration * 100));
  }

  function renderProgress() {
    const bar = document.getElementById('nowShowingProgressBar');
    if (bar && playback) bar.style.width = `${progressPercent()}%`;
  }

  // Centre the pause badge on the art, wherever the current layout put it.
  function placePauseBadge() {
    const badge = document.getElementById('nowShowingPauseBadge');
    const poster = document.getElementById('nowShowingPoster');
    if (!badge || !poster) return;
    requestAnimationFrame(() => {
      const r = poster.getBoundingClientRect();
      badge.style.left = `${r.left + r.width / 2}px`;
      badge.style.top = `${r.top + r.height / 2}px`;
    });
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
    if (nowPlayingTimer) {
      clearTimeout(nowPlayingTimer);
      nowPlayingTimer = null;
    }
    let missingSince = null;

    // One poll at a time: the next is scheduled only after this one finishes.
    async function tick() {
      const nowPlayingData = await checkNowPlaying(cfg);

      if (nowPlayingData.playing) {
        missingSince = null;
        if (currentMode !== 'nowplaying' || nowPlayingKey(nowPlayingData) !== currentItemKey) {
          // Playback started, or something else started without a stop in between
          // (the next track in a playlist)
          showNowPlaying(nowPlayingData, cfg);
        } else {
          applyPlayback(nowPlayingData); // same item: pause/resume, seek, fresh position
        }
      } else if (currentMode === 'nowplaying') {
        // A stop, or the gap before the queue's next item. Only give up the screen once nothing
        // has been playing for the grace period.
        if (missingSince === null) missingSince = Date.now();
        const gone = Date.now() - missingSince;
        holdForNextItem(queueHasMore && gone >= LOADING_LOOK_AFTER_MS);
        if (gone >= (queueHasMore ? QUEUE_GRACE_MS : STOP_GRACE_MS)) {
          missingSince = null;
          showRotation();
        }
      }
      nowPlayingTimer = setTimeout(tick, POLL_MS);
    }

    nowPlayingTimer = setTimeout(tick, POLL_MS);
  }

  // ---- boot ----
  (async function init(){
    try{
      const cfg = await loadCfg();
      applyFontSettings(cfg);

      if (previewMode === 'nowplaying' || previewMode === 'musicvideo') {
        let previewItems = [];
        try {
          previewItems = await fetchItems(cfg);
        } catch {
          previewItems = [];
        }
        showNowPlaying(makePreviewNowPlaying(previewItems, previewMode === 'musicvideo'), cfg);
        // ?state=loading previews the hold between queue items
        if (new URLSearchParams(location.search).get('state') === 'loading') holdForNextItem(true);
        return;
      }

      const items = await fetchItems(cfg);
      startRotation(cfg, items);

      if (previewMode !== 'rotation' && cfg.plexDevices && cfg.plexDevices.length > 0) {
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
