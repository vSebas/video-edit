const state = {
  status: null,
  projects: [],
  activeProjectId: null,
  activeProject: null,
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
  if (!error) window.setTimeout(() => element.classList.add('hidden'), 5000);
}

function renderCapabilities() {
  const capabilities = state.status?.capabilities;
  if (!capabilities) return;
  const items = [
    capabilities.visual.find((item) => item.id === 'openstoryline'),
    capabilities.speech.find((item) => item.id === 'faster-whisper'),
    capabilities.render,
    capabilities.editable_exports,
  ];
  $('#capability-list').innerHTML = items.map((item) => `
    <div class="capability ${item.ready ? 'ready' : ''}">
      <i></i>
      <div><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail || (item.ready ? 'Ready' : 'Pending'))}</span></div>
    </div>
  `).join('');
}

function renderProjectList() {
  $('#project-list').innerHTML = state.projects.map((project) => `
    <button class="project-button ${project.project_id === state.activeProjectId ? 'active' : ''}" data-project-id="${escapeHtml(project.project_id)}">
      <span class="project-dot ${project.status === 'ready' ? 'ready' : ''}"></span>
      <span class="project-copy">
        <strong>${escapeHtml(project.name)}</strong>
        <span>${project.asset_count} assets · ${project.concept_count} concepts</span>
      </span>
    </button>
  `).join('');
  document.querySelectorAll('[data-project-id]').forEach((button) => {
    button.addEventListener('click', () => loadProject(button.dataset.projectId));
  });
}

function formatDuration(value) {
  if (!value) return 'still';
  return `${Number(value).toFixed(value < 10 ? 1 : 0)}s`;
}

function assetCard(asset) {
  const dimensions = asset.video?.width ? `${asset.video.width}×${asset.video.height}` : asset.audio ? 'audio' : 'image';
  const thumbnail = asset.thumbnail_url
    ? `<img loading="lazy" src="${escapeHtml(asset.thumbnail_url)}" alt="Thumbnail for ${escapeHtml(asset.filename)}" />`
    : '<div class="video-placeholder">No visual preview</div>';
  return `
    <article class="media-card">
      <div class="media-thumb">${thumbnail}<span class="media-type">${escapeHtml(asset.media_type || 'video')}</span></div>
      <div class="media-info">
        <strong title="${escapeHtml(asset.filename)}">${escapeHtml(asset.filename)}</strong>
        <span>${formatDuration(asset.duration_seconds)} · ${dimensions}</span>
      </div>
    </article>
  `;
}

function conceptCard(concept, project) {
  const selected = concept.concept_id === project.selected_concept_id;
  const planAvailable = concept.concept_id === project.plan?.concept_id;
  const weaknesses = (concept.weaknesses || []).slice(0, 2).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  const missing = (concept.missing_shots || []).length;
  return `
    <article class="concept-card ${selected ? 'selected' : ''}">
      <div class="concept-top"><span class="eyebrow">${escapeHtml(concept.topic)}</span><span class="duration">${concept.target_duration_seconds}s</span></div>
      <h3>${escapeHtml(concept.title)}</h3>
      <p class="hook"><strong>Hook:</strong> ${escapeHtml(concept.hook)}</p>
      <ul>${weaknesses}</ul>
      <p>${missing} missing-shot recommendation${missing === 1 ? '' : 's'}</p>
      <div class="concept-footer">
        <span class="${planAvailable ? 'plan-ready' : 'plan-pending'}">${planAvailable ? 'EDIT PLAN READY' : 'PLAN NOT COMPILED'}</span>
        <button class="${selected ? 'secondary' : 'primary'}" data-select-concept="${escapeHtml(concept.concept_id)}">${selected ? 'Selected' : 'Select'}</button>
      </div>
    </article>
  `;
}

function outputLinks(project) {
  const labels = { render: 'Review MP4', otio: 'OTIO timeline', xmeml: 'DaVinci XML' };
  return Object.entries(project.outputs || {}).map(([kind, output]) =>
    `<a href="${escapeHtml(output.url)}" ${kind === 'render' ? 'target="_blank"' : 'download'}>${labels[kind] || kind}</a>`
  ).join('');
}

