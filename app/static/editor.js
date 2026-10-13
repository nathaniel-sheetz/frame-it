// Frame TV picture editor
// Ported from image_crop_upscale (static/js/app.js), with the resolution
// step removed and a review-and-send step added.

// State management
function initialState() {
    return {
        uploadedFilename: null,
        originalFilename: null,
        uploadedImageUrl: null,
        // The Frame is a 4K panel, so there is no resolution step.
        selectedPreset: '4k',
        selectedPresetData: { width: 3840, height: 2160 },
        cropper: null,
        resultUrl: null,
        downloadUrl: null,
        letterboxEnabled: false,
        // Diptych state
        diptychMode: false,
        image2UploadedFilename: null,
        image2OriginalFilename: null,
        image2ImageUrl: null,
        cropper2: null,
        gapPercent: 1
    };
}

let state = initialState();

// Counted up each time a first or a second picture is asked for, so an answer
// that arrives after the user has moved on to another one is dropped.
let sourceRequest = 0;
let secondRequest = 0;

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

// DOM Elements
const uploadArea = document.getElementById('upload-area');
const fileInput = document.getElementById('file-input');
const uploadStatus = document.getElementById('upload-status');
const linkForm = document.getElementById('link-form');
const linkInput = document.getElementById('link-input');
const cropSection = document.getElementById('crop-section');
const cropImage = document.getElementById('crop-image');
const cropInfo = document.getElementById('crop-info');
const processSection = document.getElementById('process-section');
const processBtn = document.getElementById('process-btn');
const processStatus = document.getElementById('process-status');
const downloadSection = document.getElementById('download-section');
const downloadBtn = document.getElementById('download-btn');
const downloadFilenameInput = document.getElementById('download-filename');
const resetBtn = document.getElementById('reset-btn');
const resultImage = document.getElementById('result-image');
const sendBtn = document.getElementById('send-btn');
const saveBtn = document.getElementById('save-btn');
const showToggle = document.getElementById('show-toggle');
const sendStatus = document.getElementById('send-status');
const letterboxToggle = document.getElementById('letterbox-toggle');
const letterboxHint = document.getElementById('letterbox-hint');

// Diptych DOM Elements
const diptychControls = document.getElementById('diptych-controls');
const diptychBtn = document.getElementById('diptych-btn');
const diptychUploadSection = document.getElementById('diptych-upload-section');
const diptychCancelBtn = document.getElementById('diptych-cancel-btn');
const uploadArea2 = document.getElementById('upload-area-2');
const fileInput2 = document.getElementById('file-input-2');
const uploadStatus2 = document.getElementById('upload-status-2');
const linkForm2 = document.getElementById('link-form-2');
const linkInput2 = document.getElementById('link-input-2');
const diptychCropSection = document.getElementById('diptych-crop-section');
const cropImage2 = document.getElementById('crop-image-2');
const cropInfo2 = document.getElementById('crop-info-2');
const diptychCropWarning = document.getElementById('diptych-crop-warning');
const diptychGapControl = document.getElementById('diptych-gap-control');
const gapSlider = document.getElementById('gap-slider');
const gapPxDisplay = document.getElementById('gap-px-display');
const gapPercentDisplay = document.getElementById('gap-percent-display');
const diptychRefreshBar = document.getElementById('diptych-refresh-bar');
const diptychRefreshBtn = document.getElementById('diptych-refresh-btn');

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    initializeUpload();
    initializeLetterbox();
    initializeProcess();
    initializeDownload();
    initializeSend();
    initializeDiptych();
    loadSourceFromQuery();
});

// Upload functionality
function initializeUpload() {
    // Click to upload
    uploadArea.addEventListener('click', () => fileInput.click());

    // File selection
    fileInput.addEventListener('change', handleFileSelect);

    // Paste a link
    linkForm.addEventListener('submit', handleLink);

    // Drag and drop
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFile(files[0]);
        }
    });
}

function handleFileSelect(e) {
    const files = e.target.files;
    if (files.length > 0) {
        handleFile(files[0]);
        // Cleared so picking the same file again after a failure still fires 'change'.
        e.target.value = '';
    }
}

