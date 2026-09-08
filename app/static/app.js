/* Vlog Studio — UX Architecture v2.
   Four workspaces (Historia / Edición / Metraje / Publicar) + Diagnóstico.
   One AI editor input; pipeline internals live behind ⋯ → Diagnóstico. */

const state = {
  status: null,
  projects: [],
  activeProjectId: null,
  activeProject: null,
  runs: [],
  busy: null,            // {title, steps: [..], current: index}
  pendingStory: null,
  workspace: null,       // 'story' | 'edit' | 'media' | 'publish' | 'diagnostics'
  mediaFilter: 'all',
  loadGeneration: 0,     // stale loadProject responses are dropped
  chat: null,            // {projectId, messages, loaded, sending}
  chatDrafts: {},        // unsent chat text per project — survives workspace re-renders
  pendingVoiceover: null,// draft awaiting a recorded note for "grabar y colocar"
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
  if (!response.ok) throw new Error(payload.detail || `La petición falló (${response.status})`);
  return payload;
}

let noticeTimer = null;
function notice(message, error = false) {
  const element = $('#notice');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.setAttribute('aria-live', error ? 'assertive' : 'polite');
  element.classList.remove('hidden');
  if (noticeTimer) { window.clearTimeout(noticeTimer); noticeTimer = null; }
  if (!error) noticeTimer = window.setTimeout(() => element.classList.add('hidden'), 6000);
}

// Ask once for permission to notify — called when a long op starts, so the
// prompt appears in context (never on load).
function requestNotifyPermission() {
  try {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  } catch { /* unsupported */ }
}

// Notify the outcome of a long op. Fires a system notification when the app is
// backgrounded (so the phone buzzes while you're elsewhere) and permission was
// granted; always also shows the in-app banner. No push server — Web
// Notifications only, per the design's MVP (polling/current APIs are enough).
function notify(message, { error = false, title = 'Vlog Studio' } = {}) {
  notice(message, error);
  try {
    if (
      'Notification' in window &&
      Notification.permission === 'granted' &&
      document.visibilityState === 'hidden'
    ) {
      const n = new Notification(title, {
        body: message, icon: '/icons/icon-192.png', tag: 'vlog-studio',
      });
      n.onclick = () => { window.focus(); n.close(); };
    }
  } catch { /* unsupported */ }
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
  if (project.plan) return 'result';
  if ((project.concepts || []).length) return 'pick';
  return 'start';
}

function defaultWorkspace(project) {
  return phaseOf(project) === 'result' ? 'edit' : 'story';
}

function usedAssetIds(project) {
  const ids = new Set();
  for (const track of project.plan?.tracks || []) {
    for (const event of track.events || []) {
      if (event.asset_id) ids.add(event.asset_id);
    }
  }
  return ids;
}

const PHASE_LABEL = {
  start: 'Paso 1 · Crear',
  pick: 'Paso 2 · Elegir historia',
  result: 'Listo para editar',
};

/* ------------------------------------------------------------------ */
/* Sidebar                                                             */

function renderProjectList() {
  $('#project-list').innerHTML = state.projects.map((project) => `
    <button class="project-button ${project.project_id === state.activeProjectId ? 'active' : ''}" data-project-id="${escapeHtml(project.project_id)}">
      <span class="project-dot ${project.has_plan || project.status === 'ready' ? 'ready' : ''}"></span>
      <span class="project-copy">
        <strong>${escapeHtml(project.name)}</strong>
        <span>${project.asset_count} clips${project.has_plan ? ' · editado' : ''}</span>
      </span>
    </button>
  `).join('');
  document.querySelectorAll('[data-project-id]').forEach((button) => {
    // While a multi-step flow runs, switching projects could cross async
    // work between projects — block it (cross-review UX blocker 1).
    button.disabled = Boolean(state.busy) && button.dataset.projectId !== state.activeProjectId;
    button.addEventListener('click', () => loadProject(button.dataset.projectId));
  });
}

function renderSystemDot() {
  const capabilities = state.status?.capabilities;
  const dot = $('#system-dot');
  if (!capabilities || !dot) return;
  const visual = capabilities.visual?.find((item) => item.id === 'owned-live-visual');
  const speech = capabilities.speech?.find((item) => item.id === 'faster-whisper');
  const ready = Boolean(visual?.ready && speech?.ready && capabilities.render?.ready);
  dot.classList.toggle('ok', ready);
  $('#system-dot-label').textContent = ready ? 'Sistema listo' : 'Revisar sistema';
}

/* ------------------------------------------------------------------ */
/* Topbar: tabs + overflow                                             */

const WORKSPACES = [
  { id: 'story', label: 'Historia', needsPlan: false },
  { id: 'edit', label: 'Edición', needsPlan: true },
  { id: 'media', label: 'Metraje', needsPlan: false },
  { id: 'publish', label: 'Publicar', needsPlan: true },
];

function renderTabs(project) {
  const hasPlan = Boolean(project?.plan);
  $('#workspace-tabs').innerHTML = WORKSPACES.map((ws) => `
    <button class="tab ${state.workspace === ws.id ? 'active' : ''}" role="tab"
      aria-selected="${state.workspace === ws.id}"
      data-workspace="${ws.id}" ${ws.needsPlan && !hasPlan ? 'disabled title="Primero crea un corte"' : ''}>
      ${ws.label}
    </button>
  `).join('');
  document.querySelectorAll('[data-workspace]').forEach((button) => {
    button.addEventListener('click', () => {
      state.workspace = button.dataset.workspace;
      localStorage.setItem(`workspace:${state.activeProjectId}`, state.workspace);
      renderProject();
    });
  });
}

function renderOverflow() {
  const menu = $('#overflow-menu');
  menu.innerHTML = `
    <button data-overflow="clone">Duplicar vlog</button>
    <button data-overflow="diagnostics">Diagnóstico</button>
    <button data-overflow="reset-keep">Reiniciar (conserva análisis)</button>
    <button data-overflow="reset-full">Reiniciar desde cero</button>
    <button data-overflow="delete" class="danger">Eliminar vlog</button>
  `;
  menu.querySelectorAll('[data-overflow]').forEach((button) => {
    button.addEventListener('click', () => {
      menu.classList.add('hidden');
      const action = button.dataset.overflow;
      if (action === 'clone') cloneProject();
      else if (action === 'diagnostics') { state.workspace = 'diagnostics'; renderProject(); }
      else if (action === 'reset-keep') resetProject(true);
      else if (action === 'reset-full') resetProject(false);
      else if (action === 'delete') deleteProject();
    });
  });
}

/* ------------------------------------------------------------------ */
/* Busy card                                                           */

function busyCard() {
  const { title, steps, current, startedAt } = state.busy;
  const elapsed = startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;
  const clock = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`;
  const progress = state.busy.progress;
  return `
    <section class="card busy-card">
      <div class="busy-head">
        <h2>${escapeHtml(title)}</h2>
        <span class="busy-clock" id="busy-clock">⏱ ${clock}</span>
      </div>
      <ol class="step-list">
        ${steps.map((step, index) => `
          <li class="${index < current ? 'done' : index === current ? 'active' : ''}">
            <i></i>${escapeHtml(step)}
            ${index === current && progress ? `
              <span class="step-progress">
                <span class="bar"><span style="width:${Math.round(progress.done / progress.total * 100)}%"></span></span>
                ${progress.done}/${progress.total}
              </span>` : ''}
          </li>
        `).join('')}
      </ol>
      <p class="muted">Puedes dejar esta página abierta. El análisis del metraje toma
      unos minutos la primera vez; después queda cacheado.</p>
    </section>
  `;
}

// live busy-card telemetry: chronometer every second, real analysis
// progress (shots/clips completed) every 3s while a project is busy
let busyTicker = null;
function startBusyTicker() {
  clearInterval(busyTicker);
  let tick = 0;
  busyTicker = setInterval(async () => {
    if (!state.busy) { clearInterval(busyTicker); busyTicker = null; return; }
    tick += 1;
    const clock = $('#busy-clock');
    if (clock && state.busy.startedAt) {
      const s = Math.floor((Date.now() - state.busy.startedAt) / 1000);
      clock.textContent = `⏱ ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }
    if (tick % 3 === 0 && state.activeProjectId) {
      try {
        const progress = await api(`/api/projects/${state.activeProjectId}/analysis-progress`);
        const changed = JSON.stringify(progress || {}) !==
          JSON.stringify(state.busy.progress || {});
        state.busy.progress = progress?.total ? progress : null;
        if (changed) renderProject();
      } catch { /* transient */ }
    }
  }, 1000);
}

/* ------------------------------------------------------------------ */
/* HISTORIA                                                            */

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

