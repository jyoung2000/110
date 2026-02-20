/* Wallpaper Scraper GUI — Vanilla JS */
let galleryOffset = 0;
const GALLERY_LIMIT = 50;
let galleryPolling = null;
let jobsPolling = null;
let sourcesPolling = null;
let globalStatusPolling = null;
let lastGlobalStatus = null;
let tableFieldsCache = [];
let currentMapping = {};

// === Tab Navigation ===
document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
        onTabSwitch(tab.dataset.tab);
    });
});

function onTabSwitch(tab) {
    stopPolling();
    if (tab === 'gallery') { loadGallerySummary(); loadGallery(); startGalleryPolling(); }
    if (tab === 'scrape') { checkBaserowStatus(); loadScrapeJobs(); startScrapePolling(); }
    if (tab === 'sources') { loadSources(); loadQueries(); loadSchedulerStatus(); loadLiveStatus(); startSourcesPolling(); }
    if (tab === 'jobs') { loadJobs(); startJobsPolling(); }
    if (tab === 'characters') { loadCharacters(); }
    if (tab === 'settings') { loadSettings(); }
    if (tab === 'baserow') { loadBaserowConfig(); }
    if (tab === 'browse') { loadBrowse(); }
    if (tab === 'stats') { loadStats(); }
    if (tab === 'logs') { loadLogs(); startLogsPolling(); }
}

function stopPolling() {
    if (galleryPolling) { clearInterval(galleryPolling); galleryPolling = null; }
    if (jobsPolling) { clearInterval(jobsPolling); jobsPolling = null; }
    if (sourcesPolling) { clearInterval(sourcesPolling); sourcesPolling = null; }
    if (typeof scrapePolling !== 'undefined' && scrapePolling) { clearInterval(scrapePolling); scrapePolling = null; }
    if (typeof logsPolling !== 'undefined' && logsPolling) { clearInterval(logsPolling); logsPolling = null; }
}

// ==================== GLOBAL STATUS (always running) ====================
function startGlobalStatusPolling() {
    if (globalStatusPolling) return;
    updateGlobalStatus(); // immediate first call
    globalStatusPolling = setInterval(updateGlobalStatus, 3000);
}

async function updateGlobalStatus() {
    try {
        const data = await api('/api/status/live');
        lastGlobalStatus = data;
        renderNavStatus(data);
    } catch (e) {
        // Silently fail — don't spam errors for the always-on poller
    }
}

function renderNavStatus(data) {
    const el = document.getElementById('nav-status');
    if (!el) return;

    const job = data.current_job;
    const discoveryRunning = data.discovery_running;
    const scheduler = data.scheduler || {};
    const enginePaused = data.engine_paused;

    const stopBtn = document.getElementById('nav-stop-btn');
    const resumeBtn = document.getElementById('nav-resume-btn');

    if (enginePaused) {
        el.innerHTML = '<span class="nav-status-paused">Paused</span>';
        if (stopBtn) stopBtn.style.display = 'none';
        if (resumeBtn) resumeBtn.style.display = '';
    } else if (job && job.status === 'running') {
        const name = job.source_name || 'Unknown';
        const progress = job.progress || 0;
        const found = job.images_found || 0;
        const uploaded = job.images_uploaded || 0;
        const page = job.pages_scraped || 0;
        const maxPages = job.max_pages || '?';
        el.innerHTML = `
            <div class="nav-status-active" title="Scraping ${esc(name)}: page ${page}/${maxPages}, ${found} found, ${uploaded} uploaded">
                <span class="nav-status-dot"></span>
                <span>Scraping: ${esc(name)} (${progress}%)</span>
            </div>
        `;
        if (stopBtn) stopBtn.style.display = '';
        if (resumeBtn) resumeBtn.style.display = 'none';
    } else if (discoveryRunning) {
        el.innerHTML = `
            <div class="nav-status-discovery">
                <span class="nav-status-dot"></span>
                <span>Discovering sources...</span>
            </div>
        `;
        if (stopBtn) stopBtn.style.display = 'none';
        if (resumeBtn) resumeBtn.style.display = 'none';
    } else if (scheduler.paused) {
        el.innerHTML = '<span class="nav-status-idle">Paused</span>';
        if (stopBtn) stopBtn.style.display = 'none';
        if (resumeBtn) resumeBtn.style.display = '';
    } else {
        el.innerHTML = '<span class="nav-status-idle">Idle</span>';
        if (stopBtn) stopBtn.style.display = 'none';
        if (resumeBtn) resumeBtn.style.display = 'none';
    }

    // Also update gallery live banner if gallery tab is active
    updateGalleryLiveBanner(job, enginePaused);
}

function updateGalleryLiveBanner(job, enginePaused) {
    const banner = document.getElementById('gallery-live-banner');
    const pausedBanner = document.getElementById('engine-paused-banner');

    if (banner) {
        if (job && job.status === 'running') {
            banner.style.display = '';
            const name = job.source_name || job.url || 'Unknown';
            const titleEl = document.getElementById('gallery-live-title');
            const detailEl = document.getElementById('gallery-live-detail');
            const progressEl = document.getElementById('gallery-live-progress');
            const statsEl = document.getElementById('gallery-live-stats');
            if (titleEl) titleEl.textContent = 'Scraping: ' + name;
            if (detailEl) detailEl.textContent = 'Page ' + (job.pages_scraped || 0) + '/' + (job.max_pages || '?') + ' \u2022 ' + (job.images_found || 0) + ' images found';
            if (progressEl) progressEl.style.width = (job.progress || 0) + '%';
            if (statsEl) statsEl.textContent = (job.images_uploaded || 0) + ' uploaded, ' + (job.duplicates || 0) + ' dupes';
        } else {
            banner.style.display = 'none';
        }
    }

    if (pausedBanner) {
        pausedBanner.style.display = enginePaused ? '' : 'none';
    }
}

// === API Helper ===
async function api(path, opts = {}) {
    const url = path.startsWith('http') ? path : path;
    const options = { headers: { 'Content-Type': 'application/json' }, ...opts };
    if (opts.body && typeof opts.body === 'object') options.body = JSON.stringify(opts.body);
    try {
        const res = await fetch(url, options);
        if (!res.ok) {
            const err = await res.text();
            throw new Error(err);
        }
        return await res.json();
    } catch (e) {
        console.error('API error:', path, e);
        throw e;
    }
}

// === Toast ===
function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const t = document.createElement('div');
    t.className = 'toast toast-' + type;
    t.textContent = message;
    container.appendChild(t);
    setTimeout(() => t.remove(), 4000);
}

// === Modal ===
function showModal(html) {
    document.getElementById('modal-content').innerHTML = html;
    document.getElementById('modal-overlay').classList.add('active');
}
function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
}
document.getElementById('modal-overlay').addEventListener('click', (e) => {
    if (e.target === document.getElementById('modal-overlay')) closeModal();
});

// === Time formatting ===
function timeAgo(iso) {
    if (!iso) return 'Never';
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff/60) + 'm ago';
    if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
    return Math.floor(diff/86400) + 'd ago';
}

// === Collapsible ===
function toggleCollapsible(id) {
    const el = document.getElementById(id);
    el.classList.toggle('open');
    const arrow = document.getElementById(id + '-arrow');
    if (arrow) arrow.innerHTML = el.classList.contains('open') ? '&#9650;' : '&#9660;';
}

// ==================== GALLERY ====================
function startGalleryPolling() {
    galleryPolling = setInterval(() => {
        loadGallerySummary();
        loadActivityFeed();
    }, 5000);
}

async function loadGallerySummary() {
    try {
        const data = await api('/api/gallery/summary');
        document.getElementById('gallery-stats').innerHTML = `
            <div class="stat-card"><div class="stat-value">${data.today || 0}</div><div class="stat-label">Today</div></div>
            <div class="stat-card"><div class="stat-value">${data.total || 0}</div><div class="stat-label">Total</div></div>
            <div class="stat-card"><div class="stat-value">${data.upgraded || 0}</div><div class="stat-label">Upgraded</div></div>
            <div class="stat-card"><div class="stat-value">${data.duplicates || 0}</div><div class="stat-label">Duplicates</div></div>
            <div class="stat-card"><div class="stat-value">${data.errors || 0}</div><div class="stat-label">Errors</div></div>
        `;
    } catch (e) {}
}

async function loadActivityFeed() {
    try {
        const data = await api('/api/gallery?limit=20');
        const feed = document.getElementById('activity-feed');
        // Filter out duplicates from the activity feed
        const entries = (data.entries || []).filter(e => e.status !== 'duplicate');
        if (entries.length === 0) {
            feed.innerHTML = '<div class="empty-state"><div class="empty-state-text">No activity yet</div><div class="empty-state-hint">Start scraping to see activity here.</div></div>';
            return;
        }
        feed.innerHTML = entries.map(e => `
            <div class="activity-item" onclick="showEntryDetail('${e.id}')">
                <img class="activity-thumb" src="/${e.thumbnail_path || ''}" alt="${esc(e.alt_text || e.title || 'Wallpaper thumbnail')}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2250%22 height=%2250%22><rect fill=%22%231c1c1e%22 width=%2250%22 height=%2250%22/></svg>'">
                <div class="activity-info">
                    <div class="activity-title">${esc(e.title || 'Untitled')}</div>
                    <div class="activity-meta">
                        <span class="badge badge-${e.status}">${e.status}</span>
                        ${esc(e.source_name || '')} &middot; ${timeAgo(e.timestamp)}
                    </div>
                </div>
            </div>
        `).join('');
    } catch (e) {}
}

async function loadGallery() {
    galleryOffset = 0;
    const search = document.getElementById('gallery-search').value;
    const sourceId = document.getElementById('gallery-source-filter').value;
    const status = document.getElementById('gallery-status-filter').value;
    let url = `/api/gallery?limit=${GALLERY_LIMIT}&offset=0`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (sourceId) url += `&source_id=${encodeURIComponent(sourceId)}`;
    if (status) url += `&status=${encodeURIComponent(status)}`;
    try {
        const data = await api(url);
        renderGalleryGrid(data.entries, false);
        document.getElementById('load-more-btn').style.display = data.entries.length >= GALLERY_LIMIT ? '' : 'none';
        loadActivityFeed();
        loadSourceFilter();
    } catch (e) {
        document.getElementById('gallery-grid').innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128444;</div><div class="empty-state-text">No wallpapers yet</div><div class="empty-state-hint">No wallpapers yet. Start scraping from the Sources tab!</div></div>';
    }
}