async function handleFile(file) {
    // Validate file type
    const validTypes = ['image/png', 'image/jpeg','image/webp', 'image/bmp'];
    if (!validTypes.includes(file.type)) {
        showUploadStatus('Invalid file type. Please upload an image file.', 'error');
        return;
    }

    // Validate file size (50MB)
    if (file.size > 50 * 1024 * 1024) {
        showUploadStatus('File too large. Maximum size is 50MB.', 'error');
        return;
    }

    const request = ++sourceRequest;
    showUploadStatus('Uploading...', 'loading');

    // Upload file
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/editor/upload', {
            method: 'POST',
            body: formData
        });

        const data = await readJson(response);

        if (request !== sourceRequest) {
            return;
        }
        if (data.success) {
            useSource(data, `Uploaded successfully! (${data.width} × ${data.height})`);
        } else {
            showUploadStatus(errorText(data, 'Upload failed'), 'error');
        }
    } catch (error) {
        if (request === sourceRequest) {
            showUploadStatus('Upload failed: ' + error.message, 'error');
        }
    }
}

// Start editing a source the server already holds: an upload, a fetched link,
// or one handed over by the gallery.
function useSource(data, message) {
    // Reset any existing state from previous image
    if (state.cropper) {
        state.cropper.destroy();
        state.cropper = null;
    }
    if (state.diptychMode) {
        resetDiptychState();
    }

    // Hide sections from previous workflow
    cropSection.style.display = 'none';
    processSection.style.display = 'none';
    downloadSection.style.display = 'none';

    // Reset button states
    processBtn.disabled = false;
    processStatus.textContent = '';

    // Update state with new image
    state.uploadedFilename = data.filename;
    state.originalFilename = data.original_filename;
    state.uploadedImageUrl = data.url;

    showUploadStatus(message, 'success');

    initializeCropper();
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

async function handleLink(e) {
    e.preventDefault();
    const request = ++sourceRequest;
    const url = linkInput.value;
    setLinkFormBusy(linkForm, true);
    showUploadStatus('Fetching...', 'loading');

    try {
        const data = await fetchLink(url);

        if (request !== sourceRequest) {
            return;
        }
        if (data.success) {
            linkInput.value = '';
            useSource(data, `Fetched successfully! (${data.width} × ${data.height})`);
        } else {
            showUploadStatus(errorText(data, 'Fetch failed'), 'error');
        }
    } catch (error) {
        if (request === sourceRequest) {
            showUploadStatus('Fetch failed: ' + error.message, 'error');
        }
    } finally {
        setLinkFormBusy(linkForm, false);
    }
}

// The gallery's "Custom fit" opens this page as /editor?source=<name>&name=<original name>.
function loadSourceFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const filename = params.get('source');
    if (!filename) {
        return;
    }
    // Removed from the address so a reload starts clean, as it does after an upload.
    history.replaceState(null, '', window.location.pathname);

    // Only the names the server issues; anything else is not put into a URL.
    if (!/^[0-9a-f]{32}\.(jpg|png|webp|bmp)$/.test(filename)) {
        return;
    }
    useSource({
        filename: filename,
        original_filename: params.get('name') || 'image.jpg',
        url: `/api/editor/source/${filename}`
    }, 'Picture ready.');
}

function showUploadStatus(message, type) {
    uploadStatus.className = type;
    uploadStatus.textContent = message;
}

function initializeLetterbox() {
    letterboxToggle.addEventListener('change', function() {
        state.letterboxEnabled = this.checked;
        letterboxHint.style.display = this.checked ? 'block' : 'none';

        if (state.cropper) {
            if (this.checked) {
                state.cropper.setAspectRatio(NaN);
            } else {
                state.cropper.setAspectRatio(state.selectedPresetData.width / state.selectedPresetData.height);
            }
        }
    });
}

function initializeCropper() {
    // Destroy existing cropper if any
    if (state.cropper) {
        state.cropper.destroy();
    }

    // Show crop section
    cropSection.style.display = 'block';
    processSection.style.display = 'block';

    // Function to initialize the cropper
    function createCropper() {
        if (state.cropper) {
            state.cropper.destroy();
        }
        // Calculate aspect ratio (free if letterbox enabled). Read here, not before the
        // image loads, so a toggle made while it was loading is honoured.
        const aspectRatio = state.letterboxEnabled ? NaN : (state.selectedPresetData.width / state.selectedPresetData.height);
        state.cropper = new Cropper(cropImage, {
            aspectRatio: aspectRatio,
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
            crop: function(event) {
                updateCropInfo(event.detail);
            }
        });
    }

    // decode() resolves once the new picture is ready, and rejects if the src
    // is replaced again first, so the cropper never starts on a stale image.
    cropImage.src = state.uploadedImageUrl;
    cropImage.decode().then(createCropper).catch(() => {
        if (cropImage.src.endsWith(state.uploadedImageUrl)) {
            // Also what a source deleted from the library since it was handed over looks like.
            showUploadStatus('Could not display that picture. Please choose it again.', 'error');
            cropSection.style.display = 'none';
            processSection.style.display = 'none';
        }
    });
}

