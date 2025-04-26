// Configuration
const BACKEND_URL = 'https://youtube-video-downloader-jgz9.onrender.com'; // Your Flask backend URL

// DOM Elements Cache
const elements = {
    urlInput: document.getElementById('youtubeUrl'),
    clearButton: document.getElementById('clearButton'),
    urlError: document.getElementById('urlError'),
    fetchButton: document.getElementById('fetchButton'),
    fetchButtonText: document.getElementById('fetchButtonText'),
    fetchButtonIcon: document.getElementById('fetchButtonIcon'),
    loadingSection: document.getElementById('loadingSection'),
    errorSection: document.getElementById('errorSection'),
    errorTitle: document.getElementById('errorTitle'),
    errorMessage: document.getElementById('errorMessage'),
    resultsSection: document.getElementById('resultsSection'),
    videoTitle: document.getElementById('videoTitle'),
    thumbnail: document.getElementById('thumbnail'),
    videoUploader: document.getElementById('videoUploader'),
    videoDuration: document.getElementById('videoDuration'),
    videoViews: document.getElementById('videoViews'),
    videoFormats: document.getElementById('videoFormats'),
    noVideoFormats: document.getElementById('noVideoFormats'),
    audioFormats: document.getElementById('audioFormats'),
    noAudioFormats: document.getElementById('noAudioFormats'),
    videoQualityFilter: document.getElementById('videoQualityFilter'),
    audioQualityFilter: document.getElementById('audioQualityFilter'),
    selectedFormatInfo: document.getElementById('selectedFormatInfo'),
    downloadButton: document.getElementById('downloadButton'),
    downloadProgress: document.getElementById('downloadProgress'), // Progress bar element (display only, logic removed)
    downloadFeedback: document.getElementById('downloadFeedback'),
    downloadFeedbackText: document.getElementById('downloadFeedbackText'),
    currentYear: document.getElementById('currentYear'),
    copyButton: document.getElementById('copyButton'),
    previewModal: document.getElementById('previewModal'),
    previewFrame: document.getElementById('previewFrame'),
    closePreview: document.getElementById('closePreview')
};

// Application State
let currentState = {
    videoUrl: '',
    videoId: '',
    selectedFormat: null,
    downloadInProgress: false,
    formats: {
        video: [],
        audio: []
    }
};

// --- Utility Functions ---
function formatDuration(seconds) {
    if (!seconds || typeof seconds !== 'number' || seconds < 0) return '0:00';
    const totalSeconds = Math.floor(seconds);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainingSeconds = totalSeconds % 60;
    if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
}

function formatViews(count) {
    if (count === null || count === undefined || typeof count !== 'number') return 'N/A views';
    if (count >= 1000000000) return `${(count / 1000000000).toFixed(1)}B views`;
    if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M views`;
    if (count >= 1000) return `${(count / 1000).toFixed(0)}K views`;
    return `${count} views`;
}

function extractVideoId(url) {
    // Attempts to extract YouTube video ID from various URL formats
    const patterns = [
        /(?:https?:\/\/)?(?:www\.)?youtube.com(?:embed\/|v\/|watch\?v=|watch\?.+&v=)([^#&?]+)/,
        /(?:https?:\/\/)?(?:www\.)?youtu\.be\/([^#&?]+)/,
        /(?:https?:\/\/)?(?:www\.)?youtube-nocookie\.com\/embed\/([^#&?]+)/
    ];
    for (const pattern of patterns) {
        const match = url.match(pattern);
        if (match && match[1] && match[1].length === 11) { return match[1]; }
    }
    // Basic fallback (might catch edge cases but less reliable)
    const regExpSimple = /^.*(v\/|u\/\w\/|embed\/|watch\?v=|&v=)([^#&?]*).*/;
    const matchSimple = url.match(regExpSimple);
    return (matchSimple && matchSimple[2].length === 11) ? matchSimple[2] : null;
}

// Corrected isValidUrl function (includes .com/13)
function isValidUrl(url) {
    if (!url) return false;
    try {
        const fullUrl = url.startsWith('http') ? url : `https://${url}`;
        const parsed = new URL(fullUrl);
        const host = parsed.hostname.toLowerCase();
        const supportedHosts = [
            'youtube.com', 'youtu.be', 'm.youtube.com',
            'www.youtube.com', 'https://youtu.be/rgKf6eVtdZU?si=WH0uTSy0I9lkw3lg',
            'youtube-nocookie.com', 'www.youtube-nocookie.com'
        ];
        return supportedHosts.some(supportedHost => host === supportedHost || (supportedHost.includes('.') && host.endsWith('.' + supportedHost)));
    } catch (e) { return false; }
}