async function loadMoreGallery() {
    galleryOffset += GALLERY_LIMIT;
    const search = document.getElementById('gallery-search').value;
    const sourceId = document.getElementById('gallery-source-filter').value;
    const status = document.getElementById('gallery-status-filter').value;
    let url = `/api/gallery?limit=${GALLERY_LIMIT}&offset=${galleryOffset}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (sourceId) url += `&source_id=${encodeURIComponent(sourceId)}`;
    if (status) url += `&status=${encodeURIComponent(status)}`;
    try {
        const data = await api(url);
        renderGalleryGrid(data.entries, true);
        document.getElementById('load-more-btn').style.display = data.entries.length >= GALLERY_LIMIT ? '' : 'none';
    } catch (e) {}
}

function renderGalleryGrid(entries, append) {
    const grid = document.getElementById('gallery-grid');
    if (!entries || entries.length === 0) {
        if (!append) grid.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128444;</div><div class="empty-state-text">No wallpapers yet</div><div class="empty-state-hint">No wallpapers yet. Start scraping from the Sources tab!</div></div>';
        return;
    }
    const html = entries.map(e => `
        <div class="gallery-item" onclick="showEntryDetail('${e.id}')">
            <img src="/${e.thumbnail_path || ''}" alt="${esc(e.alt_text || '')}" loading="lazy"
                onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><rect fill=%22%231c1c1e%22 width=%22200%22 height=%22200%22/></svg>'">
            <div class="gallery-badge">${e.width}x${e.height}</div>
            <div class="gallery-overlay">
                <div class="gallery-overlay-title">${esc(e.title || 'Untitled')}</div>
            </div>
        </div>
    `).join('');
    if (append) grid.innerHTML += html;
    else grid.innerHTML = html;
}

async function showEntryDetail(id) {
    try {
        const e = await api(`/api/gallery/${id}`);
        showModal(`
            <div class="modal-header">
                <h3>${esc(e.title || 'Untitled')}</h3>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div style="text-align:center;margin-bottom:1.5rem">
                <img src="/${e.thumbnail_path || ''}" style="max-width:100%;border-radius:var(--radius-md)" alt="${esc(e.alt_text || e.title || 'Wallpaper preview')}">
            </div>
            <table>
                <tr><td style="color:var(--text-tertiary);width:120px">Alt Text</td><td>${esc(e.alt_text || '')}</td></tr>
                <tr><td style="color:var(--text-tertiary)">Tags</td><td>${esc(e.tags || '')}</td></tr>
                <tr><td style="color:var(--text-tertiary)">Resolution</td><td>${e.width}x${e.height} (${e.aspect_ratio})</td></tr>
                <tr><td style="color:var(--text-tertiary)">Mobile</td><td>${e.is_mobile ? 'Yes' : 'No'}</td></tr>
                <tr><td style="color:var(--text-tertiary)">Source</td><td>${esc(e.source_name || '')}</td></tr>
                <tr><td style="color:var(--text-tertiary)">File Size</td><td>${e.file_size_kb || 0} KB</td></tr>
                <tr><td style="color:var(--text-tertiary)">Hash</td><td style="font-family:'SF Mono',SFMono-Regular,Menlo,monospace;font-size:12px">${esc(e.img_hash || '')}</td></tr>
                <tr><td style="color:var(--text-tertiary)">Status</td><td><span class="badge badge-${e.status}">${e.status}</span></td></tr>
                <tr><td style="color:var(--text-tertiary)">Baserow Row</td><td>${e.baserow_row_id || 'N/A'}</td></tr>
                <tr><td style="color:var(--text-tertiary)">URL</td><td><a href="${esc(e.img_url || '')}" target="_blank" style="color:var(--accent)">${esc((e.img_url||'').substring(0,60))}...</a></td></tr>
                <tr><td style="color:var(--text-tertiary)">Time</td><td>${e.timestamp || ''}</td></tr>
                ${e.error_message ? `<tr><td style="color:var(--error)">Error</td><td>${esc(e.error_message)}</td></tr>` : ''}
            </table>
        `);
    } catch (e) { toast('Failed to load details', 'error'); }
}

async function loadSourceFilter() {
    try {
        const data = await api('/api/sources');
        const sel = document.getElementById('gallery-source-filter');
        const val = sel.value;
        sel.innerHTML = '<option value="">All Sources</option>' +
            (data.sources || []).map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
        sel.value = val;
    } catch (e) {}
}

// Gallery search debounce
let searchTimeout;
document.getElementById('gallery-search').addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => loadGallery(), 500);
});
document.getElementById('gallery-source-filter').addEventListener('change', () => loadGallery());
document.getElementById('gallery-status-filter').addEventListener('change', () => loadGallery());

// ==================== SCRAPE ====================
let scrapePolling = null;
function startScrapePolling() {
    scrapePolling = setInterval(loadScrapeJobs, 3000);
}
function stopScrapePolling() {
    if (scrapePolling) { clearInterval(scrapePolling); scrapePolling = null; }
}

async function checkBaserowStatus() {
    try {
        const data = await api('/api/baserow/status');
        document.getElementById('baserow-warning').style.display = data.configured ? 'none' : '';
    } catch (e) {}
}

async function startScrape(force) {
    // Check if engine is paused
    if (lastGlobalStatus && lastGlobalStatus.engine_paused) {
        toast('Scraper is paused. Press the Start button to resume first.', 'error');
        return;
    }
    const url = document.getElementById('scrape-url').value.trim();
    if (!url) { toast('Enter a URL', 'error'); return; }
    const btn = document.getElementById('scrape-btn');
    btn.disabled = true;
    try {
        const apiUrl = '/api/scrape' + (force ? '?force=true' : '');
        await api(apiUrl, {
            method: 'POST',
            body: {
                url: url,
                source_name: document.getElementById('scrape-name').value.trim(),
                max_pages: parseInt(document.getElementById('scrape-pages').value) || 5,
            }
        });
        toast(force ? 'Stopped previous job, new scrape started!' : 'Scrape job started!', 'success');
        loadScrapeJobs();
    } catch (e) {
        // 409 = a job is already running — offer to override
        if (e.message && e.message.includes('already running')) {
            if (confirm('A scrape job is already running. Stop it and start this one instead?')) {
                btn.disabled = false;
                startScrape(true);
                return;
            }
        } else {
            toast('Failed to start scrape: ' + e.message, 'error');
        }
    }
    btn.disabled = false;
}

async function loadScrapeJobs() {
    // Show/hide paused banner on Scrape tab
    const scrapePausedBanner = document.getElementById('scrape-paused-banner');
    if (scrapePausedBanner) {
        const paused = lastGlobalStatus && lastGlobalStatus.engine_paused;
        scrapePausedBanner.style.display = paused ? '' : 'none';
    }
    try {
        const data = await api('/api/jobs');
        const el = document.getElementById('scrape-jobs');
        const jobs = [data.current, ...(data.history || [])].filter(Boolean).slice(0, 10);
        if (jobs.length === 0) { el.innerHTML = '<div style="color:var(--text-tertiary);padding:1rem 0">No recent jobs</div>'; return; }
        el.innerHTML = jobs.map(j => `
            <div class="card" style="padding:1rem">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <div style="display:flex;align-items:center;gap:0.5rem">
                        <strong style="font-size:13px">${esc(j.source_name || j.url || '')}</strong>
                        <span class="badge badge-${j.status === 'running' ? 'running' : j.status === 'completed' ? 'success' : 'error'}">${j.status}</span>
                    </div>
                    <span style="font-size:12px;color:var(--text-tertiary)">${timeAgo(j.started_at)}</span>
                </div>
                ${j.status === 'running' ? `<div class="progress" style="margin-top:0.75rem"><div class="progress-bar" style="width:${j.progress||0}%"></div></div>` : ''}
                <div style="font-size:12px;color:var(--text-tertiary);margin-top:0.5rem">
                    Pages: ${j.pages_scraped || 0} | Found: ${j.images_found || 0} | Uploaded: ${j.images_uploaded || 0} | Dupes: ${j.duplicates || 0} | Errors: ${j.errors || 0}
                </div>
            </div>
        `).join('');
    } catch (e) {}
}

// ==================== SOURCES ====================
async function loadSources() {
    try {
        const data = await api('/api/sources');
        const tbody = document.getElementById('sources-table');
        if (!data.sources || data.sources.length === 0) {
            tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text-muted)">No sources configured. Add sources or wait for discovery.</td></tr>';
            return;
        }
        tbody.innerHTML = data.sources.map(s => `
            <tr draggable="true" data-source-id="${s.id}"
                ondragstart="onSourceDragStart(event)" ondragend="onSourceDragEnd(event)"
                ondragover="onSourceDragOver(event)" ondrop="onSourceDrop(event)"
                ondragleave="onSourceDragLeave(event)">
                <td><span class="drag-handle" title="Drag to reorder">&#9776;</span></td>
                <td><label class="toggle"><input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSource('${s.id}')"><span class="toggle-slider"></span></label></td>
                <td><span class="badge badge-idle" id="source-status-${s.id}">idle</span></td>
                <td>${esc(s.name)}<br><span style="font-size:11px;color:var(--text-muted)">${esc(s.domain || '')}</span></td>
                <td><span class="badge badge-${s.category}">${s.category}</span></td>
                <td>${s.schedule_hours}h</td>
                <td>${timeAgo(s.last_scraped)}</td>
                <td><span class="countdown" id="source-next-${s.id}">--</span></td>
                <td>${s.total_uploaded || 0} / ${s.total_dupes || 0} / ${s.total_errors || 0}</td>
                <td><span class="status-dot ${(s.consecutive_failures || 0) < 5 ? 'healthy' : 'unhealthy'}"></span></td>
                <td>
                    <button class="btn btn-sm btn-secondary" onclick="scrapeSource('${s.id}')">Scrape</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteSource('${s.id}')">Del</button>
                </td>
            </tr>
        `).join('');
        // Immediately populate live status badges
        loadLiveStatus();
    } catch (e) {}
}

// ==================== DRAG & DROP (SOURCE REORDER) ====================
let dragSourceId = null;

function onSourceDragStart(e) {
    const row = e.target.closest('tr');
    if (!row) return;
    dragSourceId = row.dataset.sourceId;
    row.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragSourceId);
}

function onSourceDragEnd(e) {
    const row = e.target.closest('tr');
    if (row) row.classList.remove('dragging');
    // Clean up all drag-over highlights
    document.querySelectorAll('tr.drag-over').forEach(r => r.classList.remove('drag-over'));
    dragSourceId = null;
}

function onSourceDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const row = e.target.closest('tr');
    if (row && row.dataset.sourceId !== dragSourceId) {
        row.classList.add('drag-over');
    }
}

function onSourceDragLeave(e) {
    const row = e.target.closest('tr');
    if (row) row.classList.remove('drag-over');
}

function onSourceDrop(e) {
    e.preventDefault();
    const targetRow = e.target.closest('tr');
    if (!targetRow) return;
    targetRow.classList.remove('drag-over');

    const targetId = targetRow.dataset.sourceId;
    if (!targetId || targetId === dragSourceId) return;

    // Reorder the DOM rows, then save new order
    const tbody = document.getElementById('sources-table');
    const rows = Array.from(tbody.querySelectorAll('tr[data-source-id]'));
    const ids = rows.map(r => r.dataset.sourceId);

    const fromIdx = ids.indexOf(dragSourceId);
    const toIdx = ids.indexOf(targetId);
    if (fromIdx < 0 || toIdx < 0) return;

    // Move the dragged id to the target position
    ids.splice(fromIdx, 1);
    ids.splice(toIdx, 0, dragSourceId);

    // Visually reorder the rows
    const draggedRow = rows[fromIdx];
    if (fromIdx < toIdx) {
        tbody.insertBefore(draggedRow, targetRow.nextSibling);
    } else {
        tbody.insertBefore(draggedRow, targetRow);
    }

    saveSourceOrder(ids);
}

async function saveSourceOrder(sourceIds) {
    try {
        await api('/api/sources/reorder', {
            method: 'PUT',
            body: { source_ids: sourceIds }
        });
        toast('Source order saved', 'success');
    } catch (e) {
        toast('Failed to save order', 'error');
        loadSources(); // Reload to reset order
    }
}

function shuffleSources() {
    const tbody = document.getElementById('sources-table');
    const rows = Array.from(tbody.querySelectorAll('tr[data-source-id]'));
    if (rows.length < 2) { toast('Not enough sources to shuffle', 'error'); return; }

    // Fisher-Yates shuffle
    for (let i = rows.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [rows[i], rows[j]] = [rows[j], rows[i]];
    }

    // Re-append rows in shuffled order
    rows.forEach(row => tbody.appendChild(row));

    // Save new order to backend
    const ids = rows.map(r => r.dataset.sourceId);
    saveSourceOrder(ids);
}

async function toggleSource(id) {
    try { await api(`/api/sources/${id}/toggle`, { method: 'POST' }); } catch (e) { toast('Toggle failed', 'error'); }
}

async function scrapeSource(id, force) {
    if (lastGlobalStatus && lastGlobalStatus.engine_paused) {
        toast('Scraper is paused. Press the Start button to resume first.', 'error');
        return;
    }
    try {
        const url = `/api/sources/${id}/scrape` + (force ? '?force=true' : '');
        await api(url, { method: 'POST' });
        toast(force ? 'Stopped previous job, new scrape started' : 'Scrape started', 'success');
    } catch (e) {
        // 409 = a job is already running — offer to override
        if (e.message && e.message.includes('already running')) {
            if (confirm('A scrape job is already running. Stop it and start this one instead?')) {
                scrapeSource(id, true);
            }
        } else {
            toast('Scrape failed: ' + e.message, 'error');
        }
    }
}

async function deleteSource(id) {
    if (!confirm('Delete this source?')) return;
    try {
        await api(`/api/sources/${id}`, { method: 'DELETE' });
        toast('Source deleted', 'success');
        loadSources();
    } catch (e) { toast('Delete failed', 'error'); }
}

function showAddSourceModal() {
    showModal(`
        <div class="modal-header">
            <h3>Add Source</h3>
            <button class="modal-close" onclick="closeModal()">&times;</button>
        </div>
        <div class="form-group">
            <label>URL</label>
            <input type="text" id="add-source-url" placeholder="https://...">
        </div>
        <div class="form-group">
            <label>Name</label>
            <input type="text" id="add-source-name" placeholder="Source name">
        </div>
        <div class="form-group">
            <label>Schedule (hours)</label>
            <input type="number" id="add-source-schedule" value="12">
        </div>
        <button class="btn btn-primary" onclick="addSource()">Add Source</button>
    `);
}

async function addSource() {
    const url = document.getElementById('add-source-url').value.trim();
    if (!url) { toast('Enter a URL', 'error'); return; }
    try {
        await api('/api/sources', {
            method: 'POST',
            body: {
                url: url,
                name: document.getElementById('add-source-name').value.trim(),
                schedule_hours: parseInt(document.getElementById('add-source-schedule').value) || 12,
            }
        });
        toast('Source added', 'success');
        closeModal();
        loadSources();
    } catch (e) { toast('Failed to add source', 'error'); }
}

// ==================== QUERIES ====================
async function loadQueries() {
    try {
        const data = await api('/api/discovery/queries');
        const tbody = document.getElementById('queries-table');
        if (!data.queries || data.queries.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-muted)">No queries</td></tr>';
            return;
        }
        tbody.innerHTML = data.queries.map(q => `
            <tr>
                <td><label class="toggle"><input type="checkbox" ${q.enabled ? 'checked' : ''} onchange="toggleQuery('${q.id}', this.checked)"><span class="toggle-slider"></span></label></td>
                <td>${esc(q.query)}</td>
                <td><span class="badge badge-${q.builtin ? 'builtin' : 'user'}">${q.builtin ? 'Built-in' : 'User'}</span></td>
                <td>${q.times_used || 0}</td>
                <td>${q.sources_found || 0}</td>
                <td>${timeAgo(q.last_used)}</td>
                <td>${q.builtin ? '' : `<button class="btn btn-sm btn-danger" onclick="deleteQuery('${q.id}')">Del</button>`}</td>
            </tr>
        `).join('');
    } catch (e) {}
}

async function addQuery() {
    const input = document.getElementById('new-query-input');
    const text = input.value.trim();
    if (!text) return;
    try {
        await api('/api/discovery/queries', { method: 'POST', body: { query: text } });
        input.value = '';
        toast('Query added', 'success');
        loadQueries();
    } catch (e) { toast('Failed to add query', 'error'); }
}

async function toggleQuery(id, enabled) {
    try {
        await api(`/api/discovery/queries/${id}`, { method: 'PUT', body: { enabled } });
    } catch (e) { toast('Toggle failed', 'error'); }
}

async function deleteQuery(id) {
    try {
        await api(`/api/discovery/queries/${id}`, { method: 'DELETE' });
        toast('Query deleted', 'success');
        loadQueries();
    } catch (e) { toast('Cannot delete (built-in?)', 'error'); }
}

async function runDiscovery() {
    try {
        await api('/api/discovery/run', { method: 'POST' });
        toast('Discovery started', 'success');
    } catch (e) { toast('Discovery failed: ' + e.message, 'error'); }
}

// ==================== SCHEDULER ====================
async function loadSchedulerStatus() {
    try {
        const data = await api('/api/scheduler/status');
        const dot = document.getElementById('scheduler-dot');
        const text = document.getElementById('scheduler-text');
        const btn = document.getElementById('scheduler-toggle-btn');
        if (data.paused) {
            dot.className = 'status-dot idle';
            text.textContent = 'Scheduler: Paused';
            btn.textContent = 'Resume';
        } else if (data.running) {
            dot.className = 'status-dot healthy';
            text.textContent = 'Scheduler: Running';
            btn.textContent = 'Pause';
        } else {
            dot.className = 'status-dot idle';
            text.textContent = 'Scheduler: Stopped';
            btn.textContent = 'Resume';
        }
    } catch (e) {}
}

async function toggleScheduler() {
    const btn = document.getElementById('scheduler-toggle-btn');
    const isPaused = btn.textContent === 'Resume';
    try {
        await api(`/api/scheduler/${isPaused ? 'resume' : 'pause'}`, { method: 'POST' });
        toast(`Scheduler ${isPaused ? 'resumed' : 'paused'}`, 'success');
        loadSchedulerStatus();
    } catch (e) { toast('Failed', 'error'); }
}

// ==================== STOP ALL / RESUME ====================
async function stopAll() {
    if (!confirm('Stop the current scrape job and pause all scraping?')) return;
    try {
        await api('/api/engine/stop', { method: 'POST' });
        if (lastGlobalStatus) lastGlobalStatus.engine_paused = true;
        toast('Scraper stopped and paused', 'success');
        updateGlobalStatus();
        loadSchedulerStatus();
    } catch (e) { toast('Failed to stop: ' + e.message, 'error'); }
}

async function resumeEngine() {
    try {
        await api('/api/engine/resume', { method: 'POST' });
        // Immediately clear the cached paused state so scrape buttons
        // work right away instead of waiting for the next status poll.
        if (lastGlobalStatus) lastGlobalStatus.engine_paused = false;
        toast('Scraper resumed', 'success');
        updateGlobalStatus();
        loadSchedulerStatus();
        // Hide paused banners on all tabs
        const bannerIds = ['scrape-paused-banner', 'sources-paused-banner', 'engine-paused-banner'];
        bannerIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = 'none';
        });
    } catch (e) { toast('Failed to resume: ' + e.message, 'error'); }
}

// ==================== LIVE STATUS ====================
function startSourcesPolling() {
    sourcesPolling = setInterval(loadLiveStatus, 5000);
}

async function loadLiveStatus() {
    try {
        const data = await api('/api/status/live');

        // Update sources paused banner
        const sourcesPausedBanner = document.getElementById('sources-paused-banner');
        if (sourcesPausedBanner) {
            sourcesPausedBanner.style.display = data.engine_paused ? '' : 'none';
        }

        // Update scraping banner
        const banner = document.getElementById('scraping-banner');
        if (banner) {
            if (data.current_job && data.current_job.status === 'running') {
                banner.style.display = '';
                const j = data.current_job;
                document.getElementById('scraping-banner-title').textContent =
                    'Currently Scraping: ' + (j.source_name || j.url || 'Unknown');
                document.getElementById('scraping-banner-detail').textContent =
                    'Page ' + (j.pages_scraped || 0) + '/' + (j.max_pages || '?') + ' | ' + (j.images_found || 0) + ' images found';
                document.getElementById('scraping-banner-progress-bar').style.width = (j.progress || 0) + '%';
                document.getElementById('scraping-banner-stats').textContent =
                    (j.images_uploaded || 0) + ' uploaded, ' + (j.duplicates || 0) + ' dupes, ' + (j.errors || 0) + ' errors';
            } else {
                banner.style.display = 'none';
            }
        }

        // Update discovery banner
        const discDot = document.getElementById('discovery-dot');
        const discText = document.getElementById('discovery-text');
        const discNext = document.getElementById('discovery-next');
        if (discDot && discText) {
            if (data.discovery_running) {
                discDot.className = 'status-dot healthy';
                discText.textContent = 'Discovery: Running...';
                if (discNext) discNext.textContent = '';
            } else {
                discDot.className = 'status-dot idle';
                const browserOk = data.browser_available;
                discText.textContent = browserOk ? 'Discovery: Idle' : 'Discovery: Browser not available';
                if (discNext) {
                    if (data.next_discovery) {
                        discNext.textContent = 'Next run: ' + formatCountdown(data.next_discovery);
                    } else {
                        discNext.textContent = 'Next run: Pending (first run)';
                    }
                }
            }
        }

        // Update per-source status badges
        if (data.sources_status) {
            for (const ss of data.sources_status) {
                const badge = document.getElementById('source-status-' + ss.id);
                if (badge) {
                    badge.className = 'badge badge-' + ss.status;
                    badge.textContent = ss.status;
                }
                const nextEl = document.getElementById('source-next-' + ss.id);
                if (nextEl) {
                    if (ss.next_scrape) {
                        nextEl.textContent = formatCountdown(ss.next_scrape);
                    } else {
                        nextEl.textContent = 'Pending';
                    }
                }
            }
        }
    } catch (e) { console.error('Live status error:', e); }
}

function formatCountdown(isoDate) {
    if (!isoDate) return '';
    const diff = (new Date(isoDate).getTime() - Date.now()) / 1000;
    if (diff <= 0) return 'Due now';
    if (diff < 60) return Math.ceil(diff) + 's';
    if (diff < 3600) return Math.ceil(diff / 60) + 'm';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ' + Math.ceil((diff % 3600) / 60) + 'm';
    return Math.floor(diff / 86400) + 'd ' + Math.floor((diff % 86400) / 3600) + 'h';
}

// ==================== JOBS ====================
function startJobsPolling() {
    jobsPolling = setInterval(loadJobs, 3000);
}

async function loadJobs() {
    try {
        const data = await api('/api/jobs');
        const el = document.getElementById('jobs-list');
        const allJobs = [data.current, ...(data.history || []), ...(data.saved || [])].filter(Boolean);
        const statusFilter = document.getElementById('jobs-status-filter').value;
        const filtered = statusFilter ? allJobs.filter(j => j.status === statusFilter) : allJobs;
        if (filtered.length === 0) {
            el.innerHTML = '<div class="empty-state"><div class="empty-state-text">No jobs yet</div></div>';
            return;
        }
        el.innerHTML = filtered.map(j => `
            <div class="card" style="padding:1rem">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <div style="display:flex;align-items:center;gap:0.5rem">
                        <strong style="font-size:13px">${esc(j.source_name || j.url || 'Unknown')}</strong>
                        <span class="badge badge-${j.status === 'running' ? 'running' : j.status === 'completed' ? 'success' : 'error'}">${j.status}</span>
                    </div>
                    <span style="font-size:12px;color:var(--text-tertiary)">${timeAgo(j.started_at)} ${j.completed_at ? '- ' + timeAgo(j.completed_at) : ''}</span>
                </div>
                ${j.status === 'running' ? `<div class="progress" style="margin-top:0.75rem"><div class="progress-bar" style="width:${j.progress||0}%"></div></div>` : ''}
                <div style="font-size:12px;color:var(--text-tertiary);margin-top:0.5rem">
                    Pages: ${j.pages_scraped || 0}/${j.max_pages || '?'} | Found: ${j.images_found || 0} | Downloaded: ${j.images_downloaded || 0} | Uploaded: ${j.images_uploaded || 0} | Dupes: ${j.duplicates || 0} | Errors: ${j.errors || 0}
                </div>
                ${(j.error_log && j.error_log.length > 0) ? `<div style="font-size:11px;color:var(--error);margin-top:0.5rem;max-height:80px;overflow-y:auto;line-height:1.5">${j.error_log.map(e => esc(e)).join('<br>')}</div>` : ''}
            </div>
        `).join('');
    } catch (e) {}
}

// ==================== SETTINGS ====================

// --- Cloud AI Provider ---

function toggleCloudAIFields() {
    const enabled = document.getElementById('set-cloud-enabled').checked;
    document.getElementById('cloud-ai-fields').style.display = enabled ? '' : 'none';
}

async function updateCloudModelOptions() {
    const provider = document.getElementById('set-cloud-provider').value;
    const modelSelect = document.getElementById('set-cloud-model');
    const hint = document.getElementById('cloud-provider-hint');
    const modelHint = document.getElementById('cloud-model-hint');
    modelSelect.innerHTML = '<option value="">Default (recommended)</option>';
    if (modelHint) modelHint.textContent = 'Leave as Default for the recommended model';

    if (provider === 'gemini') {
        hint.textContent = 'Get a free API key at aistudio.google.com';
        const models = [
            ['gemini-2.0-flash', 'Gemini 2.0 Flash (fast, affordable)'],
            ['gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite (fastest, cheapest)'],
            ['gemini-1.5-flash', 'Gemini 1.5 Flash'],
            ['gemini-1.5-pro', 'Gemini 1.5 Pro (best quality, costs more)'],
        ];
        models.forEach(([val, label]) => {
            modelSelect.innerHTML += `<option value="${val}">${label}</option>`;
        });
    } else if (provider === 'claude') {
        hint.textContent = 'Get an API key at console.anthropic.com';
        const models = [
            ['claude-haiku-4-5-20251001', 'Claude 4.5 Haiku (fast, affordable)'],
            ['claude-sonnet-4-5-20250929', 'Claude 4.5 Sonnet (best quality, costs more)'],
        ];
        models.forEach(([val, label]) => {
            modelSelect.innerHTML += `<option value="${val}">${label}</option>`;
        });
    } else if (provider === 'openrouter') {
        hint.textContent = 'Get an API key at openrouter.ai/keys';
        const apiKey = document.getElementById('set-cloud-api-key').value.trim();
        if (!apiKey) {
            modelSelect.innerHTML = '<option value="">Enter API key and test connection to load models</option>';
            if (modelHint) modelHint.textContent = 'Paste your OpenRouter API key above and click Test Connection to load models';
        } else {
            modelSelect.innerHTML = '<option value="">Loading models...</option>';
            try {
                const data = await api(`/api/ai/openrouter-models?api_key=${encodeURIComponent(apiKey)}`);
                modelSelect.innerHTML = '<option value="">Default (recommended)</option>';
                const models = data.models || [];
                const recommended = data.recommended || '';
                // Show image-optimized models first, then the rest
                const optimized = models.filter(m => m.image_optimized);
                const others = models.filter(m => !m.image_optimized);
                if (optimized.length > 0) {
                    modelSelect.innerHTML += '<optgroup label="Recommended for Image Processing">';
                    optimized.forEach(m => {
                        const costLabel = m.cost_per_hour === 0
                            ? 'free'
                            : `~$${m.cost_per_hour.toFixed(3)}/hr`;
                        const rec = m.id === recommended ? ' \u2605' : '';
                        modelSelect.innerHTML += `<option value="${m.id}">${m.name} (${costLabel})${rec}</option>`;
                    });
                    modelSelect.innerHTML += '</optgroup>';
                }
                if (others.length > 0) {
                    modelSelect.innerHTML += '<optgroup label="Other Vision Models">';
                    others.forEach(m => {
                        const costLabel = m.cost_per_hour === 0
                            ? 'free'
                            : `~$${m.cost_per_hour.toFixed(3)}/hr`;
                        const rec = m.id === recommended ? ' \u2605' : '';
                        modelSelect.innerHTML += `<option value="${m.id}">${m.name} (${costLabel})${rec}</option>`;
                    });
                    modelSelect.innerHTML += '</optgroup>';
                }
                if (modelHint) {
                    modelHint.textContent = models.length
                        ? `${models.length} vision models (${optimized.length} optimized for images). \u2605 = recommended.`
                        : 'No vision models found';
                }
            } catch (e) {
                modelSelect.innerHTML = '<option value="">Failed to load models</option>';
                if (modelHint) modelHint.textContent = 'Could not fetch models \u2014 check your API key';
            }
        }
    } else {
        hint.textContent = '';
    }

    // Hide demo section when provider changes (needs new test)
    document.getElementById('cloud-demo-section').style.display = 'none';
    document.getElementById('cloud-test-result').textContent = '';
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('set-cloud-api-key');
    const btn = document.getElementById('toggle-key-btn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = 'Hide';
    } else {
        input.type = 'password';
        btn.textContent = 'Show';
    }
}

async function testCloudAI() {
    const btn = document.getElementById('test-cloud-btn');
    const result = document.getElementById('cloud-test-result');
    const provider = document.getElementById('set-cloud-provider').value;
    const apiKey = document.getElementById('set-cloud-api-key').value.trim();

    if (!provider) { toast('Select a provider first', 'error'); return; }
    if (!apiKey) { toast('Enter an API key first', 'error'); return; }

    // Save the AI settings before testing so the API can read them
    await saveCloudAIConfig();

    btn.disabled = true;
    btn.textContent = 'Testing...';
    result.textContent = '';
    result.style.color = '';

    try {
        const data = await api('/api/ai/test-connection', { method: 'POST' });
        if (data.ok) {
            // Refresh model list now that the API key is validated
            // (must be before setting result text, as it clears it)
            await updateCloudModelOptions();
            result.textContent = data.message || 'Connected!';
            result.style.color = 'var(--success)';
            // Show demo section
            document.getElementById('cloud-demo-section').style.display = '';
        } else {
            result.textContent = data.error || 'Connection failed';
            result.style.color = 'var(--error)';
            document.getElementById('cloud-demo-section').style.display = 'none';
        }
    } catch (e) {
        result.textContent = 'Error: ' + e.message;
        result.style.color = 'var(--error)';
        document.getElementById('cloud-demo-section').style.display = 'none';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test Connection';
    }
}

async function saveCloudAIConfig() {
    // Save just the AI section so the backend has the current values
    const provider = document.getElementById('set-cloud-provider').value;
    const apiKey = document.getElementById('set-cloud-api-key').value.trim();
    const model = document.getElementById('set-cloud-model').value;
    const enabled = document.getElementById('set-cloud-enabled').checked;

    try {
        await api('/api/settings', {
            method: 'PUT',
            body: {
                ai: {
                    cloud_enabled: enabled,
                    cloud_provider: provider,
                    cloud_api_key: apiKey,
                    cloud_model: model,
                },
            }
        });
    } catch (e) {
        // Silently fail — will be saved with main settings anyway
    }
}

async function handleDemoUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    // Show preview
    const preview = document.getElementById('demo-upload-preview');
    const content = document.getElementById('demo-upload-content');
    const status = document.getElementById('demo-status');
    const results = document.getElementById('demo-results');

    const reader = new FileReader();
    reader.onload = (e) => {
        preview.src = e.target.result;
        preview.style.display = '';
        content.style.display = 'none';
    };
    reader.readAsDataURL(file);

    // Show loading, hide previous results
    status.style.display = '';
    results.style.display = 'none';

    const formData = new FormData();
    formData.append('image', file);

    try {
        const res = await fetch('/api/ai/demo-caption', {
            method: 'POST',
            body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || res.statusText);
        }
        const data = await res.json();
        document.getElementById('demo-title').textContent = data.title || '(none)';
        document.getElementById('demo-alt').textContent = data.alt || '(none)';
        document.getElementById('demo-tags').textContent = data.tags || '(none)';
        document.getElementById('demo-provider-info').textContent = `${data.provider} / ${data.model}`;
        results.style.display = '';
        toast('Demo caption generated!', 'success');
    } catch (e) {
        toast('Demo failed: ' + e.message, 'error');
    } finally {
        status.style.display = 'none';
    }
}

function resetDemoUpload() {
    document.getElementById('demo-upload-preview').style.display = 'none';
    document.getElementById('demo-upload-content').style.display = '';
    document.getElementById('demo-results').style.display = 'none';
    document.getElementById('demo-image-input').value = '';
}

// --- AI Prompts & Word Filters ---
let _aiDefaults = null;

async function resetAIField(fieldKey, elementId) {
    try {
        if (!_aiDefaults) {
            _aiDefaults = await api('/api/ai/defaults');
        }
        const defaultValue = _aiDefaults[fieldKey] || '';
        document.getElementById(elementId).value = defaultValue;
        toast('Reset to default \u2014 save settings to apply', 'info');
    } catch (e) {
        toast('Failed to load defaults', 'error');
    }
}

// --- Load / Save settings ---

async function loadSettings() {
    try {
        const data = await api('/api/settings');
        if (data.scraping) {
            document.getElementById('set-min-width').value = data.scraping.min_width || 800;
            document.getElementById('set-min-height').value = data.scraping.min_height || 600;
            document.getElementById('set-max-pages').value = data.scraping.max_pages || 10;
            document.getElementById('set-page-delay').value = data.scraping.page_delay_seconds || 2;
            document.getElementById('set-max-dl').value = data.scraping.max_concurrent_downloads || 3;
            // Aspect ratio filters
            const allowedAspects = data.scraping.allowed_aspects || [];
            document.querySelectorAll('.aspect-cb').forEach(cb => {
                cb.checked = allowedAspects.includes(cb.value);
            });
            document.getElementById('set-allow-mobile').checked = data.scraping.allow_mobile !== false;
            document.getElementById('set-watermark-detection').checked = data.scraping.watermark_detection !== false;
            document.getElementById('set-allow-nsfw').checked = !!data.scraping.allow_nsfw;
        }
        if (data.jpeg) document.getElementById('set-jpeg-quality').value = data.jpeg.quality || 85;
        if (data.scheduler) document.getElementById('set-check-interval').value = data.scheduler.check_interval_minutes || 5;
        if (data.gallery) {
            document.getElementById('set-max-entries').value = data.gallery.max_entries || 5000;
            document.getElementById('set-max-thumbs').value = data.gallery.max_thumbnails || 5000;
        }
        // Cloud AI settings
        if (data.ai) {
            document.getElementById('set-cloud-enabled').checked = !!data.ai.cloud_enabled;
            document.getElementById('set-cloud-provider').value = data.ai.cloud_provider || '';
            document.getElementById('set-cloud-api-key').value = data.ai.cloud_api_key || '';
            await updateCloudModelOptions();
            document.getElementById('set-cloud-model').value = data.ai.cloud_model || '';
            toggleCloudAIFields();
            // AI prompts & word lists — prefill with defaults when empty
            document.getElementById('set-cloud-system-prompt').value = data.ai.cloud_system_prompt || '';
            document.getElementById('set-strip-words').value = data.ai.strip_words || '';
            document.getElementById('set-robotic-adjectives').value = data.ai.robotic_adjectives || '';
            document.getElementById('set-junk-tags').value = data.ai.junk_tags || '';
            document.getElementById('set-generic-prefixes').value = data.ai.generic_prefixes || '';
            // Prefill empty fields with built-in defaults so users can see and edit them
            const prefillFields = [
                ['cloud_system_prompt', 'set-cloud-system-prompt'],
                ['strip_words', 'set-strip-words'],
                ['robotic_adjectives', 'set-robotic-adjectives'],
                ['junk_tags', 'set-junk-tags'],
                ['generic_prefixes', 'set-generic-prefixes'],
            ];
            const needsPrefill = prefillFields.some(([, id]) => !document.getElementById(id).value);
            if (needsPrefill) {
                try {
                    if (!_aiDefaults) _aiDefaults = await api('/api/ai/defaults');
                    prefillFields.forEach(([key, id]) => {
                        const el = document.getElementById(id);
                        if (!el.value) el.value = _aiDefaults[key] || '';
                    });
                } catch (e) {}
            }
        }
    } catch (e) {}
}

async function saveSettings() {
    try {
        await api('/api/settings', {
            method: 'PUT',
            body: {
                scraping: {
                    min_width: parseInt(document.getElementById('set-min-width').value),
                    min_height: parseInt(document.getElementById('set-min-height').value),
                    max_pages: parseInt(document.getElementById('set-max-pages').value),
                    page_delay_seconds: parseInt(document.getElementById('set-page-delay').value),
                    max_concurrent_downloads: parseInt(document.getElementById('set-max-dl').value),
                    allowed_aspects: Array.from(document.querySelectorAll('.aspect-cb:checked')).map(cb => cb.value),
                    allow_mobile: document.getElementById('set-allow-mobile').checked,
                    watermark_detection: document.getElementById('set-watermark-detection').checked,
                    allow_nsfw: document.getElementById('set-allow-nsfw').checked,
                },
                jpeg: { quality: parseInt(document.getElementById('set-jpeg-quality').value) },
                scheduler: { check_interval_minutes: parseInt(document.getElementById('set-check-interval').value) },
                gallery: {
                    max_entries: parseInt(document.getElementById('set-max-entries').value),
                    max_thumbnails: parseInt(document.getElementById('set-max-thumbs').value),
                },
                ai: {
                    cloud_enabled: document.getElementById('set-cloud-enabled').checked,
                    cloud_provider: document.getElementById('set-cloud-provider').value,
                    cloud_api_key: document.getElementById('set-cloud-api-key').value.trim(),
                    cloud_model: document.getElementById('set-cloud-model').value,
                    cloud_system_prompt: document.getElementById('set-cloud-system-prompt').value,
                    strip_words: document.getElementById('set-strip-words').value,
                    robotic_adjectives: document.getElementById('set-robotic-adjectives').value,
                    junk_tags: document.getElementById('set-junk-tags').value,
                    generic_prefixes: document.getElementById('set-generic-prefixes').value,
                },
            }
        });
        toast('Settings saved', 'success');
    } catch (e) { toast('Failed to save settings', 'error'); }
}

// ==================== BASEROW ====================
async function loadBaserowConfig() {
    try {
        const data = await api('/api/baserow/status');
        document.getElementById('br-url').value = data.api_url || '';
        document.getElementById('br-table-id').value = data.table_id || '';
        if (data.configured && data.field_mapping_verified) {
            document.getElementById('field-mapping-section').style.display = '';
            loadFieldMapping();
        }
    } catch (e) {}
}

function toggleTokenVisibility() {
    const input = document.getElementById('br-token');
    input.type = input.type === 'password' ? 'text' : 'password';
}

async function testBaserow() {
    const resultEl = document.getElementById('br-test-result');
    resultEl.innerHTML = '<span class="spinner"></span> Testing...';
    try {
        // Save first
        await api('/api/baserow/config', {
            method: 'PUT',
            body: {
                api_url: document.getElementById('br-url').value.trim(),
                api_token: document.getElementById('br-token').value.trim(),
                table_id: parseInt(document.getElementById('br-table-id').value) || 0,
            }
        });
        const result = await api('/api/baserow/test', { method: 'POST' });
        if (result.success) {
            resultEl.innerHTML = `<span style="color:var(--success)">&#10003; Connected! ${result.fields_count} fields found.</span>`;
            document.getElementById('field-mapping-section').style.display = '';
            autoMatchFields();
        } else {
            resultEl.innerHTML = `<span style="color:var(--error)">&#10007; ${esc(result.error || 'Connection failed')}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:var(--error)">&#10007; ${esc(e.message)}</span>`;
    }
}

