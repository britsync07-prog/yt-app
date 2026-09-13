/* YT Downloader frontend - talks to the FastAPI backend on the same origin. */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------- deploy config + auth ---------------- */

const APP_CONFIG = window.APP_CONFIG || {};
// Base URL of the backend (Render in production, localhost in dev).
// Empty string = same origin (backend serving this page itself).
const API = (APP_CONFIG.API_BASE_URL || "").replace(/\/$/, "");

function configKey() {
  return localStorage.getItem("yt_key") || APP_CONFIG.ACCESS_KEY || "";
}

/** One silent login: exchange the access key for a short-lived JWT. */
async function silentLogin() {
  const key = configKey();
  if (!key) return false;
  try {
    const res = await fetch(API + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (!data.access_token) return false;
    localStorage.setItem("yt_jwt", data.access_token);
    return true;
  } catch (e) {
    return false;
  }
}

/** fetch() with base URL + JWT. On 401: one silent re-login + retry,
 *  then fall back to the discreet key prompt. */
async function authFetch(path, options, _retried) {
  const headers = Object.assign({}, (options && options.headers) || {});
  const token = localStorage.getItem("yt_jwt");
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(API + path, Object.assign({}, options, { headers }));
  if (res.status === 401 && !_retried) {
    if (await silentLogin()) return authFetch(path, options, true);
    showKeyPrompt();
    throw new Error("Session expired. Enter the access key to continue.");
  }
  return res;
}

async function api(path, options) {
  const res = await authFetch(path, options);
  if (!res.ok) {
    let msg = "Request failed (" + res.status + ")";
    try {
      const data = await res.json();
      if (data && data.detail) msg = data.detail;
    } catch (e) { /* keep default message */ }
    throw new Error(msg);
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res;
}

function showKeyPrompt() {
  $("key-prompt").classList.remove("hidden");
}

async function submitKey() {
  const key = $("key-input").value.trim();
  const err = $("key-error");
  hideError(err);
  if (!key) {
    showError(err, "Paste the access key first.");
    return;
  }
  try {
    const res = await fetch(API + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    if (!res.ok) throw new Error("That key didn't work. Try again.");
    const data = await res.json();
    localStorage.setItem("yt_key", key);
    localStorage.setItem("yt_jwt", data.access_token);
    $("key-prompt").classList.add("hidden");
    $("key-input").value = "";
  } catch (e) {
    showError(err, e.message);
  }
}

/* ---------------- helpers ---------------- */

function fmtDuration(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined) return "";
  const m = Math.floor(totalSeconds / 60);
  const s = Math.floor(totalSeconds % 60);
  return m + ":" + String(s).padStart(2, "0");
}

function showError(el, msg) {
  el.textContent = msg;
  el.classList.remove("hidden");
}

function hideError(el) {
  el.textContent = "";
  el.classList.add("hidden");
}

/** Trigger a browser download (file lands in the user's PC downloads). */
function triggerDownload(href, filename) {
  const a = document.createElement("a");
  a.href = href;
  if (filename) a.download = filename;
  else a.setAttribute("download", "");
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function saveBlob(blob, filename) {
  const objectUrl = URL.createObjectURL(blob);
  triggerDownload(objectUrl, filename || "download");
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60 * 1000);
}

/**
 * Download a file path with real % progress.
 * onProgress(pct, receivedBytes) — pct is -1 when size is unknown.
 * Returns {blob, filename} (filename parsed from Content-Disposition).
 */
async function downloadWithProgress(path, onProgress) {
  const res = await authFetch(path);
  if (!res.ok) {
    let msg = "Download failed (" + res.status + ")";
    try {
      const data = await res.json();
      if (data && data.detail) msg = data.detail;
    } catch (e) { /* keep default message */ }
    throw new Error(msg);
  }
  let filename = "";
  const disp = res.headers.get("content-disposition") || "";
  const mStar = disp.match(/filename\*=utf-8''([^;]+)/i);
  const mPlain = disp.match(/filename="?([^";]+)"?/);
  if (mStar) {
    try {
      filename = decodeURIComponent(mStar[1]);
    } catch (e) {
      filename = mStar[1];
    }
  } else if (mPlain) {
    filename = mPlain[1];
  }
  const total = Number(res.headers.get("content-length")) || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const next = await reader.read();
    if (next.done) break;
    chunks.push(next.value);
    received += next.value.length;
    onProgress(total > 0 ? Math.round((received / total) * 100) : -1, received);
  }
  return { blob: new Blob(chunks), filename };
}