function formatBytes(bytes, decimals = 2) {
    // Formats bytes into KB, MB, GB etc. (Note: Backend sends size as string "X.XX MB")
    // This function might be useful if backend sends raw bytes in the future
    if (!bytes || bytes === 0 || isNaN(bytes)) return 'N/A';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    const index = Math.max(0, Math.min(i, sizes.length - 1));
    return parseFloat((bytes / Math.pow(k, index)).toFixed(dm)) + ' ' + sizes[index];
}

function sanitizeFilename(filename) {
    // Simple sanitization for JavaScript side (match backend if needed)
    return filename.replace(/[^a-z0-9._-]/gi, '_').substring(0, 100);
}

// --- UI Update Functions ---
function resetUI() {
    elements.loadingSection.classList.add('hidden');
    elements.errorSection.classList.add('hidden');
    elements.resultsSection.classList.add('hidden');
    elements.urlError.classList.add('hidden');
    elements.downloadProgress.classList.add('hidden');
    elements.downloadFeedback.classList.add('hidden');
    elements.clearButton.classList.add('hidden');
    currentState.selectedFormat = null;
    currentState.downloadInProgress = false;
    elements.selectedFormatInfo.innerHTML = `<i class="fas fa-info-circle mr-2 text-indigo-500"></i><span>No format selected</span>`;
    elements.downloadButton.disabled = true;
    elements.videoFormats.innerHTML = ''; elements.audioFormats.innerHTML = '';
    elements.noVideoFormats.classList.add('hidden'); elements.noAudioFormats.classList.add('hidden');
    elements.fetchButtonText.textContent = 'Fetch'; elements.fetchButtonIcon.className = 'fas fa-arrow-right ml-2'; elements.fetchButton.disabled = false;
}

function showLoading() { resetUI(); elements.loadingSection.classList.remove('hidden'); elements.fetchButtonText.textContent = 'Fetching...'; elements.fetchButtonIcon.className = 'fas fa-spinner fa-spin ml-2'; elements.fetchButton.disabled = true; }
function showError(title, message) { resetUI(); elements.errorTitle.textContent = title; elements.errorMessage.innerHTML = `<p>${message}</p>`; elements.errorSection.classList.remove('hidden'); elements.fetchButtonText.textContent = 'Fetch'; elements.fetchButtonIcon.className = 'fas fa-arrow-right ml-2'; elements.fetchButton.disabled = false; }
function showResults() { elements.loadingSection.classList.add('hidden'); elements.errorSection.classList.add('hidden'); elements.resultsSection.classList.remove('hidden'); elements.fetchButtonText.textContent = 'Fetch'; elements.fetchButtonIcon.className = 'fas fa-arrow-right ml-2'; elements.fetchButton.disabled = false; }
function showDownloadFeedback(message, isError = false) { elements.downloadFeedbackText.textContent = message; elements.downloadFeedback.classList.remove('hidden'); const icon = elements.downloadFeedback.querySelector('i'); const container = elements.downloadFeedback.querySelector('.rounded-md'); if (isError) { icon.className = 'fas fa-exclamation-circle text-red-500'; container.className = 'rounded-md p-3 bg-red-50 border border-red-100'; elements.downloadFeedbackText.className = 'text-sm text-red-700'; } else { icon.className = 'fas fa-info-circle text-blue-500'; container.className = 'rounded-md p-3 bg-blue-50 border border-blue-100'; elements.downloadFeedbackText.className = 'text-sm text-blue-700'; } }