async function saveBaserow() {
    try {
        await api('/api/baserow/config', {
            method: 'PUT',
            body: {
                api_url: document.getElementById('br-url').value.trim(),
                api_token: document.getElementById('br-token').value.trim(),
                table_id: parseInt(document.getElementById('br-table-id').value) || 0,
            }
        });
        toast('Baserow config saved! Seeds loaded. Scheduler starting.', 'success');
    } catch (e) { toast('Save failed', 'error'); }
}

async function autoMatchFields() {
    try {
        const data = await api('/api/baserow/fields');
        tableFieldsCache = data.table_fields || [];
        currentMapping = data.suggested_mapping || {};
        renderMappingTable(data);
    } catch (e) { toast('Failed to fetch fields: ' + e.message, 'error'); }
}

async function refreshFields() { await autoMatchFields(); }

function renderMappingTable(data) {
    const tbody = document.getElementById('mapping-table');
    const results = data.match_results || [];
    const allFields = (data.table_fields || []).map(f => f.name);
    let matched = 0;

    tbody.innerHTML = results.map(r => {
        const isMatched = r.match_type !== 'none';
        if (isMatched) matched++;
        const current = currentMapping[r.scraper_field] || '';
        const statusIcon = r.match_type === 'exact' ? '&#10003;' :
            r.match_type === 'case_insensitive' ? '&#128260;' :
            r.match_type === 'fuzzy' ? '&#128269;' : '&#9888;';
        const statusClass = r.match_type === 'none' ? 'color:var(--warning)' : 'color:var(--success)';
        return `
            <tr>
                <td><code>${esc(r.scraper_field)}</code></td>
                <td style="text-align:center">&rarr;</td>
                <td>
                    <select onchange="currentMapping['${r.scraper_field}']=this.value">
                        <option value="">-- Not mapped --</option>
                        ${allFields.map(f => `<option value="${esc(f)}" ${f === current ? 'selected' : ''}>${esc(f)}</option>`).join('')}
                    </select>
                </td>
                <td class="mapping-status" style="${statusClass}">${statusIcon} ${r.match_type}</td>
            </tr>
        `;
    }).join('');

    const total = results.length;
    const bar = document.getElementById('mapping-bar');
    const allGood = matched === total;
    bar.innerHTML = `
        <span>${matched}/${total} mapped ${allGood ? '&#10003; Ready' : '&#9888; ' + (total-matched) + ' need attention'}</span>
        <span style="color:${allGood ? 'var(--success)' : 'var(--warning)'}">${allGood ? 'All fields mapped' : 'Some fields unmatched'}</span>
    `;
}

