const state = {
  status: null,
  projects: [],
  activeProjectId: null,
  activeProject: null,
  runs: [],
  busy: null,        // {title, steps: [..], current: index}
  forcePick: false,  // user asked to choose a different story
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function notice(message, error = false) {
  const element = $('#notice');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.classList.remove('hidden');
  if (!error) window.setTimeout(() => element.classList.add('hidden'), 6000);
}

/* ------------------------------------------------------------------ */
/* State helpers                                                       */

function hasRun(adapter) {
  return state.runs.some((run) => run.provider?.adapter === adapter);
}

function approvedCount() {
  return state.runs.reduce((total, run) => total + (run.summary?.approved_count || 0), 0);
}

function pendingClaims() {
  return state.runs.flatMap((run) =>
    (run.observations || [])
      .filter((item) => item.normalization_status === 'accepted' && item.review_status === 'pending')
      .map((item) => ({ ...item, run_key: run.run_key }))
  );
}

function phaseOf(project) {
  if (state.forcePick && (project.concepts || []).length) return 'pick';
  if (project.plan) return 'result';
  if ((project.concepts || []).length) return 'pick';
  return 'start';
}

const PHASE_LABEL = {
  start: 'Step 1 of 3 · Create',
  pick: 'Step 2 of 3 · Pick a story',
  result: 'Step 3 of 3 · Watch, tweak, export',
};

/* ------------------------------------------------------------------ */
/* Sidebar                                                             */

function renderProjectList() {
  $('#project-list').innerHTML = state.projects.map((project) => `
    <button class="project-button ${project.project_id === state.activeProjectId ? 'active' : ''}" data-project-id="${escapeHtml(project.project_id)}">
      <span class="project-dot ${project.has_plan || project.status === 'ready' ? 'ready' : ''}"></span>
      <span class="project-copy">
        <strong>${escapeHtml(project.name)}</strong>
        <span>${project.asset_count} clips${project.has_plan ? ' · edited' : ''}</span>
      </span>
    </button>
  `).join('');
  document.querySelectorAll('[data-project-id]').forEach((button) => {
    button.addEventListener('click', () => loadProject(button.dataset.projectId));
  });
}

function renderCapabilities() {
  const capabilities = state.status?.capabilities;
  if (!capabilities) return;
  const visual = capabilities.visual.find((item) => item.id === 'owned-live-visual');
  const speech = capabilities.speech.find((item) => item.id === 'faster-whisper');
  const friendly = [
    { ready: visual?.ready, label: 'Footage understanding', detail: visual?.ready ? 'Cloud model connected' : 'Add API keys to .env' },
    { ready: speech?.ready, label: 'Speech transcription', detail: speech?.ready ? 'Runs on this machine' : 'Not installed' },
    { ready: capabilities.render?.ready, label: 'Video rendering', detail: 'Runs on this machine' },
    { ready: true, label: 'DaVinci Resolve export', detail: 'Verified import' },
  ];
  $('#capability-list').innerHTML = friendly.map((item) => `
    <div class="capability ${item.ready ? 'ready' : ''}">
      <i></i>
      <div><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span></div>
    </div>
  `).join('');
}

/* ------------------------------------------------------------------ */
/* Phase sections                                                      */

function busyCard() {
  const { title, steps, current } = state.busy;
  return `
    <section class="card busy-card">
      <h2>${escapeHtml(title)}</h2>
      <ol class="step-list">
        ${steps.map((step, index) => `
          <li class="${index < current ? 'done' : index === current ? 'active' : ''}">
            <i></i>${escapeHtml(step)}
          </li>
        `).join('')}
      </ol>
      <p class="muted">You can leave this page open — nothing else to do here. Footage
      analysis takes a few minutes the first time; results are cached afterwards.</p>
    </section>
  `;
}

function startSection(project) {
  const analyzed = hasRun('owned-live-visual');
  return `
    <section class="card start-card">
      <span class="eyebrow">${escapeHtml(project.prompt || 'No prompt yet — the editor will aim for a concise daily vlog.')}</span>
      <h2>${project.inventory?.assets?.length || 0} clips ready. Let's make a vlog.</h2>
      <p>One click runs everything: the editor watches your footage, listens for speech,
      and writes ${analyzed ? 'story ideas' : 'two story ideas'} grounded in what's actually
      on film. You pick the story you like; nothing is invented.</p>
      <p class="muted">Frames from your clips are sent to the configured cloud model for
      visual analysis. Speech is transcribed locally and never leaves this machine.</p>
      <button class="primary big" id="create-vlog">${analyzed ? 'Continue — write story ideas' : 'Create my vlog'}</button>
    </section>
  `;
}

function keptStoryIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(`keptStories:${state.activeProjectId}`) || '[]'));
  } catch { return new Set(); }
}

function toggleKeptStory(conceptId) {
  const kept = keptStoryIds();
  if (kept.has(conceptId)) kept.delete(conceptId);
  else kept.add(conceptId);
  localStorage.setItem(`keptStories:${state.activeProjectId}`, JSON.stringify([...kept]));
  renderProject();
}