// --- Format Card Creation and Selection (No Audio indicator removed) ---
function createFormatCard(format, type) {
    const card = document.createElement('div');
    const isSelected = currentState.selectedFormat?.id === format.id;
    card.className = `format-card bg-white border rounded-lg p-4 cursor-pointer hover:shadow-md transition-all ${ isSelected ? 'selected border-indigo-500' : 'border-gray-200' }`;
    card.dataset.formatId = format.id; card.dataset.type = type;

    let icon, qualityText; // Removed audioIndicator
    let fileSize = format.size || 'N/A'; // Use size string from backend

    if (type === 'video') {
        icon = '<i class="fas fa-film text-indigo-600 mr-2"></i>';
        qualityText = format.quality || 'Unknown';
        if (format.fps && format.fps > 30) qualityText += ` (${format.fps}fps)`;
        // ** No longer adding "(No Audio)" indicator **
    } else { // Audio
        icon = '<i class="fas fa-music text-indigo-600 mr-2"></i>';
        qualityText = format.quality || 'Unknown';
        // Add bitrate info if available (backend provides it in quality string now often)
        if (format.abr && typeof format.abr === 'number' && !qualityText.includes('kbps')) {
             qualityText += ` (~${Math.round(format.abr)}kbps)`;
        }
    }

    // Construct inner HTML (without audioIndicator)
    card.innerHTML = `
        <div class="flex items-center justify-between">
            <div class="flex items-center overflow-hidden">
                <div class="flex-shrink-0 text-lg">${icon}</div>
                <div class="ml-3 overflow-hidden">
                    <div class="flex items-center">
                        <h4 class="font-medium text-gray-800 truncate" title="${qualityText}">${qualityText}</h4>
                    </div>
                    <div class="flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500 mt-1">
                        <span>${format.ext?.toUpperCase() || '?'}</span>
                        <span>${fileSize}</span>
                    </div>
                </div>
            </div>
            <div class="flex-shrink-0 ml-2">
                ${isSelected ? '<i class="fas fa-check-circle text-indigo-600"></i>' : '<i class="far fa-circle text-gray-400"></i>'}
            </div>
        </div>
    `;
    card.addEventListener('click', () => selectFormat(format, type));
    return card;
}

function selectFormat(format, type) {
    const previousSelectedCard = document.querySelector('.format-card.selected');
    if (previousSelectedCard) { previousSelectedCard.classList.remove('selected', 'border-indigo-500'); previousSelectedCard.classList.add('border-gray-200'); const prevCheckmark = previousSelectedCard.querySelector('.fa-check-circle'); if (prevCheckmark) prevCheckmark.className = 'far fa-circle text-gray-400'; }
    const newSelectedCard = document.querySelector(`.format-card[data-format-id="${format.id}"]`);
    if (newSelectedCard) { newSelectedCard.classList.add('selected', 'border-indigo-500'); newSelectedCard.classList.remove('border-gray-200'); const newCheckmark = newSelectedCard.querySelector('.fa-circle'); if (newCheckmark) newCheckmark.className = 'fas fa-check-circle text-indigo-600'; }
    currentState.selectedFormat = { ...format, type: type };
    let typeIcon = type === 'video' ? '<i class="fas fa-film text-indigo-600 mr-2"></i>' : '<i class="fas fa-music text-indigo-600 mr-2"></i>';
    let typeText = type === 'video' ? 'Video' : 'Audio';
    let fileSize = format.size || 'N/A';
    elements.selectedFormatInfo.innerHTML = `<i class="fas fa-info-circle mr-2 text-indigo-500"></i><span class="font-medium">${typeText}:</span><span class="ml-1">${format.quality || '?'} (${format.ext?.toUpperCase() || '?'}, ${fileSize})</span>`;
    elements.downloadButton.disabled = false;
    elements.downloadFeedback.classList.add('hidden');
    elements.downloadProgress.classList.add('hidden');
}