function frameUrl(assetId, seconds) {
  return `/api/projects/${escapeHtml(state.activeProjectId)}/frames/${escapeHtml(assetId)}?t=${seconds.toFixed(2)}`;
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

function storyCard(concept, isCurrent) {
  const missing = concept.missing_shots || [];
  const required = missing.filter((shot) => shot.priority === 'required').length;
  const beats = (concept.structure || []).length;
  const unchecked = conceptPendingClaims(concept).length;
  const dubious = (concept.structure || []).reduce((total, beat) =>
    total + [...(beat.evidence || []), ...(beat.cutaways || [])]
      .filter((item) => item.needs_review).length, 0);
  const kept = keptStoryIds().has(concept.concept_id);
  const beatThumbs = (concept.structure || []).slice(0, 6).map((beat) => {
    const evidence = (beat.evidence || [])[0];
    if (!evidence) return '';
    const mid = (evidence.start_seconds + evidence.end_seconds) / 2;
    return `<img loading="lazy" src="${frameUrl(evidence.asset_id, mid)}" alt="" title="${escapeHtml(beat.purpose)}" />`;
  }).join('');
  return `
    <article class="concept-card story-card ${kept ? 'kept' : ''} ${isCurrent ? 'current' : ''}">
      <div class="concept-top">
        <span class="eyebrow">${concept.target_duration_seconds}s · ${beats} escenas${unchecked ? ` · pregunta por ${unchecked} momento${unchecked === 1 ? '' : 's'}` : ''}${dubious ? ` · ⚠ ${dubious} cita${dubious === 1 ? '' : 's'} por verificar` : ''}</span>
        <button class="ghost keep-toggle" data-keep-story="${escapeHtml(concept.concept_id)}" title="Las historias marcadas ★ sobreviven cuando pides ideas nuevas">${kept ? '★' : '☆'}</button>
      </div>
      ${beatThumbs ? `<div class="beat-thumbs">${beatThumbs}</div>` : ''}
      <h3>${escapeHtml(concept.title)}</h3>
      <p class="hook">${escapeHtml(concept.hook)}</p>
      ${(concept.weaknesses || []).length ? `<p class="muted">Con honestidad: ${escapeHtml(concept.weaknesses[0])}</p>` : ''}
      ${missing.length ? `
        <details>
          <summary>${missing.length} toma${missing.length === 1 ? '' : 's'} que valdría grabar${required ? ` (${required} importante${required === 1 ? '' : 's'})` : ''}</summary>
          <ul>${missing.map((shot) => `
            <li class="missing-shot"><span class="priority ${escapeHtml(shot.priority)}">${escapeHtml(shot.priority)}</span> ${escapeHtml(shot.recording_instruction)}</li>
          `).join('')}</ul>
        </details>` : ''}
      <div class="concept-footer">
        ${isCurrent
          ? '<span class="current-label">✓ Historia actual</span>'
          : `<button class="primary" data-make-story="${escapeHtml(concept.concept_id)}">Hacer esta</button>`}
      </div>
    </article>
  `;
}

function storyWorkspace(project) {
  const phase = phaseOf(project);
  if (phase === 'start') {
    const analyzed = hasRun('owned-live-visual');
    return `
      <section class="card start-card">
        <span class="eyebrow">${escapeHtml(project.prompt || 'Sin nota — el editor apuntará a un vlog diario conciso.')}</span>
        <h2>${project.inventory?.assets?.length || 0} clips listos. Hagamos un vlog.</h2>
        <p>Un click lo hace todo: el editor mira tu metraje, escucha el habla y escribe
        ideas de historia basadas en lo que realmente hay en video. Tú eliges la
        historia; nada se inventa.</p>
        <p class="muted">Los cuadros de tus clips se envían al modelo visual en la nube.
        El habla se transcribe localmente y nunca sale de esta máquina.</p>
        <button class="primary big" id="create-vlog">${analyzed ? 'Continuar — escribir ideas' : 'Crear mi vlog'}</button>
      </section>
      <div id="model-picker" class="model-picker-slot"></div>
      <div id="style-section"></div>
    `;
  }
  const keptCount = keptStoryIds().size;
  const currentId = project.plan?.concept_id;
  return `
    ${unanalyzedBanner(project)}
    <section>
      <div class="section-header">
        <div><span class="eyebrow">Historias</span><h2>El editor propone, tú decides</h2></div>
      </div>
      ${project.footage_summary ? `
        <p class="footage-summary">🎬 ${escapeHtml(project.footage_summary)}</p>` : ''}
      <div id="step-times"></div>
      <p class="muted">Cada escena cita momentos reales de tus clips. «Hacer esta» corta el
      video, renderiza la vista previa y prepara los archivos de editor. ¿No convence?
      Marca lo que valga (★), di qué quieres y pide ideas nuevas.</p>
      <div class="concept-grid">${(project.concepts || []).map((c) => storyCard(c, c.concept_id === currentId)).join('')}</div>
      <form id="more-ideas-form" class="revision-form">
        <textarea name="guidance" rows="2"
          placeholder="¿Qué quieres en su lugar? p. ej. enfócate en la demo del proyecto, más energía, menos de 40 segundos…"></textarea>
        <button type="submit" class="secondary">Ideas nuevas${keptCount ? ` (conservando ${keptCount} ★)` : ''}</button>
      </form>
      <p class="muted stateless-hint">Cada petición es independiente (no es un chat):
      describe la idea completa en un solo mensaje.</p>
      <div id="models-in-use"></div>
      <div id="style-section"></div>
    </section>
    ${needsCheckSection()}
  `;
}

async function loadStyleSection() {
  const box = $('#style-section');
  if (!box) return;
  try {
    const { styles } = await api('/api/styles');
    if (!styles.length) {
      box.innerHTML = `
        <p class="muted" style="margin-top:1rem">💡 ¿Quieres que las ideas sigan
        el estilo de un video que te gusta? Deja el video en la carpeta
        <code>references/</code> y analízalo desde Diagnóstico.</p>`;
      return;
    }
    let matches = [];
    let conditionedBy = null;
    try {
      const result = await api(`/api/projects/${state.activeProjectId}/style-matches`, { method: 'POST' });
      matches = result.matches;
      conditionedBy = result.concepts_conditioned_by;
    } catch { /* sin conceptos todavía */ }
    box.innerHTML = `
      <div class="section-header" style="margin-top:1.4rem">
        <div><span class="eyebrow">Estilos de referencia</span></div>
      </div>
      ${conditionedBy ? `
        <p class="muted">⚠ Las historias actuales se generaron CON un estilo,
        así que su compatibilidad con ese estilo mide obediencia, no ajuste
        real — para comparar estilos con neutralidad, regenera ideas sin
        estilo primero.</p>` : ''}
      <div class="style-row">
        ${styles.map((style) => {
          if (style.invalid) {
            return `
            <article class="card style-card">
              <strong>${escapeHtml(style.name)}</strong>
              <span class="muted">⚠ Este estilo quedó incompatible tras una
              actualización — re-analiza la referencia en Diagnóstico.</span>
              <button class="secondary compact" data-style-delete="${escapeHtml(style.style_id)}">Borrar</button>
            </article>`;
          }
          const best = matches.find((m) => m.style_id === style.style_id);
          const grammar = style.grammar || {};
          const cuts = Number(grammar.cuts_per_minute);
          const pace = Number.isFinite(cuts)
            ? (cuts === 0 ? 'toma continua' : `${cuts} cortes/min`) : '';
          const confidence = Number(style.confidence);
          return `
            <article class="card style-card">
              <strong>${escapeHtml(style.name)}</strong>
              <span class="muted">${escapeHtml((grammar.narrative_shape || []).slice(0, 5).join(' → ')) || 'sin forma detectada'}</span>
              <span class="muted">${pace}
                ${Number(grammar.broll_ratio) ? ` · ${Math.round(Number(grammar.broll_ratio) * 100)}% B-roll` : ''}</span>
              ${Number.isFinite(confidence) && confidence < 0.5
                ? '<span class="muted">⚠ confianza baja (pocas referencias)</span>' : ''}
              ${best ? `
                <span class="style-score" title="Puntaje heurístico de compatibilidad — no una probabilidad">Compatibilidad estimada: ${(best.score * 100).toFixed(0)}%</span>
                ${best.reasons.slice(0, 2).map((r) => `<span class="muted">✓ ${escapeHtml(r)}</span>`).join('')}
                ${best.missing.slice(0, 1).map((m) => `<span class="muted">⚠ ${escapeHtml(m)}</span>`).join('')}` : ''}
              <div class="style-actions">
                <button class="secondary compact" data-style-ideas="${escapeHtml(style.style_id)}">Ideas con este estilo</button>
                <button class="secondary compact" data-style-delete="${escapeHtml(style.style_id)}">Borrar</button>
              </div>
            </article>`;
        }).join('')}
      </div>`;
    box.querySelectorAll('[data-style-ideas]').forEach((button) => {
      button.addEventListener('click', () => regenerateWithStyle(button.dataset.styleIdeas));
    });
    box.querySelectorAll('[data-style-delete]').forEach((button) => {
      button.addEventListener('click', async () => {
        if (!window.confirm('¿Borrar este estilo? La referencia en references/ no se toca.')) return;
        try {
          await api(`/api/styles/${button.dataset.styleDelete}`, { method: 'DELETE' });
          loadStyleSection();
        } catch (error) { notice(error.message, true); }
      });
    });
  } catch { box.innerHTML = ''; }
}

async function regenerateWithStyle(styleId) {
  const projectId = state.activeProjectId;
  const kept = [...keptStoryIds()];
  try {
    setBusy('Pensando con el estilo de referencia', ['Escribiendo ideas con esa gramática'], 0);
    await runStep('concepts', {
      style_id: styleId,
      keep_concept_ids: kept.length ? kept : null,
    }, projectId);
    state.busy = null;
    notice('Ideas con el estilo de referencia listas.');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

/* ---- claim review (contextual, lives under Historia) ---- */

const FLAG_REASONS = {
  brand_or_product_claim: 'menciona una marca o producto — punto clásico de alucinación',
  intent_or_emotion_inference: 'adivina emociones o intención, que los cuadros no pueden probar',
  unverified_speech_claim: 'afirma que alguien habla sin transcripción que lo respalde',
  identity_or_continuity_inference: 'asume identidad o continuidad entre tomas',
  low_confidence_transcription: 'el audio era poco claro; la transcripción puede fallar',
};

function claimReason(observation) {
  const reasons = (observation.risk_flags || [])
    .map((flag) => FLAG_REASONS[flag])
    .filter(Boolean);
  const confidence = observation.model_confidence;
  if (confidence != null && confidence < 0.75) {
    reasons.push(`el propio modelo se dio solo ${(confidence * 100).toFixed(0)}% de certeza`);
  }
  return reasons.join('; ') || 'retenida por política';
}

function knownContext(observation) {
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
      <a ${playUrl ? `href="${escapeHtml(playUrl)}" target="_blank" title="Abrir el clip en este momento"` : ''} class="claim-visual">
        <img loading="lazy" src="/api/projects/${escapeHtml(projectId)}/frames/${escapeHtml(observation.asset_id)}?t=${midpoint}" alt="" />
        <span class="play-hint">▶ ${Number(observation.start_seconds).toFixed(1)}–${Number(observation.end_seconds).toFixed(1)}s</span>
      </a>
      <div class="claim-body">
        <div class="evidence-meta"><span>${escapeHtml(observation.filename || observation.asset_id)}</span></div>
        <p>${escapeHtml(observation.caption)}</p>
        ${known ? `<p class="muted known-context">El editor ya sabe: ${escapeHtml(known.slice(0, 160))}</p>` : ''}
        <p class="muted why-flagged">Por qué se marcó: ${escapeHtml(claimReason(observation))}.</p>
        <div class="review-actions">
          <button class="ghost approve" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="approve">Cierto — úsalo</button>
          <button class="ghost" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Corregir texto</button>
          <button class="ghost reject" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="reject">Falso — ignóralo</button>
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
        <span>${pending.length} afirmación${pending.length === 1 ? '' : 'es'} sin verificar, apartadas.
        El corte nunca las usa sin confirmar: si una historia depende de una,
        la escena se omite o se te pedirá confirmarla.</span>
        <button class="ghost" id="unskip-claims">Revisar todas</button>
      </section>
    `;
  }
  return `
    <section id="pending-review">
      <div class="section-header">
        <div><span class="eyebrow">Afirmaciones sin verificar (opcional)</span><h2>${pending.length} afirmación${pending.length === 1 ? '' : 'es'} que el editor no usará sin confirmar</h2></div>
        <p>Cada una activó una regla de seguridad concreta (se muestra en su tarjeta);
        ${approvedCount()} observaciones sólidas ya están en uso.</p>
        <button class="secondary compact" id="skip-claims">Apartar</button>
      </div>
      <div class="pending-grid">${pending.map(claimCard).join('')}</div>
    </section>
  `;
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
        <div><span class="eyebrow">Una comprobación rápida</span>
        <h2>«${escapeHtml(concept.title)}» quiere ${claims.length} momento${claims.length === 1 ? '' : 's'} sin verificar</h2></div>
        <p>Confirma los que sean ciertos; lo que rechaces o dejes sin marcar se
        recorta automáticamente. Luego continúa.</p>
      </div>
      <div class="pending-grid">${claims.map(claimCard).join('')}</div>
      <div class="confirm-actions">
        <button class="ghost" id="cancel-confirm">← Volver a las historias</button>
        <button class="primary" id="continue-story">Continuar — hacer el video</button>
      </div>
    </section>
  `;
}

/* ------------------------------------------------------------------ */
/* EDICIÓN                                                             */

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

/* Clips downloaded/synced after the last analysis sit as technical_only —
   invisible to the planner and chat until analyzed. Surface them with a
   one-click INCREMENTAL analysis (only the new clips are sent to the model). */
function unanalyzedBanner(project) {
  const pending = (project.inventory?.assets || []).filter(
    (a) => a.analysis_status === 'technical_only'
      && (a.media_type === 'video' || a.media_type === 'image'));
  if (!pending.length) return '';
  return `
    <div class="banner">
      <span>🆕 ${pending.length} clip${pending.length === 1 ? '' : 's'} nuevo${pending.length === 1 ? '' : 's'} sin analizar — el editor y el chat aún no los ven.</span>
      <button class="primary compact" id="analyze-new-clips">Analizar clips nuevos</button>
    </div>
  `;
}

async function analyzeNewClips(projectId) {
  try {
    setBusy('Analizando clips nuevos', ['Mirando el metraje nuevo', 'Escuchando el habla (local)'], 0, projectId);
    await runStep('analysis/visual', {}, projectId);   // incremental — new clips only
    if (state.activeProjectId !== projectId) { clearBusyIfOwner(projectId); return; }
    setBusy('Analizando clips nuevos', ['Mirando el metraje nuevo', 'Escuchando el habla (local)'], 1, projectId);
    await runStep('analysis/speech', {}, projectId);
    clearBusyIfOwner(projectId);
    if (state.activeProjectId !== projectId) return;
    notice('Clips nuevos analizados — ya cuentan para el chat, las ideas y los arreglos.');
    await loadProject(projectId);
  } catch (error) {
    clearBusyIfOwner(projectId);
    if (state.activeProjectId !== projectId) return;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

function newIdeasBanner(project) {
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

function editWorkspace(project) {
  const renderUrl = project.outputs?.render?.url;
  const renderFresh = project.outputs?.render?.fresh;
  const revision = project.plan?.revision || 1;
  const duration = project.plan?.project?.duration_seconds;
  return `
    ${newIdeasBanner(project)}
    ${unanalyzedBanner(project)}
    <section class="edit-grid">
      <article class="card video-stage">
        ${renderUrl
          ? `<video controls preload="metadata" src="${escapeHtml(renderUrl)}"></video>`
          : '<div class="video-placeholder">Aún no hay vista previa de este corte.</div>'}
        ${renderFresh === false || (!renderUrl && project.plan) ? `
          <div class="banner">
            <span>${renderUrl
              ? 'Esta vista previa es de un corte anterior — el plan cambió.'
              : 'El corte está listo pero sin video (el render falló o no se ha hecho).'}</span>
            <button class="primary compact" id="rerender-now">Renderizar el corte actual</button>
          </div>` : ''}
        <div class="video-caption">
          <button class="ghost compact" id="revision-toggle" aria-expanded="false">Corte ${revision} ▾</button>
          <span>${duration ? `${Number(duration).toFixed(0)}s` : ''}</span>
        </div>
        <div id="revision-history" class="revision-history hidden"></div>
      </article>
      <article class="card chat-panel">
        <div class="chat-head">
          <h3>Asistente</h3>
          <button type="button" class="ghost compact" id="chat-clear" title="Borrar la conversación">Limpiar</button>
        </div>
        <div id="chat-thread" class="chat-thread" aria-live="polite"></div>
        <form id="chat-form" class="chat-form">
          <textarea name="message" rows="2" required maxlength="4000"
            placeholder="Háblale: «quita la escena del café», «¿qué voz en off le pongo al inicio?», «haz un J-cut en la escena 4»…"></textarea>
          <button type="submit" class="primary">Enviar</button>
        </form>
        <p class="muted chat-disclaimer">Los cambios al corte los confirmas tú antes
        de aplicarse. Las ideas y guiones de voz en off son borradores basados en
        lo que vio del metraje — revísalos antes de grabar.</p>
        <div class="quick-actions vo-sources">
          <span class="muted">🎙 Voz en off:</span>
          <button class="quick" id="vo-any-rec">🎤 Grabar</button>
          <button class="quick" id="vo-any-file">📁 Archivo</button>
          <button class="quick" id="vo-any-drive">☁️ Drive</button>
        </div>
        <div class="quick-actions">
          <button class="quick" id="qa-cleanup">🧹 Afinar diálogo</button>
          <button class="quick" id="qa-vo-review">🎙️ Revisar voz en off</button>
          <button class="quick" id="qa-captions">💬 Subtítulos</button>
          <button class="quick" id="qa-story">📖 Cambiar de historia</button>
        </div>
        <div id="qa-panel"></div>
      </article>
    </section>
    <section class="storyboard-section">
      <div class="section-header"><div><span class="eyebrow">Escena por escena</span></div>
      <p class="muted">Un clic abre el clip original en ese segundo. ¿Algo no encaja? Dilo en el chat.</p></div>
      <div class="scene-strip">${sceneStrip(project)}</div>
      ${overlayLanes(project)}
    </section>
    <div id="models-in-use"></div>
    <input type="file" id="vo-capture-input"
      accept=".m4a,.wav,.mp3,audio/mp4,audio/x-m4a,audio/wav,audio/mpeg" capture hidden />
    <input type="file" id="vo-file-input"
      accept=".m4a,.wav,.mp3,.aac,.ogg,.opus,.flac,audio/*" hidden />
  `;
}

/* Unified assistant chat — one surface that both DISCUSSES the cut (voiceover
   drafting, "what's weak here?") and, when you ask to change the video, proposes
   a bounded edit you confirm before it applies (same revision-guarded path as
   the old Editor IA box). Grounded in what every scene shows. */
function chatBubble(m, index) {
  if (m.kind === 'proposal') {
    const actions = m.applied
      ? '<span class="chat-applied">✓ aplicado</span>'
      : `<div class="chat-proposal-actions">
           <button class="primary compact" data-chat-apply="${escapeHtml(m.proposal_id)}">Aplicar</button>
           <button class="ghost compact" data-chat-dismiss="${index}">Descartar</button>
         </div>`;
    return `<div class="chat-msg bot"><div class="chat-msg-body chat-proposal">
      <span class="chat-proposal-label">✏️ Cambio propuesto</span>
      <p>${escapeHtml(m.summary || m.content || '')}</p>
      ${actions}
    </div></div>`;
  }
  const vo = m.voiceover_draft;
  const voWarn = vo && vo.unverified
    ? '<span class="chat-vo-warn" title="El metraje no respalda claramente esta frase — revísala antes de grabarla">⚠ sin respaldo — verifica</span>'
    : '';
  const voButton = vo && vo.text
    ? `<div class="chat-vo-wrap">
         <span class="chat-vo-range">${fmtTime(vo.start_seconds)}–${fmtTime(vo.end_seconds)}</span>
         <button class="chat-vo-btn" data-chat-vo="${index}">🎤 Grabar</button>
         <button class="chat-vo-btn" data-chat-vo-file="${index}">📁 Archivo</button>
         <button class="chat-vo-btn" data-chat-vo-drive="${index}">☁️ Drive</button>
         ${voWarn}
       </div>`
    : '';
  return `<div class="chat-msg ${m.role === 'user' ? 'me' : 'bot'}">
    <div class="chat-msg-body">${escapeHtml(m.content)}${voButton}</div>
  </div>`;
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/* Model picker — choose the model per pipeline stage (story writer, video
   analysis). The choice persists on the project, so every trigger path (first
   run, regenerate, forced re-run) uses it. */
async function loadModelPicker() {
  const slot = $('#model-picker');
  if (!slot) return;
  slot.innerHTML = '<p class="muted">Cargando modelos…</p>';
  try {
    if (!state.modelCatalog) state.modelCatalog = await api('/api/models');
    const prefs = state.activeProject?.model_prefs || {};
    const stages = state.modelCatalog.stages || {};
    slot.innerHTML = Object.entries(stages).map(([stage, spec]) => {
      const chosen = prefs[stage] || spec.default;
      const key = (o) => `${o.provider}:${o.model}`;
      const opts = spec.options.map((o) => {
        const sel = key(o) === key(chosen) ? ' selected' : '';
        const dis = o.available ? '' : ' disabled';
        const tag = [o.note, o.available ? '' : 'sin llave API'].filter(Boolean).join(' · ');
        return `<option value="${escapeHtml(key(o))}"${sel}${dis}>${escapeHtml(o.label)}${tag ? ` — ${escapeHtml(tag)}` : ''}</option>`;
      }).join('');
      return `
        <label class="model-row">
          <span>${escapeHtml(spec.label)}</span>
          <select data-model-stage="${escapeHtml(stage)}">${opts}</select>
        </label>`;
    }).join('');
    slot.querySelectorAll('[data-model-stage]').forEach((sel) => {
      sel.addEventListener('change', () => setModelPref(sel.dataset.modelStage, sel.value));
    });
  } catch (error) {
    slot.innerHTML = `<p class="notice error">No se pudieron cargar los modelos: ${escapeHtml(error.message)}</p>`;
  }
}

/* Read-only "models in use" line for ONGOING projects: past stages can't be
   re-run on a different model retroactively, so instead of hiding the picker
   silently we show what each stage ACTUALLY used (recorded provenance) and flag
   when the next run would use something different (changeable in Diagnóstico). */
async function loadModelsInUse() {
  const slot = $('#models-in-use');
  if (!slot) return;
  const projectId = state.activeProjectId;
  try {
    const { stages } = await api(`/api/projects/${projectId}/models-in-use`);
    if (state.activeProjectId !== projectId) return;
    const name = (m) => m ? (m.model || m.provider || '?') : null;
    const parts = Object.values(stages).map((s) => {
      const used = name(s.used);
      const next = name(s.next);
      if (used && next && used !== next) {
        return `<span class="model-chip" title="Se usó ${escapeHtml(used)}; el próximo re-run usará ${escapeHtml(next)}">${escapeHtml(s.label)}: <strong>${escapeHtml(used)}</strong> → ${escapeHtml(next)}</span>`;
      }
      const shown = used || next;
      if (!shown) return '';
      const tag = used ? '' : ' <span class="muted">(aún sin usar)</span>';
      return `<span class="model-chip" title="${used ? 'Modelo usado por esta etapa' : 'Modelo que usará esta etapa'}">${escapeHtml(s.label)}: <strong>${escapeHtml(shown)}</strong>${tag}</span>`;
    }).filter(Boolean);
    if (!parts.length) { slot.innerHTML = ''; return; }
    slot.innerHTML = `<p class="muted models-in-use">🧠 Modelos — ${parts.join(' · ')}
      <span class="muted">(cámbialos para futuros re-runs en Diagnóstico)</span></p>`;
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    slot.innerHTML = '';   // display-only: never block the workspace on failure
  }
}

async function setModelPref(stage, value) {
  const [provider, model] = value.split(':');
  const projectId = state.activeProjectId;
  try {
    const { model_prefs } = await api(`/api/projects/${projectId}/model-prefs`, {
      method: 'POST', body: JSON.stringify({ stage, provider, model }),
    });
    // Only write the response back if we're still on the same project — a
    // switch during the request must not stamp A's prefs onto B (Codex review).
    if (state.activeProjectId === projectId && state.activeProject) {
      state.activeProject.model_prefs = model_prefs;
      notice('Modelo actualizado.');
    }
  } catch (error) {
    notice(error.message, true);
    loadModelPicker();  // revert the <select> to the stored value
  }
}

function renderChatThread() {
  const thread = $('#chat-thread');
  if (!thread) return;
  const chat = state.chat;
  const messages = chat?.messages || [];
  if (!chat?.loaded && !messages.length) {
    thread.innerHTML = '<p class="muted chat-empty">Cargando…</p>';
    return;
  }
  if (!messages.length && !chat?.sending) {
    thread.innerHTML = `<p class="muted chat-empty">Habla con tu vlog. Ya vio tu
      metraje escena por escena, así que puedes pedirle un cambio («quita la del
      café»), pedir ideas de voz en off, o preguntarle qué le falta al corte.</p>`;
    return;
  }
  const bubbles = messages.map((m, i) => chatBubble(m, i)).join('');
  const pending = chat?.sending
    ? '<div class="chat-msg bot"><div class="chat-msg-body muted">Pensando…</div></div>'
    : '';
  thread.innerHTML = bubbles + pending;
  thread.scrollTop = thread.scrollHeight;
}

/* Concurrency guards (Codex review 2026-09-06): a project switch REPLACES
   state.chat, so comparing the captured object identity (`state.chat === chat`)
   rejects any stale A→B→A response. `writeSeq` bumps on every write (send/clear/
   apply/dismiss), so a slow GET cannot clobber a newer send. */
async function loadChat() {
  const projectId = state.activeProjectId;
  if (state.chat?.projectId !== projectId) {
    state.chat = { projectId, messages: [], loaded: false, sending: false, writeSeq: 0 };
  }
  const chat = state.chat;
  renderChatThread();
  if (chat.loaded) return;
  const seq = chat.writeSeq;
  try {
    const { messages } = await api(`/api/projects/${projectId}/chat`);
    // Adopt the fetched history only if this chat is still current AND no write
    // happened while the GET was in flight (else it would overwrite it).
    if (state.chat === chat && chat.writeSeq === seq) chat.messages = messages || [];
  } catch {
    /* leave empty — the send path surfaces any real error */
  } finally {
    if (state.chat === chat) {
      chat.loaded = true;
      renderChatThread();
    }
  }
}

async function submitChat(event) {
  event.preventDefault();
  const chat = state.chat;
  if (!chat || chat.sending) return;
  const textarea = event.currentTarget.elements.message;
  const message = textarea.value.trim();
  if (!message) return;
  textarea.value = '';
  delete state.chatDrafts[chat.projectId];
  chat.messages = [...chat.messages, { role: 'user', content: message }];
  chat.writeSeq++;
  chat.sending = true;
  renderChatThread();
  try {
    const { messages } = await api(`/api/projects/${chat.projectId}/chat`, {
      method: 'POST', body: JSON.stringify({ message }),
    });
    if (state.chat === chat) { chat.messages = messages || chat.messages; chat.writeSeq++; }
  } catch (error) {
    if (state.chat === chat) {
      textarea.value = message;  // let them retry without retyping
      state.chatDrafts[chat.projectId] = message;
      chat.messages = [...chat.messages, { role: 'assistant', content: `⚠️ ${error.message}` }];
      chat.writeSeq++;
    }
  } finally {
    if (state.chat === chat) {
      chat.sending = false;
      renderChatThread();
    }
  }
}

async function clearChat() {
  const chat = state.chat;
  if (!chat) return;
  if (!confirm('¿Borrar la conversación?')) return;
  try {
    await api(`/api/projects/${chat.projectId}/chat`, { method: 'DELETE' });
    if (state.chat === chat) {
      chat.messages = [];
      chat.writeSeq++;
      renderChatThread();
    }
  } catch (error) {
    notice(error.message, true);
  }
}

/* Apply an edit the assistant proposed, then re-render the cut — same busy flow
   as the old Editor IA apply. The confirm gate lives on the server (revision
   guard); a stale proposal is refused with a clear message. */
async function chatApply(proposalId) {
  const chat = state.chat;
  const projectId = state.activeProjectId;
  try {
    setBusy('Cambiando tu vlog', ['Aplicando el cambio', 'Renderizando'], 0);
    const applied = await api(
      `/api/projects/${projectId}/chat/apply?proposal_id=${encodeURIComponent(proposalId)}`,
      { method: 'POST' });
    if (state.chat === chat) { chat.messages = applied.messages || chat.messages; chat.writeSeq++; }
    setBusy('Cambiando tu vlog', ['Aplicando el cambio', 'Renderizando'], 1);
    await runStep('render', undefined, projectId);
    state.busy = null;
    notice('Listo — nuevo corte arriba.');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

async function dismissChatProposal(index) {
  const chat = state.chat;
  const m = chat?.messages?.[index];
  if (!m || m.kind !== 'proposal' || m.applied) return;
  try {
    // Invalidate the proposal server-side so it can't be applied on reload or
    // from another device — not just hidden locally.
    const { messages } = await api(
      `/api/projects/${chat.projectId}/chat/dismiss?proposal_id=${encodeURIComponent(m.proposal_id)}`,
      { method: 'POST' });
    if (state.chat === chat) {
      chat.messages = messages || chat.messages;
      chat.writeSeq++;
      renderChatThread();
    }
  } catch (error) {
    notice(error.message, true);
  }
}

/* "Grabar y colocar": record (or pick) a voice note, upload it to the project,
   then place it as a voiceover at the drafted range — routed through the same
   confirm-gated add_voiceover op. */
// Load the voice note three ways — all end at the same confirm-gated
// add_voiceover placement: record on the mic IN THE PAGE (a real voice-note
// recorder — the old capture-attribute input opened the CAMERA on phones), pick
// an existing audio file, or copy one out of Drive.
function startVoiceRecording(draft, fallbackToCapture) {
  if (navigator.mediaDevices?.getUserMedia && window.MediaRecorder) {
    recordVoiceNote(draft);
    return;
  }
  if (!window.isSecureContext) {
    // Browsers only expose the microphone over HTTPS/localhost. Don't silently
    // open the OS capture UI (phones route it to the CAMERA) — explain, and
    // offer the path that does work everywhere.
    notice('El micrófono requiere HTTPS. Abre Vlog Studio por la URL https (o '
      + 'graba con la app de notas de voz del teléfono y usa 📁 Archivo).', true);
    return;
  }
  // Secure but no MediaRecorder (rare): fall back to the OS capture input.
  if (fallbackToCapture) fallbackToCapture();
}

function chatRecordVoiceover(index) {
  const draft = state.chat?.messages?.[index]?.voiceover_draft;
  if (!draft) return;
  startVoiceRecording({ ...draft, projectId: state.activeProjectId },
    () => chatPickVoiceover(index, '#vo-capture-input'));
}

/* Audio-only recorder overlay: mic permission → record with a live timer →
   stop → place through the same flow as a picked file. Audio format follows
   MediaRecorder support: audio/mp4 (.m4a, iOS/Safari) or audio/webm (Chrome/
   Firefox — backend + ffmpeg accept both). */
async function recordVoiceNote(draft) {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    notice('No hay permiso de micrófono — usa 📁 Archivo o habilita el micrófono.', true);
    return;
  }
  const pick = [['audio/mp4', 'm4a'], ['audio/webm;codecs=opus', 'webm'], ['audio/webm', 'webm']]
    .find(([mime]) => MediaRecorder.isTypeSupported(mime)) || [undefined, 'webm'];
  const recorder = new MediaRecorder(stream, pick[0] ? { mimeType: pick[0] } : undefined);
  const chunks = [];
  recorder.addEventListener('dataavailable', (e) => { if (e.data.size) chunks.push(e.data); });

  const overlay = document.createElement('div');
  overlay.className = 'vo-recorder-overlay';
  overlay.innerHTML = `
    <div class="vo-recorder">
      <p class="vo-recorder-title">🎙️ Grabando voz en off</p>
      <p class="muted">${escapeHtml(draft.text || '')}</p>
      <p class="vo-recorder-timer" id="vo-rec-timer">0:00</p>
      <div class="vo-recorder-actions">
        <button class="primary" id="vo-rec-stop">■ Terminar y colocar</button>
        <button class="ghost" id="vo-rec-cancel">Cancelar</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const startedAt = Date.now();
  const timer = setInterval(() => {
    const s = Math.floor((Date.now() - startedAt) / 1000);
    const el = overlay.querySelector('#vo-rec-timer');
    if (el) el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }, 250);

  const cleanup = () => {
    clearInterval(timer);
    stream.getTracks().forEach((t) => t.stop());
    overlay.remove();
  };
  overlay.querySelector('#vo-rec-cancel').addEventListener('click', () => {
    recorder.stop();   // discard: the stop handler checks the cancelled flag
    overlay.dataset.cancelled = '1';
  });
  overlay.querySelector('#vo-rec-stop').addEventListener('click', () => recorder.stop());
  recorder.addEventListener('stop', async () => {
    const cancelled = overlay.dataset.cancelled === '1';
    cleanup();
    if (cancelled || !chunks.length) return;
    const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
    const file = new File(chunks, `voz-${stamp}.${pick[1]}`,
      { type: pick[0] || 'audio/webm' });
    await placeVoiceoverFile(file, draft);
  });
  recorder.start();
}
function chatFileVoiceover(index) { chatPickVoiceover(index, '#vo-file-input'); }

function chatPickVoiceover(index, inputSelector) {
  const draft = state.chat?.messages?.[index]?.voiceover_draft;
  if (!draft) return;
  // Bind the pending capture to THIS project — the picker/recorder can take a
  // while, and the user might switch projects before returning (Codex review).
  state.pendingVoiceover = { ...draft, projectId: state.activeProjectId };
  const input = $(inputSelector);
  if (input) { input.value = ''; input.click(); }
}

async function chatDriveVoiceover(index) {
  const draft = state.chat?.messages?.[index]?.voiceover_draft;
  if (!draft) return;
  await driveVoiceoverPicker(draft);
}

async function driveVoiceoverPicker(draft) {
  const projectId = state.activeProjectId;
  const box = $('#qa-panel');
  if (box) box.innerHTML = '<p class="notice">Buscando notas de voz en Drive…</p>';
  try {
    const { files } = await api(`/api/projects/${projectId}/voiceover/drive-files`);
    if (state.activeProjectId !== projectId) return;
    if (!box) return;
    if (!files.length) {
      box.innerHTML = '<p class="notice">No hay audios en tu Drive (VlogInbox). Sube la nota de voz allí y vuelve a intentar.</p>';
      return;
    }
    box.innerHTML = `<div class="sync-diff">
      <p>${draft.text
        ? `Elige nota(s) de voz para <em>«${escapeHtml(draft.text)}»</em> (${fmtTime(draft.start_seconds)}–${fmtTime(draft.end_seconds)}).`
        : 'Elige nota(s) de voz — se añaden al final de la voz en off actual.'}
      Se colocan una tras otra <strong>en el orden en que las marques</strong> (mira el número):</p>
      ${files.map((f, i) => `<label class="cleanup-item">
        <input type="checkbox" data-drive-voice-check="${i}" />
        <span class="pick-order" data-pick-order="${i}"></span>
        <span>☁️ ${escapeHtml(f.name)} <span class="muted">(${(f.bytes / 1e6).toFixed(1)} MB)</span></span>
        <button class="primary compact" data-drive-voice="${escapeHtml(f.path)}">Colocar solo esta</button>
      </label>`).join('')}
      <button class="primary compact" id="drive-voice-multi">Colocar seleccionadas en orden</button>
    </div>`;
    box.querySelectorAll('[data-drive-voice]').forEach((btn) => {
      btn.addEventListener('click', () => placeVoiceoverFromDrive(
        projectId, btn.dataset.driveVoice, draft));
    });
    // Placement order = the order the user TICKED the boxes; each checked row
    // shows its position (1, 2, 3…). Unticking renumbers the rest.
    const pickOrder = [];
    const renumber = () => {
      box.querySelectorAll('[data-pick-order]').forEach((el) => {
        const pos = pickOrder.indexOf(Number(el.dataset.pickOrder));
        el.textContent = pos >= 0 ? `${pos + 1}º` : '';
      });
    };
    box.querySelectorAll('[data-drive-voice-check]').forEach((el) => {
      el.addEventListener('change', () => {
        const i = Number(el.dataset.driveVoiceCheck);
        const at = pickOrder.indexOf(i);
        if (el.checked && at < 0) pickOrder.push(i);
        if (!el.checked && at >= 0) pickOrder.splice(at, 1);
        renumber();
      });
    });
    $('#drive-voice-multi')?.addEventListener('click', () => {
      if (!pickOrder.length) { notice('Marca al menos una nota de voz.', true); return; }
      placeVoiceoverPartsFromDrive(
        projectId, pickOrder.map((i) => files[i].path), draft);
    });
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    if (box) box.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

/* Multi-part narration from Drive: place the selected notes in tick order.
   Sequencing is SERVER-side (`sequence: true` appends each part after the
   voiceover lane's current end), so the client sends the draft start and
   nothing else — no local cursors, no per-part refetches. */
async function placeVoiceoverPartsFromDrive(projectId, remotePaths, draft) {
  try {
    for (let i = 0; i < remotePaths.length; i += 1) {
      setBusy('Colocando la voz en off por partes',
        remotePaths.map((_, j) => `Parte ${j + 1}`), i, projectId);
      await api(`/api/projects/${projectId}/voiceover/place-from-drive`, {
        method: 'POST',
        body: JSON.stringify({ remote_path: remotePaths[i],
                               start_seconds: draft.start_seconds,
                               sequence: true }),
      });
      if (state.activeProjectId !== projectId) { clearBusyIfOwner(projectId); return; }
    }
    clearBusyIfOwner(projectId);
    if (state.activeProjectId !== projectId) return;
    notice(`${remotePaths.length} parte(s) de voz en off colocadas en orden. Renderiza cuando termines de editar.`);
    await loadProject(projectId);
  } catch (error) {
    clearBusyIfOwner(projectId);
    if (state.activeProjectId !== projectId) return;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

async function placeVoiceoverFromDrive(projectId, remotePath, draft) {
  try {
    setBusy('Colocando tu voz en off', ['Trayendo la nota de voz de Drive', 'Renderizando'], 0, projectId);
    const body = { remote_path: remotePath, start_seconds: draft.start_seconds };
    if (draft.sequence) body.sequence = true;
    if (draft.end_seconds > draft.start_seconds) {
      body.max_duration_seconds = draft.end_seconds - draft.start_seconds;
    }
    await api(`/api/projects/${projectId}/voiceover/place-from-drive`, {
      method: 'POST', body: JSON.stringify(body),
    });
    clearBusyIfOwner(projectId);
    if (state.activeProjectId !== projectId) return;
    notice('Voz en off colocada desde Drive. Renderiza cuando termines de editar.');
    await loadProject(projectId);
  } catch (error) {
    clearBusyIfOwner(projectId);
    if (state.activeProjectId !== projectId) return;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

async function onVoiceoverFilePicked(event) {
  const file = event.currentTarget.files?.[0];
  const draft = state.pendingVoiceover;
  state.pendingVoiceover = null;
  if (!file || !draft) return;
  await placeVoiceoverFile(file, draft);
}

async function placeVoiceoverFile(file, draft) {
  if (draft.projectId !== state.activeProjectId) {
    notice('Cambiaste de proyecto — vuelve a pedir la voz en off.', true);
    return;
  }
  const projectId = draft.projectId;
  try {
    setBusy('Colocando tu voz en off', ['Subiendo y colocando la nota de voz', 'Renderizando'], 0);
    // One server call: save the recording, find it in the inventory, and place
    // it as a voiceover at the drafted start (revision-guarded add_voiceover).
    const form = new FormData();
    form.append('files', file, file.name || 'voz-en-off.m4a');
    form.append('start_seconds', String(draft.start_seconds));
    if (draft.sequence) form.append('sequence', 'true');
    // Trim the placed voiceover to the drafted window instead of playing the
    // whole recording.
    if (draft.end_seconds > draft.start_seconds) {
      form.append('max_duration_seconds', String(draft.end_seconds - draft.start_seconds));
    }
    await fetch(`/api/projects/${projectId}/voiceover/place`, { method: 'POST', body: form })
      .then((r) => r.json().then((p) => { if (!r.ok) throw new Error(p.detail || 'No se pudo colocar la voz en off'); return p; }));
    state.busy = null;
    notice('Voz en off colocada. Renderiza cuando termines de editar («Renderizar el corte actual»).');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

function overlayLanes(project) {
  const tracks = project.plan?.tracks || [];
  const broll = tracks.find((t) => t.kind === 'video' && t.role === 'broll')?.events || [];
  const voiceover = tracks.find((t) => t.kind === 'audio' && t.role === 'voiceover')?.events || [];
  if (!broll.length && !voiceover.length) return '';
  const chip = (event, icon, removeOp) => `
    <span class="lane-chip">${icon} ${escapeHtml(event.event_id)} ·
    ${event.timeline_start_seconds.toFixed(1)}–${(event.timeline_start_seconds + event.duration_seconds).toFixed(1)}s
    (${escapeHtml(event.asset_id || '')})
    <button class="lane-chip-remove" data-lane-remove="${removeOp}"
      data-lane-event="${escapeHtml(event.event_id)}"
      title="Quitar del corte (el archivo no se borra)">✕</button></span>`;
  return `
    <div class="overlay-lanes">
      ${broll.length ? `<div><span class="eyebrow">B-roll</span> ${broll.map((e) => chip(e, '🎞', 'remove_broll')).join('')}</div>` : ''}
      ${voiceover.length ? `<div><span class="eyebrow">Voz en off</span> ${voiceover.map((e) => chip(e, '🎙', 'remove_voiceover')).join('')}</div>` : ''}
    </div>`;
}

async function toggleRevisionHistory() {
  const box = $('#revision-history');
  const toggle = $('#revision-toggle');
  if (!box.classList.contains('hidden')) {
    box.classList.add('hidden');
    toggle?.setAttribute('aria-expanded', 'false');
    return;
  }
  box.classList.remove('hidden');
  toggle?.setAttribute('aria-expanded', 'true');
  box.innerHTML = '<p class="muted">Cargando…</p>';
  try {
    const log = await api(`/api/projects/${state.activeProjectId}/plan/revisions`);
    const current = state.activeProject?.plan?.revision || 1;
    if (!log.entries?.length) {
      box.innerHTML = '<p class="muted">Sin revisiones — el corte sigue en su versión original.</p>';
      return;
    }
    box.innerHTML = [...log.entries].reverse().map((entry) => `
      <div class="revision-row">
        <strong>Corte ${entry.revision}${entry.revision === current ? ' · actual' : ''}</strong>
        <span>${escapeHtml(entry.note || entry.instruction || '')}</span>
        ${entry.revision === current
          ? (current > 1 ? `<button class="ghost compact" data-restore="${current - 1}">↶ Deshacer este cambio</button>` : '')
          : `<button class="ghost compact" data-restore="${entry.revision}">Volver al corte ${entry.revision}</button>`}
      </div>`).join('');
    box.querySelectorAll('[data-restore]').forEach((button) => {
      button.addEventListener('click', () => restoreRevision(Number(button.dataset.restore)));
    });
  } catch (error) { box.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; }
}

async function restoreRevision(revision) {
  const projectId = state.activeProjectId;
  if (!window.confirm(`¿Volver al corte ${revision}? Se crea una nueva revisión — nada se pierde.`)) return;
  try {
    setBusy('Restaurando el corte', ['Instalando la revisión', 'Renderizando'], 0);
    await api(`/api/projects/${projectId}/plan/revisions/${revision}/restore`, { method: 'POST' });
    setBusy('Restaurando el corte', ['Instalando la revisión', 'Renderizando'], 1);
    await runStep('render', undefined, projectId);
    state.busy = null;
    notice(`Corte ${revision} restaurado.`);
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

/* ---- quick actions ---- */

/* Voiceover review (A0 preflight, read-only): transcribe the placed VO, split
   into beats, and show per-beat whether the footage supports it. Provisional —
   actionable remedies (extend/pull/retime) come in later phases, after cleanup
   or an explicit skip freezes the VO timebase. */
const VO_BEAT_CLASS = {
  current_match: ['✓', 'ok', 'Ya se ve'],
  available_elsewhere: ['↺', 'warn', 'Hay metraje relevante sin usar'],
  ambiguous: ['?', 'warn', 'Ambiguo'],
  gap: ['⚠', 'bad', 'Posible falta de imagen'],
  nonvisual: ['—', 'muted', 'Narración (no necesita imagen)'],
  unknown: ['…', 'muted', 'Sin determinar (revisión incompleta)'],
};

function renderVoiceoverReport(a) {
  if (!a) return '<p class="notice">Sin análisis todavía.</p>';
  if (a.status === 'no_speech') {
    return '<p class="notice">No se detectó voz en la grabación colocada. ¿Es la nota correcta?</p>';
  }
  if (!Array.isArray(a.beats) || !a.beats.length) {
    return '<p class="notice">Sin análisis todavía.</p>';
  }
  const beats = a.beats.map((b) => {
    const [icon, cls, label] = VO_BEAT_CLASS[b.class] || ['?', 'muted', b.class];
    const cands = (b.candidates || [])
      .map((c) => `<li>${escapeHtml(c.caption || c.asset_id || '')}${c.visible ? ' <span class="muted">(ya visible)</span>' : ''}</li>`).join('');
    let extra = '';
    if (b.class === 'gap') {
      // A0 is provisional — flag, don't yet tell the user to record (that's A1).
      extra = '<p class="muted">Nada en tu material parece respaldarlo. Confírmalo tras limpiar la voz en off.</p>';
    } else if (b.class !== 'current_match' && b.class !== 'nonvisual' && cands) {
      extra = `<p class="muted">Metraje relevante disponible:</p><ul class="vo-cands">${cands}</ul>`;
    }
    return `<div class="vo-beat vo-${cls}">
      <div class="vo-beat-head">
        <span class="vo-badge">${icon} ${escapeHtml(label)}</span>
        <span class="muted">${fmtTime(b.timeline_start_seconds)}</span>
      </div>
      <p class="vo-beat-text">«${escapeHtml(b.text)}»</p>
      ${b.rationale ? `<p class="muted vo-why">${escapeHtml(b.rationale)}</p>` : ''}
      ${extra}
    </div>`;
  }).join('');
  const deadair = (a.deadair_candidates || []).length;
  const staleBanner = a.stale
    ? `<p class="notice error">⚠ Análisis desactualizado (${(a.stale_reasons || []).map(escapeHtml).join(', ')}). Vuelve a analizar.</p>`
    : '';
  const incomplete = a.coverage_complete === false
    ? '<p class="muted">Nota: hay mucho metraje; algunos beats quedan «sin determinar» en vez de marcarse como falta.</p>'
    : '';
  // Actions work off THIS fresh analysis — offer them only when it isn't stale
  // (a moved plan would edit the wrong ranges/windows).
  const fresh = !a.stale && a.event_id;
  // Cleanup (B) comes first; it's offered whenever the analysis is fresh.
  const cleanupCta = fresh
    ? `<button class="primary compact vo-cleanup-btn" data-event-id="${escapeHtml(a.event_id)}">🧹 Limpiar muletillas y silencios</button>`
    : '';
  // A1/C run only on a FROZEN timebase — the server's authoritative flag
  // (nothing left to clean OR explicitly accepted), so an accepted freeze reveals
  // the actions even with a retained filler.
  const frozen = fresh && a.timebase_frozen;
  const needsCoverage = Array.isArray(a.beats)
    && a.beats.some((b) => b.class === 'available_elsewhere' || b.class === 'gap');
  const remedyCta = frozen && needsCoverage
    ? `<button class="primary compact vo-remedy-btn" data-event-id="${escapeHtml(a.event_id)}">🎬 Arreglar imagen con metraje del proyecto</button>`
    : '';
  const retimeCta = frozen
    ? '<button class="primary compact vo-retime-btn">⏱️ Ajustar la imagen a la voz en off</button>'
    : '';
  const pendingNote = fresh && a.pending_cleanup && !a.timebase_frozen
    ? `<p class="muted">Limpia la voz en off para desbloquear los ajustes de imagen (metraje y duración), o acéptala tal cual.</p>
       <button class="quick compact vo-freeze-btn" data-event-id="${escapeHtml(a.event_id)}">✔️ Usar tal cual (saltar limpieza)</button>`
    : '';
  return `<div class="vo-report">
    ${staleBanner}
    <p class="muted">${escapeHtml(a.note || '')}</p>
    ${incomplete}
    ${deadair ? `<p class="muted">🧹 ${deadair} silencio(s) largo(s) detectado(s) en la voz en off.</p>` : ''}
    ${cleanupCta}
    ${pendingNote}
    ${remedyCta}
    ${retimeCta}
    ${beats}
  </div>`;
}

// Phase C: preview the single voiceover-led retime (match the picture end to the
// voiceover span) and apply it as one confirm-gated op. Feasibility comes from
// the server — trimming always works; extending needs verified footage.
async function voiceoverRetimeFlow(projectId, panel) {
  panel.innerHTML = '<p class="notice">Comparando la duración de la imagen con la voz en off…</p>';
  try {
    const found = await api(`/api/projects/${projectId}/voiceover/retime`, { method: 'POST' });
    if (state.activeProjectId !== projectId) return;
    const c = found.candidate || {};
    const baseRevision = found.plan_revision;
    if (c.action === 'none') {
      panel.innerHTML = `<p class="notice">${escapeHtml(c.reason || 'La imagen ya coincide con la voz en off.')}</p>`;
      return;
    }
    const applyBtn = c.feasible
      ? '<button class="primary compact" id="vo-retime-apply">Aplicar ajuste</button>'
      : '';
    panel.innerHTML = `<div class="sync-diff">
      <p>${escapeHtml(c.reason || '')}</p>
      ${c.feasible ? '' : '<p class="muted">No se aplica automáticamente.</p>'}
      ${applyBtn}
    </div>`;
    $('#vo-retime-apply')?.addEventListener('click', async () => {
      try {
        setBusy('Ajustando la imagen', ['Reajustando el clip'], 0, projectId);
        const applied = await api(`/api/projects/${projectId}/voiceover/retime/apply`, {
          method: 'POST', body: JSON.stringify({ base_revision: baseRevision }),
        });
        clearBusyIfOwner(projectId);
        if (state.activeProjectId !== projectId) return;
        notice(`${applied?.summary || 'Imagen ajustada a la voz en off'}. Renderiza cuando termines de editar.`);
        await loadProject(projectId);
      } catch (error) {
        clearBusyIfOwner(projectId);
        if (state.activeProjectId !== projectId) return;
        notice(error.message, true);
        await loadProject(projectId);
      }
    });
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    panel.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

// Phase A1: list the tiered footage remedies for the reviewed voiceover. A
// `pull` remedy overlays grounded pool footage (one-click apply, no ripple); a
// `record` remedy is advisory (nothing in the pool matches).
async function voiceoverRemediesFlow(projectId, eventId, panel) {
  panel.innerHTML = '<p class="notice">Buscando metraje del proyecto para los momentos sin imagen…</p>';
  try {
    const found = await api(`/api/projects/${projectId}/voiceover/remedies`, {
      method: 'POST', body: JSON.stringify({ event_id: eventId }),
    });
    if (state.activeProjectId !== projectId) return;
    const remedies = found.remedies || [];
    const baseRevision = found.plan_revision;
    if (!remedies.length) {
      panel.innerHTML = '<p class="notice">Cada momento ya tiene imagen relevante — nada que arreglar.</p>';
      return;
    }
    const row = (r) => {
      if (r.tier === 'pull') {
        return `<div class="cleanup-item">
          <span>🎬 <em>«${escapeHtml(r.text)}»</em> → superponer <strong>${escapeHtml(r.caption || r.asset_id || '')}</strong></span>
          <button class="primary compact" data-remedy-id="${escapeHtml(r.remedy_id)}">Aplicar</button>
        </div>`;
      }
      return `<div class="cleanup-item">
        <span>🎥 <em>«${escapeHtml(r.text)}»</em> → ${escapeHtml(r.advice || 'graba metraje nuevo')}</span>
      </div>`;
    };
    panel.innerHTML = `<div class="sync-diff">
      <p><strong>${remedies.length}</strong> momento(s) por reforzar. La superposición no mueve el resto del corte:</p>
      ${remedies.map(row).join('')}
    </div>`;
    panel.querySelectorAll('[data-remedy-id]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const remedyId = btn.dataset.remedyId;
        try {
          setBusy('Reforzando la imagen', ['Colocando el recurso'], 0, projectId);
          const applied = await api(`/api/projects/${projectId}/voiceover/remedies/apply`, {
            method: 'POST',
            body: JSON.stringify({ event_id: eventId, remedy_id: remedyId, base_revision: baseRevision }),
          });
          clearBusyIfOwner(projectId);
          if (state.activeProjectId !== projectId) return;
          notice(`${applied?.summary || 'Imagen reforzada'}. Renderiza cuando termines de editar.`);
          await loadProject(projectId);
        } catch (error) {
          clearBusyIfOwner(projectId);
          if (state.activeProjectId !== projectId) return;
          notice(error.message, true);
          await loadProject(projectId);
        }
      });
    });
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    panel.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

// Phase B: preview the conservative VO-lane cleanup, let the user uncheck any
// range, then apply it as one confirm-gated op and re-render the cut.
async function voiceoverCleanupFlow(projectId, eventId, panel) {
  panel.innerHTML = '<p class="notice">Buscando muletillas y silencios en la voz en off…</p>';
  try {
    const found = await api(`/api/projects/${projectId}/voiceover/cleanup/candidates`, {
      method: 'POST', body: JSON.stringify({ event_id: eventId }),
    });
    if (state.activeProjectId !== projectId) return;
    const cands = found.candidates || [];
    const baseRevision = found.plan_revision;
    if (!cands.length) {
      panel.innerHTML = '<p class="notice">La voz en off ya está limpia — nada que quitar.</p>';
      return;
    }
    const total = cands.reduce((s, c) => s + (c.source_end_seconds - c.source_start_seconds), 0);
    panel.innerHTML = `
      <div class="sync-diff">
        <p><strong>${cands.length}</strong> sugerencia(s) · hasta ~${total.toFixed(1)}s de audio. Marca las que quieras:</p>
        ${cands.map((c) => `
          <label class="cleanup-item">
            <input type="checkbox" data-vo-clean="${escapeHtml(c.id)}" checked />
            <span>${escapeHtml(c.reason || c.kind)} (${(c.source_end_seconds - c.source_start_seconds).toFixed(2)}s)</span>
          </label>`).join('')}
        <button class="primary compact" id="vo-cleanup-apply">Aplicar seleccionados</button>
      </div>`;
    $('#vo-cleanup-apply')?.addEventListener('click', async () => {
      const ids = [...document.querySelectorAll('[data-vo-clean]')]
        .filter((el) => el.checked).map((el) => el.dataset.voClean);
      if (!ids.length) { notice('Nada seleccionado.', true); return; }
      try {
        setBusy('Limpiando la voz en off', ['Cortando el audio'], 0, projectId);
        const applied = await api(`/api/projects/${projectId}/voiceover/cleanup/apply`, {
          method: 'POST',
          body: JSON.stringify({ event_id: eventId, candidate_ids: ids, base_revision: baseRevision }),
        });
        clearBusyIfOwner(projectId);
        if (state.activeProjectId !== projectId) return;
        notice(`${applied?.summary || 'Voz en off limpiada'}. Renderiza cuando termines de editar.`);
        await loadProject(projectId);
      } catch (error) {
        clearBusyIfOwner(projectId);
        if (state.activeProjectId !== projectId) return;
        notice(error.message, true);
        await loadProject(projectId);
      }
    });
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    panel.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

// Distinct logical narrations on the voiceover track: group by the persisted
// vo_group (Phase-B split id), else the event's own id; each is represented by
// its earliest (anchor) segment — the id the A0 report is keyed under.
function voiceoverNarrations() {
  const evs = (state.activeProject?.plan?.tracks || [])
    .flatMap((t) => (t.role === 'voiceover' ? (t.events || []) : []));
  const groups = new Map();
  for (const e of evs) {
    const key = e.vo_group || e.event_id;
    const cur = groups.get(key);
    if (!cur || e.timeline_start_seconds < cur.timeline_start_seconds) groups.set(key, e);
  }
  return [...groups.values()].sort((a, b) => a.timeline_start_seconds - b.timeline_start_seconds);
}

function wireVoiceoverReportButtons(box) {
  box.querySelectorAll('.vo-cleanup-btn').forEach((b) => b.addEventListener('click', (e) =>
    voiceoverCleanupFlow(state.activeProjectId, e.currentTarget.dataset.eventId, box)));
  box.querySelectorAll('.vo-remedy-btn').forEach((b) => b.addEventListener('click', (e) =>
    voiceoverRemediesFlow(state.activeProjectId, e.currentTarget.dataset.eventId, box)));
  box.querySelectorAll('.vo-retime-btn').forEach((b) => b.addEventListener('click', () =>
    voiceoverRetimeFlow(state.activeProjectId, box)));
  box.querySelectorAll('.vo-freeze-btn').forEach((b) => b.addEventListener('click', (e) =>
    voiceoverFreeze(state.activeProjectId, e.currentTarget.dataset.eventId)));
}

async function quickVoiceoverReview() {
  const projectId = state.activeProjectId;
  const anchors = voiceoverNarrations();
  const box0 = $('#qa-panel');
  if (!anchors.length) {
    if (box0) box0.innerHTML = '<p class="notice">No hay voz en off colocada. Grábala y colócala primero (🎤 Grabar y colocar), luego reviso si el metraje la respalda.</p>';
    return;
  }
  if (box0) box0.innerHTML = `<p class="notice">Transcribiendo y analizando ${anchors.length > 1 ? `${anchors.length} voces en off` : 'la voz en off'}… (puede tardar unos segundos)</p>`;
  try {
    // Analyze each narration (each analyze covers its whole split group).
    for (const a of anchors) {
      await runStep(`voiceover/analyze?event_id=${encodeURIComponent(a.event_id)}`);
      if (state.activeProjectId !== projectId) return;
    }
    const { analysis } = await api(`/api/projects/${projectId}/voiceover/analysis`);
    if (state.activeProjectId !== projectId) return;
    const box = $('#qa-panel');
    if (!box) return;
    const reports = anchors
      .map((a) => (analysis && analysis.analyses ? analysis.analyses[a.event_id] : null))
      .filter(Boolean);
    if (!reports.length) { box.innerHTML = '<p class="notice">Sin análisis.</p>'; return; }
    box.innerHTML = reports.map((r, i) =>
      (reports.length > 1 ? `<p class="eyebrow">Voz en off ${i + 1}</p>` : '')
      + renderVoiceoverReport(r)).join('<hr class="vo-sep" />');
    wireVoiceoverReportButtons(box);
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    const box = $('#qa-panel');
    if (box) box.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

async function voiceoverFreeze(projectId, eventId) {
  try {
    await api(`/api/projects/${projectId}/voiceover/freeze`, {
      method: 'POST', body: JSON.stringify({ event_id: eventId }),
    });
    if (state.activeProjectId !== projectId) return;
    notice('Voz en off aceptada tal cual — ajustes de imagen desbloqueados.');
    await quickVoiceoverReview();   // re-render so the A1/C actions appear
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    notice(error.message, true);
  }
}

async function quickCleanup() {
  const projectId = state.activeProjectId;
  const box = $('#qa-panel');
  box.innerHTML = '<p class="notice">Comprobando la línea de tiempo…</p>';
  try {
    // Pre-check: unsynced manual edits must be reviewed, never silently
    // swept into the plan by the cleanup chain (cross-review UX blocker 2).
    const before = await api(`/api/projects/${projectId}/opentake/sync`, { method: 'POST' });
    if (before.staleness) {
      box.innerHTML = `<p class="notice error">⚠ ${escapeHtml(before.staleness.advice)}</p>`;
      return;
    }
    if (before.changes.length) {
      box.innerHTML = `
        <p class="notice">Hay ${before.changes.length} cambio(s) manuales sin traer de
        OpenTake. Tráelos primero (Publicar → Traer cambios) y luego afina el diálogo.</p>`;
      return;
    }
    box.innerHTML = '<p class="notice">Buscando muletillas y silencios…</p>';
    const found = await api(`/api/projects/${projectId}/opentake/cleanup`, { method: 'POST' });
    if (!found.candidates.length) {
      box.innerHTML = '<p class="notice">Sin candidatos — el habla está limpia. (Los «eh» puros no aparecen en la transcripción, así que esto es normal en discurso preparado.)</p>';
      return;
    }
    box.innerHTML = `
      <div class="sync-diff">
        <p><strong>${found.candidates.length}</strong> sugerencia(s) · quita ${found.total_seconds}s. Marca las que quieras:</p>
        ${found.candidates.map((c, i) => `
          <label class="cleanup-item">
            <input type="checkbox" data-cleanup-index="${i}" checked />
            <span>${escapeHtml(c.reason)} — <em>${escapeHtml(c.context)}</em> (${((c.frames[1] - c.frames[0]) / 30).toFixed(1)}s)</span>
          </label>`).join('')}
        <button class="primary compact" id="cleanup-apply">Aplicar seleccionados</button>
      </div>`;
    $('#cleanup-apply')?.addEventListener('click', async () => {
      const indices = [...document.querySelectorAll('[data-cleanup-index]')]
        .filter((el) => el.checked).map((el) => Number(el.dataset.cleanupIndex));
      if (!indices.length) { notice('Nada seleccionado.', true); return; }
      try {
        setBusy('Afinando el diálogo', ['Cortando en OpenTake', 'Actualizando el plan', 'Renderizando'], 0);
        await api(`/api/projects/${projectId}/opentake/cleanup/apply`, {
          method: 'POST', body: JSON.stringify({ indices }),
        });
        setBusy('Afinando el diálogo', ['Cortando en OpenTake', 'Actualizando el plan', 'Renderizando'], 1);
        const preview = await api(`/api/projects/${projectId}/opentake/sync`, { method: 'POST' });
        const expected = new Set(['deleted', 'trimmed', 'split', 'moved', 'unchanged']);
        const foreign = preview.changes.filter((c) => !expected.has(c.kind));
        if (preview.staleness || foreign.length) {
          state.busy = null;
          notice('La limpieza se aplicó en OpenTake, pero hay cambios adicionales — revísalos en Publicar → Traer cambios.', true);
          await loadProject(projectId);
          return;
        }
        await api(`/api/projects/${projectId}/opentake/sync/apply`, { method: 'POST' });
        setBusy('Afinando el diálogo', ['Cortando en OpenTake', 'Actualizando el plan', 'Renderizando'], 2);
        await runStep('render', undefined, projectId);
        state.busy = null;
        notice('Diálogo afinado y corte re-renderizado.');
        await loadProject(projectId);
      } catch (error) {
        state.busy = null;
        notice(error.message, true);
        await loadProject(projectId);
      }
    });
  } catch (error) { box.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`; }
}

async function quickRerender() {
  const projectId = state.activeProjectId;
  try {
    setBusy('Renderizando', ['Renderizando el corte actual'], 0);
    await runStep('render', undefined, projectId);
    state.busy = null;
    notice('Vista previa actualizada.');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

async function quickCaptions() {
  const projectId = state.activeProjectId;
  try {
    setBusy('Subtítulos', ['Renderizando con subtítulos incrustados'], 0);
    await runStep('render?burn_captions=true', undefined, projectId);
    state.busy = null;
    notice('Vista previa con subtítulos lista.');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

// Reads the plan's music track and returns a flat view for the publish card,
// or null when there is no music track. Recommended mode = an annotation the
// user applies natively when posting; bed mode = audio burned into the MP4.
function musicRecommendation(plan) {
  if (!plan) return null;
  const track = (plan.tracks || []).find((t) => t.role === 'music');
  const music = track?.events?.[0]?.music;
  if (!music) return null;
  if (music.mode === 'bed') return { mode: 'bed' };
  const reco = music.recommended || {};
  return {
    mode: 'recommended',
    name: reco.name || null,
    vibe: reco.vibe || null,
    bpm: reco.bpm || null,
    energy: reco.energy || null,
    platform: reco.platform || null,
    artist: reco.artist || null,
    platformAudioId: reco.platform_audio_id || null,
    trendState: reco.trend_state || null,
  };
}

// Alternate candidates from the last suggestion, grouped by platform (TikTok /
// Instagram) since the same track is not always available on both.
function musicAlternatesHtml(current) {
  const all = currentMusicCandidates().map((c, i) => ({ c, i }));
  if (!all.length) return '';
  const groups = [
    ['tiktok', 'TikTok'],
    ['instagram', 'Instagram'],
  ];
  const sections = groups.map(([key, label]) => {
    const opts = all.filter(({ c }) => (c.platform || '') === key);
    if (!opts.length) return '';
    return `
      <div class="music-group">
        <span class="music-group-label">Para ${label}</span>
        <div class="music-alts">
          ${opts.slice(0, 3).map(({ c, i }) => `
            <button class="${(c.name || '') === (current.name || '') ? 'secondary' : 'ghost'} compact" data-music-use="${i}"
              title="${c.late_bound ? 'Pista real del catálogo de la plataforma' : 'Sugerida por IA — puede no existir; verifícala en la app'}">
              ${escapeHtml(c.name || c.vibe || 'opción')}${c.bpm ? ` · ${Math.round(c.bpm)} BPM` : ''}${c.late_bound ? '' : ' · IA ⚠'}
            </button>`).join('')}
        </div>
      </div>`;
  }).join('');
  // candidates with no platform tag (older model-only) fall back to a flat list
  const untagged = all.filter(({ c }) => !c.platform);
  const flat = untagged.length && !sections ? `
    <div class="music-alts">
      ${untagged.slice(0, 4).map(({ c, i }) => `
        <button class="ghost compact" data-music-use="${i}">${escapeHtml(c.name || c.vibe || 'opción')}</button>`).join('')}
    </div>` : '';
  return (sections || flat)
    ? `<p class="muted" style="margin-top:.4rem">Opciones por plataforma:</p>${sections}${flat}`
    : '';
}

// Deterministic op → propose (no LLM) → apply → re-render. Mirrors the AI-edit
// apply flow but skips interpretation because the op is already exact.
async function applyPlanOp(op, label) {
  const projectId = state.activeProjectId;
  const proposed = await api(`/api/projects/${projectId}/plan/op`, {
    method: 'POST', body: JSON.stringify({ op }),
  });
  try {
    // Apply WITHOUT auto-rendering: quick edits are often batched (quitar una
    // voz, colocar partes, música…) and a render costs minutes. The stale-
    // preview banner («Renderizar el corte actual») appears until the user
    // decides to render (user ask 2026-09-08).
    setBusy(label, ['Aplicando el cambio'], 0);
    await api(`/api/projects/${projectId}/plan/command/apply?proposal_id=${proposed.proposal_id}`, { method: 'POST' });
    state.busy = null;
    notice('Cambio aplicado. La vista previa quedó desactualizada — pulsa «Renderizar el corte actual» cuando termines de editar.');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    await loadProject(projectId);
    throw error;
  }
}

// Human-readable current intro/outro fade state for the edit-style card.
function fadesLabel(plan) {
  const t = plan?.transitions || {};
  const intro = Number(t.intro_fade_seconds || 0);
  const outro = Number(t.outro_fade_seconds || 0);
  if (!intro && !outro) return 'Ahora: sin fundidos.';
  return `Ahora: apertura ${intro.toFixed(1)}s · cierre ${outro.toFixed(1)}s.`;
}

async function setFades(intro, outro) {
  try {
    await applyPlanOp(
      { op: 'set_fades', intro_seconds: intro, outro_seconds: outro },
      'Ajustando fundidos');
  } catch (error) { notice(error.message, true); }
}

async function musicSuggest() {
  const projectId = state.activeProjectId;
  try {
    setBusy('Buscando música', ['Buscando pistas acordes al corte'], 0);
    const result = await api(`/api/projects/${projectId}/plan/music/suggest`, { method: 'POST' });
    // If the user switched projects while the search ran, do NOT touch busy
    // state or navigate — that would yank the UI back to the old project
    // (Codex review 2026-09-06). Store the (project-bound) candidates regardless.
    state.musicCandidates = { projectId, list: Array.isArray(result?.candidates) ? result.candidates : [] };
    state.musicSources = result?.sources || {};
    if (state.activeProjectId !== projectId) return;
    state.busy = null;
    notice('Opciones de música listas — TikTok e Instagram en Publicar.');
    await loadProject(projectId);
  } catch (error) {
    if (state.activeProjectId !== projectId) return;
    state.busy = null;
    notice(error.message, true);
    await loadProject(projectId);
  }
}

// The last suggestion's candidates, but only if they belong to the OPEN project.
function currentMusicCandidates() {
  return (state.musicCandidates?.projectId === state.activeProjectId)
    ? (state.musicCandidates.list || []) : [];
}

// Switch to one of the alternate candidates returned by the last suggestion.
async function musicUseCandidate(index) {
  const candidate = currentMusicCandidates()[index];
  if (!candidate) return;
  try {
    await applyPlanOp(
      { op: 'set_music_recommendation', recommended: candidate },
      'Cambiando la música');
  } catch (error) { notice(error.message, true); }
}

async function musicRemove() {
  try {
    await applyPlanOp({ op: 'remove_music' }, 'Quitando música incorporada');
  } catch (error) { notice(error.message, true); }
}

async function musicSetBed() {
  const assets = (state.activeProject?.inventory?.assets || [])
    .filter((a) => a.media_type === 'audio' || a.media_type === 'video');
  if (!assets.length) {
    notice('Agrega primero un archivo de audio en Metraje.', true);
    return;
  }
  const list = assets.map((a, i) => `${i + 1}. ${a.filename || a.asset_id}`).join('\n');
  const pick = window.prompt(
    `¿Qué archivo usar como música de fondo?\n${list}\n\nEscribe el número:`);
  const idx = Number(pick) - 1;
  if (!Number.isInteger(idx) || idx < 0 || idx >= assets.length) return;
  try {
    await applyPlanOp(
      { op: 'set_music_bed', asset_id: assets[idx].asset_id },
      'Incorporando música al corte');
  } catch (error) { notice(error.message, true); }
}

/* ------------------------------------------------------------------ */
/* METRAJE                                                             */

function formatDuration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`;
}

async function loadStepTimes() {
  const box = $('#step-times');
  if (!box) return;
  try {
    const costs = await api(`/api/projects/${state.activeProjectId}/costs`);
    const times = (costs.step_times || []).filter((t) => t.seconds > 0.5);
    if (!times.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<p class="step-times">⏱ ${times.map((t) =>
      `${escapeHtml(JOB_LABELS[t.kind] || t.kind)}: <strong>${formatDuration(t.seconds)}</strong>`
    ).join(' · ')}</p>`;
  } catch { box.innerHTML = ''; }
}

function assetObservations(assetId) {
  const moments = [];
  for (const run of state.runs || []) {
    for (const item of run.observations || []) {
      if (item.asset_id !== assetId) continue;
      if (item.normalization_status && item.normalization_status !== 'accepted') continue;
      if (item.evidence_type === 'speech') continue;  // visual reading only
      moments.push({
        start: item.start_seconds, end: item.end_seconds,
        caption: item.reviewed_caption || item.caption,
      });
    }
  }
  return moments.sort((a, b) => a.start - b.start);
}

function mediaWorkspace(project) {
  const media = project.inventory?.assets || [];
  const used = usedAssetIds(project);
  const filter = state.mediaFilter;
  const visible = media.filter((asset) => {
    if (filter === 'used') return used.has(asset.asset_id);
    if (filter === 'unused') return !used.has(asset.asset_id);
    if (filter === 'audio') return asset.media_type === 'audio';
    return true;
  });
  return `
    <section>
      <div class="section-header">
        <div><span class="eyebrow">Metraje</span><h2>${media.length} archivos en este vlog</h2></div>
      </div>
      ${unanalyzedBanner(project)}
      ${project.footage_summary ? `
        <p class="footage-summary">🎬 ${escapeHtml(project.footage_summary)}</p>` : ''}
      <p class="muted folder-row">Carpeta:
        <code>${escapeHtml(`${state.status?.workspace || ''}/${project.source_directory || ''}`)}</code>
        <button class="ghost compact" id="copy-folder-path">Copiar ruta</button>
        <span class="muted">— pégala en tu explorador de archivos para inspeccionar
        los originales.</span></p>
      <label class="add-clips">
        + Agregar clips o notas de voz
        <input type="file" id="add-clips-input" multiple accept="video/*,image/*,audio/*" />
      </label>
      <div class="media-filters">
        ${[['all', 'Todos'], ['used', 'Usados'], ['unused', 'Sin usar'], ['audio', 'Audio']]
          .map(([id, label]) => `<button class="tab small ${filter === id ? 'active' : ''}" data-media-filter="${id}">${label}</button>`).join('')}
      </div>
      <div class="media-grid">
        ${visible.map((asset) => {
          const thumbnail = asset.thumbnail_url
            ? `<img loading="lazy" src="${escapeHtml(asset.thumbnail_url)}" alt="" />`
            : '<div class="video-placeholder">Sin vista previa</div>';
          const isUsed = used.has(asset.asset_id);
          return `
            <article class="media-card ${isUsed ? 'used' : ''}">
              <div class="media-thumb">${thumbnail}
                ${isUsed ? '<span class="used-badge">en el corte</span>' : ''}
                <button class="remove-asset" data-remove-asset="${escapeHtml(asset.asset_id)}" title="Quitar del proyecto y borrar el archivo de la laptop">✕</button>
              </div>
              <div class="media-info">
                <strong title="${escapeHtml(asset.filename)}">${escapeHtml(asset.filename)}</strong>
                <span>${asset.duration_seconds ? `${Number(asset.duration_seconds).toFixed(0)}s` : 'foto'}
                  ${asset.media_url ? ` · <a href="${escapeHtml(asset.media_url)}" target="_blank" rel="noopener">ver archivo</a>` : ''}
                  ${project.plan ? ` · <a href="#" data-chat-mention="${escapeHtml(asset.filename)}" title="Menciona este clip en el chat para pedir usarlo">💬 usar en el chat</a>` : ''}</span>
                ${(() => {
                  const moments = assetObservations(asset.asset_id);
                  if (!moments.length) return '';
                  return `
                    <details class="clip-observations">
                      <summary>Qué vio el editor (${moments.length})</summary>
                      <ul>${moments.map((m) => `
                        <li><span class="muted">${Number(m.start).toFixed(0)}–${Number(m.end).toFixed(0)}s</span>
                        ${escapeHtml(m.caption || '')}</li>`).join('')}</ul>
                    </details>`;
                })()}
              </div>
            </article>
          `;
        }).join('') || '<p class="muted">Nada con este filtro.</p>'}
      </div>
      ${project.plan ? `
        <div class="section-header" style="margin-top:1.5rem"><div><span class="eyebrow">Valor de cada clip</span>
        <h2>Qué aporta cada clip a este vlog</h2></div>
        <p class="muted">Segundos en el corte, citas en historias y momentos destacados.
        Los «descartables» no aportan a esta historia — puedes borrarlos con confianza.</p></div>
        <div class="clip-score-list" id="clip-score-list">Cargando…</div>` : ''}
    </section>
  `;
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

/* ------------------------------------------------------------------ */
/* PUBLICAR                                                            */

function publishWorkspace(project) {
  const renderUrl = project.outputs?.render?.url;
  const proxyExport = project.outputs?.xmeml_proxies;
  const duration = project.plan?.project?.duration_seconds;
  const concept = (project.concepts || []).find((c) => c.concept_id === project.plan?.concept_id);
  const missing = concept?.missing_shots || [];
  const music = musicRecommendation(project.plan);
  return `
    <section class="publish-grid">
      <article class="card">
        <h3>Video final</h3>
        ${renderUrl ? `
          ${project.outputs?.render?.fresh === false
            ? '<p class="notice error">Este archivo es de un corte anterior — re-renderiza en Edición antes de publicar.</p>' : ''}
          <p class="muted">✓ ${duration ? `${Number(duration).toFixed(0)} segundos` : 'renderizado'} · corte ${project.plan?.revision || 1}</p>
          <a class="primary button-link" href="${escapeHtml(renderUrl)}" download="vlog.mp4">Descargar MP4</a>` : `
          <p class="muted">Aún no hay render — vuelve a Edición.</p>`}
      </article>
      <article class="card">
        <h3>Seguir editando en OpenTake</h3>
        <div id="opentake-stale"></div>
        <p class="muted">Coloca este corte en la línea de tiempo abierta de OpenTake
        (reemplaza lo que haya allí), edita a mano, y trae los cambios de vuelta.</p>
        <div class="review-actions">
          <button class="secondary compact" id="opentake-place">Colocar en OpenTake</button>
          <button class="secondary compact" id="opentake-sync">Traer cambios</button>
        </div>
        <div id="opentake-diff"></div>
      </article>
      <article class="card">
        <h3>Seguir editando en DaVinci Resolve</h3>
        <p class="muted">Prepara la línea de tiempo más proxies (Resolve en Linux no
        lee el H.264 del teléfono directamente).</p>
        ${proxyExport ? `
          <p>Listo: en Resolve usa <strong>File → Import → Timeline</strong> y elige<br>
          <code>runtime/projects/${escapeHtml(project.project_id)}/outputs/timeline-davinci-proxies.xml</code></p>` : ''}
        <button class="secondary compact" id="prepare-export">${proxyExport ? 'Reconstruir archivos' : 'Preparar archivos'}</button>
      </article>
      <article class="card music-card">
        <h3>🎵 Música para este video</h3>
        ${!music ? `
          <p class="muted">Este corte no tiene música todavía. Deja que el
          modelo sugiera pistas acordes al ritmo y el tono; tú eliges cuál
          agregar como audio nativo al publicar en IG/TikTok (sin incrustar
          audio, sin reclamos de copyright).</p>
          ${currentMusicCandidates().length ? `
            <p class="muted">Elige una pista para agregarla (⚠ = sugerida por IA, sin verificar):</p>
            ${musicAlternatesHtml({})}
            <div class="review-actions">
              <button class="secondary compact" id="music-suggest">Buscar otra vez</button>
              <button class="secondary compact" id="music-set-bed">Incorporar música al MP4…</button>
            </div>`
          : `
            <div class="review-actions">
              <button class="primary compact" id="music-suggest">Sugerir música</button>
              <button class="secondary compact" id="music-set-bed">Incorporar música al MP4…</button>
            </div>`}`
        : music.mode === 'bed' ? `
          <p class="muted">Este corte lleva música incorporada en el MP4
          (mezclada y bajada bajo la voz). Para IG/TikTok, considera quitarla y
          usar audio nativo — mejor alcance y sin reclamos de copyright.</p>
          <button class="secondary compact" id="music-remove">Quitar música incorporada</button>` : `
          <p class="muted">Al publicar en IG/TikTok, agrega esta música
          <em>dentro de la app</em> — favorece el alcance y evita reclamos de
          copyright:</p>
          <ul class="music-reco">
            ${music.name ? `<li><strong>Pista:</strong> ${escapeHtml(music.name)}
              ${music.platform === 'instagram' ? '<span class="chip">Instagram</span>' : ''}
              ${music.trendState === 'rising' ? '<span class="chip">tendencia</span>' : ''}</li>` : ''}
            ${music.vibe ? `<li><strong>Vibra:</strong> ${escapeHtml(music.vibe)}</li>` : ''}
            ${music.bpm ? `<li><strong>Tempo:</strong> ~${Math.round(music.bpm)} BPM</li>` : ''}
            ${music.energy ? `<li><strong>Energía:</strong> ${escapeHtml(music.energy)}</li>` : ''}
            ${music.platformAudioId ? '<li class="muted">Se agrega nativamente al publicar — no se incrusta audio.</li>' : ''}
          </ul>
          ${musicAlternatesHtml(music)}
          <div class="review-actions">
            <button class="secondary compact" id="music-suggest">Otra sugerencia</button>
            <button class="secondary compact" id="music-set-bed">Incorporar al MP4…</button>
            <button class="ghost compact" id="music-remove">Quitar</button>
          </div>`}
      </article>
      <article class="card style-card">
        <h3>🎬 Estilo de edición</h3>
        <p class="muted">Apertura y cierre con fundido a negro. ${fadesLabel(project.plan)}</p>
        <div class="review-actions">
          <button class="secondary compact" data-fades="0.4,0.6">Suaves</button>
          <button class="secondary compact" data-fades="0.8,1.0">Marcados</button>
          <button class="ghost compact" data-fades="0,0">Sin fundidos</button>
        </div>
        <p class="muted" style="margin-top:.6rem">Para un corte concreto pide en
        Edición, p. ej. «pon un fundido a negro después de la escena 2».</p>
      </article>
      ${missing.length ? `
      <article class="card reco-card">
        <h3>Para fortalecer este video</h3>
        <ul>${missing.map((shot) => `
          <li class="missing-shot">
            <span class="priority ${escapeHtml(shot.priority)}">${escapeHtml(shot.priority)}</span>
            ${escapeHtml(shot.recording_instruction)}
            ${shot.fallback ? `<div class="muted">Si no: ${escapeHtml(shot.fallback)}</div>` : ''}
          </li>`).join('')}</ul>
        <p class="muted">Graba estos clips o una voz en off, agrégalos en Metraje y
        pide el cambio en Edición.</p>
      </article>` : ''}
    </section>
  `;
}

/* ------------------------------------------------------------------ */
/* DIAGNÓSTICO                                                         */

function diagnosticsWorkspace() {
  const capabilities = state.status?.capabilities;
  const visual = capabilities?.visual?.find((item) => item.id === 'owned-live-visual');
  const speech = capabilities?.speech?.find((item) => item.id === 'faster-whisper');
  const rows = [
    ['Comprensión visual', visual?.ready, visual?.ready ? 'Modelo en la nube conectado' : 'Faltan llaves API en .env'],
    ['Transcripción de habla', speech?.ready, speech?.ready ? 'Local, en esta máquina' : 'No instalado'],
    ['Render de video', capabilities?.render?.ready, 'Local, en esta máquina'],
    ['Export DaVinci', true, 'Importación verificada'],
  ];
  const steps = [
    { id: 'analyze-visual', label: 'Re-analizar metraje (forzado)' },
    { id: 'analyze-speech', label: 'Re-transcribir habla (forzado)' },
    { id: 'generate-concepts', label: 'Regenerar ideas' },
    { id: 'render', label: 'Re-renderizar' },
    { id: 'render-captions', label: 'Re-renderizar con subtítulos' },
    { id: 'exports', label: 'Reconstruir exports' },
  ];
  return `
    <section>
      <div class="section-header"><div><span class="eyebrow">Diagnóstico</span>
      <h2>Estado del sistema y controles manuales</h2></div>
      <button class="ghost compact" id="diagnostics-back">← Volver</button></div>
      <div class="capability-list">
        ${rows.map(([label, ready, detail]) => `
          <div class="capability ${ready ? 'ready' : ''}"><i></i>
            <div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(detail)}</span></div>
          </div>`).join('')}
      </div>
      <div class="pipeline-grid" style="margin-top:1rem">
        ${steps.map((step) => `
          <button class="pipeline-step" data-pipeline="${step.id}">
            <strong>${escapeHtml(step.label)}</strong>
          </button>`).join('')}
      </div>
      <div class="section-header" style="margin-top:1.4rem"><div><span class="eyebrow">Modelos de IA</span></div></div>
      <div id="model-picker" class="model-picker-slot"></div>
      <div class="section-header" style="margin-top:1.4rem"><div><span class="eyebrow">Costos (estimados)</span></div></div>
      <div id="costs-panel" class="sync-diff">Cargando costos…</div>
      <div class="section-header" style="margin-top:1.4rem"><div><span class="eyebrow">Estilos de referencia</span></div></div>
      <div id="reference-panel" class="sync-diff">Cargando referencias…</div>
      <div id="jobs-panel" class="sync-diff" style="margin-top:1rem">Cargando trabajos…</div>
      <details class="advanced" style="margin-top:1rem">
        <summary>Datos crudos (runs, telemetría, estado)</summary>
        <div id="raw-panel" class="sync-diff">Abriendo…</div>
      </details>
    </section>
  `;
}

async function loadCostsPanel() {
  const box = $('#costs-panel');
  if (!box) return;
  try {
    const costs = await api(`/api/projects/${state.activeProjectId}/costs`);
    if (!costs.rows.length) { box.innerHTML = '<p class="muted">Sin llamadas medidas todavía.</p>'; return; }
    const fmt = (n) => (n || 0).toLocaleString('es-MX');
    box.innerHTML = `
      <table class="costs-table">
        <tr><th>Paso</th><th>Modelo</th><th>Tokens in</th><th>Tokens out</th><th>USD</th></tr>
        ${costs.rows.map((row) => `
          <tr>
            <td>${escapeHtml(row.kind || '')}</td>
            <td>${escapeHtml(row.model || '—')}</td>
            <td>${fmt(row.prompt_tokens)}</td>
            <td>${fmt(row.completion_tokens)}</td>
            <td>${row.est_usd === null ? '<span class="muted">sin precio</span>' : `$${row.est_usd.toFixed(3)}`}</td>
          </tr>`).join('')}
      </table>
      <p class="muted">${costs.total_est_usd !== null
        ? `Total estimado: $${costs.total_est_usd.toFixed(3)}${costs.all_priced ? '' : ' (parcial — hay modelos sin precio)'}`
        : 'Para ver dólares, pon tus tarifas reales en app/pricing.json (USD por millón de tokens).'}
      Aún sin registrar: ${costs.unmetered.map(escapeHtml).join('; ')}.</p>
      ${(costs.step_times || []).length ? `<p class="step-times">⏱ ${costs.step_times.map((t) =>
        `${escapeHtml(JOB_LABELS[t.kind] || t.kind)}: <strong>${formatDuration(t.seconds)}</strong>`
      ).join(' · ')}</p>` : ''}`;
  } catch (error) { box.textContent = error.message; }
}

async function loadReferencePanel() {
  const box = $('#reference-panel');
  if (!box) return;
  try {
    const [{ references }, { styles }] = await Promise.all([
      api('/api/styles/references'), api('/api/styles'),
    ]);
    box.innerHTML = `
      <p class="muted">Deja videos de referencia en <code>references/</code> y analízalos aquí (una llamada al modelo visual por video).</p>
      ${references.length ? references.map((ref) => `
        <div class="revision-row">
          <strong>${escapeHtml(ref.filename)}</strong>
          <span>${(ref.size_bytes / 1e6).toFixed(0)} MB</span>
          <button class="secondary compact" data-analyze-ref="${escapeHtml(ref.filename)}">
            ${ref.analyzed ? 'Re-analizar' : 'Analizar estilo'}
          </button>
        </div>`).join('') : '<p class="muted">La carpeta references/ está vacía.</p>'}
      ${styles.length ? `<p class="muted">${styles.length} estilo(s) en la biblioteca.</p>` : ''}
      ${styles.filter((s) => !s.invalid).length >= 2 ? `
        <button class="secondary compact" id="combine-styles">Combinar todos en un estilo multi-referencia</button>` : ''}`;
    $('#combine-styles')?.addEventListener('click', async () => {
      const name = window.prompt('Nombre del estilo combinado:', 'mi estilo');
      if (!name) return;
      try {
        const valid = styles.filter((s) => !s.invalid).map((s) => s.style_id);
        await api('/api/styles/combine', {
          method: 'POST',
          body: JSON.stringify({ style_ids: valid, name }),
        });
        notice('Estilo multi-referencia creado — más referencias, más confianza.');
        loadReferencePanel();
      } catch (error) { notice(error.message, true); }
    });
    box.querySelectorAll('[data-analyze-ref]').forEach((button) => {
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          notice('Analizando la referencia…');
          const job = await api('/api/styles/analyze', {
            method: 'POST', body: JSON.stringify({ filename: button.dataset.analyzeRef }),
          });
          await pollJob(job.job_id);
          notice('Estilo extraído — disponible en Historia.');
          loadReferencePanel();
        } catch (error) { notice(error.message, true); button.disabled = false; }
      });
    });
  } catch (error) { box.textContent = error.message; }
}

async function loadRawPanel() {
  const box = $('#raw-panel');
  if (!box) return;
  try {
    const runs = (state.activeProject?.provider_runs || []).map((run) =>
      `<p><code>${escapeHtml(run.run_key)}</code> · ${escapeHtml(run.provider?.adapter || '')} ${escapeHtml(run.provider?.model || '')}</p>`
    ).join('') || '<p class="muted">Sin runs.</p>';
    let telemetry = '';
    try {
      const data = await api(`/api/projects/${state.activeProjectId}/analysis/telemetry`);
      telemetry = `<pre>${escapeHtml(JSON.stringify(data, null, 2).slice(0, 3000))}</pre>`;
    } catch { telemetry = '<p class="muted">Sin telemetría.</p>'; }
    box.innerHTML = `<strong>Runs</strong>${runs}<strong>Telemetría</strong>${telemetry}`;
  } catch (error) { box.textContent = error.message; }
}

async function loadJobsPanel() {
  const box = $('#jobs-panel');
  if (!box) return;
  try {
    const payload = await api('/api/jobs');
    const jobs = (payload.jobs || [])
      .filter((job) => job.project_id === state.activeProjectId)
      .slice(-12).reverse();
    box.innerHTML = jobs.length
      ? jobs.map((job) => `<p><code>${escapeHtml(JOB_LABELS[job.kind] || job.kind)}</code> · ${escapeHtml(JOB_STATUS_LABELS[job.status] || job.status)}${job.error ? ` — ${escapeHtml(job.error.slice(0, 120))}` : ''}</p>`).join('')
      : '<p class="muted">Sin trabajos registrados para este proyecto.</p>';
  } catch (error) { box.textContent = error.message; }
}

/* ------------------------------------------------------------------ */
/* Main render                                                         */

function renderProject() {
  const project = state.activeProject;
  if (!project) return;
  const phase = phaseOf(project);
  if (!state.workspace) {
    state.workspace = localStorage.getItem(`workspace:${state.activeProjectId}`) || defaultWorkspace(project);
  }
  if ((state.workspace === 'edit' || state.workspace === 'publish') && !project.plan) {
    state.workspace = 'story';
  }
  $('#project-title').textContent = project.name;
  $('#project-status').textContent = state.busy ? 'Trabajando…' : (PHASE_LABEL[phase] || project.status.replaceAll('_', ' '));
  renderProjectList();
  renderTabs(project);
  renderOverflow();

  let main;
  if (state.busy) {
    main = busyCard();
  } else if (state.pendingStory) {
    main = confirmStorySection(project);
  } else if (state.workspace === 'diagnostics') {
    main = diagnosticsWorkspace();
  } else if (state.workspace === 'edit') {
    main = editWorkspace(project);
  } else if (state.workspace === 'media') {
    main = mediaWorkspace(project);
  } else if (state.workspace === 'publish') {
    main = publishWorkspace(project);
  } else {
    main = storyWorkspace(project);
  }

  $('#project-view').classList.remove('loading');
  $('#project-view').innerHTML = remoteJobsBanner() + main;
  wireHandlers();
}

/* Cross-device visibility: `state.busy` only exists on the device that STARTED
   the work — the phone saw nothing while the laptop analyzed (user report
   2026-09-08). A light poller keeps `state.remoteJobs` filled with the active
   project's queued/running server jobs; every device shows this passive banner
   (unless it is the one displaying its own busy card for that work). */
const REMOTE_JOB_LABELS = {
  visual_analysis: 'Analizando el metraje',
  speech_analysis: 'Transcribiendo el habla',
  voiceover_analysis: 'Analizando la voz en off',
  concept_generation: 'Escribiendo ideas de historia',
  plan_revision: 'Revisando el plan',
  render: 'Renderizando el corte',
  editable_exports: 'Preparando archivos de editor',
  style_analysis: 'Analizando estilo de referencia',
  source_context_analysis: 'Leyendo contexto del metraje',
  drive_import: 'Importando de Drive',
};

function remoteJobsBanner() {
  const jobs = state.remoteJobs || [];
  if (!jobs.length || state.busy) return '';
  const labels = [...new Set(jobs.map((j) => REMOTE_JOB_LABELS[j.kind] || j.kind))];
  return `<div class="banner remote-jobs-banner">
    <span>⏳ Trabajando en este proyecto: ${labels.map(escapeHtml).join(' · ')}…
    <span class="muted">(puede haberse iniciado en otro dispositivo; la vista se
    actualizará al terminar)</span></span>
  </div>`;
}

let remoteJobsTimer = null;
async function pollRemoteJobs() {
  if (document.visibilityState !== 'visible' || !state.activeProjectId) return;
  const projectId = state.activeProjectId;
  try {
    const { jobs } = await api('/api/jobs');
    if (state.activeProjectId !== projectId) return;
    const active = jobs.filter((j) => j.project_id === projectId
      && ['queued', 'running'].includes(j.status));
    const before = new Set((state.remoteJobs || []).map((j) => j.job_id));
    const finishedOne = [...before].some((id) => !active.some((j) => j.job_id === id));
    const changed = active.length !== (state.remoteJobs || []).length
      || active.some((j) => !before.has(j.job_id));
    state.remoteJobs = active;
    if (finishedOne && !state.busy) {
      // work started elsewhere just completed — pull the fresh state in
      await loadProject(projectId);
    } else if (changed && !state.busy) {
      renderProject();
    }
  } catch { /* transient — try again next tick */ }
}
remoteJobsTimer = setInterval(pollRemoteJobs, 8000);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') pollRemoteJobs();
});

function wireHandlers() {
  $('#create-vlog')?.addEventListener('click', createVlog);
  $('#more-ideas-form')?.addEventListener('submit', regenerateIdeas);
  document.querySelectorAll('[data-keep-story]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleKeptStory(button.dataset.keepStory);
    });
  });
  document.querySelectorAll('[data-make-story]').forEach((button) => {
    button.addEventListener('click', () => startStory(button.dataset.makeStory));
  });
  $('#cancel-confirm')?.addEventListener('click', () => { state.pendingStory = null; renderProject(); });
  $('#continue-story')?.addEventListener('click', () => {
    const conceptId = state.pendingStory;
    state.pendingStory = null;
    makeStory(conceptId);
  });
  document.querySelectorAll('[data-review-action]').forEach((button) => {
    button.addEventListener('click', () => reviewClaim(button));
  });
  $('#skip-claims')?.addEventListener('click', () => {
    localStorage.removeItem(`showClaims:${state.activeProjectId}`);
    renderProject();
  });
  $('#unskip-claims')?.addEventListener('click', () => {
    localStorage.setItem(`showClaims:${state.activeProjectId}`, '1');
    renderProject();
  });
  $('#see-new-ideas')?.addEventListener('click', () => { state.workspace = 'story'; renderProject(); });
  $('#analyze-new-clips')?.addEventListener('click', () => analyzeNewClips(state.activeProjectId));
  document.querySelectorAll('[data-lane-remove]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const kind = btn.dataset.laneRemove === 'remove_voiceover' ? 'voz en off' : 'B-roll';
      if (!window.confirm(`¿Quitar ${kind} ${btn.dataset.laneEvent} del corte? (El archivo de audio/video no se borra.)`)) return;
      try {
        await applyPlanOp(
          { op: btn.dataset.laneRemove, event_id: btn.dataset.laneEvent },
          `Quitando ${kind}`);
      } catch (error) { notice(error.message, true); }
    });
  });
  $('#copy-folder-path')?.addEventListener('click', async (event) => {
    const path = event.currentTarget.previousElementSibling?.textContent || '';
    try { await navigator.clipboard.writeText(path); notice('Ruta copiada.'); }
    catch { notice('No se pudo copiar — selecciónala manualmente.', true); }
  });
  if ($('#style-section')) loadStyleSection();
  if ($('#step-times')) loadStepTimes();
  if ($('#model-picker')) loadModelPicker();
  if ($('#models-in-use')) loadModelsInUse();

  // Edición — one assistant chat (discussion + voiceover + confirm-gated edits)
  $('#revision-toggle')?.addEventListener('click', toggleRevisionHistory);
  const standingVoDraft = () => ({
    projectId: state.activeProjectId, sequence: true,
    start_seconds: 0, end_seconds: 0, text: '',
  });
  $('#vo-any-rec')?.addEventListener('click', () => {
    const draft = standingVoDraft();
    startVoiceRecording(draft, () => {
      state.pendingVoiceover = draft;
      const input = $('#vo-capture-input');
      if (input) { input.value = ''; input.click(); }
    });
  });
  $('#vo-any-file')?.addEventListener('click', () => {
    state.pendingVoiceover = standingVoDraft();
    const input = $('#vo-file-input');
    if (input) { input.value = ''; input.click(); }
  });
  $('#vo-any-drive')?.addEventListener('click', () => driveVoiceoverPicker(standingVoDraft()));
  $('#qa-cleanup')?.addEventListener('click', quickCleanup);
  $('#qa-vo-review')?.addEventListener('click', quickVoiceoverReview);
  $('#qa-captions')?.addEventListener('click', quickCaptions);
  $('#qa-story')?.addEventListener('click', () => { state.workspace = 'story'; renderProject(); });
  $('#rerender-now')?.addEventListener('click', quickRerender);
  const chatThread = $('#chat-thread');
  if (chatThread) {
    $('#chat-form')?.addEventListener('submit', submitChat);
    $('#chat-clear')?.addEventListener('click', clearChat);
    // Workspace re-renders replace the textarea via innerHTML — keep the
    // unsent draft per project so switching to Metraje and back (or a clip
    // mention) never eats what was typed (Codex review 2026-09-08).
    const chatBox = document.querySelector('#chat-form textarea');
    if (chatBox) {
      const saved = state.chatDrafts[state.activeProjectId];
      if (saved && !chatBox.value) chatBox.value = saved;
      chatBox.addEventListener('input', () => {
        state.chatDrafts[state.activeProjectId] = chatBox.value;
      });
    }
    $('#vo-capture-input')?.addEventListener('change', onVoiceoverFilePicked);
    $('#vo-file-input')?.addEventListener('change', onVoiceoverFilePicked);
    // Delegated: proposal cards and voiceover buttons are re-rendered often.
    chatThread.addEventListener('click', (event) => {
      const apply = event.target.closest('[data-chat-apply]');
      if (apply) { chatApply(apply.dataset.chatApply); return; }
      const dismiss = event.target.closest('[data-chat-dismiss]');
      if (dismiss) { dismissChatProposal(Number(dismiss.dataset.chatDismiss)); return; }
      const rec = event.target.closest('[data-chat-vo]');
      if (rec) { chatRecordVoiceover(Number(rec.dataset.chatVo)); return; }
      const pick = event.target.closest('[data-chat-vo-file]');
      if (pick) { chatFileVoiceover(Number(pick.dataset.chatVoFile)); return; }
      const drive = event.target.closest('[data-chat-vo-drive]');
      if (drive) { chatDriveVoiceover(Number(drive.dataset.chatVoDrive)); }
    });
    loadChat();
  }

  // Metraje
  document.querySelectorAll('[data-media-filter]').forEach((button) => {
    button.addEventListener('click', () => { state.mediaFilter = button.dataset.mediaFilter; renderProject(); });
  });
  $('#add-clips-input')?.addEventListener('change', addClipsToProject);
  document.querySelectorAll('[data-chat-mention]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      // Jump to the chat with the clip's filename appended to the (persisted)
      // draft — the backend attaches that clip's full evidence when a message
      // names it.
      const name = link.dataset.chatMention;
      const projectId = state.activeProjectId;
      const draft = (state.chatDrafts[projectId] || '').trim();
      state.chatDrafts[projectId] = draft
        ? `${draft} ${name} ` : `Usa el clip ${name} para `;
      state.workspace = 'edit';
      renderProject();
      document.querySelector('#chat-form textarea')?.focus();
    });
  });
  document.querySelectorAll('[data-remove-asset]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      removeAsset(button.dataset.removeAsset);
    });
  });
  if ($('#clip-score-list')) loadClipScores();

  // Publicar
  $('#prepare-export')?.addEventListener('click', prepareExport);
  $('#opentake-place')?.addEventListener('click', openTakePlace);
  $('#opentake-sync')?.addEventListener('click', openTakeSyncPreview);
  loadOpenTakeStale();
  $('#music-suggest')?.addEventListener('click', musicSuggest);
  $('#music-remove')?.addEventListener('click', musicRemove);
  $('#music-set-bed')?.addEventListener('click', musicSetBed);
  document.querySelectorAll('[data-music-use]').forEach((btn) => {
    btn.addEventListener('click', () => musicUseCandidate(Number(btn.dataset.musicUse)));
  });
  document.querySelectorAll('[data-fades]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const [intro, outro] = btn.dataset.fades.split(',').map(Number);
      setFades(intro, outro);
    });
  });

  // Diagnóstico
  document.querySelectorAll('[data-pipeline]').forEach((button) => {
    button.addEventListener('click', () => runAdvancedStep(button.dataset.pipeline, button));
  });
  $('#diagnostics-back')?.addEventListener('click', () => {
    state.workspace = defaultWorkspace(state.activeProject);
    renderProject();
  });
  if ($('#jobs-panel')) loadJobsPanel();
  if ($('#reference-panel')) loadReferencePanel();
  if ($('#costs-panel')) loadCostsPanel();
  document.querySelector('#raw-panel')?.closest('details')
    ?.addEventListener('toggle', (e) => { if (e.currentTarget.open) loadRawPanel(); }, { once: true });
}