function storyCard(concept) {
  const missing = concept.missing_shots || [];
  const required = missing.filter((shot) => shot.priority === 'required').length;
  const beats = (concept.structure || []).length;
  const unchecked = conceptPendingClaims(concept).length;
  const kept = keptStoryIds().has(concept.concept_id);
  const beatThumbs = (concept.structure || []).slice(0, 6).map((beat) => {
    const evidence = (beat.evidence || [])[0];
    if (!evidence) return '';
    const mid = (evidence.start_seconds + evidence.end_seconds) / 2;
    return `<img loading="lazy" src="${frameUrl(evidence.asset_id, mid)}" alt="" title="${escapeHtml(beat.purpose)}" />`;
  }).join('');
  return `
    <article class="concept-card story-card ${kept ? 'kept' : ''}">
      <div class="concept-top">
        <span class="eyebrow">${concept.target_duration_seconds}s · ${beats} scenes${unchecked ? ` · asks about ${unchecked} unchecked moment${unchecked === 1 ? '' : 's'}` : ''}</span>
        <button class="ghost keep-toggle" data-keep-story="${escapeHtml(concept.concept_id)}" title="Kept stories survive when you ask for new ideas">${kept ? '★ kept' : '☆ keep'}</button>
      </div>
      ${beatThumbs ? `<div class="beat-thumbs">${beatThumbs}</div>` : ''}
      <h3>${escapeHtml(concept.title)}</h3>
      <p class="hook">${escapeHtml(concept.hook)}</p>
      ${(concept.weaknesses || []).length ? `<p class="muted">Honest caveat: ${escapeHtml(concept.weaknesses[0])}</p>` : ''}
      ${missing.length ? `
        <details>
          <summary>${missing.length} shot${missing.length === 1 ? '' : 's'} worth filming${required ? ` (${required} important)` : ''}</summary>
          <ul>${missing.map((shot) => `
            <li class="missing-shot"><span class="priority ${escapeHtml(shot.priority)}">${escapeHtml(shot.priority)}</span> ${escapeHtml(shot.recording_instruction)}</li>
          `).join('')}</ul>
        </details>` : ''}
      <div class="concept-footer">
        <button class="primary" data-make-story="${escapeHtml(concept.concept_id)}">Make this one</button>
      </div>
    </article>
  `;
}

function pickSection(project) {
  const keptCount = keptStoryIds().size;
  return `
    <section>
      <div class="section-header">
        <div><span class="eyebrow">Pick a story</span><h2>The editor proposes, you decide</h2></div>
      </div>
      <p class="muted">Every scene cites real moments in your clips. "Make this one" cuts the
      video, renders a preview, and prepares editor files — all in one go. Not happy?
      Mark what's worth keeping (★), tell the editor what you want, and ask for new ideas.</p>
      <div class="concept-grid">${(project.concepts || []).map(storyCard).join('')}</div>
      <form id="more-ideas-form" class="revision-form">
        <textarea name="guidance" rows="2"
          placeholder="What do you want instead? e.g. focus on the class project demo, more energy, under 40 seconds, skip the game footage…"></textarea>
        <button type="submit" class="secondary">New ideas${keptCount ? ` (keeping ${keptCount} ★)` : ''}</button>
      </form>
      ${project.plan ? '<button class="ghost" id="back-to-result">← Back to the current cut</button>' : ''}
    </section>
  `;
}

function frameUrl(assetId, seconds) {
  return `/api/projects/${escapeHtml(state.activeProjectId)}/frames/${escapeHtml(assetId)}?t=${seconds.toFixed(2)}`;
}

function sceneStrip(project) {
  const events = (project.plan?.tracks || []).find((track) => track.kind === 'video')?.events || [];
  return events.map((event, index) => {
    const mid = (event.source_start_seconds + event.source_end_seconds) / 2;
    const asset = state.activeProject.inventory.assets.find((a) => a.asset_id === event.asset_id);
    const playUrl = asset?.media_url
      ? `${asset.media_url}#t=${event.source_start_seconds.toFixed(1)},${event.source_end_seconds.toFixed(1)}`
      : null;
    return `
      <a class="scene-card" ${playUrl ? `href="${escapeHtml(playUrl)}" target="_blank" title="Ver este momento en el clip original"` : ''}>
        <img loading="lazy" src="${frameUrl(event.asset_id, mid)}" alt="" />
        <div class="scene-info">
          <strong>${index + 1} · ${(event.source_end_seconds - event.source_start_seconds).toFixed(1)}s</strong>
          <span>${escapeHtml(event.intent)}</span>
        </div>
      </a>
    `;
  }).join('');
}

function newIdeasBanner(project) {
  // Story ideas newer than the current cut: the plan's concept no longer
  // exists in the concepts list, so the cut predates the latest ideas.
  const planConcept = project.plan?.concept_id;
  if (!planConcept) return '';
  const stillListed = (project.concepts || []).some((c) => c.concept_id === planConcept);
  if (stillListed) return '';
  return `
    <div class="banner">
      <span>Hay ideas de historia nuevas — este video es de una ronda anterior.</span>
      <button class="primary compact" id="see-new-ideas">Ver ideas nuevas</button>
    </div>
  `;
}

