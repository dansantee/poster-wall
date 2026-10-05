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
  // Where the settings page is, as a phone on the LAN can reach it: the hostname with .local
  // (mDNS; a bare "poster-wall" doesn't resolve on most networks) and the IP address.
  function settingsUrls(cfg) {
    const port = location.port || '8088';
    const name = cfg && cfg.hostname && cfg.hostname !== '<hostname>' ? String(cfg.hostname) : 'poster-wall';
    const host = name.includes('.') ? name : `${name}.local`;
    return [host, cfg && cfg.ip].filter(Boolean).map(h => `http://${h}:${port}/settings.html`);
  }

  // First run: Plex isn't set up yet. Say where to finish setup, and reload once it's saved, so
  // nobody has to restart the kiosk (Dan, 2026-10-02: make setup simple). `watch` is false for
  // ?preview=setup on a configured wall, which would otherwise reload itself forever.
  const SETUP_POLL_MS = 5000;
  function showSetup(cfg, watch) {
    const [first, ...rest] = settingsUrls(cfg);
    document.body.innerHTML = `
      <div class="setup-screen">
        <div class="setup-title">Poster Wall</div>
        <div class="setup-lead">To finish setting up, open this on a phone or computer on the same network:</div>
        <div class="setup-url">${escapeHtml(first)}</div>
        ${rest.map(u => `<div class="setup-or">or</div><div class="setup-url setup-url-alt">${escapeHtml(u)}</div>`).join('')}
        <div class="setup-steps">Sign in with Plex, choose your libraries, and press Save.<br>This screen changes by itself.</div>
      </div>`;
    if (!watch) return;
    const timer = setInterval(async () => {
      try {
        const r = await fetch(`${proxyBase()}/api/config`, { cache: 'no-store' });
        if (r.ok && (await r.json()).configured) { clearInterval(timer); location.reload(); }
      } catch { /* the proxy restarting; try again */ }
    }, SETUP_POLL_MS);
  }

  // Saved settings apply by themselves: every CONFIG_WATCH_MS the kiosk compares the stored
  // config with what it loaded and reloads when it changed, but only while the poster rotation
  // is up, never in the middle of Now Playing. Without it, a TV added in the settings after the
  // first Save (the README's order) was never watched (Astra pass 3).
  const CONFIG_WATCH_MS = 15000;
  const COMPUTED_CONFIG_KEYS = ['hostname', 'ip', 'configured'];

  // A config as text, without the keys the proxy computes on every GET. loadCfg() takes the
  // baseline from the very response the page runs on, so a save landing while the page starts
  // up can't slip into the baseline unseen (Astra pass 4).
  function configSignatureOf(j) {
    const stored = { ...j };
    for (const k of COMPUTED_CONFIG_KEYS) delete stored[k];
    return JSON.stringify(Object.fromEntries(Object.keys(stored).sort().map(k => [k, stored[k]])));
  }

  async function configSignature() {
    try {
      const r = await fetch(`${proxyBase()}/api/config`, { cache: 'no-store' });
      return r.ok ? configSignatureOf(await r.json()) : null;
    } catch {
      return null;   // the proxy restarting: no verdict this time; the next tick asks again
    }
  }

  // Whether to reload now: the config changed, and the wall is showing posters. The mode is
  // checked again after the fetch: playback may have started while it was out (Astra pass 4).
  async function configChangedWhileIdle(loaded) {
    if (currentMode !== 'rotation') return false;
    const now = await configSignature();
    return now !== null && now !== loaded && currentMode === 'rotation';
  }

  function watchConfig(loaded) {
    setInterval(async () => {
      if (await configChangedWhileIdle(loaded)) location.reload();
    }, CONFIG_WATCH_MS);
  }

  function showError(message, cfg) {
    const settingsUrl = settingsUrls(cfg).join(' or ');
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
      signature:     configSignatureOf(j),   // for watchConfig: what this page was loaded with
      ip:            j.ip            ?? '',
      configured:    j.configured    !== false,   // false only when the proxy says Plex isn't set up
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
      posterColorMarquee:!!j.posterColorMarquee,
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

  // ---- metadata badges ----
  // The resolution, audio and rating badges under the poster are drawn from the now-playing
  // data (no image files): a coloured band holding either a lead label and a black box
  // ("tag": video in gold, audio in silver) or a rating, a divider and its meaning. Each badge
  // is { kind, color, lead, mark, text, title, sub, label }; badgeHtml() renders it.
  // Marks, drawn in currentColor: the Dolby double-D (Simple Icons, CC0) and a speaker.
  const MARKS = {
    dolby: '<svg viewBox="0 3.564 24 16.872" aria-hidden="true"><path d="M0 3.564v16.872h2.488c4.648 0 8.438-3.788 8.438-8.436s-3.79-8.436-8.438-8.436H0zm21.512 0c-4.648 0-8.438 3.788-8.438 8.436s3.79 8.436 8.438 8.436H24V3.564h-2.488z"/></svg>',
    speaker: '<svg viewBox="1 2 21 20" aria-hidden="true"><path d="M2 8.5h4.5L12 3.5v17l-5.5-5H2z"/><path d="M15 8.2a5 5 0 0 1 0 7.6M17.6 5.6a8.6 8.6 0 0 1 0 12.8M20.2 3a12.2 12.2 0 0 1 0 18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>'
  };

  const RATINGS = {
    'G': ['green', 'GENERAL AUDIENCES', 'ALL AGES ADMITTED'],
    'PG': ['orange', 'PARENTAL GUIDANCE SUGGESTED', 'SOME MATERIAL MAY NOT BE SUITABLE FOR CHILDREN'],
    'PG-13': ['orange', 'PARENTS STRONGLY CAUTIONED', 'SOME MATERIAL MAY BE INAPPROPRIATE FOR CHILDREN UNDER 13'],
    'R': ['red', 'RESTRICTED', 'UNDER 17 REQUIRES ACCOMPANYING PARENT OR ADULT GUARDIAN'],
    'NC-17': ['red', 'NO ONE 17 AND UNDER ADMITTED', ''],
    'TV-Y': ['green', 'ALL CHILDREN', 'APPROPRIATE FOR ALL CHILDREN'],
    'TV-Y7': ['green', 'DIRECTED TO OLDER CHILDREN', 'MOST APPROPRIATE FOR CHILDREN AGE 7 AND UP'],
    'TV-Y7-FV': ['green', 'DIRECTED TO OLDER CHILDREN', 'FANTASY VIOLENCE'],
    'TV-G': ['green', 'GENERAL AUDIENCE', 'SUITABLE FOR ALL AGES'],
    'TV-PG': ['orange', 'PARENTAL GUIDANCE SUGGESTED', 'MAY BE UNSUITABLE FOR YOUNGER CHILDREN'],
    'TV-14': ['orange', 'PARENTS STRONGLY CAUTIONED', 'MAY BE UNSUITABLE FOR CHILDREN UNDER 14'],
    'TV-MA': ['red', 'MATURE AUDIENCES ONLY', 'MAY BE UNSUITABLE FOR CHILDREN UNDER 17'],
    'NR': ['gray', 'NOT RATED', ''],
    'PASSED': ['gray', 'APPROVED', 'UNDER THE PRODUCTION CODE'],
    'XXX': ['red', 'DAM! YOU A FREAK! ;)', '']
  };
  const RATING_ALIASES = { 'NOT RATED': 'NR', 'UNRATED': 'NR', 'TV-Y7 FV': 'TV-Y7-FV' };

  function ratingBadge(rating) {
    // Plex prefixes some non-US ratings with a country ("gb/15")
    const raw = String(rating || '').trim().replace(/^[a-z]{2}\//i, '');
    const key = RATING_ALIASES[raw.toUpperCase()] || raw.toUpperCase();
    if (!raw || key === 'N/A') {
      return { kind: 'rating', color: 'gray', lead: 'N/A', title: 'NOT RATED', sub: 'RATING IS NOT SET', label: 'Not rated' };
    }
    const [color, title, sub] = RATINGS[key] || ['gray', 'RATED ' + raw.toUpperCase(), ''];
    return { kind: 'rating', color, lead: RATINGS[key] ? key : raw.toUpperCase(), title, sub, label: 'Rated ' + raw };
  }

  function videoBadge(resolution, dynamicRange) {
    const res = String(resolution || '').toLowerCase().replace(/p$/, '');
    const hdr = String(dynamicRange || '');
    if (!res || res === 'n/a') return null;
    const name = { sd: 'SD', '480': '480', '576': '576', '720': '720', '1080': '1080', '2k': '2K', '4k': '4K', '8k': '8K' }[res];
    if (!name) return null;
    const label = (name + ' ' + hdr).trim();
    // HDR leads with the resolution and names the format in the box; Dolby Vision gets the mark
    if (hdr === 'Dolby Vision') return { kind: 'tag', color: 'gold', lead: name, mark: 'dolby', text: 'VISION', label };
    if (hdr) return { kind: 'tag', color: 'gold', lead: name, text: hdr === 'HDR10' ? 'HDR' : hdr, label };
    const grade = ['4K', '8K'].includes(name) ? 'UHD' : ['720', '1080', '2K'].includes(name) ? 'HD' : 'SD';
    return { kind: 'tag', color: 'gold', lead: grade, text: name === 'SD' ? 'STANDARD' : name, label };
  }

  function audioBadge(codec, channels, profile) {
    const c = String(codec || '').toLowerCase();
    const p = String(profile || '').toLowerCase();
    const layout = String(channels || '');
    if (!c && !layout) return null;
    const label = [codec, layout].filter(Boolean).join(' ');
    const dolby = text => ({ kind: 'tag', color: 'silver', mark: 'dolby', text, label });
    const speaker = text => ({ kind: 'tag', color: 'silver', mark: 'speaker', text, label });
    if (p.includes('atmos')) return dolby('ATMOS');
    if (c === 'truehd') return dolby(('TRUEHD ' + layout).trim());
    if (c === 'eac3') return dolby(('DIGITAL+ ' + layout).trim());
    if (c === 'ac3') return dolby(('DIGITAL ' + layout).trim());
    if (c === 'dca' || c === 'dts') {
      const name = p.includes('dts:x') ? 'DTS:X' : p.includes('ma') ? 'DTS-HD MA' : p.includes('hra') ? 'DTS-HD' : 'DTS';
      return speaker((name + ' ' + layout).trim());
    }
    const named = { '1.0': 'MONO', '2.0': '2.0 STEREO' }[layout];
    if (named) return speaker(named);
    if (layout) return speaker(layout + ' SURROUND');
    return speaker(c.toUpperCase());
  }

  function badgeHtml(b) {
    // --chars lets the CSS shrink a long label to fit its box
    const sized = (cls, text) => `<span class="${cls}" style="--chars: ${Math.max(String(text).length, 1)}">${escapeHtml(text)}</span>`;
    const lead = b.lead ? sized('badge-lead', b.lead) : `<span class="badge-lead badge-mark-${b.mark}">${MARKS[b.mark]}</span>`;
    const body = b.kind === 'rating'
      ? `<span class="badge-divider"></span><span class="badge-words">${sized('badge-title', b.title)}` +
        (b.sub ? sized('badge-sub', b.sub) : '') + '</span>'
      : `<span class="badge-box">${b.lead && b.mark ? `<span class="badge-box-mark">${MARKS[b.mark]}</span>` : ''}` +
        `${sized('badge-text', b.text)}</span>`;
    return `<div class="now-showing-metadata-icon badge badge-${b.kind} badge-${b.color}" role="img" ` +
      `aria-label="${escapeHtml(b.label)}"><div class="badge-face">${lead}${body}</div></div>`;
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
      titleEl.style.setProperty('--now-showing-kerning', `${kerning}em`); // re-centres it (styles.css)
      
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
  // A cover whose darkest side is darker than this (HSL lightness, 0-1; ~30/255) gets a
  // backdrop at least BACKDROP_EDGE_GAP lighter than that side. Brighter edges keep the plain darkened
  // average: without this limit, a red or tan cover's backdrop went pale (caught on a render).
  const BACKDROP_DARK_EDGE = 0.12;
  const BACKDROP_EDGE_GAP = 0.18;

  // The backdrop colour for a 16x16 RGBA sample (a pure function, so the tests can run it).
  function backdropColor(data) {
    const n = data.length / 4, darken = 0.55;
    let r = 0, g = 0, b = 0;
    for (let i = 0; i < data.length; i += 4) { r += data[i]; g += data[i+1]; b += data[i+2]; }
    // Keep the backdrop a little lighter than the art's darkest side, so a cover with a black
    // border or black design (Billie Jean, Sober) still reads as a full square instead of
    // melting into a near-black backdrop (Dan, 2026-09-29). The darkest SIDE, not the whole
    // ring: Levitating's top and right are black but its left and bottom are bright, and a
    // ring average hid it. The backdrop keeps its own hue and saturation.
    const lightAt = (x, y) => { const i = (y * 16 + x) * 4; return rgbToHsl(data[i], data[i+1], data[i+2]).l; };
    const sides = [0, 0, 0, 0];               // top, bottom, left, right
    for (let k = 0; k < 16; k++) {
      sides[0] += lightAt(k, 0) / 16;
      sides[1] += lightAt(k, 15) / 16;
      sides[2] += lightAt(0, k) / 16;
      sides[3] += lightAt(15, k) / 16;
    }
    const edgeL = Math.min(...sides);
    const back = rgbToHsl(r / n * darken, g / n * darken, b / n * darken);
    if (edgeL < BACKDROP_DARK_EDGE && back.l < edgeL + BACKDROP_EDGE_GAP) {
      // Lightness rounds UP (to 0.1%), so the gap is never under BACKDROP_EDGE_GAP (Astra pass 1 #1)
      const l = Math.min(100, Math.ceil((edgeL + BACKDROP_EDGE_GAP) * 1000) / 10);
      return `hsl(${back.h.toFixed(2)}, ${(back.s * 100).toFixed(2)}%, ${l}%)`;
    }
    return `rgb(${Math.round(r / n * darken)}, ${Math.round(g / n * darken)}, ${Math.round(b / n * darken)})`;
  }

  // The accent from scored pixels ({ rgb, score }): the most vivid pixels' colour, lifted to a
  // bright saturated tone, and its hue. Also the most vivid colour at least ACCENT_SECOND_GAP
  // degrees of hue away (second, secondHue), for posters whose main colour is the marquee's own.
  const ACCENT_SECOND_GAP = 40;
  const ACCENT_SECOND_MIN_SCORE = 0.3;
  // The vividness a poster's best pixels need for an accent: the music screen keeps white for
  // near-greyscale art; movies and TV also colour a muted poster (Dan, 2026-10-04: Lanterns'
  // olive-green poster scored 0.149 and kept the default yellow). Black-and-white art scores
  // well under either.
  const ACCENT_MIN_SCORE = 0.15;
  const MOVIE_ACCENT_MIN_SCORE = 0.08;
  // A muted poster (admitted only by the movie cutoff) keeps a dusty version of its colour: its
  // saturation held between these, instead of lifted to 55% (Dan, 2026-10-04: Lanterns' olive
  // came out pea green at 55%; he picked sage, hsl(78, 28%, 66%), from renders)
  const MUTED_ACCENT_SAT = [0.28, 0.35];
  function hueGap(a, b) {
    const d = Math.abs(a - b) % 360;
    return Math.min(d, 360 - d);
  }

  function artAccents(pixels, minScore = ACCENT_MIN_SCORE) {
    const lift = (rgbs, minS = 0.55, l = 66, maxS = 1) => {
      const avg = [0, 1, 2].map(k => rgbs.reduce((sum, rgb) => sum + rgb[k], 0) / rgbs.length);
      const hsl = rgbToHsl(avg[0], avg[1], avg[2]);
      const s = Math.min(maxS, Math.max(hsl.s, minS));
      return { color: `hsl(${Math.round(hsl.h)}, ${Math.round(s * 100)}%, ${l}%)`, hue: hsl.h };
    };
    const top = Math.max(...pixels.map(p => p.score));
    if (!(top >= minScore)) return { accent: null, accentHue: null, second: null, secondHue: null };
    const muted = top < ACCENT_MIN_SCORE;
    const vivid = pixels.filter(p => p.score >= top * 0.6).map(p => p.rgb);
    const first = muted ? lift(vivid, MUTED_ACCENT_SAT[0], 66, MUTED_ACCENT_SAT[1]) : lift(vivid);
    const others = pixels
      .map(p => ({ ...p, hue: rgbToHsl(...p.rgb).h }))
      .filter(p => p.score >= ACCENT_SECOND_MIN_SCORE && hueGap(p.hue, first.hue) >= ACCENT_SECOND_GAP);
    let second = null;
    if (others.length) {
      const best = others.reduce((a, b) => (b.score > a.score ? b : a));
      // A minority colour is often a dark one (Doom Patrol's reds average to a dusty pink at
      // the first colour's 55% floor), so the second gets a stronger saturation floor
      second = lift(others.filter(p => p.score >= best.score * 0.6 && hueGap(p.hue, best.hue) < 20).map(p => p.rgb), 0.8, 62);
    }
    return { accent: first.color, accentHue: first.hue, second: second && second.color, secondHue: second && second.hue };
  }

  // Movies and TV: the poster's accent, unless it would look like the configured marquee
  // colour anyway (Dan, 2026-10-01: Doom Patrol's yellow Season 3 poster came out the default
  // pale yellow); then its second colour (the red there), if it has one.
  const ACCENT_LIKE_CONFIGURED = 25;
  function movieAccent(colors, configuredHex) {
    if (!colors || !colors.accent) return null;
    const rgb = hexToRgb(configuredHex);
    const conf = rgb && rgbToHsl(rgb.r, rgb.g, rgb.b);
    if (conf && conf.s >= 0.2 && colors.second && hueGap(colors.accentHue, conf.h) < ACCENT_LIKE_CONFIGURED) {
      return colors.second;
    }
    return colors.accent;
  }

  function computeArtColors(src, minScore = ACCENT_MIN_SCORE) {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const c = document.createElement('canvas');
        c.width = 16; c.height = 16;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, 16, 16);
        const { data } = ctx.getImageData(0, 0, 16, 16);
        const pixels = [];
        for (let i = 0; i < data.length; i += 4) {
          const hsl = rgbToHsl(data[i], data[i+1], data[i+2]);
          // Vivid = saturated and neither near-black nor near-white
          pixels.push({ rgb: [data[i], data[i+1], data[i+2]], score: hsl.s * (1 - Math.abs(hsl.l - 0.5) * 2) });
        }
        const backdrop = backdropColor(data);
        resolve({ backdrop, ...artAccents(pixels, minScore) });
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
      videoDynamicRange: 'Dolby Vision',
      audioCodec: 'TRUEHD',
      audioChannels: '7.1',
      audioProfile: 'dolby truehd + dolby atmos'
    };
    if (musicVideo) {
      preview.mediaType = 'musicvideo';
      preview.artist = 'Weezer';
      preview.trackTitle = 'Buddy Holly';
      preview.ratingKey = 'preview-0';
      preview.upNext = (items || []).slice(1, 4).map((it, i) => ({
        ratingKey: `preview-${i + 1}`,
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
  const AFTER_CHANGE_POLL_MS = 250; // the first poll after a song change, for up next's third item
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
      poster.onload = () => { placePauseBadge(); fitPoster(); };
      poster.src = prox(data.poster);
    }
    setScrollingText(artistEl, isMusicVideo ? (data.artist || '') : '');
    setScrollingText(songEl, isMusicVideo ? (data.displayTitle || data.trackTitle || data.title || '') : '');
    // Movies and TV: with posterColorMarquee on, the marquee and the bar take the poster's
    // colour (Dan, 2026-09-30). Off by default: a mixed poster made the pick look random, so
    // Dan standardized on the configured colour (2026-10-04). The old colour stays until the
    // new one is known; a poster with none (black and white, or one that fails to load) goes
    // back to the configured colours.
    if (!isMusicVideo && data.poster && cfg.posterColorMarquee) {
      const colorsFor = nowPlayingKey(data);
      computeArtColors(prox(data.poster), MOVIE_ACCENT_MIN_SCORE).then(colors => {
        if (currentItemKey !== colorsFor) return;
        const accent = movieAccent(colors, cfg.nowShowingColor);
        if (accent) nowShowing.style.setProperty('--movie-accent', accent);
        else nowShowing.style.removeProperty('--movie-accent');
      });
    } else {
      nowShowing.style.removeProperty('--movie-accent');
    }
    const whatEl = document.getElementById('nowShowingWhat');
    if (whatEl) whatEl.textContent = isMusicVideo ? '' : detailsText(data);
    fitPoster(); // the stack above may have changed height
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

      // Resolution and audio when Plex reports them; the rating always (N/A when unset)
      const badges = [
        videoBadge(data.videoResolution, data.videoDynamicRange),
        audioBadge(data.audioCodec, data.audioChannels, data.audioProfile),
        ratingBadge(data.rating)
      ].filter(Boolean);
      iconsContainer.innerHTML = badges.map(badgeHtml).join('');
    }

    // Show now playing screen
    nowShowing.classList.add('visible');
    currentMode = 'nowplaying';
    applyPlayback(data, true);
  }

  function showRotation() {
    const stage = document.getElementById('stage');
    const nowShowing = document.getElementById('nowShowing');

    if (nowShowing) {
      nowShowing.classList.remove('visible', 'music', 'paused', 'loading');
      nowShowing.style.removeProperty('--movie-accent');
    }
    if (stage) stage.style.display = 'block';
    currentMode = 'rotation';
    currentItemKey = null;
    playback = null;
    queueHasMore = false;
    resetPopups([]);
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

  function upNextTileHtml(item) {
    return `<div class="now-showing-upnext-item">` +
      (item.poster ? `<img src="${escapeHtml(prox(item.poster))}" alt="" />` : '<div class="now-showing-upnext-blank"></div>') +
      // Artist first: the artists line up under the covers, and a one-line song's spare
      // (reserved) line falls at the bottom of the tile, where it reads as margin.
      `<div class="now-showing-upnext-artist">${escapeHtml(item.artist)}</div>` +
      // shortTitle (from the proxy) drops "(...)" and "ft." extras; the full title stays
      // for when the song is actually playing.
      `<div class="now-showing-upnext-song">${escapeHtml(item.shortTitle || item.trackTitle || item.title)}</div>` +
    `</div>`;
  }

  function renderUpNext(items) {
    const el = document.getElementById('nowShowingUpNext');
    const list = (items || []).slice(0, 3);
    const json = JSON.stringify(list);
    if (!el || json === upNextShown) return;
    upNextShown = json;
    const before = lastUpNext.map(item => String(item.ratingKey || ''));
    lastUpNext = list;
    el.innerHTML = !list.length ? '' :
      `<div class="now-showing-upnext-label">Up next</div><div class="now-showing-upnext-row">` +
      list.map(upNextTileHtml).join('') +
      `</div>`;
    // A song change waits up to THIRD_WAIT_MS for the queue's new third item; when it comes
    // later still, the row is the two carried over, and tiles that extend the row already on
    // screen slide in from the right at the advance's pace.
    const grows = before.length > 0 && before.length < list.length &&
                  before.every((key, i) => key && key === String(list[i].ratingKey || ''));
    if (grows) {
      const tiles = [...el.querySelectorAll('.now-showing-upnext-item')];
      const step = tiles[1].getBoundingClientRect().left - tiles[0].getBoundingClientRect().left;
      tiles.slice(before.length).forEach(tile =>
        tile.animate([{ transform: `translateX(${step}px)` }, { transform: 'none' }],
                     { duration: TRACK_ANIM_MS * 0.75, easing: TRACK_EASE }));
    }
  }

  // ---- song-change transition (music layout) ----
  // When the song that starts is the first "Up next" item, that cover flies up into the main
  // art slot while the old art fades; the other two tiles slide left one slot and the new
  // third slides in from the right with them (the row waits briefly for the queue's answer if
  // it's pending); the song/artist text rises in a beat later. Any other change in the
  // music layout crossfades. All transforms/opacity (Web Animations), so the Pi's GPU runs it.
  const TRACK_ANIM_MS = 800;
  const THIRD_WAIT_MS = 1000;  // longest the row waits for a pending third up-next item
  const THIRD_POLL_MS = 150;
  const TRACK_DECODE_WAIT_MS = 500;
  const DEMO_ADVANCE_MS = 5000; // ?preview=musicvideo&demo=advance
  const TRACK_EASE = 'cubic-bezier(0.2, 0.7, 0.2, 1)';
  let lastUpNext = [];        // the up-next list on screen
  let trackAnimating = false; // the poll loop waits while a transition runs

  function preloadImage(src, timeoutMs) {
    return new Promise(resolve => {
      const img = new Image();
      let done = false;
      const finish = ok => { if (!done) { done = true; resolve(ok); } };
      img.onload = () => finish(true);
      img.onerror = () => finish(false);
      setTimeout(() => finish(false), timeoutMs);
      img.src = src;
    });
  }

  // askAgain() fetches the now-playing body again (the proxy, or the preview demo's fake queue);
  // an advance uses it to wait briefly for a pending third up-next item.
  function changeTrack(data, cfg, askAgain) {
    const nowShowing = document.getElementById('nowShowing');
    const wasMusic = nowShowing && nowShowing.classList.contains('visible') && nowShowing.classList.contains('music');
    if (data.mediaType !== 'musicvideo' || !wasMusic || !data.poster) {
      showNowPlaying(data, cfg);
      return Promise.resolve();
    }
    const advancing = lastUpNext.length > 0 && lastUpNext[0].ratingKey &&
                      String(lastUpNext[0].ratingKey) === String(data.ratingKey);
    popOutPopup();   // the old song's bubble goes as its art does
    trackAnimating = true;
    return (advancing ? animateAdvance(data, cfg, askAgain) : animateCrossfade(data, cfg))
      .catch(err => { console.warn('Track transition failed:', err); showNowPlaying(data, cfg); })
      .finally(() => { trackAnimating = false; });
  }

  // Swap in the new item's content, then bring its song/artist text up into place.
  async function settleTrack(data, cfg, anims, fadeThirdTile = false) {
    // A crossfade's row is all new content: nothing on it "extends" the old row, so
    // renderUpNext's slide-in must not run on top of the fade below (Astra).
    if (fadeThirdTile) lastUpNext = [];
    showNowPlaying(data, cfg);
    if (fadeThirdTile) {
      // Crossfades bring the third tile in with the new content (opacity only). Started as the
      // row is drawn, before the decode wait, or it would show and then blink out (Astra pass 2).
      const third = document.querySelectorAll('.now-showing-upnext-row > .now-showing-upnext-item')[2];
      if (third) third.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 500, easing: 'ease-out' });
    }
    const poster = document.getElementById('nowShowingPoster');
    // Bounded: a stalled image must not hold the poll loop (it waits on trackAnimating).
    if (poster && poster.decode) {
      await Promise.race([poster.decode().catch(() => {}), new Promise(r => setTimeout(r, TRACK_DECODE_WAIT_MS))]);
    }
    anims.forEach(a => a.cancel());
    const track = document.querySelector('.now-showing-track');
    if (track) {
      track.animate([{ opacity: 0, transform: 'translateY(1.5vh)' }, { opacity: 1, transform: 'none' }],
                    { duration: 450, easing: TRACK_EASE });
    }
  }

  // The proxy publishes a new song before its queue lookup answers (upNextPending, usually
  // ~0.25 s). Ask again every THIRD_POLL_MS for up to THIRD_WAIT_MS; resolves with the song's
  // own upNext, or null (gave up, or the song changed or stopped meanwhile).
  async function waitForUpNext(data, askAgain) {
    const until = Date.now() + THIRD_WAIT_MS;
    while (Date.now() < until) {
      await new Promise(r => setTimeout(r, THIRD_POLL_MS));
      const fresh = await Promise.race([askAgain().catch(() => null),
        new Promise(r => setTimeout(() => r(null), Math.max(0, until - Date.now())))]);
      if (!fresh || !fresh.playing || String(fresh.ratingKey) !== String(data.ratingKey)) return null;
      if (!fresh.upNextPending || (fresh.upNext || []).length >= 3) return fresh.upNext || [];
    }
    return null;
  }

  async function animateAdvance(data, cfg, askAgain) {
    const nowShowing = document.getElementById('nowShowing');
    const poster = document.getElementById('nowShowingPoster');
    const tiles = [...document.querySelectorAll('.now-showing-upnext-row > .now-showing-upnext-item')];
    const firstImg = tiles[0] && tiles[0].querySelector('img');
    if (!poster || !firstImg) return animateCrossfade(data, cfg);

    const newSrc = prox(data.poster);
    const known = tiles.length === 3 ? (data.upNext || [])[2] : null;
    await Promise.all([
      preloadImage(newSrc, 500),   // fly the full-resolution cover, so it's sharp when it lands
      known && known.poster ? preloadImage(prox(known.poster), 500) : null
    ]);
    nowShowing.classList.remove('loading', 'paused');
    const from = firstImg.getBoundingClientRect();
    const to = poster.getBoundingClientRect();

    const fly = document.createElement('img');
    fly.className = 'now-showing-fly';
    fly.src = newSrc;
    Object.assign(fly.style, { left: `${to.left}px`, top: `${to.top}px`, width: `${to.width}px`, height: `${to.height}px` });
    const anims = [];
    try {
      nowShowing.appendChild(fly);
      tiles[0].style.visibility = 'hidden';   // its cover is the one in flight
      anims.push(fly.animate([
        { transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})` },
        { transform: 'none' }
      ], { duration: TRACK_ANIM_MS, easing: TRACK_EASE, fill: 'forwards' }));
      anims.push(poster.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(0.94)' }],
                                { duration: TRACK_ANIM_MS * 0.55, easing: 'ease-in', fill: 'forwards' }));
      const track = document.querySelector('.now-showing-track');
      if (track) anims.push(track.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' }));

      // The top is right at once; the row may wait (with the first slot empty) until the
      // queue's third item is known, so all three slide together (Dan, 2026-09-26).
      if (!known && tiles.length === 3 && data.upNextPending && askAgain) {
        const fresh = await waitForUpNext(data, askAgain);
        if (fresh) data = { ...data, upNext: fresh, upNextPending: false };
      }
      const incoming = tiles.length === 3 ? (data.upNext || [])[2] : null;
      if (incoming && incoming !== known && incoming.poster) await preloadImage(prox(incoming.poster), 300);

      // A conveyor: the two remaining tiles move one slot left and the new third slides in
      // from the right edge, in step (same distance, duration and easing).
      const step = tiles[1] ? tiles[1].getBoundingClientRect().left - tiles[0].getBoundingClientRect().left : 0;
      const slide = { duration: TRACK_ANIM_MS * 0.75, delay: 60, easing: TRACK_EASE };
      tiles.slice(1).forEach(tile => anims.push(tile.animate(
        [{ transform: 'translateX(0)' }, { transform: `translateX(${-step}px)` }], { ...slide, fill: 'forwards' })));
      if (incoming) {
        // Explicit grid cells let the new tile share slot 3 with the one leaving it; the
        // settle's re-render discards it and the inline styles.
        tiles.forEach((tile, i) => { tile.style.gridArea = `1 / ${i + 1}`; });
        tiles[0].parentElement.insertAdjacentHTML('beforeend', upNextTileHtml(incoming));
        const entering = tiles[0].parentElement.lastElementChild;
        entering.style.gridArea = '1 / 3';
        anims.push(entering.animate([{ transform: `translateX(${step}px)` }, { transform: 'none' }],
                                    { ...slide, fill: 'backwards' }));
      }

      await Promise.all(anims.map(a => a.finished));
      await settleTrack(data, cfg, anims);
    } finally {
      // Also on failure, so changeTrack's fallback render isn't left under a stray clone.
      anims.forEach(a => a.cancel());
      fly.remove();
      tiles[0].style.visibility = '';
    }
  }

  async function animateCrossfade(data, cfg) {
    const poster = document.getElementById('nowShowingPoster');
    await preloadImage(prox(data.poster), 500);
    const anims = [];
    try {
      if (poster) anims.push(poster.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 250, easing: 'ease-in', fill: 'forwards' }));
      const track = document.querySelector('.now-showing-track');
      if (track) anims.push(track.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' }));
      await Promise.all(anims.map(a => a.finished));
      await settleTrack(data, cfg, anims, true);
    } finally {
      anims.forEach(a => a.cancel());
    }
    if (poster) poster.animate([{ opacity: 0, transform: 'scale(0.97)' }, { opacity: 1, transform: 'none' }],
                               { duration: 450, easing: TRACK_EASE });
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
    if (newItem) resetPopups(data.mediaType === 'musicvideo' ? data.facts : []);
    else if (data.mediaType === 'musicvideo') adoptLateFacts(data.facts);
    playback = {
      key, state,
      offset: anchorOffset,
      reported: offset,
      at: anchorAt,
      duration: Number(data.duration) || 0,
      progress: Number(data.progress) || 0
    };
    // Music videos only: a TV play queue (the show's next episodes) made backing out of an
    // episode hold the loading look for 8 s (Dan, 2026-09-30). The cost: an auto-played next
    // episode that takes 3-8 s to start (a short post-play countdown) shows posters between.
    queueHasMore = data.mediaType === 'musicvideo' && Array.isArray(data.upNext) && data.upNext.length > 0;
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
    renderEndsAt();
    renderFactMarks();
    updatePopup();
  }

  // ---- the details line under the movie/TV bar ----
  // What's on ("S2 · E9 · Wax Patrol", or "Ghosted · 2023") and when it ends at the current
  // position. A pause pushes the end time out, so it's recomputed on every progress tick.
  function detailsText(data) {
    const ep = data.mediaType === 'episode' && /^.* - S(\S+?)E(\S+?) - (.*)$/.exec(data.title || '');
    if (ep) return `S${ep[1]} · E${ep[2]} · ${ep[3]}`;
    return [data.title, data.year].filter(Boolean).join(' · ');
  }

  // A 12-hour clock without AM/PM ("Ends 11:47"), whatever the browser's locale: the Pi's
  // defaults to 24-hour (Dan, 2026-10-04)
  function endsAtText(now, remainingMs) {
    if (!(remainingMs > 0)) return '';
    const end = new Date(now + remainingMs);
    return `Ends ${end.getHours() % 12 || 12}:${String(end.getMinutes()).padStart(2, '0')}`;
  }

  function renderEndsAt() {
    const el = document.getElementById('nowShowingEnds');
    if (!el) return;
    const text = playback && playback.duration ? endsAtText(Date.now(), playback.duration - positionMs()) : '';
    if (el.textContent !== text) el.textContent = text;
  }

  // ---- fun-fact bubbles (music layout, Pop-Up Video style) ----
  // data.facts (short strings; none means no bubbles) pop up over a corner of the art: the
  // first 15 s in, then one per 35 s slot, each held for its reading time. It runs off the
  // playback position, so a pause holds the bubble and a seek picks the matching slot; nothing
  // starts in a song's last 15 s or during a song change.
  const POPUP_END_QUIET_MS = 15000;
  const POPUP_SETTLE_MS = 1500;  // after a new item: lets a change's last flourishes finish
  // Only a jump back further than this counts as a seek. Each ~10 s Plex report re-anchors the
  // local clock; measured on the Pi the step is only a few ms, so this is a safety margin
  // rather than the fix for bubbles vanishing early (see popOutPopup).
  const POPUP_SEEK_BACK_MS = 3000;
  const POPUP_MAX_READ_MS = 20000;
  // The slots cycle through the facts: each shows up to this many times, a slow steady stream
  // for anyone who missed one (Dan: "all the facts should repeat maybe once").
  const POPUP_REPEATS = 2;
  let popupTiming = { first: 15000, every: 35000 };  // ?demo=popup shortens these
  let popupFacts = [];
  let popupSlot = -1;          // the slot on screen, or -1
  let popupShown = new Set();  // slots already shown for this item: each fact shows once
  let popupShownAt = 0;        // the position (ms) at which the bubble on screen appeared
  let popupHideAt = 0;         // ... and at which it pops out
  let popupNotBefore = 0;      // wall-clock ms: no new bubble before this

  function popupReadMs(text) {
    // ~12 characters a second, plus 3 s (Dan wanted a little more time than 15/s + 2 s),
    // capped so even a very long fact fits in its 35 s slot (Astra).
    return Math.min(POPUP_MAX_READ_MS, 3000 + String(text).length / 12 * 1000);
  }

  // YouTube-style marks on the music bar where the fun facts will pop up (Dan, 2026-10-02), from
  // the schedule updatePopup() runs on: one per slot, while it can still finish before the
  // song's quiet end. A mark sits where its slot opens; the bubble follows on the next tick.
  // updatePopup() judges the quiet end from that later tick, so a slot that would end exactly
  // on it is marked only with FACT_MARK_SLACK_MS to spare (Astra pass 1 #1: a mark whose bubble
  // never came). A bubble inside that last second can still show unmarked; a mark never lies.
  const FACT_MARK_SLACK_MS = 1000;
  function factMarkTimes(duration, facts, timing) {
    const times = [];
    if (!(duration > 0) || !facts.length) return times;
    for (let slot = 0; slot < facts.length * POPUP_REPEATS; slot++) {
      const at = timing.first + slot * timing.every;
      if (at + popupReadMs(facts[slot % facts.length]) + FACT_MARK_SLACK_MS > duration - POPUP_END_QUIET_MS) break;
      times.push(at);
    }
    return times;
  }

  let factMarksFor = '';   // what the marks on screen were drawn for, to redraw only on change
  function renderFactMarks() {
    const el = document.getElementById('nowShowingMarks');
    const nowShowing = document.getElementById('nowShowing');
    if (!el || !nowShowing) return;
    const music = nowShowing.classList.contains('music') && playback;
    const times = music ? factMarkTimes(playback.duration, popupFacts, popupTiming) : [];
    const key = music ? `${playback.key}|${playback.duration}|${times.join(',')}` : '';
    if (key !== factMarksFor) {
      factMarksFor = key;
      el.innerHTML = times.map(at =>
        `<span class="now-showing-mark" data-at="${at}" style="left: ${(at / playback.duration * 100).toFixed(3)}%"></span>`).join('');
    }
    if (!times.length) return;
    const pos = positionMs();
    for (const mark of el.children) mark.classList.toggle('passed', pos >= Number(mark.dataset.at));
  }

  function resetPopups(facts) {
    const el = document.getElementById('nowShowingPopup');
    if (el) { el.getAnimations().forEach(a => a.cancel()); el.classList.remove('showing'); }
    popupFacts = Array.isArray(facts) ? facts.filter(f => typeof f === 'string' && f.trim()) : [];
    popupSlot = -1;
    popupShown = new Set();
    // A song change's final animations (the crossfade's art fade-in, the text rise) outlast
    // trackAnimating, so hold off briefly (Astra pass 1 #3).
    popupNotBefore = Date.now() + POPUP_SETTLE_MS;
  }

  // The proxy's facts lookup can miss the refresh that announced the song (it has a short
  // timeout so it never delays that news); facts arriving on a later poll are taken up.
  function adoptLateFacts(facts) {
    if (popupFacts.length || !Array.isArray(facts)) return;
    popupFacts = facts.filter(f => typeof f === 'string' && f.trim());
  }

  function popOutPopup() {
    const el = document.getElementById('nowShowingPopup');
    popupSlot = -1;
    if (!el || !el.classList.contains('showing')) return;
    el.getAnimations().forEach(a => a.cancel());
    // Once it's out: hide it, then cancel the pop-out so its scale(0) fill can't linger. On the
    // Pi's kiosk every bubble after the first vanished ~0.5 s in (2026-09-26), about when its
    // pop-in ended; the suspected cause is this fill taking over again there (headless Chromium
    // didn't show it). Not yet confirmed on the device.
    el.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.05)', offset: 0.3 }, { transform: 'scale(0)' }],
               { duration: 220, easing: 'ease-in', fill: 'forwards' })
      .finished.then(anim => {
        if (popupSlot === -1) el.classList.remove('showing');
        anim.cancel();
      }).catch(() => {});
  }

  function popInPopup(slot) {
    const el = document.getElementById('nowShowingPopup');
    const poster = document.getElementById('nowShowingPoster');
    if (!el || !poster) return;
    const r = poster.getBoundingClientRect();
    const inset = r.width * 0.05;
    const topRight = slot % 2 === 0;   // alternate corners
    el.textContent = popupFacts[slot % popupFacts.length];
    el.classList.toggle('corner-tr', topRight);
    el.classList.toggle('corner-bl', !topRight);
    Object.assign(el.style, {
      maxWidth: `${r.width * 0.72}px`,
      left: topRight ? 'auto' : `${r.left + inset}px`,
      right: topRight ? `${window.innerWidth - r.right + inset}px` : 'auto',
      top: topRight ? `${r.top + inset}px` : 'auto',
      bottom: topRight ? 'auto' : `${window.innerHeight - r.bottom + inset}px`
    });
    el.getAnimations().forEach(a => a.cancel());
    el.classList.add('showing');
    el.animate([{ transform: 'scale(0)' }, { transform: 'scale(1.08)', offset: 0.65 }, { transform: 'scale(1)' }],
               { duration: 380, easing: 'cubic-bezier(0.3, 0.6, 0.4, 1)' });
    popupSlot = slot;
    popupShown.add(slot);
  }

  function updatePopup() {
    if (!playback || !popupFacts.length) return;
    const pos = positionMs();
    if (popupSlot !== -1) {
      // Done, or a seek back. The start is stored, not recomputed: rounding once made a paused
      // bubble look like a seek back and vanish on the next tick (Astra pass 1 #2).
      if (trackAnimating || pos >= popupHideAt || pos < popupShownAt - POPUP_SEEK_BACK_MS) popOutPopup();
      return;
    }
    if (trackAnimating || Date.now() < popupNotBefore) return;
    const slot = Math.floor((pos - popupTiming.first) / popupTiming.every);
    if (slot < 0 || slot >= popupFacts.length * POPUP_REPEATS || popupShown.has(slot)) return;
    const read = popupReadMs(popupFacts[slot % popupFacts.length]);
    if (pos + read > popupTiming.first + (slot + 1) * popupTiming.every) return;   // joined too late
    if (playback.duration && pos + read > playback.duration - POPUP_END_QUIET_MS) return;
    popupShownAt = pos;
    popupHideAt = pos + read;
    popInPopup(slot);
  }

  // Centre the pause badge on the art, wherever the current layout put it.
  // Movies and TV: the poster always spans the screen's width (Dan, 2026-09-30: "nothing we do
  // should make those [black side bars] come back"). When the stack leaves it a box a little
  // shorter than the poster, it fills the box and loses a sliver top and bottom instead.
  // A very different shape (an episode frame, the last-resort artwork) is still drawn whole.
  const POSTER_FILL_MAX_CROP = 0.1;

  // The share of the poster's height cover would cut off, if filling is the right call
  function posterFillCrop(naturalW, naturalH, boxW, boxH) {
    if (!naturalW || !naturalH || !boxW || !boxH) return 0;
    const crop = 1 - (naturalW / naturalH) / (boxW / boxH);
    return crop > 0 && crop <= POSTER_FILL_MAX_CROP ? crop : 0;
  }

  function fitPoster() {
    const poster = document.getElementById('nowShowingPoster');
    const nowShowing = document.getElementById('nowShowing');
    if (!poster || !nowShowing) return;
    requestAnimationFrame(() => {
      const fill = !nowShowing.classList.contains('music') &&
        posterFillCrop(poster.naturalWidth, poster.naturalHeight, poster.clientWidth, poster.clientHeight) > 0;
      poster.classList.toggle('fill', fill);
    });
  }

  // Any change to the poster's box refits it: the image loading, the marquee's font arriving and
  // unwrapping the title, the details line, a resize (Astra pass 1 #1: a font that loaded after
  // the poster left the bars on). Toggling .fill changes only object-fit, never the box.
  function watchPosterBox() {
    const poster = document.getElementById('nowShowingPoster');
    if (!poster) return;
    if (window.ResizeObserver) new ResizeObserver(fitPoster).observe(poster);
    else window.addEventListener('resize', fitPoster);
  }
  watchPosterBox();

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
      if (trackAnimating) {           // let a song-change transition finish undisturbed
        nowPlayingTimer = setTimeout(tick, POLL_MS);
        return;
      }
      const nowPlayingData = await checkNowPlaying(cfg);

      if (nowPlayingData.playing) {
        missingSince = null;
        if (currentMode !== 'nowplaying') {
          showNowPlaying(nowPlayingData, cfg);   // playback started
        } else if (nowPlayingKey(nowPlayingData) !== currentItemKey) {
          // Something else started without a stop in between (the next track in a playlist)
          await changeTrack(nowPlayingData, cfg, () => checkNowPlaying(cfg));
          // If the queue's new third item came too late for the transition, look again soon,
          // not a full poll after it (Astra).
          nowPlayingTimer = setTimeout(tick, AFTER_CHANGE_POLL_MS);
          return;
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

      // ?preview=setup shows the first-run screen on a configured wall
      if (!cfg.configured || previewMode === 'setup') {
        showSetup(cfg, !cfg.configured);
        return;
      }

      if (previewMode === 'nowplaying' || previewMode === 'musicvideo') {
        let previewItems = [];
        try {
          previewItems = await fetchItems(cfg);
        } catch {
          previewItems = [];
        }
        const preview = makePreviewNowPlaying(previewItems, previewMode === 'musicvideo');
        // &demo=popup: the fun-fact bubbles with hardcoded facts, on a shortened clock
        if (previewMode === 'musicvideo' && new URLSearchParams(location.search).get('demo') === 'popup') {
          popupTiming = { first: 3000, every: 12000 };
          Object.assign(preview, {
            artist: 'Panic! At The Disco', trackTitle: "Emperor's New Clothes",
            viewOffset: 0, offsetAt: Date.now(), duration: 180000,
            // Songfacts and Wikipedia, 2026-09-26
            facts: [
              "Brendon Urie stacked his own voice 38 times to get an “operatic evil feel.”",
              "It riffs on Andersen’s fable, but Urie says he’s in on it: “I just choose to be naked.”",
              "He calls it autobiographical: Panic! had just become his solo project.",
              "The video is a sequel to “This Is Gospel”: he dies, drops into Hell and becomes the devil."
            ]
          });
        }
        showNowPlaying(preview, cfg);
        // ?state=loading previews the hold between queue items
        if (new URLSearchParams(location.search).get('state') === 'loading') holdForNextItem(true);
        // &demo=advance plays the song-change transition every 5 s through a looping fake queue
        if (previewMode === 'musicvideo' && new URLSearchParams(location.search).get('demo') === 'advance') {
          const art = r => ({ title: r.title, artist: r.artist, trackTitle: r.trackTitle, poster: r.poster, ratingKey: r.ratingKey });
          let ring = [preview, ...preview.upNext];
          let step = 0;
          setInterval(() => {
            if (trackAnimating || ring.length < 2) return;
            step++;
            ring = [...ring.slice(1), ring[0]];
            const upNext = ring.slice(1, 4).map(art);
            // Three cases in turn, like real Plex: the third item known at once; pending (the
            // proxy carries over two) and answered 250 ms later; answered only after the row
            // gave up waiting, arriving by the kiosk's next poll.
            const kind = upNext.length < 3 ? 'known' : ['known', 'late', 'too late'][step % 3];
            const base = { ...preview, ...art(ring[0]), playing: true, viewOffset: 0, offsetAt: Date.now() };
            const pending = { ...base, upNext: upNext.slice(0, 2), upNextPending: true };
            const answered = { ...base, upNext, upNextPending: false };
            const since = Date.now();
            const askAgain = async () => (kind === 'late' && Date.now() - since >= 250 ? answered : pending);
            changeTrack(kind === 'known' ? answered : pending, cfg, askAgain)
              .then(() => { if (kind === 'too late') setTimeout(() => renderUpNext(upNext), AFTER_CHANGE_POLL_MS); });
          }, DEMO_ADVANCE_MS);
        }
        return;
      }

      // No poster libraries chosen (a music-videos-only wall): no rotation, and no "no movies"
      // error before the now-playing monitor starts (Astra pass 2)
      const items = cfg.sectionId.length ? await fetchItems(cfg) : [];
      startRotation(cfg, items);

      if (previewMode !== 'rotation' && cfg.plexDevices && cfg.plexDevices.length > 0) {
        startNowPlayingMonitor(cfg);
      }
      if (!previewMode) watchConfig(cfg.signature);
    }catch(e){
      console.error(e);
      // Try to get hostname and address from config if we managed to load it
      let cfgForUrls = null;
      try {
        const r = await fetch(`${proxyBase()}/api/config`, { cache: 'no-store' });
        if (r.ok) cfgForUrls = await r.json();
      } catch (configError) {
        // Use the fallback hostname
      }
      showError(e.message || 'An unexpected error occurred. Please check the console for details.', cfgForUrls);
    }
  })();
})();
