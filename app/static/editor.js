// Frame TV picture editor
// Ported from image_crop_upscale: the cropper and pair from static/js/app.js, and
// the batch and the choice of a partner from static/js/bulk.js. It edits pictures
// already in the library, opened as /editor?ids=<id>[,<id>...]. One id is a single
// picture; several are a batch, edited one after another.

// The Frame is a 4K panel, so there is no resolution step.
const SCREEN = { width: 3840, height: 2160 };
const PRESET = '4k';
const MAX_BATCH = 100;
// Narrower than this, a picture opens ready to be paired. The threshold bulk mode used.
const PAIR_BELOW = 1.0;
// Pictures the picker shows before "Show all".
const PICKER_PAGE = 48;
const VALID_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/bmp'];
const MAX_BYTES = 50 * 1024 * 1024;

const HINTS = {
    fill: 'Drag and resize the box. Everything inside it fills the screen (3840 × 2160).',
    bars: 'Crop freely. Black bars fill the part of the screen the crop leaves.',
    pair: 'Crop the first picture freely. The second picture fills the rest of the screen beside it.'
};

const $ = id => document.getElementById(id);

// State management
function initialState() {
    return {
        // The pictures being edited: { id, file, name }.
        first: null,
        second: null,
        layout: 'fill',
        cropper: null,
        cropper2: null,
        // Whether the first cropper has its crop yet. The second one's shape depends on it.
        firstReady: false,
        gapPercent: 1,
        // The crops to put back when a picture made earlier is edited again.
        restore1: null,
        restore2: null,
        resultUrl: null,
        downloadUrl: null
    };
}

let state = initialState();

// Counted up each time a picture is opened, or a second one is shown, so work
// still under way for the one before is dropped.
let opening = 0;
let secondShown = 0;

// The library, for the picker and the batch strip: { items, byId }.
let library = { items: [], byId: new Map() };
let libraryLoaded = false;

// The batch. Marks are what has happened to a picture since the page opened:
// 'done', 'partner' (used as the second of a pair), 'skipped' or 'missing'.
const batch = { ids: [], at: 0, marks: new Map() };

function errorText(data, fallback) {
    if (typeof data.detail === 'string') {
        return data.detail;
    }
    // Validation errors arrive as a list of { msg, ... }.
    if (Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) {
        return `${fallback}: ${data.detail[0].msg}`;
    }
    return fallback;
}

// A non-JSON body (a plain-text server error, a proxy page) becomes a readable error.
async function readJson(response) {
    try {
        return await response.json();
    } catch (error) {
        throw new Error(`Server error (${response.status})`);
    }
}

async function loadLibrary() {
    const response = await fetch('/api/library');
    const data = await readJson(response);
    if (!response.ok) {
        throw new Error(errorText(data, 'The library could not be read'));
    }
    library = { items: data.items, byId: new Map(data.items.map(p => [p.id, p])) };
    libraryLoaded = true;
}

// What the editor needs of a library picture, or of one of a result's originals.
function source(picture) {
    return { id: picture.id, file: picture.file, name: picture.name };
}

function sourceUrl(picture) {
    return `/api/editor/source/${picture.file}`;
}

function openIds(ids) {
    window.location.href = `/editor?ids=${ids.join(',')}`;
}

// Initialize
let secondPicker = null;

document.addEventListener('DOMContentLoaded', start);

async function start() {
    initializeLayout();
    initializeSecond();
    initializeProcess();
    initializeDownload();
    initializeSend();
    initializeBatch();

    const ids = (new URLSearchParams(window.location.search).get('ids') || '')
        .split(',').filter(id => /^[0-9a-f]{32}$/.test(id));
    const unique = [...new Set(ids)];
    const cut = unique.length > MAX_BATCH;
    batch.ids = unique.slice(0, MAX_BATCH);

    try {
        await loadLibrary();
    } catch (error) {
        showPageStatus(error.message, 'error');
    }

    if (!batch.ids.length) {
        showStart();
        return;
    }
    restoreMarks();
    // Only the library can say a picture is gone, so nothing is marked missing without it.
    if (libraryLoaded) {
        for (const id of batch.ids) {
            if (!library.byId.has(id)) {
                batch.marks.set(id, 'missing');
            }
        }
    }
    const at = parseInt(window.location.hash.slice(1), 10) - 1;
    if (at >= 0 && at < batch.ids.length) {
        openAt(at);
    } else {
        const next = nextOpen(-1);
        // A finished batch has no picture in the address. Opening the first one
        // would look like the marks had been forgotten.
        if (batch.ids.length > 1 && next < 0) {
            showBatchDone();
        } else {
            openAt(Math.max(0, next));
        }
    }
    if (cut) {
        showBatchStatus(`The first ${MAX_BATCH} pictures are open. The rest were left out.`, 'error');
    }
}