function resultSection(project) {
  const renderUrl = project.outputs?.render?.url;
  const revision = project.plan?.revision || 1;
  const proxyExport = project.outputs?.xmeml_proxies;
  const duration = project.plan?.project?.duration_seconds;
  return `
    ${newIdeasBanner(project)}
    <section class="result-hero">
      <article class="card video-stage">
        ${renderUrl
          ? `<video controls preload="metadata" src="${escapeHtml(renderUrl)}"></video>`
          : '<div class="video-placeholder">Rendering has not produced a preview yet — use "Make this one" on a story.</div>'}
        <div class="video-caption">
          <span>${escapeHtml(project.plan?.concept_id || '')} · cut ${revision}${duration ? ` · ${Number(duration).toFixed(0)}s` : ''}</span>
        </div>
      </article>
      <article class="card chat-card">
        <h3>Tell the editor what to change</h3>
        <form id="revision-form" class="revision-form vertical">
          <textarea name="instruction" rows="3" required minlength="3"
            placeholder="shorten the intro… drop the fridge shot… end on the scooter…"></textarea>
          <button type="submit" class="primary">Change it</button>
        </form>
        <p class="muted">Changes re-cut and re-render in about a minute. Your footage
        analysis is cached, so this is fast and cheap. Every previous cut is kept.</p>
        <button class="ghost" id="change-story">Choose a different story</button>
      </article>
    </section>
    <section class="card export-card">
      <div>
        <h3>Take it into your editor</h3>
        <p class="muted">Prepares a DaVinci Resolve timeline plus smooth-editing proxy media
        (Resolve on Linux can't read phone H.264 directly).</p>
        ${proxyExport ? `
          <p>Ready: in Resolve use <strong>File → Import → Timeline</strong> and pick<br>
          <code>runtime/projects/${escapeHtml(project.project_id)}/outputs/timeline-davinci-proxies.xml</code></p>` : ''}
      </div>
      <button class="secondary" id="prepare-export">${proxyExport ? 'Rebuild editor files' : 'Prepare DaVinci files'}</button>
    </section>
    ${resultRecommendations(project)}
    <section id="clip-value">
      <div class="section-header"><div><span class="eyebrow">Valor de cada clip</span>
      <h2>Qué aporta cada clip a este vlog</h2></div>
      <p class="muted">Puntaje transparente: segundos en el corte, citas en historias,
      momentos destacados y tu voz. Los marcados «descartable» no aportan nada a esta
      historia — puedes borrarlos de la carpeta con confianza.</p></div>
      <div class="clip-score-list" id="clip-score-list">Cargando…</div>
    </section>
    <section>
      <div class="section-header"><div><span class="eyebrow">Escena por escena</span>
      <h2>Qué hay en este corte y por qué</h2></div>
      <p class="muted">Cada tarjeta muestra el momento usado y la intención del editor.
      Click abre el clip original en ese segundo. ¿Algo no encaja? Dilo en el chat de arriba.</p></div>
      <div class="scene-strip">${sceneStrip(project)}</div>
    </section>
  `;
}

function resultRecommendations(project) {
  const concept = (project.concepts || []).find(
    (item) => item.concept_id === project.plan?.concept_id
  );
  const missing = concept?.missing_shots || [];
  if (!missing.length) return '';
  return `
    <section class="card reco-card">
      <h3>Para fortalecer este video — graba y agrega a la carpeta</h3>
      <ul>${missing.map((shot) => `
        <li class="missing-shot">
          <span class="priority ${escapeHtml(shot.priority)}">${escapeHtml(shot.priority)}</span>
          ${escapeHtml(shot.recording_instruction)}
          ${shot.fallback ? `<div class="muted">Si no: ${escapeHtml(shot.fallback)}</div>` : ''}
        </li>
      `).join('')}</ul>
      <p class="muted">Graba estos clips o voz en off, déjalos en la carpeta del proyecto,
      re-analiza, y pide el cambio en el chat — el resto del análisis queda cacheado.</p>
    </section>
  `;
}

const FLAG_REASONS = {
  brand_or_product_claim: 'names a brand or product — a classic AI-hallucination spot',
  intent_or_emotion_inference: 'guesses feelings or intent, which frames cannot prove',
  unverified_speech_claim: 'claims someone is speaking without a matching transcript',
  identity_or_continuity_inference: 'assumes identity or continuity across shots',
  low_confidence_transcription: 'the audio was unclear, so the transcript may be wrong',
};

function claimReason(observation) {
  const reasons = (observation.risk_flags || [])
    .map((flag) => FLAG_REASONS[flag])
    .filter(Boolean);
  const confidence = observation.model_confidence;
  if (confidence != null && confidence < 0.75) {
    reasons.push(`the model itself rated this only ${(confidence * 100).toFixed(0)}% sure (blurry/ambiguous footage)`);
  }
  return reasons.join('; ') || 'held back by policy';
}

function knownContext(observation) {
  // The approved shot caption covering this claim: what the editor already
  // knows about the same footage, shown so the user judges the delta only.
  const midpoint = (Number(observation.start_seconds) + Number(observation.end_seconds)) / 2;
  for (const run of state.runs) {
    for (const other of run.observations || []) {
      if (
        other.asset_id === observation.asset_id
        && other.review_status === 'reviewed'
        && !(other.clip_id || '').includes('_m')
        && other.start_seconds <= midpoint && midpoint <= other.end_seconds
      ) {
        return other.reviewed_caption || other.caption;
      }
    }
  }
  return null;
}

function claimCard(observation) {
  const projectId = state.activeProjectId;
  const midpoint = ((Number(observation.start_seconds) + Number(observation.end_seconds)) / 2).toFixed(2);
  const asset = state.activeProject.inventory.assets.find((item) => item.asset_id === observation.asset_id);
  const playUrl = asset?.media_url
    ? `${asset.media_url}#t=${Number(observation.start_seconds).toFixed(1)},${Number(observation.end_seconds).toFixed(1)}`
    : null;
  const known = knownContext(observation);
  return `
    <article class="evidence-item">
      <a ${playUrl ? `href="${escapeHtml(playUrl)}" target="_blank" title="Open the clip at this moment"` : ''} class="claim-visual">
        <img loading="lazy" src="/api/projects/${escapeHtml(projectId)}/frames/${escapeHtml(observation.asset_id)}?t=${midpoint}" alt="" />
        <span class="play-hint">▶ ${Number(observation.start_seconds).toFixed(1)}–${Number(observation.end_seconds).toFixed(1)}s</span>
      </a>
      <div class="claim-body">
        <div class="evidence-meta"><span>${escapeHtml(observation.filename || observation.asset_id)}</span></div>
        <p>${escapeHtml(observation.caption)}</p>
        ${known ? `<p class="muted known-context">Editor already knows: ${escapeHtml(known.slice(0, 160))}</p>` : ''}
        <p class="muted why-flagged">Why it's flagged: ${escapeHtml(claimReason(observation))}.</p>
        <div class="review-actions">
          <button class="ghost approve" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="approve">True — use it</button>
          <button class="ghost" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Fix wording</button>
          <button class="ghost reject" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="reject">Wrong — ignore it</button>
        </div>
      </div>
    </article>
  `;
}