function fmtMB(bytes) {
  return (bytes / 1048576).toFixed(1) + " MB";
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- health + tabs ---------------- */

async function checkHealth() {
  const dot = $("health-dot");
  const text = $("health-text");
  try {
    const data = await api("/health");
    if (data && data.status === "ok") {
      dot.className = "dot ok";
      text.textContent = "Server online";
      return;
    }
    throw new Error("bad status");
  } catch (e) {
    dot.className = "dot bad";
    text.textContent = "Server offline";
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("panel-" + tab.dataset.tab).classList.add("active");
  });
});

/* ---------------- single video ---------------- */

let singleVideoUrl = "";

function singleFormat() {
  const checked = document.querySelector('input[name="single-format"]:checked');
  return checked ? checked.value : "video";
}

async function loadSingleVideo() {
  const url = $("video-url").value.trim();
  const err = $("video-error");
  hideError(err);
  $("video-preview").classList.add("hidden");
  if (!url) {
    showError(err, "Please paste a YouTube video URL first.");
    return;
  }
  $("video-loading").classList.remove("hidden");
  $("video-get").disabled = true;
  try {
    const info = await api("/video-info?url=" + encodeURIComponent(url));
    singleVideoUrl = info.url || url;
    $("video-thumb").src = info.thumbnail || "";
    $("video-title").textContent = info.title || "(no title)";
    const parts = [];
    if (info.uploader) parts.push(info.uploader);
    if (info.duration !== null && info.duration !== undefined)
      parts.push(fmtDuration(info.duration));
    $("video-meta").textContent = parts.join(" • ");
    $("video-preview").classList.remove("hidden");
  } catch (e) {
    showError(err, e.message);
  } finally {
    $("video-loading").classList.add("hidden");
    $("video-get").disabled = false;
  }
}

let singleBusy = false;

async function downloadSingle(format) {
  if (!singleVideoUrl || singleBusy) return;
  singleBusy = true;
  const btnVideo = $("video-download-video");
  const btnAudio = $("video-download-audio");
  btnVideo.disabled = true;
  btnAudio.disabled = true;
  const box = $("video-dl");
  const bar = $("video-dl-bar");
  const pct = $("video-dl-pct");
  const label = $("video-dl-label");
  const err = $("video-error");
  hideError(err);
  box.classList.remove("hidden");
  bar.style.width = "0%";
  pct.textContent = "0%";
  label.textContent = format === "audio" ? "Downloading audio…" : "Downloading video…";
  try {
    const result = await downloadWithProgress(
      "/download?url=" + encodeURIComponent(singleVideoUrl) + "&format=" + format,
      (p, received) => {
        if (p >= 0) {
          bar.style.width = p + "%";
          pct.textContent = p + "%";
        } else {
          pct.textContent = fmtMB(received);
        }
      }
    );
    saveBlob(result.blob, result.filename);
    bar.style.width = "100%";
    pct.textContent = "100%";
    label.textContent = "Saved ✓";
    setTimeout(() => box.classList.add("hidden"), 3000);
  } catch (e) {
    showError(err, e.message);
    box.classList.add("hidden");
  } finally {
    btnVideo.disabled = false;
    btnAudio.disabled = false;
    singleBusy = false;
  }
}

$("video-get").addEventListener("click", loadSingleVideo);
$("video-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadSingleVideo();
});
$("video-download-video").addEventListener("click", () => downloadSingle("video"));
$("video-download-audio").addEventListener("click", () => downloadSingle("audio"));
document.querySelectorAll('input[name="single-format"]').forEach((r) =>
  r.addEventListener("change", () => { /* format read at click time */ })
);

/* ---------------- playlist ---------------- */

let playlistVideos = []; // {id, title, thumbnail, url, duration, selected}