/* ------------------------------------------------------------------ */
/* Media actions                                                       */

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
    if (!response.ok) throw new Error(payload.detail || `La subida falló (${response.status})`);
    notice(`${payload.added.length} archivo(s) agregados.`);
    const projectId = state.activeProjectId;
    await loadProject(projectId);
    // Same behavior as the Drive download: freshly added VISUAL clips chain the
    // incremental analysis right away so they become usable without hunting for
    // a button. Voice notes skip it (they're transcribed when placed).
    const visual = (payload.added || []).some((name) =>
      !/\.(m4a|mp3|wav|aac|ogg|opus|flac)$/i.test(name));
    if (visual && state.activeProjectId === projectId) {
      await analyzeNewClips(projectId);
    }
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

/* ------------------------------------------------------------------ */
/* Project lifecycle                                                   */

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
    state.workspace = 'story';
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
    `¿Eliminar «${project.name}»? Se borran el análisis, el corte y los renders. ` +
    'Tus clips originales NO se tocan.'
  );
  if (!confirmed) return;
  try {
    await api(`/api/projects/${project.project_id}`, { method: 'DELETE' });
    notice('Vlog eliminado. Tus clips siguen intactos.');
    await refreshProjects();
    const next = state.projects[0];
    if (next) await loadProject(next.project_id);
    else $('#project-view').innerHTML = '<div class="empty-state">Aún no hay vlogs — agrega tus clips.</div>';
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
        <span class="muted">${listing.media_count ? `${listing.media_count} archivo${listing.media_count === 1 ? '' : 's'} aquí` : ''}</span>
      </div>
      ${listing.parent !== null ? `<button type="button" class="browser-item up" data-browse="${escapeHtml(listing.parent)}">← atrás</button>` : ''}
      ${listing.directories.map((dir) => `
        <button type="button" class="browser-item" data-browse="${escapeHtml(dir.path)}">
          📁 ${escapeHtml(dir.name)}
          ${dir.media_count ? `<span class="count">${dir.media_count} clips</span>` : ''}
        </button>
      `).join('') || '<p class="muted">Sin subcarpetas.</p>'}
    `;
    container.querySelectorAll('[data-browse]').forEach((button) => {
      button.addEventListener('click', () => browseTo(button.dataset.browse));
    });
  } catch (error) {
    container.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ */
/* Pipeline actions                                                    */

function setBusy(title, steps, current, owner) {
  const startedAt = state.busy?.title === title ? state.busy.startedAt : Date.now();
  state.busy = { title, steps, current, startedAt, owner: owner ?? null,
                 progress: state.busy?.progress || null };
  renderProject();
  startBusyTicker();
}

// Clear the busy card only if THIS project still owns it — so a late-finishing
// operation neither clobbers a newer project's busy state nor leaves its own
// card stranded after the user navigated away.
function clearBusyIfOwner(owner) {
  if (state.busy && state.busy.owner === owner) state.busy = null;
}

async function pollJob(jobId) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === 'completed') return job;
    if (job.status === 'failed') throw new Error(job.error || 'Algo salió mal');
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

async function runStep(path, body, projectId = state.activeProjectId) {
  const result = await api(`/api/projects/${projectId}/${path}`, {
    method: 'POST',
    body: JSON.stringify(body || {}),
  });
  if (result.job_id) return pollJob(result.job_id);
  return result;
}

async function createVlog() {
  const projectId = state.activeProjectId;
  const steps = [];
  if (!hasRun('owned-live-visual')) steps.push({ label: 'Mirando tu metraje (unos minutos)', path: 'analysis/visual' });
  if (!hasRun('local-asr')) steps.push({ label: 'Escuchando el habla (local)', path: 'analysis/speech' });
  steps.push({ label: 'Escribiendo ideas de historia', path: 'concepts' });
  requestNotifyPermission();  // long op — you may leave the tab
  try {
    for (let index = 0; index < steps.length; index += 1) {
      setBusy('Creando tu vlog', steps.map((step) => step.label), index);
      await runStep(steps[index].path, undefined, projectId);
    }
    state.busy = null;
    state.workspace = 'story';
    notify('Ideas listas — elige una.');
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notify(error.message, { error: true });
    await loadProject(projectId);
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
  const projectId = state.activeProjectId;
  const steps = ['Fijando la historia', 'Cortando el video', 'Renderizando la vista previa', 'Preparando archivos de editor'];
  requestNotifyPermission();  // long op — you may leave the tab
  try {
    setBusy('Haciendo tu vlog', steps, 0);
    await api(`/api/projects/${projectId}/selection`, {
      method: 'POST', body: JSON.stringify({ concept_id: conceptId }),
    });
    setBusy('Haciendo tu vlog', steps, 1);
    await api(`/api/projects/${projectId}/plan`, {
      method: 'POST', body: JSON.stringify({ concept_id: conceptId }),
    });
    setBusy('Haciendo tu vlog', steps, 2);
    await runStep('render', undefined, projectId);
    setBusy('Haciendo tu vlog', steps, 3);
    await runStep('exports', { include_proxies: true }, projectId);
    state.busy = null;
    state.workspace = 'edit';
    localStorage.setItem(`workspace:${projectId}`, 'edit');
    notify('Tu vlog está listo — míralo.', { title: 'Corte listo' });
    await loadProject(projectId);
  } catch (error) {
    state.busy = null;
    notify(error.message, { error: true, title: 'Falló el render' });
    await loadProject(projectId);
  }
}

async function regenerateIdeas(event) {
  event?.preventDefault?.();
  const guidance = event?.currentTarget
    ? new FormData(event.currentTarget).get('guidance')?.toString().trim()
    : '';
  const kept = [...keptStoryIds()];
  try {
    setBusy('Pensando ángulos nuevos', ['Escribiendo ideas de historia'], 0);
    await runStep('concepts', {
      guidance: guidance || null,
      keep_concept_ids: kept.length ? kept : null,
    });
    state.busy = null;
    notice(kept.length ? `Ideas nuevas (conservando ${kept.length}).` : 'Ideas nuevas abajo.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
  }
}

async function prepareExport() {
  try {
    setBusy('Preparando archivos de editor', ['Exportando línea de tiempo + proxies'], 0);
    await runStep('exports', { include_proxies: true });
    state.busy = null;
    notice('Archivos de DaVinci listos.');
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
    caption = window.prompt('Corrige la descripción:', observation?.caption || '');
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
  'render-captions': 'render?burn_captions=true',
  exports: 'exports',
};

async function runAdvancedStep(stepId, button) {
  const path = ADVANCED_CALLS[stepId];
  if (!path) return;
  button.disabled = true;
  try {
    notice('Ejecutando…');
    const body = (stepId === 'analyze-visual' || stepId === 'analyze-speech')
      ? { force: true }
      : path === 'exports' ? { include_proxies: true } : {};
    await runStep(path, body);
    notice('Listo.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* OpenTake (Publicar)                                                 */

async function openTakePlace() {
  const project = state.activeProject;
  if (!project) return;
  const sure = window.confirm(
    'Esto REEMPLAZA la línea de tiempo del proyecto abierto en OpenTake con el corte actual. ¿Continuar?'
  );
  if (!sure) return;
  try {
    const summary = await api(`/api/projects/${project.project_id}/opentake/place`, { method: 'POST' });
    const extras = [];
    if (summary.placed_broll_clips) extras.push(`${summary.placed_broll_clips} B-roll`);
    if (summary.placed_voiceover_clips) extras.push(`${summary.placed_voiceover_clips} voz en off`);
    notice(`Colocado en OpenTake: ${summary.placed_clips} clips${extras.length ? ` + ${extras.join(' + ')}` : ''}.`);
  } catch (error) {
    notice(error.message, true);
  }
}

// Stale-session state (design §9): OpenTake was edited & saved since the last
// placement/sync, so there are changes to pull before continuing. A best-effort,
// no-live-MCP signal from the saved bundle on disk; shown as a designed banner,
// not a raw error.
async function loadOpenTakeStale() {
  const box = $('#opentake-stale');
  if (!box) return;
  try {
    const status = await api(`/api/projects/${state.activeProjectId}/opentake/status`);
    if (status?.opentake_changed) {
      box.innerHTML = `
        <div class="stale-banner">
          <strong>OpenTake cambió</strong>
          <span class="muted">Editaste en OpenTake desde la última vez.
          Trae esos cambios antes de seguir, o se perderán al colocar de nuevo.</span>
          <div class="review-actions">
            <button class="primary compact" id="stale-sync">Traer cambios de OpenTake</button>
          </div>
        </div>`;
      $('#stale-sync')?.addEventListener('click', openTakeSyncPreview);
    } else {
      box.innerHTML = '';
    }
  } catch { box.innerHTML = ''; }
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
        <ul>${preview.changes.map((c) => `<li><code>${escapeHtml(DIFF_LABELS[c.kind] || c.kind)}</code> ${escapeHtml(c.event_id || '')} — ${escapeHtml(c.detail || '')}</li>`).join('')}</ul>
        ${preview.staleness
          ? '<p class="notice error">Guarda el proyecto en OpenTake antes de poder aplicar.</p>'
          : '<button class="primary compact" id="opentake-sync-apply">Aplicar al corte y renderizar</button>'}
      </div>`;
    $('#opentake-sync-apply')?.addEventListener('click', async () => {
      try {
        setBusy('Trayendo tus cambios', ['Aplicando al plan', 'Renderizando'], 0);
        await api(`/api/projects/${project.project_id}/opentake/sync/apply`, { method: 'POST' });
        setBusy('Trayendo tus cambios', ['Aplicando al plan', 'Renderizando'], 1);
        await runStep('render');
        state.busy = null;
        notice('Cambios aplicados y corte re-renderizado.');
        await loadProject(project.project_id);
      } catch (error) {
        state.busy = null;
        notice(error.message, true);
        await loadProject(project.project_id);
      }
    });
  } catch (error) {
    notice(error.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* Loading                                                             */

const DIFF_LABELS = {
  deleted: 'eliminado', trimmed: 'recortado', split: 'dividido',
  moved: 'movido', unchanged: 'sin cambios',
  volume_changed: 'volumen', broll_added: 'B-roll añadido',
  broll_removed: 'B-roll quitado', broll_trimmed: 'B-roll recortado',
  broll_moved: 'B-roll movido', broll_audio_ignored: 'audio de B-roll ignorado',
  voiceover_added: 'voz en off añadida', voiceover_removed: 'voz en off quitada',
  voiceover_changed: 'voz en off cambiada',
};

const JOB_STATUS_LABELS = {
  queued: 'en cola', running: 'en curso', completed: 'completado',
  failed: 'falló', interrupted: 'interrumpido',
};

const JOB_LABELS = {
  visual_analysis: 'Mirando tu metraje',
  speech_analysis: 'Escuchando el habla',
  concept_generation: 'Escribiendo ideas',
  render: 'Renderizando la vista previa',
  editable_exports: 'Preparando archivos de editor',
  plan_revision: 'Recortando según tu instrucción',
  voiceover_analysis: 'Analizando la voz en off',
};

async function loadProject(projectId, { silent = false } = {}) {
  if (state.activeProjectId !== projectId) {
    state.workspace = null;
    state.mediaFilter = 'all';
    state.pendingStory = null;
    silent = false;  // a real project switch always shows the fresh load
  }
  state.activeProjectId = projectId;
  const generation = ++state.loadGeneration;
  const current = () => state.loadGeneration === generation
    && state.activeProjectId === projectId;
  // Silent refresh (e.g. regained focus): keep the cached view + runs until a
  // COMPLETE response arrives, so reopening offline never blanks the cut
  // (Codex review 2026-09-06). A normal load clears and shows "Cargando…".
  if (!silent) {
    state.runs = [];
    renderProjectList();
    if (!state.busy) $('#project-view').innerHTML = '<div class="empty-state">Cargando…</div>';
  } else {
    renderProjectList();
  }
  try {
    const project = await api(`/api/projects/${projectId}`);
    if (!current()) return;
    state.activeProject = project;
    // Render immediately; analysis runs load after (one corrupt run must
    // not blank the whole project — cross-review UX finding 10).
    renderProject();
    const settled = await Promise.allSettled(
      (project.provider_runs || []).map(async (run) => ({
        ...(await api(run.detail_url)),
        run_key: run.run_key,
      }))
    );
    if (!current()) return;
    state.runs = settled
      .filter((item) => item.status === 'fulfilled')
      .map((item) => item.value);
    const failed = settled.length - state.runs.length;
    if (failed) notice(`${failed} análisis no se pudieron cargar (ver Diagnóstico).`, true);
    if (!state.busy) {
      const jobs = (await api('/api/jobs')).jobs.filter(
        (job) => job.project_id === projectId && ['queued', 'running'].includes(job.status)
      );
      if (jobs.length) {
        setBusy(
          'Todavía trabajando — retomando donde iba',
          jobs.map((job) => JOB_LABELS[job.kind] || job.kind),
          0,
        );
        // the chronometer survives a refresh: the job knows when it began
        const runningJob = jobs.find((job) => job.status === 'running' && job.started_at);
        if (runningJob) {
          state.busy.startedAt = Date.parse(runningJob.started_at);
        }
        Promise.allSettled(jobs.map((job) => pollJob(job.job_id))).then(async (settled) => {
          if (!current()) return;
          state.busy = null;
          const failed = settled.filter((item) => item.status === 'rejected');
          if (failed.length) {
            notice(failed[0].reason?.message || 'Un trabajo falló — revisa Diagnóstico.', true);
          } else {
            notice('Listo.');
          }
          await loadProject(projectId);
        });
        return;
      }
    }
    if (current()) renderProject();
  } catch (error) {
    // A silent refresh that fails keeps the cached view (offline reopen);
    // a normal load surfaces the error.
    if (!silent) notice(error.message, true);
  }
}

async function refreshProjects() {
  const payload = await api('/api/projects');
  state.projects = payload.projects;
  renderProjectList();
  refreshDriveInbox();
}

let inboxPollTimer = null;
// in-flight Drive imports: folder -> {expected} — rendered inside the
// banner so an import never hijacks whatever project view is open
const activeImports = new Map();

async function refreshDriveInbox() {
  let banner = $('#drive-inbox');
  try {
    const payload = await api('/api/drive/inbox');
    // Actionable: never-imported folders AND imported ones that GREW (new clips
    // dropped into the same Drive folder after the project was created).
    const waiting = (payload.folders || [])
      .filter((folder) => !folder.imported || folder.new_files > 0);
    // a refresh must not forget an in-flight import: rebuild from the
    // server's running jobs (matched via the folder's slug)
    try {
      const running = (await api('/api/jobs')).jobs.filter(
        (job) => job.kind === 'drive_import'
          && ['queued', 'running'].includes(job.status)
      );
      for (const folder of waiting) {
        const job = running.find((j) => j.project_id === folder.slug);
        if (job && !activeImports.has(folder.name)) {
          activeImports.set(folder.name, { expected: folder.total_bytes });
          watchImportJob(folder.name, job.job_id);
        }
      }
    } catch { /* jobs endpoint transient */ }
    const receiving = waiting.some((folder) => folder.receiving);
    // while something is arriving, keep watching so the phone can see its
    // own Drive upload land without reloading
    clearTimeout(inboxPollTimer);
    if ((receiving || waiting.length) && document.visibilityState === 'visible') {
      inboxPollTimer = setTimeout(refreshDriveInbox, receiving ? 15000 : 60000);
    }
    if (!waiting.length) { banner?.remove(); return; }
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'drive-inbox';
      banner.className = 'banner';
      document.querySelector('.workspace')?.prepend(banner);
    }
    banner.innerHTML = `
      <span>☁️ Drive VlogInbox:</span>
      ${waiting.slice(0, 3).map((folder) => {
        const isUpdate = folder.imported;
        const bytes = isUpdate ? folder.new_bytes : folder.total_bytes;
        const count = isUpdate ? folder.new_files : folder.file_count;
        const size = bytes >= 1e9
          ? `${(bytes / 1e9).toFixed(1)} GB`
          : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
        const status = folder.receiving
          ? '<span class="inbox-receiving">⬆ recibiendo…</span>'
          : '<span class="inbox-ready">listo</span>';
        const importing = activeImports.has(folder.name);
        return `
        <span class="inbox-folder">
          <strong>${escapeHtml(folder.name)}</strong>
          <span class="muted">${count} clip${count === 1 ? '' : 's'}${isUpdate ? ' nuevo' + (count === 1 ? '' : 's') : ''} · ${size}</span>
          ${importing
            ? `<span class="inbox-receiving" data-import-progress="${escapeHtml(folder.name)}">⬇ importando…</span>
               <button class="secondary compact" data-drive-cancel="${escapeHtml(folder.name)}">Cancelar</button>`
            : `${status}
               <button class="primary compact" data-drive-import="${escapeHtml(folder.name)}"
                 ${folder.receiving ? 'disabled title="Espera a que Drive termine de recibir"' : ''}>
                 ${isUpdate
                   ? 'Descargar nuevos'
                   : folder.local_bytes > 0 && folder.local_bytes < folder.total_bytes
                     ? `Reanudar (~${Math.min(99, Math.round(folder.local_bytes / folder.total_bytes * 100))}%)`
                     : 'Importar'}
               </button>`}
        </span>`;
      }).join('')}
    `;
    banner.querySelectorAll('[data-drive-import]').forEach((button) => {
      button.addEventListener('click', () => importFromDrive(button.dataset.driveImport));
    });
    banner.querySelectorAll('[data-drive-cancel]').forEach((button) => {
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          await api('/api/drive/import/cancel', {
            method: 'POST', body: JSON.stringify({ folder: button.dataset.driveCancel }),
          });
        } catch (error) { notice(error.message, true); }
      });
    });
  } catch { banner?.remove(); /* rclone no configurado u offline */ }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    refreshDriveInbox();
    maybeRefreshActiveProject();
  } else clearTimeout(inboxPollTimer);
});