function needsCheckSection() {
  const pending = pendingClaims();
  if (!pending.length) return '';
  const projectId = state.activeProjectId;
  if (localStorage.getItem(`showClaims:${projectId}`) !== '1') {
    return `
      <section id="pending-review" class="card skipped-claims">
        <span>${pending.length} unchecked claim${pending.length === 1 ? '' : 's'} set aside.
        You don't need to review them — if a story wants one, you'll be asked about
        just that one when you pick the story.</span>
        <button class="ghost" id="unskip-claims">Review all anyway</button>
      </section>
    `;
  }
  return `
    <section id="pending-review">
      <div class="section-header">
        <div><span class="eyebrow">Unchecked claims (optional)</span><h2>${pending.length} claim${pending.length === 1 ? '' : 's'} the editor won't rely on unchecked</h2></div>
        <p>Each tripped a specific safety rule (shown per card). Unchecked claims are
        excluded from editing; ${approvedCount()} solid observations are already in use.</p>
        <button class="secondary compact" id="skip-claims">Set aside</button>
      </div>
      <div class="pending-grid">${pending.map(claimCard).join('')}</div>
    </section>
  `;
}

function conceptPendingClaims(concept) {
  const pending = pendingClaims();
  const hits = [];
  for (const beat of concept.structure || []) {
    for (const evidence of beat.evidence || []) {
      for (const claim of pending) {
        if (
          claim.asset_id === evidence.asset_id
          && claim.start_seconds < evidence.end_seconds
          && claim.end_seconds > evidence.start_seconds
          && !hits.some((item) => item.evidence_id === claim.evidence_id)
        ) {
          hits.push(claim);
        }
      }
    }
  }
  return hits;
}

function confirmStorySection(project) {
  const concept = (project.concepts || []).find(
    (item) => item.concept_id === state.pendingStory
  );
  if (!concept) { state.pendingStory = null; return ''; }
  const claims = conceptPendingClaims(concept);
  return `
    <section>
      <div class="section-header">
        <div><span class="eyebrow">One quick check</span>
        <h2>"${escapeHtml(concept.title)}" wants ${claims.length} unchecked moment${claims.length === 1 ? '' : 's'}</h2></div>
        <p>Confirm the ones that are true; anything you reject or leave unchecked is
        cut around automatically. Then continue.</p>
      </div>
      <div class="pending-grid">${claims.map(claimCard).join('')}</div>
      <div class="confirm-actions">
        <button class="ghost" id="cancel-confirm">← Back to stories</button>
        <button class="primary" id="continue-story">Continue — make the video</button>
      </div>
    </section>
  `;
}

function advancedSection(project) {
  const media = project.inventory?.assets || [];
  const steps = [
    { id: 'analyze-visual', label: 'Re-analyze footage' },
    { id: 'analyze-speech', label: 'Re-transcribe speech' },
    { id: 'generate-concepts', label: 'Regenerate story ideas' },
    { id: 'render', label: 'Re-render preview' },
    { id: 'exports', label: 'Rebuild editor files' },
  ];
  return `
    <details class="advanced">
      <summary>Advanced — clips, evidence, and manual pipeline steps</summary>
      <div class="pipeline-grid">
        ${steps.map((step) => `
          <button class="pipeline-step" data-pipeline="${step.id}">
            <strong>${escapeHtml(step.label)}</strong>
          </button>
        `).join('')}
      </div>
      <div class="pipeline-grid">
        <button class="pipeline-step" id="opentake-place">
          <strong>Enviar a OpenTake</strong>
          <span>Coloca el corte en la línea de tiempo abierta (la reemplaza)</span>
        </button>
        <button class="pipeline-step" id="opentake-sync">
          <strong>Traer cambios de OpenTake</strong>
          <span>Lee la línea de tiempo y muestra qué cambiaría en el plan</span>
        </button>
      </div>
      <div id="opentake-diff"></div>
      <div class="pipeline-grid">
        <button class="pipeline-step" id="clone-project">
          <strong>Duplicate vlog</strong>
          <span>Same clips, shared analysis, fresh story</span>
        </button>
        <button class="pipeline-step" id="reset-keep">
          <strong>Start over</strong>
          <span>Back to step 1, analysis kept (fast, free)</span>
        </button>
        <button class="pipeline-step" id="reset-full">
          <strong>Start from zero</strong>
          <span>Also re-analyzes footage (slow, costs a bit)</span>
        </button>
        <button class="pipeline-step danger" id="delete-project">
          <strong>Delete vlog</strong>
          <span>Removes this project; your clips stay</span>
        </button>
      </div>
      <label class="add-clips">
        Add clips or voiceover recordings to this vlog
        <input type="file" id="add-clips-input" multiple accept="video/*,image/*,audio/*" />
      </label>
      <div class="media-grid">
        ${media.map((asset) => {
          const thumbnail = asset.thumbnail_url
            ? `<img loading="lazy" src="${escapeHtml(asset.thumbnail_url)}" alt="" />`
            : '<div class="video-placeholder">No preview</div>';
          return `
            <article class="media-card">
              <div class="media-thumb">${thumbnail}
                <button class="remove-asset" data-remove-asset="${escapeHtml(asset.asset_id)}" title="Quitar del proyecto y borrar el archivo de la laptop">✕</button>
              </div>
              <div class="media-info">
                <strong title="${escapeHtml(asset.filename)}">${escapeHtml(asset.filename)}</strong>
                <span>${asset.duration_seconds ? `${Number(asset.duration_seconds).toFixed(0)}s` : 'still'}</span>
              </div>
            </article>
          `;
        }).join('')}
      </div>
    </details>
  `;
}