function playlistFormat() {
  const checked = document.querySelector('input[name="playlist-format"]:checked');
  return checked ? checked.value : "video";
}

function selectedVideos() {
  return playlistVideos.filter((v) => v.selected);
}

function updateSelectedCount() {
  $("selected-count").textContent =
    selectedVideos().length + " / " + playlistVideos.length + " selected";
  const all = playlistVideos.length > 0 && selectedVideos().length === playlistVideos.length;
  $("select-all").checked = all;
  const anySelected = selectedVideos().length > 0;
  $("playlist-download-selected").disabled = !anySelected;
  $("playlist-download-zip").disabled = !anySelected;
}

function renderGrid() {
  const grid = $("video-grid");
  grid.innerHTML = "";
  playlistVideos.forEach((video, i) => {
    const card = document.createElement("div");
    card.className = "card" + (video.selected ? " selected" : "");

    const check = document.createElement("div");
    check.className = "card-check";
    check.textContent = video.selected ? "✓" : "";

    const img = document.createElement("img");
    img.src = video.thumbnail || "";
    img.alt = "";
    img.loading = "lazy";

    const body = document.createElement("div");
    body.className = "card-body";
    const title = document.createElement("p");
    title.className = "card-title";
    title.textContent = video.title || "(no title)";
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = fmtDuration(video.duration);
    const status = document.createElement("p");
    status.className = "card-status";
    status.id = "card-status-" + i;

    body.appendChild(title);
    body.appendChild(meta);
    body.appendChild(status);
    card.appendChild(check);
    card.appendChild(img);
    card.appendChild(body);
    card.addEventListener("click", () => {
      playlistVideos[i].selected = !playlistVideos[i].selected;
      card.classList.toggle("selected", playlistVideos[i].selected);
      check.textContent = playlistVideos[i].selected ? "✓" : "";
      updateSelectedCount();
    });
    grid.appendChild(card);
  });
  updateSelectedCount();
}

async function loadPlaylist() {
  const url = $("playlist-url").value.trim();
  const err = $("playlist-error");
  hideError(err);
  ["playlist-head", "playlist-toolbar", "multi-hint"].forEach((id) =>
    $(id).classList.add("hidden")
  );
  $("video-grid").innerHTML = "";
  $("zip-panel").classList.add("hidden");
  playlistVideos = [];
  if (!url) {
    showError(err, "Please paste a public playlist URL first.");
    return;
  }
  $("playlist-loading").classList.remove("hidden");
  $("playlist-get").disabled = true;
  try {
    const data = await api("/playlist?url=" + encodeURIComponent(url));
    playlistVideos = (data.videos || []).map((v) => ({
      id: v.id,
      title: v.title,
      thumbnail: v.thumbnail,
      url: v.url,
      duration: v.duration,
      selected: true,
    }));
    if (playlistVideos.length === 0) {
      showError(err, "No downloadable videos found in that playlist.");
      return;
    }
    $("playlist-title").textContent = data.playlist_title || "Playlist";
    const skipped = (data.count || 0) - playlistVideos.length;
    $("playlist-count").textContent =
      playlistVideos.length + " videos" + (skipped > 0 ? " (" + skipped + " unavailable skipped)" : "");
    ["playlist-head", "playlist-toolbar", "multi-hint"].forEach((id) =>
      $(id).classList.remove("hidden")
    );
    renderGrid();
  } catch (e) {
    showError(err, e.message);
  } finally {
    $("playlist-loading").classList.add("hidden");
    $("playlist-get").disabled = false;
  }
}

$("playlist-get").addEventListener("click", loadPlaylist);
$("playlist-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadPlaylist();
});
$("select-all").addEventListener("change", (e) => {
  playlistVideos.forEach((v) => (v.selected = e.target.checked));
  renderGrid();
});

/** Download each selected video as its own file, one after another,
 *  with a per-card status (Queued → Downloading… → ✓ Saved / Failed). */