function updateCropInfo(detail) {
    const width = Math.round(detail.width);
    const height = Math.round(detail.height);
    const x = Math.round(detail.x);
    const y = Math.round(detail.y);

    let info = `Crop area: ${width} × ${height}px at position (${x}, ${y})`;

    if (state.letterboxEnabled && state.selectedPresetData && width > 0 && height > 0) {
        const targetW = state.selectedPresetData.width;
        const targetH = state.selectedPresetData.height;
        const scale = Math.min(targetW / width, targetH / height);
        const scaledW = Math.round(width * scale);
        const scaledH = Math.round(height * scale);
        const barX = Math.round((targetW - scaledW) / 2);
        const barY = Math.round((targetH - scaledH) / 2);

        if (barX > 0) {
            info += ` | Pillarbox: ${barX}px bars left & right`;
        } else if (barY > 0) {
            info += ` | Letterbox: ${barY}px bars top & bottom`;
        } else {
            info += ` | No bars (fits frame exactly)`;
        }

        // Show/hide diptych button based on whether there's room for a second image
        const sw1 = Math.round(width * targetH / height);
        const gap = Math.round(targetW * state.gapPercent / 100);
        if (sw1 < targetW - gap && !state.diptychMode) {
            diptychControls.style.display = 'block';
        } else if (!state.diptychMode) {
            diptychControls.style.display = 'none';
        }

        // Update cropper2 aspect ratio if in diptych mode and Image 2 is loaded
        if (state.diptychMode && state.cropper2) {
            const ratio2 = computeImage2Ratio();
            if (ratio2 !== null && ratio2 > 0) {
                state.cropper2.setAspectRatio(ratio2);
                diptychCropWarning.style.display = 'none';
                diptychRefreshBar.style.display = 'flex';
            } else {
                diptychRefreshBar.style.display = 'none';
                diptychCropWarning.textContent = 'Image 1 crop is too wide — no room for a second image.';
                diptychCropWarning.style.display = 'block';
            }
        }
    } else {
        diptychControls.style.display = 'none';
    }

    cropInfo.textContent = info;
}

// Process image
function initializeProcess() {
    processBtn.addEventListener('click', processImage);
}

async function processImage() {
    if (state.diptychMode) {
        processDiptychImage();
        return;
    }

    if (!state.cropper) {
        showProcessStatus('Please select a crop area first', 'error');
        return;
    }

    // Get crop data
    const cropData = state.cropper.getData(true);

    const requestData = {
        filename: state.uploadedFilename,
        original_filename: state.originalFilename,
        preset: state.selectedPreset,
        crop: {
            x: Math.round(cropData.x),
            y: Math.round(cropData.y),
            width: Math.round(cropData.width),
            height: Math.round(cropData.height)
        },
        letterbox: state.letterboxEnabled
    };

    processBtn.disabled = true;
    showProcessStatus('Processing image...', 'loading');

    try {
        const response = await fetch('/api/editor/process', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        });

        const data = await readJson(response);

        if (data.success) {
            showResult(data);
        } else {
            showProcessStatus(errorText(data, 'Processing failed'), 'error');
        }
    } catch (error) {
        showProcessStatus('Processing failed: ' + error.message, 'error');
    }
    // Left enabled so the crop can be changed and processed again.
    processBtn.disabled = false;
}

function showResult(data) {
    state.resultUrl = data.url;
    state.downloadUrl = data.download_url;

    // Populate filename input (remove .jpg extension)
    const nameWithoutExt = data.suggested_filename.replace(/\.jpg$/i, '');
    downloadFilenameInput.value = nameWithoutExt;

    // A fresh result has not been sent or saved yet
    sendBtn.disabled = false;
    saveBtn.disabled = false;
    sendStatus.textContent = '';

    showProcessStatus('Processing complete!', 'success');

    resultImage.onload = function() {
        downloadSection.style.display = 'block';
        downloadSection.scrollIntoView({ behavior: 'smooth' });
    };
    resultImage.src = data.url;
}

function showProcessStatus(message, type) {
    processStatus.className = type;
    processStatus.textContent = message;
}

