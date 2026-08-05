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

function isFixture(project) {
  return project.project_id === 'morning-routine';
}

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

function visibleProjects() {
  const showArchive = localStorage.getItem('showArchive') === '1';
  return state.projects.filter((project) =>
    showArchive || project.project_id !== 'morning-routine');
}

function renderProjectList() {
  const hasArchive = state.projects.some((project) => project.project_id === 'morning-routine');
  const showArchive = localStorage.getItem('showArchive') === '1';
  $('#project-list').innerHTML = visibleProjects().map((project) => `
    <button class="project-button ${project.project_id === state.activeProjectId ? 'active' : ''}" data-project-id="${escapeHtml(project.project_id)}">
      <span class="project-dot ${project.has_plan || project.status === 'ready' ? 'ready' : ''}"></span>
      <span class="project-copy">
        <strong>${escapeHtml(project.name)}${project.project_id === 'morning-routine' ? ' (archive)' : ''}</strong>
        <span>${project.asset_count} clips${project.has_plan ? ' · edited' : ''}</span>
      </span>
    </button>
  `).join('') + (hasArchive ? `
    <button class="ghost archive-toggle" id="toggle-archive">
      ${showArchive ? 'Hide archive' : 'Show archive (1)'}
    </button>` : '');
  document.querySelectorAll('[data-project-id]').forEach((button) => {
    button.addEventListener('click', () => loadProject(button.dataset.projectId));
  });
  $('#toggle-archive')?.addEventListener('click', () => {
    localStorage.setItem('showArchive', showArchive ? '0' : '1');
    renderProjectList();
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

function storyCard(concept) {
  const missing = concept.missing_shots || [];
  const required = missing.filter((shot) => shot.priority === 'required').length;
  const beats = (concept.structure || []).length;
  return `
    <article class="concept-card story-card">
      <div class="concept-top"><span class="eyebrow">${concept.target_duration_seconds}s · ${beats} scenes</span></div>
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
  return `
    <section>
      <div class="section-header">
        <div><span class="eyebrow">Pick a story</span><h2>The editor proposes, you decide</h2></div>
        <button class="secondary compact" id="more-ideas">More ideas</button>
      </div>
      <p class="muted">Every scene cites real moments in your clips. "Make this one" cuts the
      video, renders a preview, and prepares editor files — all in one go.</p>
      <div class="concept-grid">${(project.concepts || []).map(storyCard).join('')}</div>
      ${project.plan ? '<button class="ghost" id="back-to-result">← Back to the current cut</button>' : ''}
    </section>
  `;
}

function cutList(project) {
  const events = (project.plan?.tracks || []).find((track) => track.kind === 'video')?.events || [];
  return events.map((event, index) => `
    <li>
      <strong>${index + 1}.</strong> ${escapeHtml(event.asset_id)} ·
      ${(event.source_end_seconds - event.source_start_seconds).toFixed(1)}s
      <span class="muted">— ${escapeHtml(event.intent)}</span>
    </li>
  `).join('');
}

function resultSection(project) {
  const renderUrl = project.outputs?.render?.url;
  const revision = project.plan?.revision || 1;
  const proxyExport = project.outputs?.xmeml_proxies;
  const duration = project.plan?.project?.duration_seconds;
  return `
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
    <section>
      <details class="cut-details">
        <summary>What's in this cut (${(project.plan?.tracks?.find((t) => t.kind === 'video')?.events || []).length} scenes)</summary>
        <ol class="cut-list">${cutList(project)}</ol>
      </details>
    </section>
  `;
}

function needsCheckSection() {
  const pending = pendingClaims();
  if (!pending.length) return '';
  return `
    <section id="pending-review">
      <div class="section-header">
        <div><span class="eyebrow">Needs your check</span><h2>${pending.length} thing${pending.length === 1 ? '' : 's'} the editor wasn't sure about</h2></div>
        <p>Brands, emotions, or unclear audio the AI noticed but won't rely on unless you
        confirm. Everything else (${approvedCount()} observations) was solid and approved
        automatically. Ignoring these is fine.</p>
      </div>
      <div class="pending-grid">
        ${pending.map((observation) => `
          <article class="evidence-item">
            <div class="evidence-meta">
              <span>${escapeHtml(observation.filename || observation.asset_id)}</span>
              <span>${Number(observation.start_seconds).toFixed(1)}–${Number(observation.end_seconds).toFixed(1)}s</span>
            </div>
            <p>${escapeHtml(observation.caption)}</p>
            <div class="review-actions">
              <button class="ghost approve" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="approve">True — use it</button>
              <button class="ghost" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Fix wording</button>
              <button class="ghost reject" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="reject">Wrong — ignore it</button>
            </div>
          </article>
        `).join('')}
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
      <button class="ghost reject" id="delete-project">Delete this vlog (keeps your clips)</button>
      <div class="media-grid">
        ${media.map((asset) => {
          const thumbnail = asset.thumbnail_url
            ? `<img loading="lazy" src="${escapeHtml(asset.thumbnail_url)}" alt="" />`
            : '<div class="video-placeholder">No preview</div>';
          return `
            <article class="media-card">
              <div class="media-thumb">${thumbnail}</div>
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
  } else if (isFixture(project)) {
    main = `
      <section class="card start-card">
        <span class="eyebrow">Archived benchmark</span>
        <h2>${escapeHtml(project.name)}</h2>
        <p class="muted">This is the July proof-of-concept, kept for reference. Its evidence
        and outputs are readable through the API but it is not part of the daily flow.</p>
        ${project.outputs?.render?.url ? `<video controls preload="metadata" src="${escapeHtml(project.outputs.render.url)}"></video>` : ''}
      </section>
    `;
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
  $('#more-ideas')?.addEventListener('click', regenerateIdeas);
  $('#back-to-result')?.addEventListener('click', () => { state.forcePick = false; renderProject(); });
  $('#change-story')?.addEventListener('click', () => { state.forcePick = true; renderProject(); });
  $('#prepare-export')?.addEventListener('click', prepareExport);
  $('#revision-form')?.addEventListener('submit', submitRevision);
  document.querySelectorAll('[data-make-story]').forEach((button) => {
    button.addEventListener('click', () => makeStory(button.dataset.makeStory));
  });
  document.querySelectorAll('[data-review-action]').forEach((button) => {
    button.addEventListener('click', () => reviewClaim(button));
  });
  document.querySelectorAll('[data-pipeline]').forEach((button) => {
    button.addEventListener('click', () => runAdvancedStep(button.dataset.pipeline, button));
  });
  $('#delete-project')?.addEventListener('click', deleteProject);
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
    const next = visibleProjects()[0] || state.projects[0];
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

async function regenerateIdeas() {
  try {
    setBusy('Thinking of new angles', ['Writing story ideas'], 0);
    await runStep('concepts');
    state.busy = null;
    notice('Fresh ideas below.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    state.busy = null;
    notice(error.message, true);
    await loadProject(state.activeProjectId);
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
    renderProject();
  } catch (error) {
    notice(error.message, true);
  }
}

async function refreshProjects() {
  const payload = await api('/api/projects');
  state.projects = payload.projects.sort((a, b) =>
    (a.project_id === 'morning-routine') - (b.project_id === 'morning-routine'));
  renderProjectList();
}

async function createProject(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  const submit = event.currentTarget.querySelector('[type="submit"]');
  submit.disabled = true;
  submit.textContent = 'Indexing…';
  try {
    const project = await api('/api/projects', { method: 'POST', body: JSON.stringify(payload) });
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
    state.projects = projects.projects.sort((a, b) =>
      (a.project_id === 'morning-routine') - (b.project_id === 'morning-routine'));
    renderCapabilities();
    const preferred = state.projects.find((project) => project.project_id !== 'morning-routine')
      || state.projects[0];
    if (preferred) await loadProject(preferred.project_id);
  } catch (error) {
    notice(error.message, true);
  }
}

const dialog = $('#new-project-dialog');
$('#new-project-button').addEventListener('click', () => {
  dialog.showModal();
  browseTo('');
});
$('#close-dialog').addEventListener('click', () => dialog.close());
$('#cancel-dialog').addEventListener('click', () => dialog.close());
$('#new-project-form').addEventListener('submit', createProject);

initialize();