async function downloadSelected() {
  const videos = selectedVideos();
  if (videos.length === 0) return;
  const format = playlistFormat();
  const btnSel = $("playlist-download-selected");
  const btnZip = $("playlist-download-zip");
  btnSel.disabled = true;
  btnZip.disabled = true;
  const idxs = [];
  playlistVideos.forEach((v, i) => {
    if (v.selected) idxs.push(i);
  });
  const setStatus = (i, text, cls) => {
    const el = document.getElementById("card-status-" + i);
    if (el) {
      el.textContent = text;
      el.className = "card-status " + cls;
    }
  };
  idxs.forEach((i) => setStatus(i, "Queued", "queued"));
  let saved = 0;
  $("selected-count").textContent = "0 / " + idxs.length + " saved";
  for (const i of idxs) {
    const v = playlistVideos[i];
    setStatus(i, "Downloading…", "active");
    try {
      const result = await downloadWithProgress(
        "/download?url=" + encodeURIComponent(v.url) + "&format=" + format,
        () => {}
      );
      saveBlob(result.blob, result.filename);
      saved++;
      setStatus(i, "✓ Saved", "done");
    } catch (e) {
      setStatus(i, "Failed", "failed");
    }
    $("selected-count").textContent = saved + " / " + idxs.length + " saved";
    await sleep(600); // breathing room between files
  }
  updateSelectedCount();
}

/** Pack selected videos into ONE zip via a background job + polling. */
let zipPollTimer = null;
let activeZipJobId = null;

async function downloadZip() {
  const videos = selectedVideos();
  if (videos.length === 0) return;
  const format = playlistFormat();
  const zipErr = $("zip-error");
  hideError(zipErr);
  $("zip-panel").classList.remove("hidden");
  $("zip-bar").style.width = "0%";
  $("zip-label").textContent = "Starting…";
  $("zip-count").textContent = "0 / " + videos.length;
  $("zip-current").textContent = "";
  $("playlist-download-zip").disabled = true;
  $("playlist-download-selected").disabled = true;
  if (zipPollTimer) clearInterval(zipPollTimer);

  let jobId;
  try {
    const created = await api("/zip-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls: videos.map((v) => v.url), format }),
    });
    jobId = created.job_id;
    activeZipJobId = jobId;
  } catch (e) {
    showError(zipErr, e.message);
    updateSelectedCount();
    return;
  }

  zipPollTimer = setInterval(async () => {
    try {
      const job = await api("/zip-jobs/" + jobId);
      const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
      $("zip-bar").style.width = pct + "%";
      $("zip-count").textContent = job.done + " / " + job.total;
      $("zip-current").textContent = job.current || "";
      $("zip-label").textContent =
        job.state === "done" ? "ZIP ready!" : "Packing your ZIP…";

      if (job.state === "done") {
        clearInterval(zipPollTimer);
        zipPollTimer = null;
        activeZipJobId = null;
        const res = await api("/zip-jobs/" + jobId + "/file");
        const blob = await res.blob();
        saveBlob(blob, job.filename || "videos.zip");
        $("zip-label").textContent = "Saved ✓";
        updateSelectedCount();
      } else if (job.state === "error") {
        clearInterval(zipPollTimer);
        zipPollTimer = null;
        activeZipJobId = null;
        showError(zipErr, "Something went wrong: " + (job.error || "unknown error"));
        updateSelectedCount();
      } else if (job.state === "cancelled") {
        clearInterval(zipPollTimer);
        zipPollTimer = null;
        activeZipJobId = null;
        updateSelectedCount();
      }
    } catch (e) {
      clearInterval(zipPollTimer);
      zipPollTimer = null;
      showError(zipErr, e.message);
      updateSelectedCount();
    }
  }, 2000);
}

$("playlist-download-selected").addEventListener("click", downloadSelected);
$("playlist-download-zip").addEventListener("click", downloadZip);

/* If the user leaves mid-ZIP, tell the server to stop that job
 * so it doesn't keep working and eating disk. */
window.addEventListener("pagehide", () => {
  if (activeZipJobId) {
    navigator.sendBeacon(API + "/zip-jobs/" + activeZipJobId + "/cancel");
  }
});

/* ---------------- init ---------------- */
$("key-save").addEventListener("click", submitKey);
$("key-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitKey();
});
silentLogin().catch(() => {});
checkHealth();