// Opened without a picture: choose one from the library, or add one.
function showStart() {
    $('start-section').hidden = false;
    const picker = createPicker($('start-picker'), {
        onPick: picture => openIds([picture.id]),
        onAdded: data => openIds([data.id]),
        emptyText: 'Nothing in the library yet. Add a picture below.'
    });
    picker.update({ candidates: library.items });
}

function showPageStatus(message, type) {
    $('page-status').className = type || '';
    $('page-status').textContent = message;
}

// Opening a picture

async function openAt(index) {
    batch.at = index;
    if (batch.ids.length > 1) {
        // Kept in the address, so a reload comes back to the same picture.
        history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${index + 1}`);
    }
    $('queue-done').hidden = true;
    renderBatch();

    const id = batch.ids[index];
    const token = ++opening;
    clearEditor();
    showPageStatus('Opening...', 'loading');

    let picture;
    try {
        const response = await fetch(`/api/library/${id}`);
        picture = await readJson(response);
        if (response.status === 404) {
            if (token === opening) {
                cannotOpen(id, 'That picture is no longer in the library.');
            }
            return;
        }
        if (!response.ok) {
            throw new Error(errorText(picture, 'That picture could not be opened'));
        }
    } catch (error) {
        if (token === opening) {
            showPageStatus(error.message, 'error');
        }
        return;
    }
    if (token === opening) {
        editPicture(picture);
    }
}

function cannotOpen(id, message) {
    if (batch.ids.length > 1) {
        batch.marks.set(id, 'missing');
        renderBatch();
        message += ' Skip to go on to the next one.';
    }
    showPageStatus(message, 'error');
}

function editPicture(picture) {
    let note = '';
    let noteType = '';
    if (picture.kind === 'original') {
        state.first = source(picture);
        state.layout = picture.width / picture.height < PAIR_BELOW ? 'pair' : 'fill';
    } else {
        // A result is edited again from what it was made from, with the same settings.
        const [parent1, parent2] = picture.parents || [];
        if (!parent1 || !parent1.id) {
            cannotOpen(picture.id, `${picture.name} cannot be edited again: the picture it was made from is no longer in the library.`);
            return;
        }
        const recipe = picture.recipe || {};
        state.first = source(parent1);
        state.restore1 = parent1.crop;
        if (recipe.type === 'pair') {
            state.layout = 'pair';
            state.gapPercent = Math.max(1, Math.min(10, Math.round(recipe.gap_percent || 1)));
            if (parent2 && parent2.id) {
                state.second = source(parent2);
                state.restore2 = parent2.crop;
            } else {
                note = 'The second picture of this pair is no longer in the library. Pick another.';
                noteType = 'error';
            }
        } else if (recipe.type === 'auto') {
            // Sent with the automatic fit: the whole picture, cropped to fill or with bars.
            state.layout = recipe.crop ? 'fill' : 'bars';
        } else {
            state.layout = recipe.letterbox ? 'bars' : 'fill';
        }
        note = note || `Editing ${picture.name} again. Saving or sending makes a new picture; this one stays in the library.`;
    }

    showPageStatus(note, noteType);
    $('crop-title').textContent = state.first.name;
    document.querySelector(`input[name="layout"][value="${state.layout}"]`).checked = true;
    $('crop-section').hidden = false;
    $('process-section').hidden = false;
    applyLayout(false);
    initializeCropper();
}

// Put the page back to having no picture open.
function clearEditor() {
    if (state.cropper) {
        state.cropper.destroy();
    }
    if (state.cropper2) {
        state.cropper2.destroy();
    }
    state = initialState();
    secondShown++;

    $('crop-section').hidden = true;
    $('second-section').hidden = true;
    $('process-section').hidden = true;
    $('download-section').hidden = true;
    $('crop-info').textContent = '';
    $('crop-info-2').textContent = '';
    $('diptych-refresh-bar').hidden = true;
    $('diptych-crop-warning').hidden = true;
    $('process-btn').disabled = false;
    showProcessStatus('', '');
    showSendStatus('', '');
    $('show-toggle').checked = false;
    showPageStatus('', '');
    // A result that finishes loading after the next picture opens must not reveal itself there.
    const resultImage = $('result-image');
    resultImage.onload = null;
    resultImage.removeAttribute('src');
}

// Layout: fill the screen, keep the whole picture with bars, or pair it with another.

function initializeLayout() {
    document.querySelectorAll('input[name="layout"]').forEach(input => {
        input.addEventListener('change', () => {
            state.layout = input.value;
            applyLayout(true);
        });
    });
}

// `chosen` is true when the layout was picked by hand rather than opened with.
function applyLayout(chosen) {
    $('layout-hint').textContent = HINTS[state.layout];
    $('second-section').hidden = state.layout !== 'pair';
    if (state.cropper && state.firstReady) {
        state.cropper.setAspectRatio(state.layout === 'fill' ? SCREEN.width / SCREEN.height : NaN);
        if (state.layout === 'pair' && chosen) {
            makeRoom();
        }
    }
    showSecond();
}

// A first crop that leaves less than a quarter of the screen for a second picture
// is narrowed to half the screen, centred, when a pair is chosen.
function makeRoom() {
    const ratio2 = computeImage2Ratio();
    if (ratio2 !== null && ratio2 * SCREEN.height >= SCREEN.width / 4) {
        return;
    }
    const data = state.cropper.getData();
    const width = Math.min(data.width, data.height * SCREEN.width / 2 / SCREEN.height);
    state.cropper.setData({ x: data.x + (data.width - width) / 2, y: data.y, width: width, height: data.height });
}

function initializeCropper() {
    const token = opening;
    const cropImage = $('crop-image');

    // decode() resolves once the new picture is ready, and rejects if the src
    // is replaced again first, so the cropper never starts on a stale image.
    cropImage.src = sourceUrl(state.first);
    cropImage.decode().then(() => {
        if (token !== opening) {
            return;
        }
        // Read here, not before the image loads, so a layout chosen while it was loading is honoured.
        state.cropper = new Cropper(cropImage, {
            aspectRatio: state.layout === 'fill' ? SCREEN.width / SCREEN.height : NaN,
            viewMode: 1,
            dragMode: 'move',
            autoCropArea: 1,
            restore: false,
            guides: true,
            center: true,
            highlight: false,
            cropBoxMovable: true,
            cropBoxResizable: true,
            toggleDragModeOnDblclick: false,
            ready: function() {
                if (token !== opening) {
                    return;
                }
                state.cropper = this.cropper;
                // A layout chosen since the cropper was made.
                state.cropper.setAspectRatio(state.layout === 'fill' ? SCREEN.width / SCREEN.height : NaN);
                if (state.restore1) {
                    state.cropper.setData(state.restore1);
                    state.restore1 = null;
                } else if (state.layout === 'pair') {
                    makeRoom();
                }
                state.firstReady = true;
                showSecond();
            },
            crop: function(event) {
                updateCropInfo(event.detail);
            },
            cropend: function() {
                // The best partners depend on the space this crop leaves.
                if (state.layout === 'pair' && !state.second) {
                    renderSecondPicker();
                }
            }
        });
    }).catch(() => {
        if (token === opening) {
            // Also what a picture deleted from the library since the page opened looks like.
            showPageStatus('Could not display that picture.', 'error');
            $('crop-section').hidden = true;
            $('second-section').hidden = true;
            $('process-section').hidden = true;
        }
    });
}

function updateCropInfo(detail) {
    const width = Math.round(detail.width);
    const height = Math.round(detail.height);
    const x = Math.round(detail.x);
    const y = Math.round(detail.y);

    let info = `Crop area: ${width} × ${height}px at position (${x}, ${y})`;

    if (state.layout === 'bars' && width > 0 && height > 0) {
        const scale = Math.min(SCREEN.width / width, SCREEN.height / height);
        const barX = Math.round((SCREEN.width - Math.round(width * scale)) / 2);
        const barY = Math.round((SCREEN.height - Math.round(height * scale)) / 2);

        if (barX > 0) {
            info += ` | Pillarbox: ${barX}px bars left & right`;
        } else if (barY > 0) {
            info += ` | Letterbox: ${barY}px bars top & bottom`;
        } else {
            info += ` | No bars (fits frame exactly)`;
        }
    }

    // The second crop follows the space the first one leaves.
    if (state.layout === 'pair' && state.cropper2) {
        followFirstCrop();
    }

    $('crop-info').textContent = info;
}

// Give the second crop the shape of the space beside the first, or say there is none.
function followFirstCrop() {
    const ratio2 = computeImage2Ratio();
    if (ratio2 !== null && ratio2 > 0) {
        state.cropper2.setAspectRatio(ratio2);
        $('diptych-crop-warning').hidden = true;
        $('diptych-refresh-bar').hidden = false;
    } else {
        $('diptych-refresh-bar').hidden = true;
        $('diptych-crop-warning').textContent = 'The first crop is too wide: there is no room for a second picture.';
        $('diptych-crop-warning').hidden = false;
    }
}

// The second picture

function initializeSecond() {
    secondPicker = createPicker($('second-picker'), {
        onPick: picture => pickSecond(source(picture)),
        onAdded: data => pickSecond({ id: data.id, file: data.filename, name: data.original_filename }),
        emptyText: 'No other pictures in the library yet. Add one below.'
    });

    $('second-change').addEventListener('click', function() {
        if (state.cropper2) {
            state.cropper2.destroy();
            state.cropper2 = null;
        }
        secondShown++;
        state.second = null;
        state.restore2 = null;
        showSecond();
    });

    $('gap-slider').addEventListener('input', function() {
        state.gapPercent = parseInt(this.value, 10);
        syncGapDisplay();
        if (state.cropper2) {
            // The gap changes how much room the second picture has, so its crop must follow.
            followFirstCrop();
        }
    });

    $('diptych-refresh-btn').addEventListener('click', initializeCropper2);
}

function syncGapDisplay() {
    $('gap-slider').value = String(state.gapPercent);
    $('gap-percent-display').textContent = state.gapPercent + '%';
    $('gap-px-display').textContent = '~' + Math.round(SCREEN.width * state.gapPercent / 100) + 'px';
}

function pickSecond(picture) {
    if (state.cropper2) {
        state.cropper2.destroy();
        state.cropper2 = null;
    }
    state.second = picture;
    state.restore2 = null;
    showSecond();
}

// In a pair, show the picker until there is a second picture, then its cropper.
function showSecond() {
    if (state.layout !== 'pair' || !state.first) {
        return;
    }
    $('second-picker').hidden = !!state.second;
    $('second-chosen').hidden = !state.second;
    if (!state.second) {
        renderSecondPicker();
        return;
    }
    $('second-name').textContent = state.second.name;
    // Its shape comes from the first crop, so it waits for that.
    if (state.firstReady && !state.cropper2) {
        initializeCropper2();
    }
}

function renderSecondPicker() {
    const ratio = state.firstReady ? computeImage2Ratio() : null;
    secondPicker.update({
        candidates: library.items.filter(p => p.kind === 'original' && p.id !== state.first.id),
        ratio: ratio,
        batch: batch.ids.length > 1 ? new Set(batch.ids) : null,
        note: state.firstReady && !ratio
            ? 'The first crop leaves no room for a second picture. Narrow it, and the best partners are listed first.'
            : ''
    });
}

function computeImage2Ratio() {
    if (!state.cropper) return null;

    const cropData = state.cropper.getData();
    const cropW = Math.round(cropData.width);
    const cropH = Math.round(cropData.height);

    if (cropW <= 0 || cropH <= 0) return null;

    const gap = Math.round(SCREEN.width * state.gapPercent / 100);
    const sw1 = Math.round(cropW * SCREEN.height / cropH);
    const sw2 = SCREEN.width - gap - sw1;

    if (sw2 <= 0) return null;

    return sw2 / SCREEN.height;
}

function initializeCropper2() {
    const token = ++secondShown;
    if (state.cropper2) {
        state.cropper2.destroy();
        state.cropper2 = null;
    }
    // Put back once, when the pair is first opened. Refresh starts the crop afresh.
    const restore = state.restore2;
    state.restore2 = null;

    syncGapDisplay();
    $('diptych-refresh-bar').hidden = true;

    const ratio2 = computeImage2Ratio();
    if (ratio2 === null) {
        $('diptych-crop-warning').textContent = 'The first crop is too wide: there is no room for a second picture.';
        $('diptych-crop-warning').hidden = false;
    } else {
        $('diptych-crop-warning').hidden = true;
    }

    // Same reasoning as the first cropper: wait for the current src to be ready.
    // Refresh re-runs this with the src unchanged, which decode() also handles.
    const cropImage2 = $('crop-image-2');
    cropImage2.src = sourceUrl(state.second);
    cropImage2.decode().then(() => {
        if (token !== secondShown) {
            return;
        }
        // Read here, not before the image loads, so a gap or first crop changed
        // while it was loading is the shape the second picture gets.
        const ratioNow = computeImage2Ratio();
        if (ratioNow === null) {
            $('diptych-crop-warning').textContent = 'The first crop is too wide: there is no room for a second picture.';
            $('diptych-crop-warning').hidden = false;
        } else {
            $('diptych-crop-warning').hidden = true;
        }
        state.cropper2 = new Cropper(cropImage2, {
            aspectRatio: ratioNow !== null && ratioNow > 0 ? ratioNow : 1,
            viewMode: 1,
            dragMode: 'move',
            autoCropArea: 1,
            restore: false,
            guides: true,
            center: true,
            highlight: false,
            cropBoxMovable: true,
            cropBoxResizable: true,
            toggleDragModeOnDblclick: false,
            ready: function() {
                if (token === secondShown && restore) {
                    this.cropper.setData(restore);
                }
            },
            crop: function(event) {
                updateCropInfo2(event.detail);
            }
        });
    }).catch(() => {
        if (token === secondShown) {
            $('diptych-crop-warning').textContent = 'Could not display that picture. Change it for another.';
            $('diptych-crop-warning').hidden = false;
        }
    });
}

function updateCropInfo2(detail) {
    const width = Math.round(detail.width);
    const height = Math.round(detail.height);
    const x = Math.round(detail.x);
    const y = Math.round(detail.y);
    $('crop-info-2').textContent = `Crop area: ${width} × ${height}px at position (${x}, ${y})`;
}

// The picker: library pictures to choose from, and a way to add a new one.

function createPicker(container, { onPick, onAdded, emptyText }) {
    let options = { candidates: [], ratio: null, batch: null, note: '' };
    let showAll = false;
    let request = 0;

    container.classList.add('picker');

    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'link-input picker-search';
    search.placeholder = 'Find by name, creator or subject';
    search.setAttribute('aria-label', 'Find pictures');
    search.addEventListener('input', draw);

    const note = document.createElement('p');
    note.className = 'warning-text';
    note.hidden = true;

    const groups = document.createElement('div');

    const add = document.createElement('div');
    add.className = 'picker-add';
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = VALID_TYPES.join(',');
    fileInput.hidden = true;
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'btn btn-secondary btn-sm';
    addButton.textContent = 'Add a new picture';
    const linkForm = document.createElement('form');
    linkForm.className = 'link-row';
    const linkInput = document.createElement('input');
    linkInput.type = 'url';
    linkInput.className = 'link-input';
    linkInput.placeholder = 'or paste a picture link';
    linkInput.required = true;
    linkInput.setAttribute('aria-label', 'Picture link');
    const linkButton = document.createElement('button');
    linkButton.className = 'btn btn-secondary btn-sm';
    linkButton.textContent = 'Fetch';
    linkForm.append(linkInput, linkButton);
    const status = document.createElement('div');
    status.className = 'picker-status';
    add.append(addButton, fileInput, linkForm, status);

    container.append(search, note, groups, add);

    function say(message, type) {
        status.className = `picker-status ${type || ''}`;
        status.textContent = message;
    }

    function draw() {
        const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
        const scored = options.candidates
            .filter(picture => matches(picture, words))
            .map(picture => ({ picture, keeps: options.ratio ? keeps(picture, options.ratio) : null }));
        if (options.ratio) {
            // Stable, so pictures that fit as well stay newest first.
            scored.sort((a, b) => b.keeps - a.keeps);
        }
        const inBatch = options.batch ? scored.filter(s => options.batch.has(s.picture.id)) : [];
        const rest = options.batch ? scored.filter(s => !options.batch.has(s.picture.id)) : scored;

        note.textContent = options.note;
        note.hidden = !options.note;
        groups.replaceChildren();
        if (inBatch.length) {
            groups.append(group('From this batch', inBatch, Infinity));
        }
        groups.append(group(inBatch.length ? 'Library' : '', rest, showAll ? Infinity : PICKER_PAGE,
            words.length ? 'No pictures match that.' : emptyText));
    }

    function group(title, list, limit, empty) {
        const el = document.createElement('div');
        el.className = 'picker-group';
        if (title) {
            const heading = document.createElement('h3');
            heading.textContent = title;
            el.append(heading);
        }
        if (!list.length) {
            const p = document.createElement('p');
            p.className = 'instruction';
            p.textContent = empty;
            el.append(p);
            return el;
        }
        const grid = document.createElement('div');
        grid.className = 'picker-grid';
        grid.append(...list.slice(0, limit).map(tile));
        el.append(grid);
        if (list.length > limit) {
            const more = document.createElement('button');
            more.type = 'button';
            more.className = 'btn btn-secondary btn-sm';
            more.textContent = `Show all ${list.length}`;
            more.addEventListener('click', () => { showAll = true; draw(); });
            el.append(more);
        }
        return el;
    }

    function tile({ picture, keeps }) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'pick-tile';
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.alt = '';
        img.src = `/api/library/${picture.id}/thumb`;
        const name = document.createElement('span');
        name.className = 'pick-name';
        name.textContent = picture.name;
        const meta = document.createElement('span');
        meta.className = 'pick-meta';
        meta.textContent = [
            keeps !== null ? `keeps ${Math.round(keeps * 100)}%` : `${picture.width}×${picture.height}`,
            picture.kind === 'result' ? 'edited' : '',
            picture.building_block ? 'building block' : '',
            picture.used_in ? `used in ${picture.used_in}` : ''
        ].filter(Boolean).join(' · ');
        button.title = keeps !== null
            ? `${picture.name}: cropped to fit beside the first picture, ${Math.round(keeps * 100)}% of it is kept`
            : picture.name;
        button.append(img, name, meta);
        button.addEventListener('click', () => onPick(picture));
        return button;
    }

    // Keep the bytes in the library, then hand back what the server answered.
    async function bring(label, send) {
        const token = ++request;
        say(label, 'loading');
        try {
            const data = await send();
            if (token !== request) {
                return;
            }
            if (!data.success) {
                say(errorText(data, 'That picture could not be added'), 'error');
                return;
            }
            await loadLibrary().catch(() => {});
            say('', '');
            onAdded(data);
        } catch (error) {
            if (token === request) {
                say('That picture could not be added: ' + error.message, 'error');
            }
        }
    }

    function addFile(file) {
        if (!VALID_TYPES.includes(file.type)) {
            say('Invalid file type. Please choose a JPEG, PNG, WEBP or BMP picture.', 'error');
            return;
        }
        if (file.size > MAX_BYTES) {
            say('File too large. Maximum size is 50MB.', 'error');
            return;
        }
        const formData = new FormData();
        formData.append('file', file);
        bring('Uploading...', async () => readJson(await fetch('/api/editor/upload', { method: 'POST', body: formData })));
    }

    addButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', e => {
        if (e.target.files.length > 0) {
            addFile(e.target.files[0]);
            // Cleared so picking the same file again after a failure still fires 'change'.
            e.target.value = '';
        }
    });
    linkForm.addEventListener('submit', async e => {
        e.preventDefault();
        setLinkFormBusy(linkForm, true);
        await bring('Fetching...', async () => {
            const data = await fetchLink(linkInput.value);
            if (data.success) {
                linkInput.value = '';
            }
            return data;
        });
        setLinkFormBusy(linkForm, false);
    });
    add.addEventListener('dragover', e => {
        e.preventDefault();
        add.classList.add('dragover');
    });
    add.addEventListener('dragleave', () => add.classList.remove('dragover'));
    add.addEventListener('drop', e => {
        e.preventDefault();
        add.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            addFile(e.dataTransfer.files[0]);
        }
    });

    return {
        update(next) {
            options = { ...options, ...next };
            draw();
        }
    };
}

// The same rule as the library page's search: every word in the name, creators or subjects.
function matches(picture, words) {
    const from = picture.from_originals || {};
    const text = [
        picture.name, ...(picture.creators || []), ...(picture.subjects || []),
        ...(from.creators || []), ...(from.subjects || [])
    ].join('\n').toLowerCase();
    return words.every(word => text.includes(word));
}

// How much of a picture is left when it is cropped to the shape `ratio` (width / height).
function keeps(picture, ratio) {
    const aspect = picture.width / picture.height;
    return Math.min(aspect, ratio) / Math.max(aspect, ratio);
}

// Spaces and accented characters are encoded the way the address bar would,
// so the server can ask for them.
function encodedLink(value) {
    try {
        return new URL(value.trim()).href;
    } catch (error) {
        return value.trim();
    }
}

// The server downloads the link; the answer has the same shape as an upload.
async function fetchLink(url) {
    const response = await fetch('/api/editor/fetch', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url: encodedLink(url) })
    });
    return readJson(response);
}

// A download can take a while, so the form is off until it is over.
function setLinkFormBusy(form, busy) {
    form.querySelectorAll('input, button').forEach((element) => {
        element.disabled = busy;
    });
}

// Process image

function initializeProcess() {
    $('process-btn').addEventListener('click', processImage);
}

function cropOf(cropper) {
    const data = cropper.getData(true);
    return {
        x: Math.round(data.x),
        y: Math.round(data.y),
        width: Math.round(data.width),
        height: Math.round(data.height)
    };
}

async function processImage() {
    if (!state.cropper) {
        showProcessStatus('Please select a crop area first', 'error');
        return;
    }

    if (state.layout === 'pair') {
        if (!state.second || !state.cropper2) {
            showProcessStatus('Pick the second picture and crop it first', 'error');
            return;
        }
        await submit('/api/editor/process-diptych', {
            filename1: state.first.file,
            filename2: state.second.file,
            original_filename1: state.first.name,
            original_filename2: state.second.name,
            preset: PRESET,
            gap_percent: state.gapPercent,
            crop1: cropOf(state.cropper),
            crop2: cropOf(state.cropper2)
        }, 'Processing the pair...');
        return;
    }

    await submit('/api/editor/process', {
        filename: state.first.file,
        original_filename: state.first.name,
        preset: PRESET,
        crop: cropOf(state.cropper),
        letterbox: state.layout === 'bars'
    }, 'Processing image...');
}

async function submit(url, requestData, label) {
    const token = opening;
    $('process-btn').disabled = true;
    showProcessStatus(label, 'loading');

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        });

        const data = await readJson(response);

        if (token !== opening) {
            return;
        }
        if (data.success) {
            showResult(data);
        } else {
            showProcessStatus(errorText(data, 'Processing failed'), 'error');
        }
    } catch (error) {
        if (token === opening) {
            showProcessStatus('Processing failed: ' + error.message, 'error');
        }
    }
    // Left enabled so the crop can be changed and processed again.
    $('process-btn').disabled = false;
}

function showResult(data) {
    state.resultUrl = data.url;
    state.downloadUrl = data.download_url;

    // Populate filename input (remove .jpg extension)
    $('download-filename').value = data.suggested_filename.replace(/\.jpg$/i, '');

    // A fresh result has not been sent or saved yet
    $('send-btn').disabled = false;
    $('save-btn').disabled = false;
    showSendStatus('', '');

    showProcessStatus('Processing complete!', 'success');

    const resultImage = $('result-image');
    const token = opening;
    resultImage.onload = function() {
        if (token !== opening) {
            return;
        }
        $('download-section').hidden = false;
        $('download-section').scrollIntoView({ behavior: 'smooth' });
    };
    resultImage.src = data.url;
}

function showProcessStatus(message, type) {
    $('process-status').className = type;
    $('process-status').textContent = message;
}

// Send to TV

function initializeSend() {
    $('send-btn').addEventListener('click', sendToTV);
    $('save-btn').addEventListener('click', saveToLibrary);
    loadPlaylists();
}

// Offer the playlists there are. With none, the choice stays hidden.
async function loadPlaylists() {
    try {
        const response = await fetch('/api/playlists');
        const data = await readJson(response);
        for (const playlist of data.items || []) {
            $('playlist-select').append(new Option(playlist.name, playlist.id));
        }
        $('playlist-choice').hidden = $('playlist-select').options.length < 2;
    } catch (error) {
        // Sending and saving work without it.
    }
}

// Keep the result in the library. Returns what the library calls it.
// `url` and `name` are the ones from the click: the page may have moved on since.
async function keep(url, name) {
    const response = await fetch(`${url}/save`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ name })
    });
    const data = await readJson(response);
    if (!response.ok) {
        throw new Error(errorText(data, 'Save failed'));
    }
    return data;
}

// Put a kept result in the chosen playlist. Returns the words to add to the message.
async function addToPlaylist(imageId) {
    const playlist = $('playlist-select').selectedOptions[0];
    if (!playlist.value) {
        return '';
    }
    try {
        const response = await fetch(`/api/playlists/${playlist.value}/pictures`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ pictures: [imageId] })
        });
        if (!response.ok) {
            throw new Error(errorText(await readJson(response), 'request failed'));
        }
        return ` Added to ${playlist.textContent}.`;
    } catch (error) {
        return ` It could not be added to ${playlist.textContent}: ${error.message}.`;
    }
}

// The library keeps the result under the name in the filename box.
function keptName() {
    return $('download-filename').value.trim();
}

async function sendToTV() {
    if (!state.resultUrl) {
        return;
    }

    const pending = pendingResult();
    const name = keptName();
    const show = $('show-toggle').checked;
    const sendBtn = $('send-btn');
    sendBtn.disabled = true;
    showSendStatus('Sending to the TV...', 'loading');

    try {
        // A playlist takes library pictures, and sending does not say which one this became.
        // Kept first, it is the same library picture when it is sent.
        const kept = $('playlist-select').value ? await keep(pending.url, name) : null;
        const response = await fetch(`${pending.url}/send`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ show, name })
        });

        const data = await readJson(response);

        if (response.ok) {
            // The button stays disabled so the same result is not sent twice.
            // Sending kept it in the library, so there is nothing left to save.
            if (pending.opening === opening) {
                $('save-btn').disabled = true;
            }
            const added = kept ? await addToPlaylist(kept.id) : '';
            const message = data.warning ? data.warning + added
                : data.shown ? 'Sent. It is on display now.' + added
                : data.selected ? 'Sent and selected. Art Mode is off, so it was not put on screen.' + added
                : 'Sent to the TV.' + added;
            finished(message, data.warning ? 'error' : 'success', pending);
        } else if (pending.opening === opening) {
            showSendStatus(errorText(data, 'Send failed'), 'error');
            sendBtn.disabled = false;
        }
    } catch (error) {
        if (pending.opening === opening) {
            showSendStatus('Send failed: ' + error.message, 'error');
            sendBtn.disabled = false;
        }
    }
}

// Keep the result for later without sending it.
async function saveToLibrary() {
    if (!state.resultUrl) {
        return;
    }

    const pending = pendingResult();
    const name = keptName();
    $('save-btn').disabled = true;
    showSendStatus('Saving...', 'loading');

    try {
        const data = await keep(pending.url, name);
        finished(`Saved to the library as ${data.name}.` + await addToPlaylist(data.id), 'success', pending);
    } catch (error) {
        if (pending.opening === opening) {
            showSendStatus(error.message, 'error');
            $('save-btn').disabled = false;
        }
    }
}

function showSendStatus(message, type) {
    $('send-status').className = type;
    $('send-status').textContent = message;
}

// The picture a send or save belongs to, taken before the request. Leaving that
// picture while it runs must not mark whichever one is open when the reply comes.
function pendingResult() {
    const id = batch.ids.length > 1 && batch.at >= 0 ? batch.ids[batch.at] : null;
    const partnerId = id && state.layout === 'pair' && state.second && state.second.id !== id
        && batch.ids.includes(state.second.id) ? state.second.id : null;
    return { url: state.resultUrl, opening, id, name: state.first ? state.first.name : '', partnerId };
}

// A picture is sent or saved. In a batch, that one is done and, if it is still
// the picture on screen, the next one opens.
function finished(message, type, pending) {
    const here = pending.opening === opening;
    if (here) {
        showSendStatus(message, type);
    }
    if (!pending.id) {
        return;
    }
    batch.marks.set(pending.id, 'done');
    // A partner from the same batch has been used, so it is not offered again as the next one.
    // One already sent or saved stays counted as made.
    if (pending.partnerId && batch.marks.get(pending.partnerId) !== 'done') {
        batch.marks.set(pending.partnerId, 'partner');
    }
    renderBatch();
    if (!here) {
        return;
    }
    showBatchStatus(`${pending.name}: ${message}`, type);
    advance();
}

// Download functionality
function initializeDownload() {
    $('download-btn').addEventListener('click', function() {
        // Get custom filename from input
        const customName = $('download-filename').value.trim();

        if (customName) {
            // Add custom name as query parameter
            const url = new URL(state.downloadUrl, window.location.origin);
            url.searchParams.set('name', customName);
            window.location.href = url.toString();
        } else {
            window.location.href = state.downloadUrl;
        }
    });
}

// The batch: a strip of its pictures, Back and Skip.

const MARKS = {
    done: { sign: '✓', text: 'done' },
    partner: { sign: '✓', text: 'used in a pair' },
    skipped: { sign: '–', text: 'skipped' },
    missing: { sign: '!', text: 'cannot be edited' }
};

function initializeBatch() {
    $('queue-back').addEventListener('click', () => {
        if (batch.at > 0) {
            showBatchStatus('', '');
            openAt(batch.at - 1);
        } else if (batch.at < 0) {
            showBatchStatus('', '');
            openAt(batch.ids.length - 1);
        }
    });
    $('queue-skip').addEventListener('click', () => {
        const id = batch.ids[batch.at];
        if (!batch.marks.has(id)) {
            batch.marks.set(id, 'skipped');
        }
        showBatchStatus('', '');
        advance();
    });
}

// The next picture after `index` not yet dealt with, going round to the start. -1 if none is left.
function nextOpen(index) {
    for (let step = 1; step <= batch.ids.length; step++) {
        const i = (index + step) % batch.ids.length;
        if (!batch.marks.has(batch.ids[i])) {
            return i;
        }
    }
    return -1;
}

function advance() {
    const next = nextOpen(batch.at);
    if (next >= 0) {
        openAt(next);
        return;
    }
    showBatchDone();
}

// Every picture has been dealt with. Also how a finished batch comes back on reload.
function showBatchDone() {
    opening++;
    clearEditor();
    batch.at = -1;
    history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
    renderBatch();
    $('queue-done').hidden = false;
}

// The marks are kept for this tab, so a reload carries on where the batch was.
// Without storage (a private window, blocked site data) they last as long as the page.
function marksKey() {
    return `editor-marks:${batch.ids.join(',')}`;
}

function restoreMarks() {
    try {
        const saved = JSON.parse(sessionStorage.getItem(marksKey()) || '{}');
        for (const [id, mark] of Object.entries(saved)) {
            if (batch.ids.includes(id) && MARKS[mark]) {
                batch.marks.set(id, mark);
            }
        }
    } catch (error) {
        // Starts with no marks.
    }
}

function saveMarks() {
    try {
        sessionStorage.setItem(marksKey(), JSON.stringify(Object.fromEntries(batch.marks)));
    } catch (error) {
        // Kept for as long as the page is open.
    }
}

function renderBatch() {
    if (batch.ids.length < 2) {
        return;
    }
    saveMarks();
    $('queue-section').hidden = false;
    if (batch.at >= 0) {
        $('queue-position').textContent = `Picture ${batch.at + 1} of ${batch.ids.length}`;
    } else {
        const counts = { made: 0, skipped: 0 };
        for (const mark of batch.marks.values()) {
            if (mark === 'done' || mark === 'partner') counts.made++;
            if (mark === 'skipped') counts.skipped++;
        }
        $('queue-position').textContent = `All done: ${counts.made} made, ${counts.skipped} skipped`;
    }
    $('queue-back').disabled = batch.at === 0;
    $('queue-skip').disabled = batch.at < 0;

    $('queue-strip').replaceChildren(...batch.ids.map((id, i) => {
        const picture = library.byId.get(id);
        const mark = MARKS[batch.marks.get(id)];
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'queue-thumb'
            + (i === batch.at ? ' current' : '')
            + (mark ? ` marked ${batch.marks.get(id)}` : '');
        let title = picture ? picture.name : 'No longer in the library';
        if (mark) {
            title += ` (${mark.text})`;
        } else if (picture && picture.used_in) {
            title += ' (made into a picture before)';
        }
        button.title = title;
        button.setAttribute('aria-label', `Picture ${i + 1}: ${title}`);
        if (picture) {
            const img = document.createElement('img');
            img.alt = '';
            img.src = `/api/library/${id}/thumb`;
            button.append(img);
        }
        const sign = document.createElement('span');
        sign.className = 'queue-mark';
        sign.textContent = mark ? mark.sign : (picture && picture.used_in ? '•' : '');
        button.append(sign);
        button.addEventListener('click', () => {
            showBatchStatus('', '');
            openAt(i);
        });
        return button;
    }));
}

function showBatchStatus(message, type) {
    $('queue-status').className = type || '';
    $('queue-status').textContent = message;
}