// Send to TV
function initializeSend() {
    sendBtn.addEventListener('click', sendToTV);
    saveBtn.addEventListener('click', saveToLibrary);
}

// The library keeps the result under the name in the filename box.
function keptName() {
    return downloadFilenameInput.value.trim();
}

async function sendToTV() {
    if (!state.resultUrl) {
        return;
    }

    sendBtn.disabled = true;
    showSendStatus('Sending to the TV...', 'loading');

    try {
        const response = await fetch(`${state.resultUrl}/send`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ show: showToggle.checked, name: keptName() })
        });

        const data = await readJson(response);

        if (response.ok) {
            // The button stays disabled so the same result is not sent twice.
            // Sending kept it in the library, so there is nothing left to save.
            saveBtn.disabled = true;
            if (data.warning) {
                showSendStatus(data.warning, 'error');
            } else if (data.shown) {
                showSendStatus('Sent. It is on display now.', 'success');
            } else if (data.selected) {
                showSendStatus('Sent and selected. Art Mode is off, so it was not put on screen.', 'success');
            } else {
                showSendStatus('Sent to the TV.', 'success');
            }
        } else {
            showSendStatus(errorText(data, 'Send failed'), 'error');
            sendBtn.disabled = false;
        }
    } catch (error) {
        showSendStatus('Send failed: ' + error.message, 'error');
        sendBtn.disabled = false;
    }
}

// Keep the result for later without sending it.
async function saveToLibrary() {
    if (!state.resultUrl) {
        return;
    }

    saveBtn.disabled = true;
    showSendStatus('Saving...', 'loading');

    try {
        const response = await fetch(`${state.resultUrl}/save`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name: keptName() })
        });

        const data = await readJson(response);

        if (response.ok) {
            showSendStatus(`Saved to the library as ${data.name}.`, 'success');
        } else {
            showSendStatus(errorText(data, 'Save failed'), 'error');
            saveBtn.disabled = false;
        }
    } catch (error) {
        showSendStatus('Save failed: ' + error.message, 'error');
        saveBtn.disabled = false;
    }
}

function showSendStatus(message, type) {
    sendStatus.className = type;
    sendStatus.textContent = message;
}

// Download functionality
function initializeDownload() {
    downloadBtn.addEventListener('click', function() {
        // Get custom filename from input
        const customName = downloadFilenameInput.value.trim();

        if (customName) {
            // Add custom name as query parameter
            const url = new URL(state.downloadUrl, window.location.origin);
            url.searchParams.set('name', customName);
            window.location.href = url.toString();
        } else {
            window.location.href = state.downloadUrl;
        }
    });

    resetBtn.addEventListener('click', function() {
        // Destroy croppers before resetting state
        if (state.cropper) {
            state.cropper.destroy();
        }
        if (state.cropper2) {
            state.cropper2.destroy();
        }

        // Reset state
        state = initialState();
        sourceRequest++;
        secondRequest++;

        // Reset UI
        fileInput.value = '';
        linkInput.value = '';
        uploadStatus.textContent = '';
        cropSection.style.display = 'none';
        processSection.style.display = 'none';
        downloadSection.style.display = 'none';
        processBtn.disabled = false;
        processStatus.textContent = '';
        sendStatus.textContent = '';
        showToggle.checked = false;
        letterboxToggle.checked = false;
        letterboxToggle.disabled = false;
        letterboxHint.style.display = 'none';

        // Reset diptych UI
        diptychControls.style.display = 'none';
        diptychUploadSection.style.display = 'none';
        diptychCropSection.style.display = 'none';
        diptychCropWarning.style.display = 'none';
        diptychRefreshBar.style.display = 'none';
        diptychGapControl.style.display = 'none';
        gapSlider.value = '1';
        gapPercentDisplay.textContent = '1%';
        gapPxDisplay.textContent = '~38px';
        uploadStatus2.textContent = '';
        fileInput2.value = '';
        linkInput2.value = '';

        // Scroll to top
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
}

// Diptych functionality
function initializeDiptych() {
    // Diptych button click
    diptychBtn.addEventListener('click', function() {
        state.diptychMode = true;
        letterboxToggle.disabled = true;
        diptychControls.style.display = 'none';
        diptychUploadSection.style.display = 'block';
        diptychUploadSection.scrollIntoView({ behavior: 'smooth' });
    });

    // Back out of side-by-side mode without re-uploading the first picture
    diptychCancelBtn.addEventListener('click', function() {
        resetDiptychState();
        if (state.cropper) {
            // Brings back the "add second image" button if there is still room for one.
            updateCropInfo(state.cropper.getData());
        }
    });

    // Upload area 2 click
    uploadArea2.addEventListener('click', () => fileInput2.click());

    // File input 2
    fileInput2.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            handleFile2(e.target.files[0]);
            e.target.value = '';
        }
    });

    // Paste a link for the second picture
    linkForm2.addEventListener('submit', handleLink2);

    // Gap slider
    gapSlider.addEventListener('input', function() {
        state.gapPercent = parseInt(this.value);
        gapPercentDisplay.textContent = state.gapPercent + '%';
        if (state.selectedPresetData) {
            const px = Math.round(state.selectedPresetData.width * state.gapPercent / 100);
            gapPxDisplay.textContent = '~' + px + 'px';
        }
        if (state.diptychMode && state.cropper2) {
            const ratio2 = computeImage2Ratio();
            if (ratio2 !== null && ratio2 > 0) {
                // The gap changes how much room image 2 has, so its crop must follow.
                state.cropper2.setAspectRatio(ratio2);
                diptychRefreshBar.style.display = 'flex';
                diptychCropWarning.style.display = 'none';
            } else {
                diptychRefreshBar.style.display = 'none';
                diptychCropWarning.textContent = 'Image 1 crop is too wide — no room for a second image.';
                diptychCropWarning.style.display = 'block';
            }
        }
    });

    // Refresh button
    diptychRefreshBtn.addEventListener('click', function() {
        initializeCropper2();
    });

    // Drag and drop for upload area 2
    uploadArea2.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea2.classList.add('dragover');
    });

    uploadArea2.addEventListener('dragleave', () => {
        uploadArea2.classList.remove('dragover');
    });

    uploadArea2.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea2.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleFile2(e.dataTransfer.files[0]);
        }
    });
}