// Filter function for quality dropdowns
function filterFormats() {
    const videoFilter = elements.videoQualityFilter.value;
    const audioFilter = elements.audioQualityFilter.value;
    elements.videoFormats.innerHTML = ''; elements.audioFormats.innerHTML = '';
    let videoFormatsFound = 0; let audioFormatsFound = 0;

    // --- ** Re-added Adaptive Format Filter Function (Inline for Clarity) ** ---
    function isAdaptiveFormat(format) { if (format?.protocol?.includes('m3u8') || format?.protocol?.includes('dash')) { /*console.log(`Filtering adaptive: ${format.id}`);*/ return true; } return false; }
    // --- End of Filter Function ---

    // Filter and display video formats
    currentState.formats.video.forEach(format => {
        if (isAdaptiveFormat(format)) return; // Skip adaptive formats
        let showFormat = true;
        if (videoFilter === 'hd') { showFormat = (format.height && format.height >= 720) || (format.quality && /720|1080|1440|2160|4k/i.test(format.quality)); }
        else if (videoFilter === '4k') { showFormat = (format.height && format.height >= 2160) || (format.quality && /2160|4k/i.test(format.quality)); }
        if (showFormat) { elements.videoFormats.appendChild(createFormatCard(format, 'video')); videoFormatsFound++; }
    });
    // Filter and display audio formats
    currentState.formats.audio.forEach(format => {
        if (isAdaptiveFormat(format)) return; // Skip adaptive formats
        let showFormat = true;
        if (audioFilter === 'high') { showFormat = format.abr && format.abr >= 128; }
        if (showFormat) { elements.audioFormats.appendChild(createFormatCard(format, 'audio')); audioFormatsFound++; }
    });
    elements.noVideoFormats.classList.toggle('hidden', videoFormatsFound > 0);
    elements.noAudioFormats.classList.toggle('hidden', audioFormatsFound > 0);
    // Reset selection if the selected format is no longer visible
    if (currentState.selectedFormat) {
        const isVisible = document.querySelector(`.format-card[data-format-id="${currentState.selectedFormat.id}"]`);
        if (!isVisible) {
             currentState.selectedFormat = null;
             elements.selectedFormatInfo.innerHTML = `<i class="fas fa-info-circle mr-2 text-indigo-500"></i><span>No format selected</span>`;
             elements.downloadButton.disabled = true;
        }
    }
}

// --- Core Logic Functions ---
async function fetchVideoInfo() {
    const url = elements.urlInput.value.trim();
    if (!isValidUrl(url)) { elements.urlError.classList.remove('hidden'); return; }
    else { elements.urlError.classList.add('hidden'); }
    currentState.videoUrl = url;
    currentState.videoId = extractVideoId(url); // Attempt to extract Video ID
    showLoading();
    try {
        const apiUrl = `${BACKEND_URL}/api/get-formats?url=${encodeURIComponent(url)}`;
        const response = await fetch(apiUrl);
        if (!response.ok) { let eMsg='Failed fetch.'; try { const eData=await response.json(); eMsg=eData.error||`Server Error (${response.status})`; } catch(e){eMsg=`Server Error (${response.status})`;} throw new Error(eMsg); }
        const data = await response.json(); if (!data.success) { throw new Error(data.error || 'Backend error.'); }
        // Update UI
        elements.videoTitle.textContent = data.videoTitle || 'N/A';
        elements.thumbnail.src = data.thumbnailUrl || 'https://via.placeholder.com/320x180?text=No+Thumb';
        elements.videoUploader.textContent = data.uploader || 'Unknown';
        elements.videoDuration.textContent = formatDuration(data.duration);
        elements.videoViews.textContent = formatViews(data.viewCount);
        // Store formats
        currentState.formats.video = data.formats?.video || [];
        currentState.formats.audio = data.formats?.audio || [];
        filterFormats(); // Apply filters and display initially
        showResults();
        elements.clearButton.classList.remove('hidden');
    } catch (error) { console.error('Fetch error:', error); showError('Error Fetching Video', error.message || 'Unexpected error.'); }
}

// ** Function to Initiate Download (Uses <a> tag method) **
async function downloadSelectedFormat() {
     if (!currentState.selectedFormat || !currentState.videoUrl || currentState.downloadInProgress) return;
     currentState.downloadInProgress = true;
     try {
         const { id, ext } = currentState.selectedFormat;
         const videoTitle = elements.videoTitle.textContent || 'video';
         const safeTitle = sanitizeFilename(videoTitle);
         const filename = `${safeTitle}.${ext || 'mp4'}`; // Use actual ext from format, fallback mp4

         // Construct the backend URL that triggers the server-side download/merge
         const downloadUrl = `${BACKEND_URL}/api/download?url=${encodeURIComponent(currentState.videoUrl)}&format_id=${encodeURIComponent(id)}&filename=${encodeURIComponent(filename)}`;

         // Update feedback for server-side processing
         showDownloadFeedback(`Processing ${filename} on server... Please wait. Download will start automatically.`);
         elements.downloadButton.disabled = true;
         console.log(`Requesting download: ${filename} via URL: ${downloadUrl}`);

         // Create anchor element to trigger download request
         const link = document.createElement('a');
         link.href = downloadUrl;
         link.download = filename; // Suggest filename to browser

         document.body.appendChild(link);
         link.click();
         document.body.removeChild(link);

         // Re-enable button after a delay (server processing takes time)
         setTimeout(() => {
              elements.downloadButton.disabled = false;
              currentState.downloadInProgress = false;
         }, 5000); // Adjust delay if needed

     } catch (error) {
         console.error('Error initiating download request:', error);
         showDownloadFeedback('Could not start download process.', true);
         elements.downloadButton.disabled = false;
         currentState.downloadInProgress = false;
     }
}

