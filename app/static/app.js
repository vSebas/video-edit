const state = {
  status: null,
  projects: [],
  activeProjectId: null,
  activeProject: null,
  activeProviderRuns: [],
  activeReviewOutcome: null,
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

function providerLabel(run) {
  const labels = { qwen: 'Qwen', 'gemini-vlm': 'Gemini VLM' };
  return labels[run.provider?.id] || run.provider?.id || 'Provider';
}

function conflictForEvidence(runKey, evidenceId) {
  return state.activeReviewOutcome?.material_conflicts?.find((item) =>
    item.run_key === runKey && item.evidence_id === evidenceId
  );
}

function evidenceItem(observation, runKey) {
  const flags = [...(observation.risk_flags || [])];
  if ((observation.adjustments || []).includes('end_clamped_to_source_duration')) flags.push('time clamped');
  const conflict = conflictForEvidence(runKey, observation.evidence_id);
  if (conflict) flags.push('independent benchmark conflict');
  const reviewedCaption = observation.reviewed_caption && observation.reviewed_caption !== observation.caption
    ? `<div class="reviewed-caption"><strong>Reviewed wording</strong><p>${escapeHtml(observation.reviewed_caption)}</p></div>`
    : '';
  const reviewControls = observation.review_status === 'pending'
    ? `<div class="review-actions">
        <button class="ghost approve" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="approve">Approve</button>
        <button class="ghost" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Edit + approve</button>
        <button class="ghost reject" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="reject">Reject</button>
      </div>`
    : `<div class="review-actions completed-review">
        <div class="review-decision ${observation.review_status}">${escapeHtml(observation.review_status)}</div>
        ${observation.review_status === 'reviewed'
          ? `<button class="ghost" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Edit approval</button>
             <button class="ghost reject" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="reject">Reject instead</button>`
          : `<button class="ghost approve" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="approve">Approve instead</button>
             <button class="ghost" data-review-run="${escapeHtml(runKey)}" data-review-id="${escapeHtml(observation.evidence_id)}" data-review-action="edit">Edit + approve</button>`}
      </div>`;
  return `
    <article class="evidence-item ${conflict ? 'has-conflict' : ''}" data-evidence-run="${escapeHtml(runKey)}" data-evidence-id="${escapeHtml(observation.evidence_id)}">
      <div class="evidence-meta">
        <span>${Number(observation.start_seconds).toFixed(2)}–${Number(observation.end_seconds).toFixed(2)}s</span>
        <span>${escapeHtml(observation.clip_id || 'unmapped clip')}</span>
      </div>
      <p>${escapeHtml(observation.caption)}</p>
      ${reviewedCaption}
      ${conflict ? `<div class="evidence-conflict"><strong>Verified-footage conflict</strong><p>${escapeHtml(conflict.summary)}</p><p><em>Independent observation:</em> ${escapeHtml(conflict.verified_observation)}</p></div>` : ''}
      ${flags.length ? `<div class="evidence-flags">${flags.map((flag) => `<span>${escapeHtml(flag.replaceAll('_', ' '))}</span>`).join('')}</div>` : ''}
      ${reviewControls}
    </article>
  `;
}

function reviewOutcomeSection(outcome, runs) {
  if (!runs.length) return '';
  const allReviewed = runs.every((run) => (run.summary.pending_review_count ?? run.summary.accepted_range_count) === 0);
  if (!allReviewed) return '';
  if (!outcome) {
    return `
      <section id="review-outcome">
        <div class="section-header"><div><span class="eyebrow">Completed review</span><h2>Finalize provider evidence</h2></div></div>
        <div class="card outcome-empty">
          <div><h3>All provider ranges have a decision</h3><p>Create the versioned evidence sets and provider scorecard. This does not pick a winner or render a video.</p></div>
          <button class="primary" data-finalize-reviews>Finalize review outcome</button>
        </div>
      </section>
    `;
  }
  const labels = {
    conflicts_require_resolution: 'Conflicts need resolution',
    deterministic_rerun_required: 'Deterministic rerun required',
    provider_selection_required: 'Provider selection required',
  };
  const scorecards = outcome.candidate_sets.map((candidate) => {
    const benchmark = candidate.benchmark || {};
    const timing = benchmark.end_to_end_seconds == null ? 'not recorded' : `${benchmark.end_to_end_seconds}s`;
    return `
      <article class="card scorecard">
        <div class="scorecard-heading"><div><span class="eyebrow">${escapeHtml(candidate.provider.adapter)}</span><h3>${escapeHtml(providerLabel(candidate))}</h3></div><span>${escapeHtml(candidate.provider.model)}</span></div>
        <div class="scorecard-metrics">
          <span><strong>${candidate.review.approved_count}</strong>approved</span>
          <span><strong>${candidate.review.rejected_count}</strong>rejected</span>
          <span><strong>${candidate.quality_signals.flagged_approved_count}</strong>risk-flagged</span>
          <span class="${candidate.quality_signals.material_conflict_count ? 'bad' : ''}"><strong>${candidate.quality_signals.material_conflict_count}</strong>conflicts</span>
        </div>
        <p>${benchmark.split_count ?? '—'} ranges · ${timing} end to end · ${candidate.quality_signals.clamped_count} endpoints clamped</p>
      </article>
    `;
  }).join('');
  const conflicts = outcome.material_conflicts.map((conflict) => `
    <article class="conflict-row">
      <div><strong>${escapeHtml(conflict.filename)} · ${Number(conflict.start_seconds).toFixed(1)}–${Number(conflict.end_seconds).toFixed(1)}s</strong><span>${escapeHtml(conflict.summary)}</span></div>
      <button class="ghost" data-jump-conflict-run="${escapeHtml(conflict.run_key)}" data-jump-conflict-id="${escapeHtml(conflict.evidence_id)}">Review caption</button>
    </article>
  `).join('');
  return `
    <section id="review-outcome">
      <div class="section-header">
        <div><span class="eyebrow">Versioned review outcome · ${escapeHtml(outcome.revision_id)}</span><h2>Provider scorecard</h2></div>
        <button class="secondary compact" data-finalize-reviews>${outcome.freshness === 'stale' ? 'Refresh scorecard' : 'Rebuild scorecard'}</button>
      </div>
      <div class="outcome-status ${outcome.material_conflicts.length ? 'blocked' : ''}">
        <div><strong>${escapeHtml(labels[outcome.status] || outcome.status)}</strong><p>${escapeHtml(outcome.recommendation.reason)}</p></div>
        <span>${outcome.planning_eligible ? 'PLANNING ELIGIBLE' : 'NOT PROMOTED'}</span>
      </div>
      <div class="scorecard-grid">${scorecards}</div>
      <div class="comparison-verdict">
        <strong>No automatic winner</strong>
        <p>${escapeHtml(outcome.comparison.reason || outcome.recommendation.next_action)}</p>
      </div>
      ${conflicts ? `<div class="conflict-list"><h3>Approved captions that conflict with verified footage</h3>${conflicts}</div>` : ''}
    </section>
  `;
}

function providerComparisonSection(runs) {
  if (!runs.length) return '';
  const filenames = [...new Set(runs.flatMap((run) =>
    run.observations.filter((item) => item.normalization_status === 'accepted').map((item) => item.filename)
  ).filter(Boolean))].sort();
  const summaryCards = runs.map((run) => {
    const summary = run.summary;
    const pending = summary.pending_review_count ?? summary.accepted_range_count;
    const candidate = state.activeReviewOutcome?.candidate_sets?.find((item) => item.run_key === run.run_key);
    const statusText = pending > 0
      ? 'Review required · never compiled directly into an edit plan'
      : candidate?.quality_signals.material_conflict_count
        ? `Review complete · ${candidate.quality_signals.material_conflict_count} verified-footage conflict${candidate.quality_signals.material_conflict_count === 1 ? '' : 's'}`
        : candidate
          ? 'Review complete · candidate evidence finalized'
          : 'Review complete · finalize to produce the scorecard';
    return `
      <article class="provider-summary card">
        <div><span class="eyebrow">${escapeHtml(run.provider.adapter)}</span><h3>${escapeHtml(providerLabel(run))}</h3></div>
        <span class="model-name">${escapeHtml(run.provider.model)}</span>
        <div class="provider-metrics">
          <span><strong>${summary.observation_count}</strong> captions</span>
          <span><strong>${summary.clamped_count}</strong> clamped</span>
          <span><strong>${pending}</strong> pending</span>
        </div>
        <p>${escapeHtml(statusText)}</p>
      </article>
    `;
  }).join('');
  const fileComparisons = filenames.map((filename, index) => `
    <details class="comparison-file" data-comparison-file="${escapeHtml(filename)}" ${index === 0 ? 'open' : ''}>
      <summary><strong>${escapeHtml(filename)}</strong><span>Compare provider captions</span></summary>
      <div class="provider-columns">
        ${runs.map((run) => {
          const observations = run.observations.filter((item) =>
            item.filename === filename && item.normalization_status === 'accepted'
          );
          return `
            <div class="provider-column">
              <h4>${escapeHtml(providerLabel(run))} <span>${observations.length} ranges</span></h4>
              ${observations.map((item) => evidenceItem(item, run.run_key)).join('') || '<p class="no-evidence">No mapped evidence.</p>'}
            </div>
          `;
        }).join('')}
      </div>
    </details>
  `).join('');
  return `
    <section id="provider-comparison">
      <div class="section-header">
        <div><span class="eyebrow">Review-only evidence</span><h2>Qwen and Gemini comparison</h2></div>
        <p>Ranges are normalized against original filenames and ffprobe durations.</p>
      </div>
      <div class="provider-summary-grid">${summaryCards}</div>
      <div class="comparison-files">${fileComparisons}</div>
    </section>
  `;
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
    ${reviewOutcomeSection(state.activeReviewOutcome, state.activeProviderRuns)}
    ${providerComparisonSection(state.activeProviderRuns)}
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
  bindReviewControls();
}

function bindReviewControls() {
  document.querySelectorAll('[data-review-action]').forEach((button) => {
    button.addEventListener('click', () => reviewEvidence(button));
  });
  document.querySelectorAll('[data-finalize-reviews]').forEach((button) => {
    button.addEventListener('click', () => finalizeReviews(button));
  });
  document.querySelectorAll('[data-jump-conflict-run]').forEach((button) => {
    button.addEventListener('click', () => jumpToEvidence(
      button.dataset.jumpConflictRun,
      button.dataset.jumpConflictId,
    ));
  });
}

function jumpToEvidence(runKey, evidenceId) {
  const evidence = [...document.querySelectorAll('.evidence-item')].find((item) =>
    item.dataset.evidenceRun === runKey && item.dataset.evidenceId === evidenceId
  );
  const details = evidence?.closest('.comparison-file');
  if (details) details.open = true;
  evidence?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function captureReviewPosition(button) {
  const evidence = button.closest('.evidence-item');
  return {
    scrollY: window.scrollY,
    anchorTop: evidence?.getBoundingClientRect().top ?? null,
    runKey: evidence?.dataset.evidenceRun,
    evidenceId: evidence?.dataset.evidenceId,
    openFiles: [...document.querySelectorAll('.comparison-file[open]')]
      .map((item) => item.dataset.comparisonFile),
  };
}

function restoreReviewPosition(snapshot) {
  const details = [...document.querySelectorAll('.comparison-file')];
  details.forEach((item) => {
    item.open = snapshot.openFiles.includes(item.dataset.comparisonFile);
  });
  const restore = () => {
    const evidence = [...document.querySelectorAll('.evidence-item')].find((item) =>
      item.dataset.evidenceRun === snapshot.runKey
      && item.dataset.evidenceId === snapshot.evidenceId
    );
    if (evidence && snapshot.anchorTop !== null) {
      window.scrollBy(0, evidence.getBoundingClientRect().top - snapshot.anchorTop);
    } else {
      window.scrollTo(0, snapshot.scrollY);
    }
  };
  restore();
  window.requestAnimationFrame(restore);
}

async function loadProject(projectId) {
  state.activeProjectId = projectId;
  state.activeProviderRuns = [];
  state.activeReviewOutcome = null;
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
    if (state.activeProject.review_outcome?.detail_url) {
      state.activeReviewOutcome = await api(state.activeProject.review_outcome.detail_url);
    }
    renderProject();
  } catch (error) {
    notice(error.message, true);
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
    caption = window.prompt(
      'Edit the factual observation before approval:',
      observation.reviewed_caption || observation.caption,
    );
    if (caption === null) return;
    action = 'approve';
  }
  const position = captureReviewPosition(button);
  button.disabled = true;
  try {
    let finalizationWarning = null;
    const updatedRun = await api(`/api/projects/${state.activeProjectId}/analysis/runs/${runKey}/reviews`, {
      method: 'POST',
      body: JSON.stringify({ evidence_id: evidenceId, action, caption }),
    });
    state.activeProviderRuns = state.activeProviderRuns.map((item) =>
      item.run_key === runKey ? { ...updatedRun, run_key: runKey } : item
    );
    if (state.activeProviderRuns.every((item) => item.summary.pending_review_count === 0)) {
      try {
        state.activeReviewOutcome = await api(`/api/projects/${state.activeProjectId}/analysis/finalized`, {
          method: 'POST',
          body: JSON.stringify({ run_keys: state.activeProviderRuns.map((item) => item.run_key) }),
        });
      } catch (finalizationError) {
        state.activeReviewOutcome = null;
        finalizationWarning = finalizationError.message;
      }
    }
    const outcome = $('#review-outcome');
    if (outcome) outcome.outerHTML = reviewOutcomeSection(state.activeReviewOutcome, state.activeProviderRuns);
    const comparison = $('#provider-comparison');
    if (comparison) {
      comparison.outerHTML = providerComparisonSection(state.activeProviderRuns);
      bindReviewControls();
      restoreReviewPosition(position);
    }
    notice(
      finalizationWarning
        ? `Decision saved, but scorecard refresh failed: ${finalizationWarning}`
        : action === 'approve' ? 'Evidence approved.' : 'Evidence rejected.',
      Boolean(finalizationWarning),
    );
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
  }
}

async function finalizeReviews(button) {
  button.disabled = true;
  try {
    state.activeReviewOutcome = await api(`/api/projects/${state.activeProjectId}/analysis/finalized`, {
      method: 'POST',
      body: JSON.stringify({ run_keys: state.activeProviderRuns.map((item) => item.run_key) }),
    });
    const outcome = $('#review-outcome');
    if (outcome) outcome.outerHTML = reviewOutcomeSection(state.activeReviewOutcome, state.activeProviderRuns);
    const comparison = $('#provider-comparison');
    if (comparison) comparison.outerHTML = providerComparisonSection(state.activeProviderRuns);
    bindReviewControls();
    notice('Versioned evidence sets and provider scorecard are current.');
  } catch (error) {
    notice(error.message, true);
    button.disabled = false;
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