async function loadFieldMapping() {
    try {
        const data = await api('/api/baserow/field-mapping');
        currentMapping = data.field_mapping || {};
        if (tableFieldsCache.length === 0) {
            await autoMatchFields();
        }
    } catch (e) {}
}

async function saveMapping() {
    try {
        await api('/api/baserow/field-mapping', { method: 'PUT', body: { field_mapping: currentMapping } });
        toast('Field mapping saved', 'success');
    } catch (e) { toast('Failed to save mapping', 'error'); }
}

async function resetMapping() {
    try {
        await api('/api/baserow/field-mapping/reset', { method: 'POST' });
        toast('Mapping reset to defaults', 'info');
        autoMatchFields();
    } catch (e) { toast('Reset failed', 'error'); }
}

// ==================== BROWSE BASEROW ====================
let browsePage = 1;
const BROWSE_PAGE_SIZE = 40;
let browseFieldMapping = {};
let browseApiUrl = '';

async function loadBrowse(page) {
    if (page !== undefined) browsePage = page;
    else browsePage = 1;

    // Check if Baserow is configured
    try {
        const status = await api('/api/baserow/status');
        if (!status.configured) {
            document.getElementById('browse-unconfigured').style.display = '';
            document.getElementById('browse-main').style.display = 'none';
            return;
        }
        document.getElementById('browse-unconfigured').style.display = 'none';
        document.getElementById('browse-main').style.display = '';
    } catch (e) {
        document.getElementById('browse-unconfigured').style.display = '';
        document.getElementById('browse-main').style.display = 'none';
        return;
    }

    const search = document.getElementById('browse-search').value.trim();
    const sort = document.getElementById('browse-sort').value;

    let url = `/api/baserow/rows?page=${browsePage}&size=${BROWSE_PAGE_SIZE}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (sort) url += `&order_by=${encodeURIComponent(sort)}`;

    // Show loading state
    const loadBtn = document.getElementById('browse-load-btn');
    const loadingBanner = document.getElementById('browse-loading-banner');
    if (loadBtn) { loadBtn.disabled = true; loadBtn.textContent = 'Loading...'; }
    if (loadingBanner) loadingBanner.style.display = '';

    try {
        const data = await api(url);
        browseFieldMapping = data.field_mapping || {};
        browseApiUrl = data.api_url || '';
        const rows = data.results || [];
        const total = data.count || 0;
        const totalPages = Math.ceil(total / BROWSE_PAGE_SIZE);

        document.getElementById('browse-count').textContent = `${total} wallpapers`;
        renderBrowseGrid(rows);
        renderBrowsePagination(browsePage, totalPages, total);
    } catch (e) {
        document.getElementById('browse-grid').innerHTML =
            '<div class="empty-state"><div class="empty-state-icon">&#9888;</div><div class="empty-state-text">Failed to load</div><div class="empty-state-hint">' + esc(e.message) + '</div></div>';
        document.getElementById('browse-pagination').innerHTML = '';
        document.getElementById('browse-count').textContent = '';
    } finally {
        if (loadBtn) { loadBtn.disabled = false; loadBtn.textContent = 'Refresh'; }
        if (loadingBanner) loadingBanner.style.display = 'none';
    }
}

function retryAllBrowseImages() {
    const errorDivs = document.querySelectorAll('.browse-img-error');
    if (errorDivs.length === 0) {
        toast('No failed images to retry', 'info');
        return;
    }
    errorDivs.forEach(div => {
        const retryBtn = div.querySelector('.browse-retry-btn');
        if (retryBtn) retryBtn.click();
    });
    toast(`Retrying ${errorDivs.length} images...`, 'info');
}

function browseField(row, scraperField) {
    const colName = browseFieldMapping[scraperField] || scraperField;
    return row[colName];
}

function proxyImgUrl(rawUrl) {
    // Route all Baserow image URLs through our backend proxy to avoid
    // CORS, auth, and relative-URL issues.
    if (!rawUrl) return '';
    // Resolve relative URLs against the Baserow api_url
    if (rawUrl.startsWith('/')) {
        rawUrl = browseApiUrl.replace(/\/+$/, '') + rawUrl;
    }
    // If the URL has a different origin than our Baserow api_url (e.g., internal
    // Docker hostname), replace the origin with the configured api_url
    if (browseApiUrl && rawUrl.startsWith('http')) {
        try {
            const urlObj = new URL(rawUrl);
            const apiObj = new URL(browseApiUrl);
            if (urlObj.host !== apiObj.host) {
                rawUrl = apiObj.origin + urlObj.pathname + urlObj.search;
            }
        } catch (e) { /* keep rawUrl as-is */ }
    }
    // Ensure the URL actually has a protocol — some Baserow configs return
    // protocol-relative or bare hostnames
    if (rawUrl && !rawUrl.startsWith('http') && !rawUrl.startsWith('/')) {
        rawUrl = browseApiUrl.replace(/\/+$/, '') + '/' + rawUrl;
    }
    return '/api/baserow/image-proxy?url=' + encodeURIComponent(rawUrl);
}

function browseFileUrl(file) {
    // Extract best URL from a Baserow file object. Tries multiple sources
    // since self-hosted Baserow may return internal URLs or omit url entirely.
    if (!file) return '';
    // 1. Try file.url (standard Baserow response — most reliable)
    if (file.url) return file.url;
    // 2. Try file.original_name or file.visible_name path
    if (file.name) return '/media/user_files/' + file.name;
    return '';
}

function browseThumbnailUrl(row) {
    // Use Baserow's card_cover thumbnail (~300px) for fast grid loading.
    // Falls back to small thumbnail, then full URL as last resort.
    const fileField = browseField(row, 'imageFile');
    if (!fileField || !Array.isArray(fileField) || fileField.length === 0) return '';
    const file = fileField[0];

    if (file.thumbnails) {
        const thumb = file.thumbnails.card_cover || file.thumbnails.small;
        if (thumb && thumb.url) return proxyImgUrl(thumb.url);
    }
    if (file.url) return proxyImgUrl(file.url);
    if (file.name) return proxyImgUrl('/media/user_files/' + file.name);
    return '';
}

function browseImageUrl(row) {
    // Full resolution for lightbox viewing.
    const fileField = browseField(row, 'imageFile');
    if (!fileField || !Array.isArray(fileField) || fileField.length === 0) return '';
    const file = fileField[0];

    if (file.url) return proxyImgUrl(file.url);
    if (file.name) return proxyImgUrl('/media/user_files/' + file.name);
    if (file.thumbnails) {
        const thumb = file.thumbnails.card_cover || file.thumbnails.small;
        if (thumb && thumb.url) return proxyImgUrl(thumb.url);
    }
    return '';
}

function renderBrowseGrid(rows) {
    const grid = document.getElementById('browse-grid');
    if (!rows || rows.length === 0) {
        grid.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128444;</div><div class="empty-state-text">No wallpapers found</div><div class="empty-state-hint">Upload wallpapers by scraping sources, or adjust your search.</div></div>';
        return;
    }
    // Stash rows for retry logic and reset selection
    window._browseRows = rows;
    window._browseSelectedIdx = -1;

    grid.innerHTML = rows.map((row, idx) => {
        const title = browseField(row, 'wallpaperTitle') || 'Untitled';
        const width = browseField(row, 'Width') || 0;
        const height = browseField(row, 'Height') || 0;
        const thumb = browseThumbnailUrl(row);
        const isMobile = browseField(row, 'isMobile');
        const rowId = row.id;
        const hasThumb = !!thumb;
        return `
            <div class="browse-item ${hasThumb ? 'browse-item-loading' : ''}" id="browse-item-${rowId}" data-idx="${idx}" onclick="openLightbox(${idx})">
                ${hasThumb ? `
                    <img src="${esc(thumb)}" alt="${esc(title)}" loading="lazy"
                        onload="this.parentElement.classList.remove('browse-item-loading')"
                        onerror="handleBrowseImgError(this, ${rowId}, ${idx})">
                ` : `
                    <div class="browse-img-error">
                        <span class="browse-img-error-icon">&#128444;</span>
                        <span>No image file</span>
                    </div>
                `}
                <div class="browse-badge">${width}x${height}${isMobile ? ' M' : ''}</div>
                <div class="browse-overlay">
                    <div class="browse-overlay-title">${esc(title)}</div>
                </div>
            </div>
        `;
    }).join('');
}

function handleBrowseImgError(img, rowId, idx) {
    // Try falling back to the full image URL if thumbnail failed
    const row = window._browseRows && window._browseRows[idx];
    if (row && !img.dataset.retried) {
        img.dataset.retried = '1';
        const fullUrl = browseImageUrl(row);
        if (fullUrl && fullUrl !== img.src) {
            img.src = fullUrl;
            return;
        }
    }
    // All URLs failed — show error state with retry button
    img.style.display = 'none';
    const container = img.parentElement;
    container.classList.remove('browse-item-loading');
    const errorDiv = document.createElement('div');
    errorDiv.className = 'browse-img-error';
    errorDiv.innerHTML = `
        <span class="browse-img-error-icon">&#128444;</span>
        <span>Image unavailable</span>
        <button class="browse-retry-btn" onclick="retryBrowseImage(event, ${rowId}, ${idx})">Retry</button>
    `;
    container.insertBefore(errorDiv, container.firstChild);
}

function retryBrowseImage(event, rowId, idx) {
    event.stopPropagation();
    const container = document.getElementById('browse-item-' + rowId);
    if (!container) return;
    const row = window._browseRows && window._browseRows[idx];
    if (!row) return;

    // Remove error state
    const errorDiv = container.querySelector('.browse-img-error');
    if (errorDiv) errorDiv.remove();

    // Re-create img with cache-busting
    const thumb = browseThumbnailUrl(row);
    const title = browseField(row, 'wallpaperTitle') || '';
    const cacheBust = thumb + (thumb.includes('?') ? '&' : '?') + '_t=' + Date.now();

    const existingImg = container.querySelector('img');
    if (existingImg) existingImg.remove();

    container.classList.add('browse-item-loading');
    const newImg = document.createElement('img');
    newImg.src = cacheBust;
    newImg.alt = title;
    newImg.loading = 'lazy';
    newImg.onload = () => container.classList.remove('browse-item-loading');
    newImg.onerror = () => handleBrowseImgError(newImg, rowId, idx);
    container.insertBefore(newImg, container.firstChild);
}

function renderBrowsePagination(current, totalPages, total) {
    const el = document.getElementById('browse-pagination');
    if (totalPages <= 1) { el.innerHTML = ''; return; }

    let html = '';
    // Previous button
    if (current > 1) {
        html += `<button class="btn btn-sm btn-secondary" onclick="loadBrowse(${current - 1})">&laquo; Prev</button>`;
    }

    // Page numbers (show max 7 pages around current)
    const start = Math.max(1, current - 3);
    const end = Math.min(totalPages, current + 3);
    if (start > 1) {
        html += `<button class="btn btn-sm btn-secondary" onclick="loadBrowse(1)">1</button>`;
        if (start > 2) html += `<span class="browse-page-ellipsis">...</span>`;
    }
    for (let i = start; i <= end; i++) {
        if (i === current) {
            html += `<button class="btn btn-sm btn-primary browse-page-active">${i}</button>`;
        } else {
            html += `<button class="btn btn-sm btn-secondary" onclick="loadBrowse(${i})">${i}</button>`;
        }
    }
    if (end < totalPages) {
        if (end < totalPages - 1) html += `<span class="browse-page-ellipsis">...</span>`;
        html += `<button class="btn btn-sm btn-secondary" onclick="loadBrowse(${totalPages})">${totalPages}</button>`;
    }

    // Next button
    if (current < totalPages) {
        html += `<button class="btn btn-sm btn-secondary" onclick="loadBrowse(${current + 1})">Next &raquo;</button>`;
    }

    el.innerHTML = html;
}

// ==================== BROWSE LIGHTBOX ====================
let lightboxCurrentIdx = -1;
let lightboxRows = [];

function openLightbox(idx) {
    if (!window._browseRows || window._browseRows.length === 0) return;
    lightboxRows = window._browseRows;
    lightboxCurrentIdx = idx;
    document.getElementById('lightbox-overlay').classList.add('active');
    document.body.style.overflow = 'hidden';
    renderLightboxContent(idx);
}

function closeLightbox() {
    document.getElementById('lightbox-overlay').classList.remove('active');
    document.body.style.overflow = '';
    lightboxCurrentIdx = -1;
}

function lightboxPrev() {
    if (lightboxCurrentIdx > 0) {
        lightboxCurrentIdx--;
        renderLightboxContent(lightboxCurrentIdx);
    }
}

function lightboxNext() {
    if (lightboxCurrentIdx < lightboxRows.length - 1) {
        lightboxCurrentIdx++;
        renderLightboxContent(lightboxCurrentIdx);
    }
}

function renderLightboxContent(idx) {
    const row = lightboxRows[idx];
    if (!row) return;

    const title = browseField(row, 'wallpaperTitle') || 'Untitled';
    const width = browseField(row, 'Width') || 0;
    const height = browseField(row, 'Height') || 0;
    const imgUrl = browseField(row, 'imgUrl') || '';
    const altText = browseField(row, 'altText') || '';
    const artist = browseField(row, 'artistText') || '';
    const artistLink = browseField(row, 'artistLink') || '';
    const tags = browseField(row, 'categoryTags') || '';
    const isMobile = browseField(row, 'isMobile');
    const imgHash = browseField(row, 'imgHash') || '';
    const fullImgUrl = browseImageUrl(row);

    // Update image with loading state
    const img = document.getElementById('lightbox-image');
    const spinner = document.getElementById('lightbox-spinner');
    img.classList.add('loading');
    spinner.classList.add('active');
    img.onload = () => { img.classList.remove('loading'); spinner.classList.remove('active'); };
    img.onerror = () => { img.classList.remove('loading'); spinner.classList.remove('active'); };
    img.src = fullImgUrl;
    img.alt = altText || title;

    // Update info panel
    const info = document.getElementById('lightbox-info');
    let infoHtml = `<div class="lightbox-info-title">${esc(title)}</div>`;
    infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Resolution</span><span class="lightbox-info-value">${width}x${height}</span></div>`;
    if (altText) infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Alt Text</span><span class="lightbox-info-value">${esc(altText)}</span></div>`;
    if (tags) infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Tags</span><span class="lightbox-info-value">${esc(tags)}</span></div>`;
    infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Mobile</span><span class="lightbox-info-value">${isMobile ? 'Yes' : 'No'}</span></div>`;
    if (artist) {
        infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Artist</span><span class="lightbox-info-value">${artistLink ? `<a href="${esc(artistLink)}" target="_blank">${esc(artist)}</a>` : esc(artist)}</span></div>`;
    }
    if (imgHash) infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Hash</span><span class="lightbox-info-value" style="font-family:'SF Mono',SFMono-Regular,Menlo,monospace;font-size:11px">${esc(imgHash)}</span></div>`;
    if (imgUrl) infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Source</span><span class="lightbox-info-value"><a href="${esc(imgUrl)}" target="_blank">${esc(imgUrl.substring(0, 40))}...</a></span></div>`;
    infoHtml += `<div class="lightbox-info-row"><span class="lightbox-info-label">Row ID</span><span class="lightbox-info-value">#${row.id}</span></div>`;
    infoHtml += `<div class="lightbox-info-actions"><a href="${esc(fullImgUrl)}" download class="btn btn-primary btn-sm" target="_blank">Download Full Size</a></div>`;
    info.innerHTML = infoHtml;

    // Update counter
    document.getElementById('lightbox-counter').textContent = `${idx + 1} / ${lightboxRows.length}`;

    // Update nav button states
    document.getElementById('lightbox-prev').disabled = idx === 0;
    document.getElementById('lightbox-next').disabled = idx === lightboxRows.length - 1;
}