async function handleFile2(file) {
    const validTypes = ['image/png', 'image/jpeg','image/webp', 'image/bmp'];
    if (!validTypes.includes(file.type)) {
        uploadStatus2.className = 'error';
        uploadStatus2.textContent = 'Invalid file type. Please upload an image file.';
        return;
    }

    if (file.size > 50 * 1024 * 1024) {
        uploadStatus2.className = 'error';
        uploadStatus2.textContent = 'File too large. Maximum size is 50MB.';
        return;
    }

    const request = ++secondRequest;
    uploadStatus2.className = 'loading';
    uploadStatus2.textContent = 'Uploading...';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/editor/upload', {
            method: 'POST',
            body: formData
        });

        const data = await readJson(response);

        if (request !== secondRequest) {
            return;
        }
        if (data.success) {
            useSecondSource(data, `Uploaded successfully! (${data.width} × ${data.height})`);
        } else {
            uploadStatus2.className = 'error';
            uploadStatus2.textContent = errorText(data, 'Upload failed');
        }
    } catch (error) {
        if (request === secondRequest) {
            uploadStatus2.className = 'error';
            uploadStatus2.textContent = 'Upload failed: ' + error.message;
        }
    }
}

function useSecondSource(data, message) {
    state.image2UploadedFilename = data.filename;
    state.image2OriginalFilename = data.original_filename;
    state.image2ImageUrl = data.url;

    uploadStatus2.className = 'success';
    uploadStatus2.textContent = message;

    initializeCropper2();
}

async function handleLink2(e) {
    e.preventDefault();
    const request = ++secondRequest;
    const url = linkInput2.value;
    setLinkFormBusy(linkForm2, true);
    uploadStatus2.className = 'loading';
    uploadStatus2.textContent = 'Fetching...';

    try {
        const data = await fetchLink(url);

        if (request !== secondRequest) {
            return;
        }
        if (data.success) {
            linkInput2.value = '';
            useSecondSource(data, `Fetched successfully! (${data.width} × ${data.height})`);
        } else {
            uploadStatus2.className = 'error';
            uploadStatus2.textContent = errorText(data, 'Fetch failed');
        }
    } catch (error) {
        if (request === secondRequest) {
            uploadStatus2.className = 'error';
            uploadStatus2.textContent = 'Fetch failed: ' + error.message;
        }
    } finally {
        setLinkFormBusy(linkForm2, false);
    }
}