// Copy function using navigator.clipboard API
function copyVideoInfo() {
    if (!currentState.videoUrl) {
        showDownloadFeedback('No video info loaded to copy.', true);
        return;
    }
    const title = elements.videoTitle.textContent || 'N/A';
    const uploader = elements.videoUploader.textContent || 'N/A';
    const duration = elements.videoDuration.textContent || 'N/A';
    const textToCopy = `${title} - by ${uploader} (${duration})\nURL: ${currentState.videoUrl}`;

    navigator.clipboard.writeText(textToCopy).then(() => {
        const originalText = elements.copyButton.innerHTML;
        elements.copyButton.innerHTML = '<i class="fas fa-check mr-1"></i> Copied!';
        elements.copyButton.disabled = true; // Briefly disable
        setTimeout(() => {
            elements.copyButton.innerHTML = originalText;
            elements.copyButton.disabled = false;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy using navigator.clipboard:', err);
        showDownloadFeedback('Failed to copy info. Browser might not support it or requires permissions.', true);
    });
}

// Video Preview Modal Functions
function showVideoPreview() {
    if (!currentState.videoId) {
        console.warn("No video ID found for preview."); return;
    }
    // Correct YouTube embed URL
    elements.previewFrame.src = `youtu.be4...{currentState.videoId}?autoplay=1&modestbranding=1&rel=0`;
    elements.previewModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden'; // Prevent background scroll
}

function hideVideoPreview() {
    elements.previewFrame.src = ''; // Stop video
    elements.previewModal.classList.add('hidden');
    document.body.style.overflow = ''; // Restore scroll
}

// --- Event Listeners ---
// Use optional chaining in case elements are missing
elements.fetchButton?.addEventListener('click', fetchVideoInfo);
elements.downloadButton?.addEventListener('click', downloadSelectedFormat);
elements.copyButton?.addEventListener('click', copyVideoInfo);
elements.closePreview?.addEventListener('click', hideVideoPreview);

elements.urlInput?.addEventListener('keypress', (e) => { if (e.key === 'Enter') { e.preventDefault(); fetchVideoInfo(); } });
elements.urlInput?.addEventListener('input', () => { elements.urlError?.classList.add('hidden'); elements.clearButton?.classList.toggle('hidden', !elements.urlInput.value); });
elements.clearButton?.addEventListener('click', () => { if(elements.urlInput) { elements.urlInput.value = ''; elements.clearButton.classList.add('hidden'); elements.urlInput.focus(); } });

elements.videoQualityFilter?.addEventListener('change', filterFormats);
elements.audioQualityFilter?.addEventListener('change', filterFormats);
elements.thumbnail?.addEventListener('click', showVideoPreview);
elements.previewModal?.addEventListener('click', (e) => { if (e.target === elements.previewModal) { hideVideoPreview(); } });

// --- Initialize ---
document.addEventListener('DOMContentLoaded', () => {
    if (elements.currentYear) elements.currentYear.textContent = new Date().getFullYear();
    resetUI();

    // Initialize Clipboard.js ONLY IF navigator.clipboard might not be available AND you want a fallback.
    // Otherwise, the copyVideoInfo function using navigator.clipboard is sufficient for modern browsers.
    // if (typeof ClipboardJS !== 'undefined' && elements.copyButton) {
    //      console.log("Initializing Clipboard.js as fallback");
    //      const clipboard = new ClipboardJS(elements.copyButton, { /* ... config ... */ });
    //      clipboard.on('success', ...);
    //      clipboard.on('error', ...);
    // }
});

// Remove jQuery $(document).ready unless specifically needed for other libraries/plugins
// $(document).ready(function() { /* ... */ });