// Lightbox event listeners
document.getElementById('lightbox-close').addEventListener('click', closeLightbox);
document.getElementById('lightbox-prev').addEventListener('click', lightboxPrev);
document.getElementById('lightbox-next').addEventListener('click', lightboxNext);
document.getElementById('lightbox-overlay').addEventListener('click', (e) => {
    if (e.target === document.getElementById('lightbox-overlay')) closeLightbox();
});

// Keep the old function as a fallback but redirect to lightbox
async function showBrowseDetail(rowId) {
    // Find index in current browse rows
    if (window._browseRows) {
        const idx = window._browseRows.findIndex(r => r.id === rowId);
        if (idx >= 0) {
            openLightbox(idx);
            return;
        }
    }
    // Fallback: fetch single row and show in lightbox
    try {
        const row = await api(`/api/baserow/rows/${rowId}`);
        const fm = row.field_mapping || browseFieldMapping;
        // Merge field mapping into the row so browseField works
        browseFieldMapping = fm;
        window._browseRows = [row];
        openLightbox(0);
    } catch (e) { toast('Failed to load wallpaper details', 'error'); }
}

// Browse search debounce
let browseSearchTimeout;
document.getElementById('browse-search').addEventListener('input', () => {
    clearTimeout(browseSearchTimeout);
    browseSearchTimeout = setTimeout(() => loadBrowse(), 500);
});
document.getElementById('browse-sort').addEventListener('change', () => loadBrowse());