function renderProject() {
  const project = state.activeProject;
  if (!project) return;
  $('#project-title').textContent = project.name;
  $('#project-status').textContent = project.status.replaceAll('_', ' ');
  renderProjectList();

  const visualGood = project.analysis.visual === 'reviewed' || project.analysis.visual === 'completed';
  const speechGood = project.analysis.speech === 'completed';
  const renderUrl = project.outputs?.render?.url;
  const media = project.inventory?.assets || [];
  const concepts = project.concepts || [];
  const selectedPlanReady = project.selected_concept_id === project.plan?.concept_id;
  const poster = media.find((asset) => asset.asset_id === 'img_0997')?.thumbnail_url
    || media.find((asset) => asset.thumbnail_url)?.thumbnail_url;

  const analysisCallout = concepts.length ? '' : `
    <div class="callout">
      <h3>Semantic analysis is still required</h3>
      <p>${escapeHtml(project.analysis.warning)} The technical inventory below is real and reusable, but this application will not fabricate story concepts until a visual or speech adapter supplies grounded observations.</p>
    </div>
  `;

  const conceptSection = concepts.length ? `
    <section>
      <div class="section-header"><div><span class="eyebrow">Creative direction</span><h2>Grounded concepts</h2></div><p>Selecting a concept does not silently invent a plan.</p></div>
      <div class="concept-grid">${concepts.map((concept) => conceptCard(concept, project)).join('')}</div>
    </section>
  ` : '';

  const planSection = project.plan ? `
    <section>
      <div class="section-header"><div><span class="eyebrow">Deterministic execution</span><h2>Approved edit plan</h2></div></div>
      <div class="card plan-card">
        <div>
          <span class="eyebrow">${escapeHtml(project.plan_summary?.format || '')}</span>
          <h3>${project.plan_summary?.duration_seconds}s · ${project.plan_summary?.tracks?.video || 0} video cuts · ${project.plan_summary?.tracks?.title || 0} title beats</h3>
          <p>${selectedPlanReady ? 'The selected concept matches the compiled plan.' : 'Select the chronological concept to render the existing compiled plan.'}</p>
          <div class="downloads">${outputLinks(project)}</div>
        </div>
        <div class="plan-actions">
          <button class="secondary" id="export-button" ${selectedPlanReady ? '' : 'disabled'}>Rebuild exports</button>
          <button class="primary" id="render-button" ${selectedPlanReady ? '' : 'disabled'}>Render video</button>
        </div>
      </div>
    </section>
  ` : '';

  $('#project-view').classList.remove('loading');
  $('#project-view').innerHTML = `
    <div class="hero">
      <article class="card summary-card">
        <span class="eyebrow">Footage understanding</span>
        <h2>${escapeHtml(project.footage_summary)}</h2>
        <p><strong>Prompt:</strong> ${escapeHtml(project.prompt || 'No creative prompt supplied.')}</p>
        <div class="meta-row">
          <span class="pill good">Technical: ${escapeHtml(project.analysis.technical)}</span>
          <span class="pill ${visualGood ? 'good' : 'warn'}">Visual: ${escapeHtml(project.analysis.visual)}</span>
          <span class="pill ${speechGood ? 'good' : 'warn'}">Speech: ${escapeHtml(project.analysis.speech)}</span>
          <span class="pill">${media.length} assets</span>
        </div>
      </article>
      <article class="card output-card">
        ${renderUrl ? `<video controls preload="metadata" ${poster ? `poster="${escapeHtml(poster)}"` : ''} src="${escapeHtml(renderUrl)}"></video>` : '<div class="video-placeholder">No rendered review yet</div>'}
      </article>
    </div>
    ${analysisCallout}
    <section>
      <div class="section-header"><div><span class="eyebrow">Source inventory</span><h2>Recorded media</h2></div><p>Files remain linked to their originals.</p></div>
      <div class="media-grid">${media.map(assetCard).join('')}</div>
    </section>
    ${conceptSection}
    ${planSection}
  `;

  document.querySelectorAll('[data-select-concept]').forEach((button) => {
    button.addEventListener('click', () => selectConcept(button.dataset.selectConcept));
  });
  $('#render-button')?.addEventListener('click', () => startJob('render'));
  $('#export-button')?.addEventListener('click', () => startJob('exports'));
}

async function loadProject(projectId) {
  state.activeProjectId = projectId;
  renderProjectList();
  $('#project-view').innerHTML = '<div class="empty-state">Loading project…</div>';
  try {
    state.activeProject = await api(`/api/projects/${projectId}`);
    renderProject();
  } catch (error) {
    notice(error.message, true);
  }
}

async function selectConcept(conceptId) {
  try {
    const result = await api(`/api/projects/${state.activeProjectId}/selection`, {
      method: 'POST',
      body: JSON.stringify({ concept_id: conceptId }),
    });
    notice(result.plan_available ? 'Concept selected. Its deterministic plan is ready.' : 'Concept selected, but its edit plan has not been compiled yet.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
  }
}

async function startJob(kind) {
  try {
    notice(kind === 'render' ? 'Render queued…' : 'Editable export queued…');
    const job = await api(`/api/projects/${state.activeProjectId}/${kind}`, { method: 'POST' });
    await pollJob(job.job_id);
  } catch (error) {
    notice(error.message, true);
  }
}

async function pollJob(jobId) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === 'completed') {
      notice(job.kind === 'render' ? 'Render completed.' : 'Editable exports rebuilt.');
      await loadProject(job.project_id);
      return;
    }
    if (job.status === 'failed') throw new Error(job.error || 'Job failed');
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
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
    notice('Technical inventory completed. Semantic adapters are the next gate.');
  } catch (error) {
    notice(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = 'Index footage';
  }
}

async function refreshProjects() {
  const payload = await api('/api/projects');
  state.projects = payload.projects;
  renderProjectList();
}

async function initialize() {
  try {
    const [status, projects] = await Promise.all([api('/api/status'), api('/api/projects')]);
    state.status = status;
    state.projects = projects.projects;
    renderCapabilities();
    const initial = state.projects[0];
    if (initial) await loadProject(initial.project_id);
  } catch (error) {
    notice(error.message, true);
  }
}

const dialog = $('#new-project-dialog');
$('#new-project-button').addEventListener('click', () => dialog.showModal());
$('#close-dialog').addEventListener('click', () => dialog.close());
$('#cancel-dialog').addEventListener('click', () => dialog.close());
$('#new-project-form').addEventListener('submit', createProject);

initialize();