/* ------------------------------------------------------------------ */
/* Main render                                                         */

function renderProject() {
  const project = state.activeProject;
  if (!project) return;
  const phase = phaseOf(project);
  $('#project-title').textContent = project.name;
  $('#project-status').textContent = state.busy ? 'Working…' : (PHASE_LABEL[phase] || project.status.replaceAll('_', ' '));
  renderProjectList();

  let main;
  if (state.busy) {
    main = busyCard();
  } else if (state.pendingStory) {
    main = confirmStorySection(project) + advancedSection(project);
  } else {
    main = phase === 'start' ? startSection(project)
      : phase === 'pick' ? pickSection(project)
      : resultSection(project);
    main += needsCheckSection();
    main += advancedSection(project);
  }

  $('#project-view').classList.remove('loading');
  $('#project-view').innerHTML = main;

  $('#create-vlog')?.addEventListener('click', createVlog);
  $('#more-ideas-form')?.addEventListener('submit', regenerateIdeas);
  document.querySelectorAll('[data-keep-story]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleKeptStory(button.dataset.keepStory);
    });
  });
  $('#back-to-result')?.addEventListener('click', () => { state.forcePick = false; renderProject(); });
  $('#change-story')?.addEventListener('click', () => { state.forcePick = true; renderProject(); });
  $('#prepare-export')?.addEventListener('click', prepareExport);
  $('#revision-form')?.addEventListener('submit', submitRevision);
  if ($('#clip-score-list')) loadClipScores();
  document.querySelectorAll('[data-make-story]').forEach((button) => {
    button.addEventListener('click', () => startStory(button.dataset.makeStory));
  });
  $('#cancel-confirm')?.addEventListener('click', () => {
    state.pendingStory = null;
    renderProject();
  });
  $('#continue-story')?.addEventListener('click', () => {
    const conceptId = state.pendingStory;
    state.pendingStory = null;
    makeStory(conceptId);
  });
  document.querySelectorAll('[data-review-action]').forEach((button) => {
    button.addEventListener('click', () => reviewClaim(button));
  });
  document.querySelectorAll('[data-pipeline]').forEach((button) => {
    button.addEventListener('click', () => runAdvancedStep(button.dataset.pipeline, button));
  $('#opentake-place')?.addEventListener('click', openTakePlace);
  $('#opentake-sync')?.addEventListener('click', openTakeSyncPreview);
  });
  $('#delete-project')?.addEventListener('click', deleteProject);
  $('#clone-project')?.addEventListener('click', cloneProject);
  $('#add-clips-input')?.addEventListener('change', addClipsToProject);
  document.querySelectorAll('[data-remove-asset]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      removeAsset(button.dataset.removeAsset);
    });
  });
  $('#reset-keep')?.addEventListener('click', () => resetProject(true));
  $('#reset-full')?.addEventListener('click', () => resetProject(false));
  $('#see-new-ideas')?.addEventListener('click', () => {
    state.forcePick = true;
    renderProject();
  });
  $('#skip-claims')?.addEventListener('click', () => {
    localStorage.removeItem(`showClaims:${state.activeProjectId}`);
    renderProject();
  });
  $('#unskip-claims')?.addEventListener('click', () => {
    localStorage.setItem(`showClaims:${state.activeProjectId}`, '1');
    renderProject();
  });
}