// ==================== STATS ====================
async function loadStats() {
    try {
        const data = await api('/api/stats');
        const summary = data.summary || {};
        document.getElementById('stats-summary').innerHTML = `
            <div class="stat-card"><div class="stat-value">${summary.total || 0}</div><div class="stat-label">Total Processed</div></div>
            <div class="stat-card"><div class="stat-value">${summary.uploaded || 0}</div><div class="stat-label">Uploaded</div></div>
            <div class="stat-card"><div class="stat-value">${summary.upgraded || 0}</div><div class="stat-label">Upgraded</div></div>
            <div class="stat-card"><div class="stat-value">${summary.duplicates || 0}</div><div class="stat-label">Duplicates</div></div>
            <div class="stat-card"><div class="stat-value">${summary.errors || 0}</div><div class="stat-label">Errors</div></div>
            <div class="stat-card"><div class="stat-value">${data.total_sources || 0}</div><div class="stat-label">Total Sources</div></div>
            <div class="stat-card"><div class="stat-value">${data.enabled_sources || 0}</div><div class="stat-label">Enabled Sources</div></div>
        `;

        const sourcesTbody = document.getElementById('stats-sources');
        const sources = data.sources || [];
        sourcesTbody.innerHTML = sources.map(s => `
            <tr>
                <td>${esc(s.name)}</td>
                <td><span class="badge badge-${s.category}">${s.category}</span></td>
                <td>${s.total_uploaded || 0}</td>
                <td>${s.total_dupes || 0}</td>
                <td>${s.total_errors || 0}</td>
                <td>${timeAgo(s.last_scraped)}</td>
            </tr>
        `).join('') || '<tr><td colspan="6" style="color:var(--text-muted)">No source data</td></tr>';

        const disc = data.discovery || {};
        document.getElementById('stats-discovery').innerHTML = `
            <div class="stat-grid" style="margin-bottom:0">
                <div class="stat-card">
                    <div class="stat-value" style="font-size:1.5rem">${disc.total_queries || 0}</div>
                    <div class="stat-label">Total Queries</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" style="font-size:1.5rem">${disc.builtin_queries || 0}</div>
                    <div class="stat-label">Built-in</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" style="font-size:1.5rem">${disc.user_queries || 0}</div>
                    <div class="stat-label">User Queries</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" style="font-size:1.5rem">${disc.total_sources_discovered || 0}</div>
                    <div class="stat-label">Sources Discovered</div>
                </div>
            </div>
        `;
    } catch (e) {}
}

