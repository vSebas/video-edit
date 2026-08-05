const state = {
  status: null,
  projects: [],
  activeProjectId: null,
  activeProject: null,
  activeProviderRuns: [],
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

function renderCapabilities() {
  const capabilities = state.status?.capabilities;
  if (!capabilities) return;
  const items = [
    capabilities.visual.find((item) => item.id === 'owned-live-visual'),
    capabilities.speech.find((item) => item.id === 'faster-whisper'),
    capabilities.render,
    capabilities.editable_exports,
  ].filter(Boolean);
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
      <span class="project-dot ${['ready', 'plan_ready'].includes(project.status) ? 'ready' : ''}"></span>
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
  const missingShots = (concept.missing_shots || []).map((shot) => `
    <li class="missing-shot"><span class="priority ${escapeHtml(shot.priority)}">${escapeHtml(shot.priority)}</span> ${escapeHtml(shot.recording_instruction)}</li>
  `).join('');
  return `
    <article class="concept-card ${selected ? 'selected' : ''}">
      <div class="concept-top"><span class="eyebrow">${escapeHtml(concept.topic)}</span><span class="duration">${concept.target_duration_seconds}s</span></div>
      <h3>${escapeHtml(concept.title)}</h3>
      <p class="hook"><strong>Hook:</strong> ${escapeHtml(concept.hook)}</p>
      <ul>${weaknesses}</ul>
      ${missingShots ? `<details><summary>Missing-shot advice</summary><ul>${missingShots}</ul></details>` : ''}
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

function pipelineSection(project) {
  const runs = state.activeProviderRuns;
  const hasVisual = runs.some((run) => run.provider?.adapter === 'owned-live-visual');
  const hasSpeech = runs.some((run) => run.provider?.adapter === 'local-asr');
  const approvedCount = runs.reduce((total, run) => total + (run.summary?.approved_count || 0), 0);
  const hasConcepts = (project.concepts || []).length > 0;
  const isFixture = project.project_id === 'morning-routine';
  if (isFixture) return '';
  const steps = [
    {
      id: 'analyze-visual', label: hasVisual ? 'Re-analyze footage' : '1 · Analyze footage',
      hint: 'Shots + keyframes described by the visual model', enabled: true,
    },
    {
      id: 'analyze-speech', label: hasSpeech ? 'Re-transcribe speech' : '2 · Transcribe speech',
      hint: 'Local Whisper, audio stays on this machine', enabled: true,
    },
    {
      id: 'generate-concepts', label: hasConcepts ? 'Regenerate concepts' : '3 · Propose concepts',
      hint: approvedCount ? `${approvedCount} approved observations available` : 'Needs approved evidence first',
      enabled: approvedCount > 0,
    },
    {
      id: 'compile-plan', label: project.plan ? 'Recompile plan' : '4 · Compile edit plan',
      hint: project.selected_concept_id ? `For ${project.selected_concept_id}` : 'Select a concept first',
      enabled: Boolean(project.selected_concept_id),
    },
    {
      id: 'render', label: '5 · Render review video', hint: 'Deterministic FFmpeg render',
      enabled: Boolean(project.plan),
    },
    {
      id: 'exports', label: '6 · Export timelines', hint: 'OTIO + DaVinci XML',
      enabled: Boolean(project.plan),
    },
  ];
  return `
    <section id="pipeline">
      <div class="section-header"><div><span class="eyebrow">Pipeline</span><h2>From footage to edit</h2></div><p>Each step is repeatable; analysis results are cached and reused.</p></div>
      <div class="pipeline-grid">
        ${steps.map((step) => `
          <button class="pipeline-step" data-pipeline="${step.id}" ${step.enabled ? '' : 'disabled'}>
            <strong>${escapeHtml(step.label)}</strong>
            <span>${escapeHtml(step.hint)}</span>
          </button>
        `).join('')}
      </div>
    </section>
  `;
}

function pendingReviewSection() {
  const pending = state.activeProviderRuns.flatMap((run) =>
    (run.observations || [])
      .filter((item) => item.normalization_status === 'accepted' && item.review_status === 'pending')
      .map((item) => ({ ...item, run_key: run.run_key }))
  );
  const approvedCount = state.activeProviderRuns.reduce(
    (total, run) => total + (run.summary?.approved_count || 0), 0,
  );
  if (!pending.length) {
    return approvedCount ? `
      <section>
        <div class="section-header"><div><span class="eyebrow">Evidence</span><h2>All claims settled</h2></div>
        <p>${approvedCount} observations approved (routine evidence auto-approved by policy).</p></div>
      </section>` : '';
  }
  const items = pending.map((observation) => `
    <article class="evidence-item">
      <div class="evidence-meta">
        <span>${escapeHtml(observation.filename || observation.asset_id)}</span>
        <span>${Number(observation.start_seconds).toFixed(2)}–${Number(observation.end_seconds).toFixed(2)}s</span>
        <span>conf ${Number(observation.model_confidence ?? 0).toFixed(2)}</span>
      </div>
      <p>${escapeHtml(observation.caption)}</p>
      ${(observation.risk_flags || []).length ? `<div class="evidence-flags">${observation.risk_flags.map((flag) => `<span>${escapeHtml(flag.replaceAll('_', ' '))}</span>`).join('')}</div>` : ''}
      <div class="review-actions">
        <button class="ghost approve" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="approve">Approve</button>
        <button class="ghost" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Edit + approve</button>
        <button class="ghost reject" data-review-run="${escapeHtml(observation.run_key)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="reject">Reject</button>
      </div>
    </article>
  `).join('');
  return `
    <section id="pending-review">
      <div class="section-header">
        <div><span class="eyebrow">Needs your judgement</span><h2>${pending.length} flagged claim${pending.length === 1 ? '' : 's'}</h2></div>
        <p>Routine evidence was auto-approved (${approvedCount} so far). Only risky or uncertain claims are listed here; they never enter a plan unapproved.</p>
      </div>
      <div class="pending-grid">${items}</div>
    </section>
  `;
}

function revisionSection(project) {
  if (!project.plan || project.project_id === 'morning-routine') return '';
  return `
    <section id="revision">
      <div class="section-header"><div><span class="eyebrow">Talk to the editor</span><h2>Revise this edit</h2></div>
      <p>Revision changes only the plan and render; footage analysis stays cached. Revision ${project.plan.revision || 1} is current.</p></div>
      <form id="revision-form" class="revision-form">
        <textarea name="instruction" rows="2" required minlength="3"
          placeholder="e.g. Shorten the intro, drop the fridge shot, end on the scooter ride…"></textarea>
        <button type="submit" class="primary">Revise + re-render</button>
      </form>
    </section>
  `;
}

function renderProject() {
  const project = state.activeProject;
  if (!project) return;
  $('#project-title').textContent = project.name;
  $('#project-status').textContent = project.status.replaceAll('_', ' ');
  renderProjectList();

  const visualGood = ['reviewed', 'completed'].includes(project.analysis.visual);
  const speechGood = project.analysis.speech === 'completed';
  const renderUrl = project.outputs?.render?.url;
  const media = project.inventory?.assets || [];
  const concepts = project.concepts || [];
  const poster = media.find((asset) => asset.thumbnail_url)?.thumbnail_url;

  const conceptSection = concepts.length ? `
    <section>
      <div class="section-header"><div><span class="eyebrow">Creative direction</span><h2>Grounded concepts</h2></div><p>Every beat cites real assets and timecodes; gaps become missing-shot advice.</p></div>
      <div class="concept-grid">${concepts.map((concept) => conceptCard(concept, project)).join('')}</div>
    </section>
  ` : '';

  const planSection = project.plan ? `
    <section>
      <div class="section-header"><div><span class="eyebrow">Deterministic execution</span><h2>Edit plan · revision ${project.plan.revision || 1}</h2></div></div>
      <div class="card plan-card">
        <div>
          <span class="eyebrow">${escapeHtml(project.plan_summary?.format || `${project.plan.project.width}x${project.plan.project.height} @ ${project.plan.project.fps}fps`)}</span>
          <h3>${(project.plan_summary?.duration_seconds ?? project.plan.project.duration_seconds).toFixed ? (project.plan_summary?.duration_seconds ?? project.plan.project.duration_seconds).toFixed(1) : project.plan.project.duration_seconds}s · ${(project.plan.tracks.find((t) => t.kind === 'video')?.events || []).length} cuts</h3>
          <p>Concept: ${escapeHtml(project.plan.concept_id)}</p>
          <div class="downloads">${outputLinks(project)}</div>
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
    ${pipelineSection(project)}
    ${pendingReviewSection()}
    ${conceptSection}
    ${planSection}
    ${revisionSection(project)}
    <section>
      <div class="section-header"><div><span class="eyebrow">Source inventory</span><h2>Recorded media</h2></div><p>Files remain linked to their originals.</p></div>
      <div class="media-grid">${media.map(assetCard).join('')}</div>
    </section>
  `;

  document.querySelectorAll('[data-select-concept]').forEach((button) => {
    button.addEventListener('click', () => selectConcept(button.dataset.selectConcept));
  });
  document.querySelectorAll('[data-pipeline]').forEach((button) => {
    button.addEventListener('click', () => runPipelineStep(button.dataset.pipeline, button));
  });
  document.querySelectorAll('[data-review-action]').forEach((button) => {
    button.addEventListener('click', () => reviewEvidence(button));
  });
  $('#revision-form')?.addEventListener('submit', submitRevision);
}

const PIPELINE_CALLS = {
  'analyze-visual': { path: 'analysis/visual', job: true, message: 'Visual analysis running (this takes a few minutes)…' },
  'analyze-speech': { path: 'analysis/speech', job: true, message: 'Transcribing locally…' },
  'generate-concepts': { path: 'concepts', job: true, message: 'Proposing grounded concepts…' },
  'compile-plan': { path: 'plan', job: false, message: 'Compiling the edit plan…' },
  render: { path: 'render', job: true, message: 'Render queued…' },
  exports: { path: 'exports', job: true, message: 'Editable export queued…' },
};

async function runPipelineStep(stepId, button) {
  const call = PIPELINE_CALLS[stepId];
  if (!call) return;
  button.disabled = true;
  try {
    notice(call.message);
    const result = await api(`/api/projects/${state.activeProjectId}/${call.path}`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    if (call.job) {
      await pollJob(result.job_id);
    } else {
      notice('Edit plan compiled and validated.');
      await loadProject(state.activeProjectId);
    }
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
}

async function submitRevision(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const instruction = new FormData(form).get('instruction')?.toString().trim();
  if (!instruction) return;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  submit.textContent = 'Revising…';
  try {
    const job = await api(`/api/projects/${state.activeProjectId}/plan/revise`, {
      method: 'POST',
      body: JSON.stringify({ instruction }),
    });
    const finished = await pollJob(job.job_id, { reload: false });
    notice(finished.result?.revision_note || 'Plan revised.');
    const renderJob = await api(`/api/projects/${state.activeProjectId}/render`, { method: 'POST' });
    notice('Plan revised. Re-rendering…');
    await pollJob(renderJob.job_id);
  } catch (error) {
    notice(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = 'Revise + re-render';
  }
}

async function reviewEvidence(button) {
  const runKey = button.dataset.reviewRun;
  const evidenceId = button.dataset.reviewId;
  const requestedAction = button.dataset.reviewAction;
  const run = state.activeProviderRuns.find((item) => item.run_key === runKey);
  const observation = run?.observations.find((item) => item.evidence_id === evidenceId);
  if (!observation) return notice('Evidence is no longer available.', true);
  let action = requestedAction;
  let caption = null;
  if (requestedAction === 'edit') {
    caption = window.prompt('Edit the factual observation before approval:', observation.caption);
    if (caption === null) return;
    action = 'approve';
  }
  button.disabled = true;
  try {
    await api(`/api/projects/${state.activeProjectId}/analysis/runs/${runKey}/reviews`, {
      method: 'POST',
      body: JSON.stringify({ evidence_id: evidenceId, action, caption }),
    });
    notice(action === 'approve' ? 'Claim approved for planning.' : 'Claim rejected.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
}

async function selectConcept(conceptId) {
  try {
    await api(`/api/projects/${state.activeProjectId}/selection`, {
      method: 'POST',
      body: JSON.stringify({ concept_id: conceptId }),
    });
    notice('Concept selected. Compile the edit plan when ready.');
    await loadProject(state.activeProjectId);
  } catch (error) {
    notice(error.message, true);
  }
}

async function pollJob(jobId, options = {}) {
  const { reload = true } = options;
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === 'completed') {
      if (reload) {
        notice('Done.');
        await loadProject(job.project_id);
      }
      return job;
    }
    if (job.status === 'failed') throw new Error(job.error || 'Job failed');
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

async function loadProject(projectId) {
  state.activeProjectId = projectId;
  state.activeProviderRuns = [];
  renderProjectList();
  $('#project-view').innerHTML = '<div class="empty-state">Loading project…</div>';
  try {
    state.activeProject = await api(`/api/projects/${projectId}`);
    state.activeProviderRuns = await Promise.all(
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
    notice('Folder indexed. Run "Analyze footage" to start the pipeline.');
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
    const preferred = state.projects.find((project) => project.project_id !== 'morning-routine')
      || state.projects[0];
    if (preferred) await loadProject(preferred.project_id);
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