function computeImage2Ratio() {
    if (!state.cropper || !state.selectedPresetData) return null;

    const cropData = state.cropper.getData();
    const cropW = Math.round(cropData.width);
    const cropH = Math.round(cropData.height);

    if (cropW <= 0 || cropH <= 0) return null;

    const targetW = state.selectedPresetData.width;
    const targetH = state.selectedPresetData.height;
    const gap = Math.round(targetW * state.gapPercent / 100);
    const sw1 = Math.round(cropW * targetH / cropH);
    const sw2 = targetW - gap - sw1;

    if (sw2 <= 0) return null;

    return sw2 / targetH;
}

function initializeCropper2() {
    if (state.cropper2) {
        state.cropper2.destroy();
    }

    diptychCropSection.style.display = 'block';

    // Show and sync gap control
    gapSlider.value = String(state.gapPercent);
    gapPercentDisplay.textContent = state.gapPercent + '%';
    if (state.selectedPresetData) {
        const px = Math.round(state.selectedPresetData.width * state.gapPercent / 100);
        gapPxDisplay.textContent = '~' + px + 'px';
    }
    diptychGapControl.style.display = 'flex';
    diptychRefreshBar.style.display = 'none';

    const ratio2 = computeImage2Ratio();
    const aspectRatio = (ratio2 !== null && ratio2 > 0) ? ratio2 : 1;

    function createCropper2() {
        if (state.cropper2) {
            state.cropper2.destroy();
        }
        state.cropper2 = new Cropper(cropImage2, {
            aspectRatio: aspectRatio,
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
            crop: function(event) {
                updateCropInfo2(event.detail);
            }
        });
    }

    // Same reasoning as the first cropper: wait for the current src to be ready.
    // Refresh re-runs this with the src unchanged, which decode() also handles.
    cropImage2.src = state.image2ImageUrl;
    cropImage2.decode().then(createCropper2).catch(() => {
        if (cropImage2.src.endsWith(state.image2ImageUrl)) {
            uploadStatus2.className = 'error';
            uploadStatus2.textContent = 'Could not display that picture.';
        }
    });

    diptychCropSection.scrollIntoView({ behavior: 'smooth' });
}

function updateCropInfo2(detail) {
    const width = Math.round(detail.width);
    const height = Math.round(detail.height);
    const x = Math.round(detail.x);
    const y = Math.round(detail.y);
    cropInfo2.textContent = `Crop area: ${width} × ${height}px at position (${x}, ${y})`;
}

function resetDiptychState() {
    if (state.cropper2) {
        state.cropper2.destroy();
        state.cropper2 = null;
    }
    // A second picture still on its way is no longer wanted.
    secondRequest++;
    state.diptychMode = false;
    state.image2UploadedFilename = null;
    state.image2OriginalFilename = null;
    state.image2ImageUrl = null;

    diptychControls.style.display = 'none';
    diptychUploadSection.style.display = 'none';
    diptychCropSection.style.display = 'none';
    diptychCropWarning.style.display = 'none';
    diptychRefreshBar.style.display = 'none';
    diptychGapControl.style.display = 'none';
    uploadStatus2.textContent = '';
    fileInput2.value = '';
    linkInput2.value = '';
    letterboxToggle.disabled = false;
}

async function processDiptychImage() {
    if (!state.cropper || !state.cropper2) {
        showProcessStatus('Please crop both images first', 'error');
        return;
    }

    const cropData1 = state.cropper.getData(true);
    const cropData2 = state.cropper2.getData(true);

    const requestData = {
        filename1: state.uploadedFilename,
        filename2: state.image2UploadedFilename,
        original_filename1: state.originalFilename,
        original_filename2: state.image2OriginalFilename,
        preset: state.selectedPreset,
        gap_percent: state.gapPercent,
        crop1: {
            x: Math.round(cropData1.x),
            y: Math.round(cropData1.y),
            width: Math.round(cropData1.width),
            height: Math.round(cropData1.height)
        },
        crop2: {
            x: Math.round(cropData2.x),
            y: Math.round(cropData2.y),
            width: Math.round(cropData2.width),
            height: Math.round(cropData2.height)
        }
    };

    processBtn.disabled = true;
    showProcessStatus('Processing diptych...', 'loading');

    try {
        const response = await fetch('/api/editor/process-diptych', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        });

        const data = await readJson(response);

        if (data.success) {
            showResult(data);
        } else {
            showProcessStatus(errorText(data, 'Processing failed'), 'error');
        }
    } catch (error) {
        showProcessStatus('Processing failed: ' + error.message, 'error');
    }
    processBtn.disabled = false;
}