// === Utilities ===
function esc(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = String(s);
    return div.innerHTML;
}

// ==================== ARROW KEY GALLERY NAVIGATION ====================
let galleryEntries = [];   // Cached entry IDs for keyboard navigation
let gallerySelectedIdx = -1; // Currently selected gallery item index

// Update cached entries when gallery renders
function updateGalleryEntries() {
    const items = document.querySelectorAll('.gallery-item');
    galleryEntries = Array.from(items);
    // Clear selection if gallery was reloaded
    gallerySelectedIdx = -1;
}

function selectGalleryItem(idx) {
    if (galleryEntries.length === 0) return;
    // Clamp index
    idx = Math.max(0, Math.min(idx, galleryEntries.length - 1));

    // Remove previous selection highlight
    if (gallerySelectedIdx >= 0 && gallerySelectedIdx < galleryEntries.length) {
        galleryEntries[gallerySelectedIdx].classList.remove('gallery-selected');
    }

    gallerySelectedIdx = idx;
    const item = galleryEntries[idx];
    item.classList.add('gallery-selected');

    // Scroll item into view smoothly
    item.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
}

document.addEventListener('keydown', (e) => {
    // Don't intercept when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    // Handle lightbox navigation (browse page)
    const lightbox = document.getElementById('lightbox-overlay');
    if (lightbox && lightbox.classList.contains('active')) {
        if (e.key === 'Escape') { closeLightbox(); e.preventDefault(); return; }
        if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { lightboxPrev(); e.preventDefault(); return; }
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { lightboxNext(); e.preventDefault(); return; }
        return;
    }

    // Handle modal close
    const modal = document.getElementById('modal-overlay');
    if (modal && modal.classList.contains('active')) {
        if (e.key === 'Escape') { closeModal(); e.preventDefault(); }
        return;
    }

    // Gallery tab arrow key navigation
    const galleryTab = document.getElementById('tab-gallery');
    if (galleryTab && galleryTab.classList.contains('active')) {
        updateGalleryEntries();
        if (galleryEntries.length === 0) return;

        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            e.preventDefault();
            if (gallerySelectedIdx < 0) {
                selectGalleryItem(0);
            } else {
                selectGalleryItem(gallerySelectedIdx + 1);
            }
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (gallerySelectedIdx < 0) {
                selectGalleryItem(0);
            } else {
                selectGalleryItem(gallerySelectedIdx - 1);
            }
        } else if (e.key === 'Enter') {
            if (gallerySelectedIdx >= 0 && gallerySelectedIdx < galleryEntries.length) {
                e.preventDefault();
                galleryEntries[gallerySelectedIdx].click();
            }
        }
    }

    // Browse tab arrow key navigation (grid selection, Enter opens lightbox)
    const browseTab = document.getElementById('tab-browse');
    if (browseTab && browseTab.classList.contains('active')) {
        const items = document.querySelectorAll('.browse-item');
        if (items.length === 0) return;

        if (!window._browseSelectedIdx) window._browseSelectedIdx = -1;

        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            e.preventDefault();
            window._browseSelectedIdx = window._browseSelectedIdx < 0 ? 0 : Math.min(window._browseSelectedIdx + 1, items.length - 1);
            items.forEach(i => i.classList.remove('browse-selected'));
            items[window._browseSelectedIdx].classList.add('browse-selected');
            items[window._browseSelectedIdx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            e.preventDefault();
            window._browseSelectedIdx = window._browseSelectedIdx < 0 ? 0 : Math.max(window._browseSelectedIdx - 1, 0);
            items.forEach(i => i.classList.remove('browse-selected'));
            items[window._browseSelectedIdx].classList.add('browse-selected');
            items[window._browseSelectedIdx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else if (e.key === 'Enter') {
            if (window._browseSelectedIdx >= 0 && window._browseSelectedIdx < items.length) {
                e.preventDefault();
                openLightbox(window._browseSelectedIdx);
            }
        }
    }
});

// ==================== LOGS VIEWER ====================
let logsPolling = null;

function startLogsPolling() {
    logsPolling = setInterval(loadLogs, 5000);
}

async function loadLogs() {
    const level = document.getElementById('logs-level-filter').value;
    const lines = document.getElementById('logs-line-count').value;
    let url = `/api/logs?lines=${lines}`;
    if (level) url += `&level=${encodeURIComponent(level)}`;

    try {
        const data = await api(url);
        const container = document.getElementById('logs-container');
        const totalEl = document.getElementById('logs-total');

        if (totalEl) totalEl.textContent = `${data.total || 0} total lines`;

        if (!data.lines || data.lines.length === 0) {
            container.innerHTML = '<div style="color:var(--text-muted);padding:2rem;text-align:center">No log entries found</div>';
            return;
        }

        container.innerHTML = data.lines.map(line => {
            const parsed = parseLogLine(line);
            return `<div class="log-line ${parsed.levelClass}">${parsed.html}</div>`;
        }).join('');

        // Auto-scroll to bottom
        const autoScroll = document.getElementById('logs-auto-scroll');
        if (autoScroll && autoScroll.checked) {
            container.scrollTop = container.scrollHeight;
        }
    } catch (e) {
        const container = document.getElementById('logs-container');
        container.innerHTML = `<div style="color:var(--error);padding:2rem;text-align:center">Failed to load logs: ${esc(e.message)}</div>`;
    }
}

function parseLogLine(line) {
    // Log format: 2024-01-15 12:30:45,123 | INFO     | scraper.engine | Some message
    const match = line.match(/^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,\.]\d+)\s*\|\s*(\w+)\s*\|\s*([^\|]+)\|\s*(.*)/);
    if (match) {
        const timestamp = match[1];
        const level = match[2].trim();
        const name = match[3].trim();
        const message = match[4];
        const levelLower = level.toLowerCase();
        const levelClass = 'log-level-' + levelLower;
        const html = `<span class="log-timestamp">${esc(timestamp)}</span> | <span class="${levelClass}">${esc(level.padEnd(8))}</span> | <span class="log-name">${esc(name)}</span> | ${esc(message)}`;
        return { html, levelClass };
    }
    // Unstructured line — return as-is
    return { html: esc(line), levelClass: '' };
}

// ==================== CHARACTERS ====================
let allCharacters = [];

async function loadCharacters() {
    try {
        const filter = document.getElementById('char-filter').value;
        const url = filter ? `/api/characters?source=${filter}` : '/api/characters';
        const data = await api(url);
        allCharacters = data.characters || [];
        renderCharStats(data.stats || {});
        renderDiscoveries(allCharacters);
        filterCharacters();
        loadCharDiscoveryLog();
    } catch (e) {}
}

function renderCharStats(stats) {
    const el = document.getElementById('char-stats');
    el.innerHTML = `
        <span class="char-stat">${stats.total || 0} total</span>
        <span class="char-stat char-stat-builtin">${stats.builtin || 0} built-in</span>
        <span class="char-stat char-stat-user">${stats.user || 0} user</span>
        <span class="char-stat char-stat-discovered">${stats.discovered || 0} discovered</span>
        ${stats.unconfirmed ? `<span class="char-stat char-stat-pending">${stats.unconfirmed} pending</span>` : ''}
    `;
}