// Reopening the installed PWA must never show a stale cut: re-fetch the active
// project when the app regains focus — unless the user is mid-action (a job is
// running, a dialog is open, or they're typing an edit), and stay on the cached
// view when offline.
async function maybeRefreshActiveProject() {
  if (!state.activeProjectId || state.busy) return;
  if (document.querySelector('dialog[open]')) return;
  const el = document.activeElement;
  if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') && el.value) return;
  // Silent: keep the cached cut on screen until fresh data fully loads.
  try { await loadProject(state.activeProjectId, { silent: true }); } catch { /* offline: keep cached view */ }
}

async function importFromDrive(folder) {
  // progress lives in the Drive banner — the import belongs to its own
  // (future) project, never to whatever project happens to be open
  let progressTimer = null;
  try {
    const inbox = await api('/api/drive/inbox').catch(() => null);
    const entry = inbox?.folders?.find((f) => f.name === folder);
    const expected = entry?.total_bytes || 0;
    activeImports.set(folder, { expected });
    refreshDriveInbox();
    notice(entry?.imported
      ? `Descargando los clips nuevos de «${folder}» — puedes seguir trabajando.`
      : `Importando «${folder}» como proyecto nuevo — puedes seguir trabajando.`);
    progressTimer = startImportProgressTimer(folder, expected);
    const job = await api('/api/drive/import', {
      method: 'POST',
      body: JSON.stringify({ folder }),
    });
    const done = await pollJob(job.job_id);
    await refreshProjects();
    // Respect where the user IS: an update to a project they navigated away
    // from must neither yank them back nor start analysis behind their back —
    // the 🆕 banner offers it when they return (Codex review 2026-09-08). A
    // FIRST import still opens its new project (the click asked for that).
    const updated = done.result?.updated;
    const onIt = state.activeProjectId === done.result?.project_id;
    notice(updated
      ? `«${folder}»: ${(done.result.added || []).length} clip(s) nuevo(s) añadidos al proyecto.`
      : `«${folder}» importado — abriendo el proyecto.`);
    if (done.result?.project_id && (!updated || onIt)) {
      await loadProject(done.result.project_id);
    }
    // Downloaded clips are invisible until analyzed — chain the INCREMENTAL
    // analysis (new clips only) when the user is on the project they updated.
    if (updated && (done.result.added || []).length && onIt) {
      await analyzeNewClips(done.result.project_id);
    }
  } catch (error) {
    notice(error.message, true);
    await refreshProjects();
  } finally {
    clearInterval(progressTimer);
    activeImports.delete(folder);
    refreshDriveInbox();
  }
}

