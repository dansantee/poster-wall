(function () {
  // ---------- helpers ----------
  const has = id => !!document.getElementById(id);
  const el  = id => document.getElementById(id);
  // Whether libraries were ever saved; set in init, read by the Connect to Plex library picker
  let librariesChosen = true;

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

  async function loadBuildInfo() {
    const r = await fetch(`${proxyBase()}/api/build-info`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`GET /api/build-info ${r.status}`);
    return await r.json();
  }

  function renderBuildInfo(info) {
    if (!has('buildInfo')) return;
    if (info.error) {
      el('buildInfo').textContent = `Build info unavailable: ${info.error}`;
      return;
    }
    const dirtyLabel = info.dirty ? 'dirty' : 'clean';
    const statusText = (info.status && info.status.length > 0) ? info.status.join('\n') : 'clean';
    el('buildInfo').textContent =
      `Host: ${info.hostname || 'unknown'}\n` +
      `Commit: ${info.shortCommit || 'unknown'}\n` +
      `Working tree: ${dirtyLabel}\n` +
      `Status:\n${statusText}`;
  }

  // ---------- init ----------
  (async function init() {
    try {
      const cfg = await loadServerCfg();
      try {
        renderBuildInfo(await loadBuildInfo());
      } catch (buildError) {
        renderBuildInfo({ error: String(buildError) });
      }

      // populate (null-safe; fields may be removed from HTML)
      // Libraries never chosen: the library picker suggests a role for each (the field's '1'
      // below is only the old default, not a choice)
      librariesChosen = cfg.sectionId !== undefined || cfg.musicVideoSectionId !== undefined;
      if (has('sectionId'))   {
        // Handle both string and array formats for backward compatibility
        const sections = cfg.sectionId;
        if (Array.isArray(sections)) {
          el('sectionId').value = sections.join(', ');
        } else {
          el('sectionId').value = sections ?? '1';
        }
      }
      if (has('rotateSec'))   el('rotateSec').value    = cfg.rotateSec ?? 10;
      if (has('plexUrl'))     el('plexUrl').value      = cfg.plexUrl   ?? '';
      if (has('plexToken'))   el('plexToken').value    = cfg.plexToken ?? '';
      if (has('plexInsecure'))el('plexInsecure').checked = cfg.plexInsecure ?? true;
      if (has('plexDevices')) el('plexDevices').value   = (cfg.plexDevices || []).join('\n');
      if (has('autoDim'))     el('autoDim').checked    = !!cfg.autoDim;
      if (has('autoDimStrength')) el('autoDimStrength').value = cfg.autoDimStrength ?? 0.5;
      if (has('nowShowingText')) el('nowShowingText').value = cfg.nowShowingText ?? 'NOW SHOWING';
      if (has('musicVideoSectionId')) {
        const music = cfg.musicVideoSectionId;
        el('musicVideoSectionId').value = Array.isArray(music) ? music.join(', ') : (music ?? '');
      }
      if (has('nowShowingFont')) el('nowShowingFont').value = cfg.nowShowingFont ?? "'Bebas Neue', sans-serif";
      if (has('nowShowingFontSize')) el('nowShowingFontSize').value = cfg.nowShowingFontSize ?? 9;
      if (has('nowShowingKerning')) el('nowShowingKerning').value = cfg.nowShowingKerning ?? 0.1;
      if (has('nowShowingFontWeight')) el('nowShowingFontWeight').value = cfg.nowShowingFontWeight ?? 700;
      if (has('nowShowingColor')) el('nowShowingColor').value = cfg.nowShowingColor ?? '#E0B44C';
      if (has('progressBarColor')) el('progressBarColor').value = cfg.progressBarColor ?? '#E0B44C';
      if (has('progressTrackColor')) el('progressTrackColor').value = cfg.progressTrackColor ?? '#788496';
      if (has('progressTrackOpacity')) el('progressTrackOpacity').value = cfg.progressTrackOpacity ?? 0.92;
      
      // Initialize color pickers with dynamic updates
      initializeColorPickers();
      
      if (has('progressBarPadding')) el('progressBarPadding').value = cfg.progressBarPadding ?? 1.5;
      if (has('progressBarHeight')) el('progressBarHeight').value = cfg.progressBarHeight ?? 2.5;
      if (has('posterTransitions')) el('posterTransitions').checked = !!cfg.posterTransitions;
      if (has('posterColorMarquee')) el('posterColorMarquee').checked = !!cfg.posterColorMarquee;
      
      // Handle transition types array (checkboxes)
      const transitionTypes = cfg.transitionTypes || ['crossfade'];
      const transitionCheckboxes = ['crossfade', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'flip', 'scale-fade'];
      transitionCheckboxes.forEach(type => {
        const checkboxId = `transition-${type}`;
        if (has(checkboxId)) {
          el(checkboxId).checked = transitionTypes.includes(type);
        }
      });
      
      if (has('autoDim'))     el('autoDim').checked    = !!cfg.autoDim;
      if (has('adminKey'))    el('adminKey').value     = ''; // never persist the key in JSON

      bindHandlers(cfg);
      bindPlexSetup();
    } catch (e) {
      // minimal inline error
      const pre = document.getElementById('testOut');
      if (pre) pre.textContent = `Failed to load server config: ${e}`;
      console.error(e);
      bindHandlers({}); // still bind so user can try saving
      bindPlexSetup();
    }
  })();

  function bindHandlers(cfg) {
    // Save button
    if (has('btnSave')) {
      el('btnSave').addEventListener('click', async () => {
        try {
          const next = { ...cfg };
          if (has('sectionId')) {
            const sections = (el('sectionId').value || '').split(',')
              .map(id => id.trim())
              .filter(id => id.length > 0);
            next.sectionId = sections;
          }
          if (has('rotateSec'))    next.rotateSec    = Math.max(3, Number(el('rotateSec').value) || 10);
          if (has('autoDim'))      next.autoDim      = !!el('autoDim').checked;
          if (has('autoDimStrength')) next.autoDimStrength = Math.min(1, Math.max(0.2, Number(el('autoDimStrength').value) || 0.5));
          if (has('nowShowingText')) next.nowShowingText = (el('nowShowingText').value || 'NOW SHOWING').trim();
          if (has('musicVideoSectionId')) next.musicVideoSectionId = (el('musicVideoSectionId').value || '').split(',')
            .map(id => id.trim())
            .filter(id => id.length > 0);
          if (has('nowShowingFont')) next.nowShowingFont = el('nowShowingFont').value || "'Bebas Neue', sans-serif";
          if (has('nowShowingFontSize')) next.nowShowingFontSize = Number(el('nowShowingFontSize').value) || 9;
          if (has('nowShowingKerning')) next.nowShowingKerning = Number(el('nowShowingKerning').value) || 0.1;
          if (has('nowShowingFontWeight')) next.nowShowingFontWeight = Number(el('nowShowingFontWeight').value) || 700;
          if (has('nowShowingColor')) next.nowShowingColor = el('nowShowingColor').value || '#E0B44C';
          if (has('progressBarColor')) next.progressBarColor = el('progressBarColor').value || '#E0B44C';
          if (has('progressTrackColor')) next.progressTrackColor = el('progressTrackColor').value || '#788496';
          if (has('progressTrackOpacity')) next.progressTrackOpacity = Math.min(1, Math.max(0.1, Number(el('progressTrackOpacity').value) || 0.92));
          if (has('progressBarPadding')) next.progressBarPadding = Number(el('progressBarPadding').value) || 1.5;
          if (has('progressBarHeight')) next.progressBarHeight = Number(el('progressBarHeight').value) || 2.5;
          if (has('posterTransitions')) next.posterTransitions = !!el('posterTransitions').checked;
          if (has('posterColorMarquee')) next.posterColorMarquee = !!el('posterColorMarquee').checked;
          
          // Collect selected transition types from checkboxes
          const selectedTransitions = [];
          const transitionCheckboxes = ['crossfade', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'flip', 'scale-fade'];
          transitionCheckboxes.forEach(type => {
            const checkboxId = `transition-${type}`;
            if (has(checkboxId) && el(checkboxId).checked) {
              selectedTransitions.push(type);
            }
          });
          // Ensure at least one transition is selected (fallback to crossfade)
          next.transitionTypes = selectedTransitions.length > 0 ? selectedTransitions : ['crossfade'];
          
          if (has('plexUrl')) {
            let u = (el('plexUrl').value || '').trim();
            if (u && !/^https?:\/\//i.test(u)) u = 'http://' + u; // normalize
            next.plexUrl = u;
          }
          if (has('plexToken'))    next.plexToken    = (el('plexToken').value || '').trim();
          if (has('plexInsecure')) next.plexInsecure = !!el('plexInsecure').checked;
          if (has('plexDevices')) {
            const devices = (el('plexDevices').value || '').split('\n')
              .map(line => line.trim())
              .filter(line => line.length > 0);
            next.plexDevices = devices;
          }

          await saveServerCfg(next);
          try {
            renderBuildInfo(await loadBuildInfo());
          } catch {}
          alert('Settings saved to server.');
        } catch (e) {
          alert(`Save failed: ${e}`);
        }
      });
    }

    // Restart button
    if (has('btnRestart')) {
      el('btnRestart').addEventListener('click', async () => {
        try {
          const headers = { 'Content-Type': 'application/json' };
          const key = has('adminKey') ? (el('adminKey').value || '').trim() : '';
          if (key) headers['X-Admin-Key'] = key;
          
          const r = await fetch(`${proxyBase()}/api/restart-kiosk`, {
            method: 'POST', headers
          });
          
          if (r.ok) {
            try {
              renderBuildInfo(await loadBuildInfo());
            } catch {}
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
        if (has('sectionId')) {
          const sections = (el('sectionId').value || '').split(',')
            .map(id => id.trim())
            .filter(id => id.length > 0);
          next.sectionId = sections;
        }
        if (has('rotateSec'))    next.rotateSec    = Math.max(3, Number(el('rotateSec').value) || 10);
        if (has('autoDim'))      next.autoDim      = !!el('autoDim').checked;
        if (has('autoDimStrength')) next.autoDimStrength = Math.min(1, Math.max(0.2, Number(el('autoDimStrength').value) || 0.5));
        if (has('nowShowingText')) next.nowShowingText = (el('nowShowingText').value || 'NOW SHOWING').trim();
        if (has('musicVideoSectionId')) next.musicVideoSectionId = (el('musicVideoSectionId').value || '').split(',')
          .map(id => id.trim())
          .filter(id => id.length > 0);
        if (has('nowShowingFont')) next.nowShowingFont = el('nowShowingFont').value || "'Bebas Neue', sans-serif";
        if (has('nowShowingFontSize')) next.nowShowingFontSize = Number(el('nowShowingFontSize').value) || 9;
        if (has('nowShowingKerning')) next.nowShowingKerning = Number(el('nowShowingKerning').value) || 0.1;
        if (has('nowShowingFontWeight')) next.nowShowingFontWeight = Number(el('nowShowingFontWeight').value) || 700;
        if (has('nowShowingColor')) next.nowShowingColor = el('nowShowingColor').value || '#E0B44C';
        if (has('progressBarColor')) next.progressBarColor = el('progressBarColor').value || '#E0B44C';
        if (has('progressTrackColor')) next.progressTrackColor = el('progressTrackColor').value || '#788496';
        if (has('progressTrackOpacity')) next.progressTrackOpacity = Math.min(1, Math.max(0.1, Number(el('progressTrackOpacity').value) || 0.92));
        if (has('progressBarPadding')) next.progressBarPadding = Number(el('progressBarPadding').value) || 1.5;
        if (has('progressBarHeight')) next.progressBarHeight = Number(el('progressBarHeight').value) || 2.5;
        if (has('posterTransitions')) next.posterTransitions = !!el('posterTransitions').checked;
        if (has('posterColorMarquee')) next.posterColorMarquee = !!el('posterColorMarquee').checked;
        
        // Collect selected transition types from checkboxes
        const selectedTransitions = [];
        const transitionCheckboxes = ['crossfade', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'flip', 'scale-fade'];
        transitionCheckboxes.forEach(type => {
          const checkboxId = `transition-${type}`;
          if (has(checkboxId) && el(checkboxId).checked) {
            selectedTransitions.push(type);
          }
        });
        // Ensure at least one transition is selected (fallback to crossfade)
        next.transitionTypes = selectedTransitions.length > 0 ? selectedTransitions : ['crossfade'];
        
        if (has('plexUrl')) {
          let u = (el('plexUrl').value || '').trim();
          if (u && !/^https?:\/\//i.test(u)) u = 'http://' + u; // normalize
          next.plexUrl = u;
        }
        if (has('plexToken'))    next.plexToken    = (el('plexToken').value || '').trim();
        if (has('plexInsecure')) next.plexInsecure = !!el('plexInsecure').checked;
        if (has('plexDevices')) {
          const devices = (el('plexDevices').value || '').split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0);
          next.plexDevices = devices;
        }

        await saveServerCfg(next);
        try {
          renderBuildInfo(await loadBuildInfo());
        } catch {}
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
            section: Array.isArray(c.sectionId) ? c.sectionId[0] : (c.sectionId || '1'),
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

    if (has('btnPreviewRotation')) {
      el('btnPreviewRotation').addEventListener('click', () => {
        window.open('index.html?preview=rotation', '_blank');
      });
    }

    if (has('btnPreviewNowPlaying')) {
      el('btnPreviewNowPlaying').addEventListener('click', () => {
        window.open('index.html?preview=nowplaying', '_blank');
      });
    }

    if (has('btnPreviewMusicVideo')) {
      el('btnPreviewMusicVideo').addEventListener('click', () => {
        window.open('index.html?preview=musicvideo', '_blank');
      });
    }
  }

  // ---------- Connect to Plex (first-run setup) ----------
  // Sign in with Plex's device-link flow (a code entered at plex.tv/link), pick a server and what
  // each library is for, and find the players to watch. It fills the fields below; Save Settings
  // stores them, and an unconfigured wall then loads by itself (Dan, 2026-10-02).
  const PIN_POLL_MS = 2000;
  const PIN_GIVE_UP_MS = 15 * 60 * 1000;
  const LIBRARY_ROLES = [['posters', 'Posters'], ['music', 'Music videos'], ['off', 'Not shown']];

  function setupHeaders(extra) {
    const headers = { ...(extra || {}) };
    const key = has('adminKey') ? (el('adminKey').value || '').trim() : '';
    if (key) headers['X-Admin-Key'] = key;
    return headers;
  }

  function plexFieldHeaders() {
    const headers = setupHeaders();
    if (has('plexUrl') && el('plexUrl').value.trim()) headers['X-Plex-Url'] = el('plexUrl').value.trim();
    if (has('plexToken') && el('plexToken').value.trim()) headers['X-Plex-Token'] = el('plexToken').value.trim();
    if (has('plexInsecure') && el('plexInsecure').checked) headers['X-Allow-Insecure'] = '1';
    return headers;
  }

  async function setupFetch(path, options) {
    const r = await fetch(`${proxyBase()}${path}`, { cache: 'no-store', ...options });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `${path} ${r.status}`);
    return body;
  }

  function showStep(id, on) { if (has(id)) el(id).hidden = !on; }
  function say(id, text) { if (has(id)) el(id).textContent = text; }

  function idList(id) {
    return has(id) ? el(id).value.split(',').map(s => s.trim()).filter(Boolean) : [];
  }

  // Bumped by each sign-in and each server choice; an older one still awaiting a reply stops
  // when it sees a newer number, so it can't overwrite what the user picked since (Astra pass 1)
  let signInGen = 0;
  let serverGen = 0;

  async function signInWithPlex() {
    const gen = ++signInGen;
    serverGen++;
    showStep('plexServers', false);
    showStep('plexLibraries', false);
    say('plexSignInStatus', 'Asking Plex for a code...');
    showStep('plexSignIn', true);
    let pin;
    try {
      pin = await setupFetch('/api/plex/pin', { method: 'POST', headers: setupHeaders() });
    } catch (e) {
      if (gen === signInGen) say('plexSignInStatus', `Couldn't start sign-in: ${e.message}`);
      return;
    }
    if (gen !== signInGen) return;
    say('plexPinCode', pin.code || '');
    if (has('plexLinkUrl')) el('plexLinkUrl').href = pin.linkUrl || 'https://plex.tv/link';
    say('plexSignInStatus', 'Waiting for you to enter the code...');
    const started = Date.now();
    while (Date.now() - started < PIN_GIVE_UP_MS) {
      await new Promise(res => setTimeout(res, PIN_POLL_MS));
      if (gen !== signInGen) return;
      let check;
      try {
        check = await setupFetch(`/api/plex/pin/${encodeURIComponent(pin.id)}`, { headers: setupHeaders() });
      } catch (e) {
        say('plexSignInStatus', `Still waiting (${e.message})...`);
        continue;
      }
      if (gen !== signInGen) return;
      if (check.expired) break;
      if (check.linked) {
        say('plexSignInStatus', 'Signed in.');
        showServers(check.servers || []);
        return;
      }
    }
    if (gen === signInGen) say('plexSignInStatus', 'The code expired. Press Sign in with Plex to get a new one.');
  }

  let plexServers = [];
  function showServers(servers) {
    plexServers = servers;
    if (!servers.length) {
      say('plexServerStatus', 'This Plex account has no servers.');
      showStep('plexServers', true);
      return;
    }
    const select = el('plexServer');
    select.innerHTML = '';
    servers.forEach((s, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = s.owned ? s.name : `${s.name} (shared with you)`;
      select.appendChild(opt);
    });
    say('plexServerStatus', '');
    showStep('plexServers', true);
    if (servers.length === 1) useServer();
  }

  async function useServer() {
    const server = plexServers[Number(el('plexServer').value) || 0];
    if (!server) return;
    const gen = ++serverGen;
    say('plexServerStatus', `Finding ${server.name} on your network...`);
    try {
      const found = await setupFetch('/api/plex/connect', {
        method: 'POST',
        headers: setupHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ token: server.token, candidates: server.candidates, machineId: server.id })
      });
      if (gen !== serverGen) return;
      el('plexUrl').value = found.plexUrl;
      el('plexToken').value = server.token;
      say('plexServerStatus', `Connected to ${server.name} at ${found.plexUrl}.`);
      await loadLibraries(gen);
    } catch (e) {
      if (gen === serverGen) say('plexServerStatus', `Couldn't reach ${server.name} from the wall: ${e.message}`);
    }
  }

  // A first setup's suggestion for a library. An "Other Videos" library (Plex's "none" agent)
  // reports type movie too, so it isn't put in the poster rotation; one named for music videos
  // is offered as the music library.
  function freshLibraryRole(lib) {
    const otherVideos = /\.none$/.test(lib.agent || '');
    if (otherVideos) return /music/i.test(lib.title || '') ? 'music' : 'off';
    return lib.type === 'movie' || lib.type === 'show' ? 'posters' : 'off';
  }

  async function loadLibraries(gen) {
    const list = el('plexLibraryList');
    list.textContent = 'Loading libraries...';
    showStep('plexLibraries', true);
    let libraries;
    try {
      libraries = (await setupFetch('/api/plex/libraries', { headers: plexFieldHeaders() })).libraries || [];
    } catch (e) {
      if (gen === serverGen) list.textContent = `Couldn't list the libraries: ${e.message}`;
      return;
    }
    if (gen !== serverGen) return;
    const posters = new Set(idList('sectionId'));
    const music = new Set(idList('musicVideoSectionId'));
    // A first setup (no libraries ever saved) gets a suggested role for each library
    const fresh = !librariesChosen;
    list.innerHTML = '';
    for (const lib of libraries) {
      const role = fresh ? freshLibraryRole(lib)
        : music.has(lib.key) ? 'music' : posters.has(lib.key) ? 'posters' : 'off';
      const row = document.createElement('label');
      row.className = 'plex-library';
      const name = document.createElement('span');
      name.textContent = lib.title;
      const select = document.createElement('select');
      select.dataset.key = lib.key;
      for (const [value, text] of LIBRARY_ROLES) {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = text;
        opt.selected = value === role;
        select.appendChild(opt);
      }
      select.addEventListener('change', applyLibraryRoles);
      row.append(name, select);
      list.appendChild(row);
    }
    applyLibraryRoles();
  }

  // The library choices, written into the Section ID fields that Save Settings reads
  function applyLibraryRoles() {
    const picked = role => [...el('plexLibraryList').querySelectorAll('select')]
      .filter(s => s.value === role).map(s => s.dataset.key);
    if (has('sectionId')) el('sectionId').value = picked('posters').join(', ');
    if (has('musicVideoSectionId')) el('musicVideoSectionId').value = picked('music').join(', ');
    say('plexLibraryHint', 'Now press Save Settings at the bottom of the page.');
  }

  async function findPlayers() {
    const out = el('playersList');
    out.textContent = 'Looking for players...';
    let players;
    try {
      players = (await setupFetch('/api/plex/players', { headers: plexFieldHeaders() })).players || [];
    } catch (e) {
      out.textContent = `Couldn't ask Plex: ${e.message}`;
      return;
    }
    if (!players.length) {
      out.textContent = 'Nothing is playing. Start something on the TV, then try again.';
      return;
    }
    out.innerHTML = '';
    for (const p of players) {
      const row = document.createElement('div');
      row.className = 'plex-player';
      const text = document.createElement('span');
      text.textContent = `${p.title || p.product} (${p.address}) playing ${p.playing}`;
      const add = document.createElement('button');
      add.type = 'button';
      add.className = 'btn';
      add.textContent = 'Add';
      add.addEventListener('click', () => {
        const lines = el('plexDevices').value.split('\n').map(s => s.trim()).filter(Boolean);
        if (!lines.includes(p.address)) lines.push(p.address);
        el('plexDevices').value = lines.join('\n');
        add.textContent = 'Added';
        add.disabled = true;
      });
      row.append(text, add);
      out.appendChild(row);
    }
  }

  function bindPlexSetup() {
    if (has('btnPlexSignIn')) el('btnPlexSignIn').addEventListener('click', signInWithPlex);
    if (has('btnPlexUseServer')) el('btnPlexUseServer').addEventListener('click', useServer);
    if (has('btnFindPlayers')) el('btnFindPlayers').addEventListener('click', findPlayers);
  }

  // Initialize enhanced color pickers with live hex updates
  function initializeColorPickers() {
    const colorPickers = [
      { input: 'nowShowingColor', hex: 'nowShowingColorHex' },
      { input: 'progressBarColor', hex: 'progressBarColorHex' },
      { input: 'progressTrackColor', hex: 'progressTrackColorHex' }
    ];

    colorPickers.forEach(picker => {
      const inputEl = document.getElementById(picker.input);
      const hexEl = document.getElementById(picker.hex);

      if (inputEl && hexEl) {
        // Update hex display
        function updateHexDisplay() {
          const color = inputEl.value;
          hexEl.textContent = color.toUpperCase();
        }

        // Initialize display
        updateHexDisplay();

        // Update on color change
        inputEl.addEventListener('input', updateHexDisplay);
        inputEl.addEventListener('change', updateHexDisplay);

        // Make hex value clickable to select all text
        hexEl.addEventListener('click', function() {
          const range = document.createRange();
          range.selectNodeContents(hexEl);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        });
      }
    });
  }
})();