function renderDiscoveries(characters) {
    const discoveries = characters.filter(c => c.source === 'discovered' && !c.confirmed);
    const card = document.getElementById('char-discoveries');
    const list = document.getElementById('char-discovery-list');
    if (discoveries.length === 0) {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';
    list.innerHTML = discoveries.map(c => `
        <div class="char-discovery-item">
            <div class="char-discovery-info">
                <strong>${esc(c.name)}</strong>
                ${c.franchise ? `<span class="char-discovery-franchise">from ${esc(c.franchise)}</span>` : ''}
                ${c.media_type ? `<span class="badge badge-${c.media_type}">${esc(c.media_type)}</span>` : ''}
                <span class="char-discovery-count">seen ${c.discovery_count || 1}x</span>
            </div>
            <div class="char-discovery-actions">
                <button class="btn btn-success btn-xs" onclick="confirmCharacter('${c.id}')">Confirm</button>
                <button class="btn btn-danger btn-xs" onclick="rejectCharacter('${c.id}')">Reject</button>
                <button class="btn btn-secondary btn-xs" onclick="editCharacter('${c.id}')">Edit</button>
            </div>
        </div>
    `).join('');
}

function filterCharacters() {
    const q = (document.getElementById('char-search').value || '').toLowerCase();
    const filtered = q
        ? allCharacters.filter(c =>
            c.name.toLowerCase().includes(q) ||
            (c.franchise || '').toLowerCase().includes(q) ||
            (c.media_type || '').toLowerCase().includes(q)
          )
        : allCharacters;
    // Don't show unconfirmed discoveries in the main table (they're in the banner)
    const tableChars = filtered.filter(c => c.confirmed || c.source !== 'discovered');
    renderCharTable(tableChars);
}

function renderCharTable(characters) {
    const tbody = document.getElementById('char-table');
    if (characters.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No characters found</td></tr>';
        return;
    }
    tbody.innerHTML = characters.map(c => {
        const sourceBadge = c.source === 'builtin'
            ? '<span class="badge badge-builtin">built-in</span>'
            : c.source === 'user'
            ? '<span class="badge badge-user">user</span>'
            : '<span class="badge badge-discovered">discovered</span>';
        const typeBadge = c.media_type
            ? `<span class="badge badge-${c.media_type}">${esc(c.media_type)}</span>`
            : '<span class="badge">unknown</span>';
        const actions = c.source === 'builtin'
            ? ''
            : `<button class="btn btn-sm btn-secondary" onclick="editCharacter('${c.id}')">Edit</button>
               <button class="btn btn-sm btn-danger" onclick="deleteCharacter('${c.id}')">Del</button>`;
        return `<tr>
            <td><strong>${esc(c.name)}</strong></td>
            <td>${esc(c.franchise || '—')}</td>
            <td>${typeBadge}</td>
            <td>${sourceBadge}</td>
            <td>${actions}</td>
        </tr>`;
    }).join('');
}

function showAddCharacter() {
    document.getElementById('char-form').style.display = '';
    document.getElementById('char-form-title').textContent = 'Add Character';
    document.getElementById('char-edit-id').value = '';
    document.getElementById('char-name').value = '';
    document.getElementById('char-franchise').value = '';
    document.getElementById('char-media-type').value = '';
    document.getElementById('char-clip-desc').value = '';
    resetCharUpload();
    document.getElementById('char-name').focus();
}

function hideCharForm() {
    document.getElementById('char-form').style.display = 'none';
    resetCharUpload();
}

function resetCharUpload() {
    const preview = document.getElementById('char-upload-preview');
    const content = document.getElementById('char-upload-content');
    const status = document.getElementById('char-upload-status');
    const input = document.getElementById('char-image-input');
    preview.style.display = 'none';
    preview.src = '';
    content.style.display = '';
    status.style.display = 'none';
    input.value = '';
}

async function handleCharImageUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    // Show preview
    const preview = document.getElementById('char-upload-preview');
    const content = document.getElementById('char-upload-content');
    const status = document.getElementById('char-upload-status');

    const reader = new FileReader();
    reader.onload = (e) => {
        preview.src = e.target.result;
        preview.style.display = '';
        content.style.display = 'none';
    };
    reader.readAsDataURL(file);

    // Show analyzing status
    status.style.display = '';

    // Upload to API for description
    const formData = new FormData();
    formData.append('image', file);
    formData.append('character_name', document.getElementById('char-name').value.trim());
    formData.append('media_type', document.getElementById('char-media-type').value);

    try {
        const res = await fetch('/api/characters/describe-image', {
            method: 'POST',
            body: formData,
        });
        if (!res.ok) {
            const err = await res.text();
            throw new Error(err);
        }
        const data = await res.json();
        document.getElementById('char-clip-desc').value = data.description || '';
        toast('Description generated from image', 'success');
    } catch (e) {
        toast('Failed to analyze image: ' + e.message, 'error');
    } finally {
        status.style.display = 'none';
    }
}

function editCharacter(id) {
    const c = allCharacters.find(x => x.id === id);
    if (!c) return;
    document.getElementById('char-form').style.display = '';
    document.getElementById('char-form-title').textContent = 'Edit Character';
    document.getElementById('char-edit-id').value = c.id;
    document.getElementById('char-name').value = c.name;
    document.getElementById('char-franchise').value = c.franchise || '';
    document.getElementById('char-media-type').value = c.media_type || '';
    document.getElementById('char-clip-desc').value = c.clip_description || '';
    document.getElementById('char-name').focus();
}

async function saveCharacter() {
    const editId = document.getElementById('char-edit-id').value;
    const name = document.getElementById('char-name').value.trim();
    if (!name) { toast('Name is required', 'error'); return; }
    const body = {
        name,
        franchise: document.getElementById('char-franchise').value.trim(),
        media_type: document.getElementById('char-media-type').value,
        clip_description: document.getElementById('char-clip-desc').value.trim(),
    };
    try {
        if (editId) {
            await api(`/api/characters/${editId}`, { method: 'PUT', body });
            toast('Character updated', 'success');
        } else {
            await api('/api/characters', { method: 'POST', body });
            toast('Character added', 'success');
        }
        hideCharForm();
        loadCharacters();
    } catch (e) {
        toast('Failed: ' + e.message, 'error');
    }
}

async function deleteCharacter(id) {
    if (!confirm('Delete this character?')) return;
    try {
        await api(`/api/characters/${id}`, { method: 'DELETE' });
        toast('Character deleted', 'success');
        loadCharacters();
    } catch (e) { toast('Failed to delete', 'error'); }
}

async function confirmCharacter(id) {
    try {
        await api(`/api/characters/${id}/confirm`, { method: 'POST' });
        toast('Character confirmed', 'success');
        loadCharacters();
    } catch (e) { toast('Failed to confirm', 'error'); }
}

async function rejectCharacter(id) {
    try {
        await api(`/api/characters/${id}/reject`, { method: 'POST' });
        toast('Character rejected', 'success');
        loadCharacters();
    } catch (e) { toast('Failed to reject', 'error'); }
}

// ==================== CHARACTER DISCOVERY ====================

async function discoverCharacters() {
    const btn = document.getElementById('char-discover-btn');
    btn.disabled = true;
    btn.textContent = 'Discovering...';
    try {
        await api('/api/discovery/characters', { method: 'POST' });
        toast('Character discovery started — searching the web for new characters', 'success');
        // Poll for results
        pollCharDiscovery();
    } catch (e) {
        toast('Discovery failed: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Discover Characters';
    }
}

function pollCharDiscovery() {
    let attempts = 0;
    const maxAttempts = 60; // poll for up to 5 minutes
    const interval = setInterval(async () => {
        attempts++;
        try {
            const data = await api('/api/discovery/characters/results');
            renderCharDiscoveryLog(data.results || []);
            if (!data.running || attempts >= maxAttempts) {
                clearInterval(interval);
                const btn = document.getElementById('char-discover-btn');
                btn.disabled = false;
                btn.textContent = 'Discover Characters';
                if (data.results && data.results.length > 0) {
                    const added = data.results.filter(r => r.added).length;
                    if (added > 0) {
                        toast(`Discovery found ${added} new character${added !== 1 ? 's' : ''}`, 'success');
                    } else {
                        toast('Discovery finished — no new characters found', 'info');
                    }
                }
                loadCharacters();
            }
        } catch (e) {
            // Keep polling silently
        }
    }, 5000);
}

function renderCharDiscoveryLog(results) {
    const card = document.getElementById('char-discovery-log-card');
    const log = document.getElementById('char-discovery-log');
    if (!results || results.length === 0) {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';
    log.innerHTML = results.map(r => {
        const icon = r.added ? '&#9989;' : '&#8212;';
        const label = r.added ? 'new' : 'known';
        const badge = `<span class="badge badge-${r.added ? 'discovered' : 'builtin'}">${label}</span>`;
        const franchise = r.franchise ? ` <span style="color:var(--text-muted)">from ${esc(r.franchise)}</span>` : '';
        const mediaType = r.media_type ? ` <span class="badge badge-${r.media_type}">${esc(r.media_type)}</span>` : '';
        return `<div style="padding:0.25rem 0;border-bottom:1px solid var(--separator)">
            ${icon} <strong>${esc(r.name)}</strong>${franchise}${mediaType} ${badge}
        </div>`;
    }).join('');
}

// Load character discovery results on tab switch
async function loadCharDiscoveryLog() {
    try {
        const data = await api('/api/discovery/characters/results');
        renderCharDiscoveryLog(data.results || []);
        if (data.running) {
            const btn = document.getElementById('char-discover-btn');
            btn.disabled = true;
            btn.textContent = 'Discovering...';
            pollCharDiscovery();
        }
    } catch (e) {}
}

// ==================== MOBILE TAB BAR ====================
(function initMobileTabBar() {
    const tabBar = document.getElementById('mobile-tab-bar');
    const moreSheet = document.getElementById('mobile-more-sheet');
    const moreBackdrop = document.getElementById('mobile-more-backdrop');
    if (!tabBar) return;

    function switchTab(tabName) {
        // Update desktop nav tabs
        document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        const desktopTab = document.querySelector(`.nav-tab[data-tab="${tabName}"]`);
        if (desktopTab) desktopTab.classList.add('active');
        document.getElementById('tab-' + tabName).classList.add('active');
        onTabSwitch(tabName);

        // Update mobile bar highlight
        tabBar.querySelectorAll('.mobile-tab').forEach(t => t.classList.remove('active'));
        const mobileTab = tabBar.querySelector(`.mobile-tab[data-tab="${tabName}"]`);
        if (mobileTab) {
            mobileTab.classList.add('active');
        } else {
            // It's from the More sheet — highlight the More button
            tabBar.querySelector('.mobile-tab[data-tab="more"]').classList.add('active');
        }
    }

    // Main tab buttons
    tabBar.querySelectorAll('.mobile-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tab === 'more') {
                moreSheet.classList.toggle('open');
                return;
            }
            moreSheet.classList.remove('open');
            switchTab(btn.dataset.tab);
        });
    });

    // More sheet items
    moreSheet.querySelectorAll('.mobile-more-item').forEach(btn => {
        btn.addEventListener('click', () => {
            moreSheet.classList.remove('open');
            switchTab(btn.dataset.tab);
        });
    });

    // Backdrop tap closes sheet
    if (moreBackdrop) {
        moreBackdrop.addEventListener('click', () => {
            moreSheet.classList.remove('open');
        });
    }
})();

// === Init ===
window.addEventListener('DOMContentLoaded', () => {
    loadGallerySummary();
    loadGallery();
    startGalleryPolling();
    startGlobalStatusPolling();
});