function startImportProgressTimer(folder, expected) {
  return setInterval(async () => {
    try {
      const local = await api(`/api/drive/local-progress?folder=${encodeURIComponent(folder)}`);
      const copied = Math.round(local.copied_bytes / 1e6);
      const label = expected
        ? `⬇ ${copied} / ${Math.round(expected / 1e6)} MB`
        : `⬇ ${copied} MB`;
      const el = document.querySelector(
        `[data-import-progress="${CSS.escape(folder)}"]`
      );
      if (el) el.textContent = label;
    } catch { /* transient */ }
  }, 3000);
}

async function watchImportJob(folder, jobId) {
  // a refresh must restore the LIVE progress too, not just the label
  const expected = activeImports.get(folder)?.expected || 0;
  const progressTimer = startImportProgressTimer(folder, expected);
  try {
    const done = await pollJob(jobId);
    await refreshProjects();
    const updated = done.result?.updated;
    const onIt = state.activeProjectId === done.result?.project_id;
    notice(updated
      ? `«${folder}»: ${(done.result.added || []).length} clip(s) nuevo(s) añadidos al proyecto.`
      : `«${folder}» importado — abriendo el proyecto.`);
    if (done.result?.project_id && (!updated || onIt)) {
      await loadProject(done.result.project_id);
    }
    if (updated && (done.result.added || []).length && onIt) {
      await analyzeNewClips(done.result.project_id);
    }
  } catch (error) {
    notice(error.message, true);
    await refreshProjects();
  } finally {
    clearInterval(progressTimer);
    activeImports.delete(folder);
    refreshDriveInbox();
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
            reject(new Error(payload.detail || `La subida falló (${xhr.status})`));
          }
        };
        xhr.onerror = () => reject(new Error('La subida falló — revisa el WiFi'));
        xhr.send(body);
      });
    } else {
      if (!form.get('source_directory')) throw new Error('Sube archivos o elige una carpeta.');
      submit.textContent = 'Indexando…';
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
    notice('Clips indexados. Pulsa «Crear mi vlog» cuando quieras.');
  } catch (error) {
    notice(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = 'Agregar mis clips';
  }
}