async function addClipsToProject(event) {
  const files = [...event.currentTarget.files].filter((file) => file.size > 0);
  if (!files.length) return;
  try {
    notice(`Subiendo ${files.length} archivo${files.length === 1 ? '' : 's'}…`);
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    const response = await fetch(`/api/projects/${state.activeProjectId}/uploads`, {
      method: 'POST', body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Upload failed (${response.status})`);
    notice(`${payload.added.length} archivo(s) agregados. Re-analiza para incluirlos en historias.`);
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
  }
}

async function removeAsset(assetId) {
  if (!window.confirm(
    '¿Quitar este clip y BORRAR el archivo de la carpeta en la laptop? '
    + '(El original en tu iPhone no se toca.)'
  )) return;
  try {
    await api(`/api/projects/${state.activeProjectId}/assets/${assetId}?delete_file=true`, { method: 'DELETE' });
    notice('Clip quitado y archivo borrado de la laptop.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
  }
}

async function cloneProject() {
  const name = window.prompt(
    'Nombre del nuevo vlog (mismos clips, análisis compartido, historia desde cero):',
    `${state.activeProject?.name || 'Vlog'} — v2`
  );
  if (!name) return;
  try {
    const clone = await api(`/api/projects/${state.activeProjectId}/clone`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    notice('Vlog duplicado — análisis compartido, listo para ideas nuevas.');
    await refreshProjects();
    await loadProject(clone.project_id);
  } catch (error) {
    notice(error.message, true);
  }
}

async function resetProject(keepAnalysis) {
  const confirmed = window.confirm(
    keepAnalysis
      ? 'Volver al paso 1 conservando el análisis (rápido y sin costo). Se borran ideas, corte y renders. ¿Continuar?'
      : 'Volver al paso 1 borrando TODO, incluido el análisis (re-analizar cuesta tiempo y unos dólares). ¿Continuar?'
  );
  if (!confirmed) return;
  try {
    await api(`/api/projects/${state.activeProjectId}/reset`, {
      method: 'POST',
      body: JSON.stringify({ keep_analysis: keepAnalysis }),
    });
    state.forcePick = false;
    localStorage.removeItem(`keptStories:${state.activeProjectId}`);
    notice('Proyecto reiniciado — paso 1.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
  }
}

async function deleteProject() {
  const project = state.activeProject;
  if (!project) return;
  const confirmed = window.confirm(
    `Delete "${project.name}"? The AI analysis, cut, and renders are removed. ` +
    'Your original clips are NOT touched.'
  );
  if (!confirmed) return;
  try {
    await api(`/api/projects/${project.project_id}`, { method: 'DELETE' });
    notice('Vlog deleted. Your clips are untouched.');
    await refreshProjects();
    const next = state.projects[0];
    if (next) await loadProject(next.project_id);
    else $('#project-view').innerHTML = '<div class="empty-state">No vlogs yet — add your clips.</div>';
  } catch (error) {
    notice(error.message, true);
  }
}

/* Folder picker in the new-vlog dialog */
async function browseTo(path) {
  const input = document.querySelector('[name="source_directory"]');
  const container = $('#folder-browser');
  try {
    const listing = await api(`/api/browse?path=${encodeURIComponent(path)}`);
    if (listing.media_count > 0) input.value = listing.path;
    container.innerHTML = `
      <div class="browser-head">
        <span>/${escapeHtml(listing.path)}</span>
        <span class="muted">${listing.media_count ? `${listing.media_count} media file${listing.media_count === 1 ? '' : 's'} here` : ''}</span>
      </div>
      ${listing.parent !== null ? `<button type="button" class="browser-item up" data-browse="${escapeHtml(listing.parent)}">← back</button>` : ''}
      ${listing.directories.map((dir) => `
        <button type="button" class="browser-item" data-browse="${escapeHtml(dir.path)}">
          📁 ${escapeHtml(dir.name)}
          ${dir.media_count ? `<span class="count">${dir.media_count} clips</span>` : ''}
        </button>
      `).join('') || '<p class="muted">No subfolders.</p>'}
    `;
    container.querySelectorAll('[data-browse]').forEach((button) => {
      button.addEventListener('click', () => browseTo(button.dataset.browse));
    });
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ */
/* Actions                                                             */

function setBusy(title, steps, current) {
  state.busy = { title, steps, current };
  renderProject();
}

async function pollJob(jobId) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === 'completed') return job;
    if (job.status === 'failed') throw new Error(job.error || 'Something went wrong');
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

async function runStep(path, body) {
  const result = await api(`/api/projects/${state.activeProjectId}/${path}`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  });
  if (result.job_id) return pollJob(result.job_id);
  return result;
}

async function createVlog() {
  const steps = [];
  if (!hasRun('owned-live-visual')) steps.push({ label: 'Watching your footage (a few minutes)', path: 'analysis/visual' });
  if (!hasRun('local-asr')) steps.push({ label: 'Listening for speech (stays local)', path: 'analysis/speech' });
  steps.push({ label: 'Writing story ideas', path: 'concepts' });
  try {
    for (let index = 0; index < steps.length; index += 1) {
      setBusy('Creating your vlog', steps.map((step) => step.label), index);
      await runStep(steps[index].path);
    }
    state.busy = null;
    state.forcePick = false;
    notice('Story ideas are ready — pick one.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
  }
}

function startStory(conceptId) {
  const concept = (state.activeProject.concepts || []).find(
    (item) => item.concept_id === conceptId
  );
  if (concept && conceptPendingClaims(concept).length) {
    state.pendingStory = conceptId;
    renderProject();
    return;
  }
  makeStory(conceptId);
}

async function makeStory(conceptId) {
  const steps = ['Locking in the story', 'Cutting the video', 'Rendering the preview', 'Preparing editor files'];
  try {
    setBusy('Making your vlog', steps, 0);
    await api(`/api/projects/${state.activeProjectId}/selection`, {
      method: 'POST', body: JSON.stringify({ concept_id: conceptId }),
    });
    setBusy('Making your vlog', steps, 1);
    await api(`/api/projects/${state.activeProjectId}/plan`, {
      method: 'POST', body: JSON.stringify({ concept_id: conceptId }),
    });
    setBusy('Making your vlog', steps, 2);
    await runStep('render');
    setBusy('Making your vlog', steps, 3);
    await runStep('exports', { include_proxies: true });
    state.busy = null;
    state.forcePick = false;
    notice('Your vlog is ready — watch it below.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
  }
}

async function regenerateIdeas(event) {
  event?.preventDefault?.();
  const guidance = event?.currentTarget
    ? new FormData(event.currentTarget).get('guidance')?.toString().trim()
    : '';
  const kept = [...keptStoryIds()];
  try {
    setBusy('Thinking of new angles', ['Writing story ideas'], 0);
    await runStep('concepts', {
      guidance: guidance || null,
      keep_concept_ids: kept.length ? kept : null,
    });
    state.busy = null;
    notice(kept.length ? `Fresh ideas below (kept ${kept.length}).` : 'Fresh ideas below.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
  }
}

async function loadClipScores() {
  try {
    const payload = await api(`/api/projects/${state.activeProjectId}/clip-scores`);
    const container = $('#clip-score-list');
    if (!container) return;
    container.innerHTML = payload.clips.map((clip) => `
      <div class="clip-score verdict-${escapeHtml(clip.verdict.replaceAll(' ', '-'))}">
        <img loading="lazy" src="${frameUrl(clip.asset_id, clip.duration_seconds / 2)}" alt="" />
        <div class="clip-score-body">
          <div class="clip-score-head">
            <strong>${escapeHtml(clip.filename)}</strong>
            <span class="score-badge">${clip.score}</span>
          </div>
          <span class="verdict-label">${escapeHtml(clip.verdict)}</span>
          <span class="muted">${escapeHtml(clip.reason)}</span>
        </div>
      </div>
    `).join('');
  } catch (error) {
    const container = $('#clip-score-list');
    if (container) container.textContent = error.message;
  }
}

async function submitRevision(event) {
  event.preventDefault();
  const instruction = new FormData(event.currentTarget).get('instruction')?.toString().trim();
  if (!instruction) return;
  try {
    setBusy('Changing your vlog', ['Re-cutting to your instruction', 'Rendering the new preview'], 0);
    const revision = await runStep('plan/revise', { instruction });
    setBusy('Changing your vlog', ['Re-cutting to your instruction', 'Rendering the new preview'], 1);
    await runStep('render');
    state.busy = null;
    notice(revision.result?.revision_note || 'Done — new cut below.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
  }
}

async function prepareExport() {
  try {
    setBusy('Preparing editor files', ['Exporting timeline + transcoding proxies'], 0);
    await runStep('exports', { include_proxies: true });
    state.busy = null;
    notice('DaVinci files are ready.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
  }
}

async function reviewClaim(button) {
  const runKey = button.dataset.reviewRun;
  const evidenceId = button.dataset.reviewId;
  let action = button.dataset.reviewAction;
  let caption = null;
  if (action === 'edit') {
    const run = state.runs.find((item) => item.run_key === runKey);
    const observation = run?.observations.find((item) => item.evidence_id === evidenceId);
    caption = window.prompt('Correct the description:', observation?.caption || '');
    if (caption === null) return;
    action = 'approve';
  }
  button.disabled = true;
  try {
    await api(`/api/projects/${state.activeProjectId}/analysis/runs/${runKey}/reviews`, {
      method: 'POST',
      body: JSON.stringify({ evidence_id: evidenceId, action, caption }),
    });
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
}

const ADVANCED_CALLS = {
  'analyze-visual': 'analysis/visual',
  'analyze-speech': 'analysis/speech',
  'generate-concepts': 'concepts',
  render: 'render',
  exports: 'exports',
};

async function openTakePlace() {
  const project = state.activeProject;
  if (!project) return;
  const sure = window.confirm(
    'Esto REEMPLAZA la línea de tiempo del proyecto abierto en OpenTake con el corte actual. ¿Continuar?'
  );
  if (!sure) return;
  try {
    const summary = await api(`/api/projects/${project.project_id}/opentake/place`, { method: 'POST' });
    notice(`Colocado en OpenTake: ${summary.placed_clips} clips (${summary.total_frames} frames).`);
  } catch (error) {
    notice(error.message, true);
  }
}

async function openTakeSyncPreview() {
  const project = state.activeProject;
  if (!project) return;
  const box = $('#opentake-diff');
  try {
    const preview = await api(`/api/projects/${project.project_id}/opentake/sync`, { method: 'POST' });
    const warn = preview.staleness
      ? `<p class="notice error">⚠ ${escapeHtml(preview.staleness.advice)} (guardado: ${preview.staleness.saved_clips.video} clips, interfaz: ${preview.staleness.live_clips.video})</p>`
      : '';
    if (!preview.changes.length) {
      box.innerHTML = warn + '<p class="notice">La línea de tiempo coincide con el plan — nada que traer.</p>';
      return;
    }
    box.innerHTML = warn + `
      <div class="sync-diff">
        <p><strong>${preview.changes.length}</strong> cambio(s) en OpenTake — nueva duración ${preview.duration_seconds}s
        (${preview.unchanged_count} escenas sin cambios):</p>
        <ul>${preview.changes.map((c) => `<li><code>${escapeHtml(c.kind)}</code> ${escapeHtml(c.event_id || '')} — ${escapeHtml(c.detail || '')}</li>`).join('')}</ul>
        <button class="pipeline-step" id="opentake-sync-apply"><strong>Aplicar al plan</strong>
        <span>Archiva la revisión actual y usa estos cambios al renderizar</span></button>
      </div>`;
    $('#opentake-sync-apply')?.addEventListener('click', async () => {
      try {
        const applied = await api(`/api/projects/${project.project_id}/opentake/sync/apply`, { method: 'POST' });
        notice(`Plan actualizado a la revisión ${applied.revision}. Re-renderiza para ver el resultado.`);
        box.innerHTML = '';
        await loadProject(project.project_id);
      } catch (error) {
        notice(error.message, true);
      }
    });
  } catch (error) {
    notice(error.message, true);
  }
}

async function runAdvancedStep(stepId, button) {
  const path = ADVANCED_CALLS[stepId];
  if (!path) return;
  button.disabled = true;
  try {
    notice('Running…');
    await runStep(path, path === 'exports' ? { include_proxies: true } : {});
    notice('Done.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* Loading                                                             */

const JOB_LABELS = {
  visual_analysis: 'Watching your footage',
  speech_analysis: 'Listening for speech',
  concept_generation: 'Writing story ideas',
  render: 'Rendering the preview',
  editable_exports: 'Preparing editor files',
  plan_revision: 'Re-cutting to your instruction',
};

async function loadProject(projectId) {
  state.activeProjectId = projectId;
  state.runs = [];
  renderProjectList();
  if (!state.busy) $('#project-view').innerHTML = '<div class="empty-state">Loading…</div>';
  try {
    state.activeProject = await api(`/api/projects/${projectId}`);
    state.runs = await Promise.all(
      (state.activeProject.provider_runs || []).map(async (run) => ({
        ...(await api(run.detail_url)),
        run_key: run.run_key,
      }))
    );
    // Reconnect to jobs still running server-side (e.g. after a reload)
    // so the working state is visible and buttons are not double-clicked.
    if (!state.busy) {
      const jobs = (await api('/api/jobs')).jobs.filter(
        (job) => job.project_id === projectId && ['queued', 'running'].includes(job.status)
      );
      if (jobs.length) {
        setBusy(
          'Still working — picking up where it left off',
          jobs.map((job) => JOB_LABELS[job.kind] || job.kind),
          0,
        );
        Promise.allSettled(jobs.map((job) => pollJob(job.job_id))).then(async () => {
          state.busy = null;
          notice('Done.');
          await loadProject(projectId);
        });
        return;
      }
    }
    renderProject();
  } catch (error) {
    notice(error.message, true);
  }
}

async function refreshProjects() {
  const payload = await api('/api/projects');
  state.projects = payload.projects;
  renderProjectList();
  refreshDriveInbox();
}

async function refreshDriveInbox() {
  let banner = $('#drive-inbox');
  try {
    const payload = await api('/api/drive/inbox');
    const waiting = (payload.folders || []).filter((folder) => !folder.imported);
    if (!waiting.length) { banner?.remove(); return; }
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'drive-inbox';
      banner.className = 'banner';
      document.querySelector('.workspace')?.prepend(banner);
    }
    banner.innerHTML = `
      <span>☁️ ${waiting.length} vlog${waiting.length === 1 ? '' : 's'} en tu Drive VlogInbox:</span>
      ${waiting.slice(0, 3).map((folder) => `
        <button class="primary compact" data-drive-import="${escapeHtml(folder.name)}">
          Importar «${escapeHtml(folder.name)}»
        </button>`).join('')}
    `;
    banner.querySelectorAll('[data-drive-import]').forEach((button) => {
      button.addEventListener('click', () => importFromDrive(button.dataset.driveImport));
    });
  } catch { banner?.remove(); /* rclone not configured or offline */ }
}

async function importFromDrive(folder) {
  try {
    setBusy(`Importando «${folder}» desde Drive`, ['Descargando clips', 'Indexando'], 0);
    const job = await api('/api/drive/import', {
      method: 'POST',
      body: JSON.stringify({ folder }),
    });
    const done = await pollJob(job.job_id);
    state.busy = null;
    notice(`«${folder}» importado.`);
    await refreshProjects();
    if (done.result?.project_id) await loadProject(done.result.project_id);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await refreshProjects();
  }
}

async function createProject(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const uploads = form.getAll('files').filter((file) => file && file.size > 0);
  const submit = formElement.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    let project;
    if (uploads.length) {
      const totalMb = uploads.reduce((sum, file) => sum + file.size, 0) / 1e6;
      const body = new FormData();
      body.append('name', form.get('name'));
      body.append('prompt', form.get('prompt') || '');
      uploads.forEach((file) => body.append('files', file));
      project = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/uploads');
        xhr.upload.onprogress = (progress) => {
          if (progress.lengthComputable) {
            const pct = Math.round((progress.loaded / progress.total) * 100);
            submit.textContent = `Subiendo ${uploads.length} clips… ${pct}% de ${totalMb.toFixed(0)} MB`;
          }
        };
        xhr.onload = () => {
          let payload = {};
          try { payload = JSON.parse(xhr.responseText); } catch {}
          if (xhr.status >= 200 && xhr.status < 300) {
            submit.textContent = 'Indexando…';
            resolve(payload);
          } else {
            reject(new Error(payload.detail || `Upload failed (${xhr.status})`));
          }
        };
        xhr.onerror = () => reject(new Error('Upload failed — check the WiFi connection'));
        xhr.send(body);
      });
    } else {
      if (!form.get('source_directory')) throw new Error('Upload files or pick a folder.');
      submit.textContent = 'Indexing…';
      project = await api('/api/projects', {
        method: 'POST',
        body: JSON.stringify({
          name: form.get('name'),
          source_directory: form.get('source_directory'),
          prompt: form.get('prompt') || '',
        }),
      });
    }
    $('#new-project-dialog').close();
    await refreshProjects();
    await loadProject(project.project_id);
    notice('Clips indexed. Hit "Create my vlog" when ready.');
  } catch (error) {
    notice(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = 'Add my clips';
  }
}

async function initialize() {
  try {
    const [status, projects] = await Promise.all([api('/api/status'), api('/api/projects')]);
    state.status = status;
    state.projects = projects.projects;
    renderCapabilities();
    if (state.projects[0]) await loadProject(state.projects[0].project_id);
    else $('#project-view').innerHTML = '<div class="empty-state">No vlogs yet — add your clips.</div>';
  } catch (error) {
    notice(error.message, true);
  }
}

/* Receiver-side upload visibility: when a phone (or the Shortcut) is
   sending media, every open browser shows the incoming transfer live. */
let uploadsWereActive = false;
window.setInterval(async () => {
  try {
    const payload = await api('/api/uploads/active');
    const uploads = payload.uploads || [];
    let banner = $('#incoming-uploads');
    if (uploads.length) {
      uploadsWereActive = true;
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'incoming-uploads';
        banner.className = 'banner';
        document.querySelector('.workspace')?.prepend(banner);
      }
      banner.innerHTML = uploads.map((upload) => {
        const pct = upload.total ? Math.round((upload.received / upload.total) * 100) : 0;
        const mb = (upload.received / 1e6).toFixed(0);
        return `<span>📥 Recibiendo ${escapeHtml(upload.label)} — ${pct}% (${mb} MB)</span>`;
      }).join('');
    } else if (banner) {
      banner.remove();
      if (uploadsWereActive) {
        uploadsWereActive = false;
        notice('Subida completada.');
        await refreshProjects();
      }
    }
  } catch { /* app may be restarting; ignore */ }
}, 3000);

const dialog = $('#new-project-dialog');
$('#new-project-button').addEventListener('click', () => {
  dialog.showModal();
  browseTo('');
});
$('#upload-files')?.addEventListener('change', (event) => {
  const files = [...event.currentTarget.files];
  const totalMb = files.reduce((sum, file) => sum + file.size, 0) / 1e6;
  notice(files.length
    ? `${files.length} archivo(s) listos (${totalMb.toFixed(0)} MB) — pon nombre y pulsa "Add my clips".`
    : 'Selección vacía.');
});
$('#close-dialog').addEventListener('click', () => dialog.close());
$('#cancel-dialog').addEventListener('click', () => dialog.close());
$('#new-project-form').addEventListener('submit', createProject);

initialize();
