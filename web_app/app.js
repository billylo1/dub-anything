const player = document.getElementById('video-player');
const titleEl = document.getElementById('video-title');
const badgesEl = document.getElementById('video-badges');
const videoListEl = document.getElementById('video-list');
const countLabelEl = document.getElementById('video-count-label');
const searchInput = document.getElementById('search-input');
const downloadBtn = document.getElementById('download-btn');
const shareBtn = document.getElementById('share-btn');
const shareBtnLabel = document.getElementById('share-btn-label');
const pipBtn = document.getElementById('pip-btn');
const theaterBtn = document.getElementById('theater-btn');
const mainLayout = document.getElementById('main-content');
const openLocalBtn = document.getElementById('open-local-btn');
const localFileInput = document.getElementById('local-file-input');
const speedButtons = Array.from(document.querySelectorAll('.speed-btn'));

let allVideos = [];
let activeVideoId = null;

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return null;
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0)} ${sizes[i]}`;
}

function formatDuration(sec) {
  if (!sec || !Number.isFinite(sec) || sec <= 0) return null;
  const total = Math.round(sec);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function cleanTitle(filename) {
  const base = filename.replace(/\.[^/.]+$/, '');
  return base
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\b(Cbc|Bbc|Nbc|Abc|Pbs|Tv|Hd|4k|Ai|Uk|Us|Ca)\b/g, (m) => m.toUpperCase());
}

async function fetchVideosManifest() {
  try {
    const res = await fetch('videos/manifest.json', { cache: 'no-cache' });
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.videos) && data.videos.length > 0) {
        return data.videos;
      }
    }
  } catch {
    // Fallback below if manifest fetch fails
  }

  // Fallback if served by a local directory-listing server
  try {
    const res = await fetch('videos/');
    if (res.ok) {
      const html = await res.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const links = Array.from(doc.querySelectorAll('a'))
        .map((a) => decodeURIComponent(a.getAttribute('href') || ''))
        .filter((href) => /\.(mp4|webm|mov|m4v|ogg|mkv)$/i.test(href));
      if (links.length > 0) {
        return links.map((href) => {
          const filename = href.split('/').pop();
          return {
            id: filename,
            filename,
            title: cleanTitle(filename),
            url: `videos/${encodeURIComponent(filename)}`
          };
        });
      }
    }
  } catch {
    // Ignore directory listing fallback error
  }

  return [];
}

function renderBadges(video, liveDuration, liveResolution) {
  badgesEl.innerHTML = '';

  const items = [];
  if (video.filename) {
    items.push({ text: video.filename, cls: 'badge badge-filename' });
  }
  const durationText = liveDuration || video.durationFormatted;
  if (durationText) {
    items.push({ text: durationText, cls: 'badge' });
  }
  const resText = liveResolution || video.resolution;
  if (resText) {
    items.push({ text: resText, cls: 'badge' });
  }
  if (video.sizeFormatted) {
    items.push({ text: video.sizeFormatted, cls: 'badge' });
  }
  if (video.isLocal) {
    items.push({ text: 'Local file', cls: 'badge' });
  }

  for (const item of items) {
    const span = document.createElement('span');
    span.className = item.cls;
    span.textContent = item.text;
    badgesEl.appendChild(span);
  }
}

function normalizeSearch(text) {
  return (text || '')
    .toLowerCase()
    .replace(/mandarin|国语|國語|普通話/gi, '普通话')
    .replace(/cantonese|广东话|廣東話|粵語/gi, '粤语')
    .replace(/多倫多/g, '多伦多')
    .replace(/全國/g, '全国')
    .replace(/新聞/g, '新闻');
}

function renderLibrary(filterText = '') {
  const query = filterText.trim().toLowerCase();
  const nq = normalizeSearch(query);
  const filtered = allVideos.filter((v) => {
    if (!query) return true;
    return (
      v.title.toLowerCase().includes(query) ||
      v.filename.toLowerCase().includes(query) ||
      normalizeSearch(v.title).includes(nq) ||
      normalizeSearch(v.filename).includes(nq)
    );
  });

  countLabelEl.textContent = `${filtered.length} of ${allVideos.length} video${allVideos.length === 1 ? '' : 's'}`;
  videoListEl.innerHTML = '';

  if (filtered.length === 0) {
    const li = document.createElement('li');
    li.className = 'empty-state';
    li.textContent = query
      ? `No videos matching "${filterText}"`
      : 'No video files found in videos/ folder.';
    videoListEl.appendChild(li);
    return;
  }

  for (const video of filtered) {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `video-card${video.id === activeVideoId ? ' is-active' : ''}`;
    btn.setAttribute('role', 'option');
    btn.setAttribute('aria-selected', video.id === activeVideoId ? 'true' : 'false');

    const metaParts = [];
    if (video.durationFormatted) metaParts.push(video.durationFormatted);
    if (video.resolution) metaParts.push(video.resolution);
    if (video.sizeFormatted) metaParts.push(video.sizeFormatted);

    btn.innerHTML = `
      <span class="card-icon" aria-hidden="true">
        <svg viewBox="0 0 20 20" width="18" height="18" fill="none">
          <path d="M6.5 5.5v9l8-4.5-8-4.5z" fill="currentColor"/>
        </svg>
      </span>
      <span class="card-body">
        <div class="card-title"></div>
        <div class="card-sub"></div>
      </span>
    `;

    btn.querySelector('.card-title').textContent = video.title;
    btn.querySelector('.card-sub').textContent = metaParts.join(' • ') || video.filename;

    btn.addEventListener('click', () => {
      selectVideo(video.id, { autoplay: true });
    });

    li.appendChild(btn);
    videoListEl.appendChild(li);
  }
}

function selectVideo(videoId, { autoplay = false } = {}) {
  const video = allVideos.find((v) => v.id === videoId);
  if (!video) return;

  activeVideoId = video.id;
  titleEl.textContent = video.title;
  document.title = `${video.title} — Videos Billy Lo`;

  const currentRate = player.playbackRate || 1;
  player.src = video.url;
  player.playbackRate = currentRate;

  downloadBtn.href = video.url;
  downloadBtn.setAttribute('download', video.filename);

  renderBadges(video);
  renderLibrary(searchInput.value);

  if (!video.isLocal) {
    const url = new URL(window.location.href);
    url.searchParams.set('v', video.filename);
    window.history.replaceState({}, '', url);
  }

  if (autoplay) {
    player.play().catch(() => {
      // Autoplay might be blocked by browser policy if not user-initiated
    });
  }
}

player.addEventListener('loadedmetadata', () => {
  const video = allVideos.find((v) => v.id === activeVideoId);
  if (!video) return;

  const liveDuration = formatDuration(player.duration);
  const liveResolution =
    player.videoWidth && player.videoHeight
      ? `${player.videoWidth}x${player.videoHeight}`
      : null;

  if (liveDuration && !video.durationFormatted) {
    video.durationFormatted = liveDuration;
  }
  if (liveResolution && !video.resolution) {
    video.resolution = liveResolution;
  }

  renderBadges(video, liveDuration, liveResolution);
  renderLibrary(searchInput.value);
});

// Playback speed controls
for (const btn of speedButtons) {
  btn.addEventListener('click', () => {
    const speed = parseFloat(btn.dataset.speed || '1');
    player.playbackRate = speed;
    for (const b of speedButtons) {
      b.classList.toggle('is-active', b === btn);
    }
  });
}

// Picture-in-Picture
if (!document.pictureInPictureEnabled) {
  pipBtn.hidden = true;
} else {
  pipBtn.addEventListener('click', async () => {
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else {
        await player.requestPictureInPicture();
      }
    } catch {
      // Ignore PiP failure if video not ready
    }
  });
}

// Theater mode
function toggleTheaterMode() {
  mainLayout.classList.toggle('is-theater');
}
theaterBtn.addEventListener('click', toggleTheaterMode);

// Copy direct link
shareBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(window.location.href);
    shareBtnLabel.textContent = 'Copied!';
    setTimeout(() => {
      shareBtnLabel.textContent = 'Copy Link';
    }, 1800);
  } catch {
    // Fallback if clipboard unavailable
  }
});

// Search filter
searchInput.addEventListener('input', () => {
  renderLibrary(searchInput.value);
});

// Local file picker & drag-and-drop support
function addLocalFiles(fileList) {
  const files = Array.from(fileList).filter((f) =>
    f.type.startsWith('video/') || /\.(mp4|webm|mov|m4v|ogg|mkv)$/i.test(f.name)
  );
  if (files.length === 0) return;

  let firstAddedId = null;
  for (const file of files) {
    const id = `local:${file.name}:${file.size}`;
    const existing = allVideos.find((v) => v.id === id);
    if (!existing) {
      const objUrl = URL.createObjectURL(file);
      allVideos.unshift({
        id,
        filename: file.name,
        title: cleanTitle(file.name),
        url: objUrl,
        sizeBytes: file.size,
        sizeFormatted: formatBytes(file.size),
        isLocal: true
      });
    }
    if (!firstAddedId) firstAddedId = id;
  }

  if (firstAddedId) {
    selectVideo(firstAddedId, { autoplay: true });
  }
}

openLocalBtn.addEventListener('click', () => {
  localFileInput.click();
});

localFileInput.addEventListener('change', () => {
  if (localFileInput.files?.length) {
    addLocalFiles(localFileInput.files);
    localFileInput.value = '';
  }
});

window.addEventListener('dragover', (e) => {
  e.preventDefault();
  document.body.classList.add('drag-over');
});

window.addEventListener('dragleave', (e) => {
  if (e.relatedTarget === null) {
    document.body.classList.remove('drag-over');
  }
});

window.addEventListener('drop', (e) => {
  e.preventDefault();
  document.body.classList.remove('drag-over');
  if (e.dataTransfer?.files?.length) {
    addLocalFiles(e.dataTransfer.files);
  }
});

// Keyboard shortcuts
window.addEventListener('keydown', (e) => {
  const tag = document.activeElement?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  switch (e.key.toLowerCase()) {
    case ' ':
    case 'k':
      if (tag === 'VIDEO' || tag === 'BUTTON') return;
      e.preventDefault();
      if (player.paused) player.play();
      else player.pause();
      break;
    case 'j':
      e.preventDefault();
      player.currentTime = Math.max(0, (player.currentTime || 0) - 10);
      break;
    case 'l':
      e.preventDefault();
      player.currentTime = Math.min(player.duration || Infinity, (player.currentTime || 0) + 10);
      break;
    case 'arrowleft':
      if (tag === 'VIDEO') return;
      e.preventDefault();
      player.currentTime = Math.max(0, (player.currentTime || 0) - 5);
      break;
    case 'arrowright':
      if (tag === 'VIDEO') return;
      e.preventDefault();
      player.currentTime = Math.min(player.duration || Infinity, (player.currentTime || 0) + 5);
      break;
    case 'm':
      e.preventDefault();
      player.muted = !player.muted;
      break;
    case 'f':
      e.preventDefault();
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        player.requestFullscreen?.();
      }
      break;
    case 't':
      e.preventDefault();
      toggleTheaterMode();
      break;
    case 'p':
      if (document.pictureInPictureEnabled) {
        e.preventDefault();
        pipBtn.click();
      }
      break;
  }
});

// Initialize
async function init() {
  allVideos = await fetchVideosManifest();

  if (allVideos.length === 0) {
    titleEl.textContent = 'No videos found in videos/';
    renderLibrary();
    return;
  }

  const params = new URLSearchParams(window.location.search);
  const requested = params.get('v');
  const initialVideo =
    allVideos.find((v) => v.filename === requested || v.id === requested) ||
    allVideos[0];

  selectVideo(initialVideo.id, { autoplay: false });
}

init();