async function initialize() {
  try {
    const [status, projects] = await Promise.all([api('/api/status'), api('/api/projects')]);
    state.status = status;
    state.projects = projects.projects;
    renderSystemDot();
    refreshDriveInbox();  // the Drive banner must exist on first paint
    if (state.projects[0]) await loadProject(state.projects[0].project_id);
    else $('#project-view').innerHTML = '<div class="empty-state">Aún no hay vlogs — agrega tus clips.</div>';
  } catch (error) {
    notice(error.message, true);
  }
}

/* Receiver-side upload visibility: when the phone is sending media, every
   open browser shows the incoming transfer live. */
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
  } catch { /* la app puede estar reiniciando; ignorar */ }
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
    ? `${files.length} archivo(s) listos (${totalMb.toFixed(0)} MB) — pon nombre y pulsa «Agregar mis clips».`
    : 'Selección vacía.');
});
$('#close-dialog').addEventListener('click', () => dialog.close());
$('#cancel-dialog').addEventListener('click', () => dialog.close());
$('#new-project-form').addEventListener('submit', createProject);
$('#rename-project')?.addEventListener('click', async () => {
  const project = state.activeProject;
  if (!project) return;
  const current = project.name || '';
  const name = window.prompt('Nuevo nombre del proyecto:', current);
  if (name === null) return;                        // cancelled
  const trimmed = name.trim();
  if (!trimmed || trimmed === current) return;
  const projectId = state.activeProjectId;
  try {
    const res = await api(`/api/projects/${projectId}/rename`, {
      method: 'POST', body: JSON.stringify({ name: trimmed }),
    });
    // Update the header + sidebar in place, guarding against a project switch
    // that happened while the prompt/request was open.
    const row = (state.projects || []).find((p) => p.project_id === projectId);
    if (row) row.name = res.name;
    if (state.activeProjectId === projectId && state.activeProject) {
      state.activeProject.name = res.name;
      $('#project-title').textContent = res.name;
    }
    renderProjectList();
    notice('Nombre actualizado.');
  } catch (error) {
    notice(error.message, true);
  }
});

$('#overflow-button').addEventListener('click', (event) => {
  event.stopPropagation();
  const menu = $('#overflow-menu');
  menu.classList.toggle('hidden');
  $('#overflow-button').setAttribute('aria-expanded', String(!menu.classList.contains('hidden')));
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.overflow-wrap')) {
    $('#overflow-menu')?.classList.add('hidden');
    $('#overflow-button')?.setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    $('#overflow-menu')?.classList.add('hidden');
    $('#overflow-button')?.setAttribute('aria-expanded', 'false');
    $('#overflow-button')?.focus();
  }
});
$('#system-dot').addEventListener('click', () => {
  if (!state.activeProject) return;
  state.workspace = 'diagnostics';
  renderProject();
});

initialize();
