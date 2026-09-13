/* YT extraction relay for Cloudflare Workers (no build step, paste & deploy).
 *
 * Why: YouTube bot-checks datacenter IPs (Render). Cloudflare egress IPs
 * pass fine, so this Worker fetches YouTube's Innertube API and hands our
 * backend clean metadata. The backend uses it ONLY when yt-dlp is
 * challenged - normal path is untouched.
 *
 * Deploy (dashboard, ~3 min, no CLI):
 *   1. Workers & Pages -> Create -> Worker -> deploy the starter -> Edit code
 *   2. Paste this whole file -> Save and deploy
 *   3. Settings -> Variables -> add secret  WORKER_KEY = <same value you
 *      will put in backend WORKER_KEY env>
 *   4. Copy the worker URL, e.g. https://yt-relay.<you>.workers.dev
 *
 * Routes (all require ?key=<WORKER_KEY>):
 *   GET /health
 *   GET /video?id=<youtubeId>       -> {id,title,uploader,duration,thumbnail,url}
 *   GET /playlist?id=<playlistId>   -> {playlist_title,count,videos[]}
 */
const INNERTUBE_KEY = 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8';
const ANDROID_CLIENT = {
  clientName: 'ANDROID',
  clientVersion: '21.26.364',
  androidSdkVersion: 30,
  osName: 'Android',
  osVersion: '11',
  hl: 'en',
  gl: 'US',
};
const ANDROID_UA = 'com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip';
const WEB_CLIENT = {
  clientName: 'WEB',
  clientVersion: '2.20260101.00.00',
  hl: 'en',
  gl: 'US',
};
const WEB_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  });
}

async function innertube(endpoint, body, ua) {
  const res = await fetch(
    `https://www.youtube.com/youtubei/v1/${endpoint}?key=${INNERTUBE_KEY}&prettyPrint=false`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': ua,
      },
      body: JSON.stringify(body),
    }
  );
  if (!res.ok) throw new Error(`innertube ${endpoint} -> ${res.status}`);
  return res.json();
}

function bestThumb(thumbs) {
  if (!thumbs || !thumbs.length) return null;
  return thumbs[thumbs.length - 1].url;
}

function parseDuration(t) {
  if (!t) return null;
  const parts = String(t).trim().split(':').map(Number);
  if (!parts.length || parts.some(isNaN)) return null;
  return parts.reduce((a, b) => a * 60 + b, 0);
}

/** Duration badge inside the new lockupViewModel thumbnail ("3:51"). */
function lockupDuration(lk) {
  const overlays = (((lk.contentImage || {}).thumbnailViewModel || {}).overlays) || [];
  for (const o of overlays) {
    const badges = (o.thumbnailBottomOverlayViewModel || {}).badges || [];
    for (const b of badges) {
      const t = b.thumbnailBadgeViewModel && b.thumbnailBadgeViewModel.text;
      if (t && /^\d+:\d{2}(?::\d{2})?$/.test(t)) return parseDuration(t);
    }
  }
  return null;
}

function lockupThumb(lk) {
  return bestThumb((((lk.contentImage || {}).thumbnailViewModel || {}).image || {}).sources);
}

/** All itemSectionRenderer contents across every section (layout-proof). */
function sectionItems(tab) {
  const out = [];
  const secs = (((tab.content || {}).sectionListRenderer || {}).contents) || [];
  for (const s of secs) {
    out.push(...(((s.itemSectionRenderer || {}).contents) || []));
  }
  return out;
}

function headerTitle(data) {
  const h = data.header || {};
  if (h.pageHeaderRenderer) {
    if (h.pageHeaderRenderer.pageTitle) return h.pageHeaderRenderer.pageTitle;
    try {
      return h.pageHeaderRenderer.content.pageHeaderViewModel.title.dynamicTextViewModel.text.content;
    } catch (e) { /* fall through */ }
  }
  const t = h.playlistHeaderRenderer && h.playlistHeaderRenderer.title;
  if (t) return t.simpleText || ((t.runs || []).map((r) => r.text).join(''));
  return '';
}

async function videoInfo(id) {
  const data = await innertube(
    'player',
    { videoId: id, context: { client: ANDROID_CLIENT } },
    ANDROID_UA
  );
  const status = data.playabilityStatus || {};
  if (status.status && status.status !== 'OK') {
    throw new Error(status.reason || `video not playable (${status.status})`);
  }
  const vd = data.videoDetails || {};
  const sd = data.streamingData || {};
  const streams = [...(sd.formats || []), ...(sd.adaptiveFormats || [])]
    .filter((s) => s.url)
    .map((s) => ({
      url: s.url,
      mime: (s.mimeType || '').split(';')[0],
      quality: s.qualityLabel || s.quality || '',
      size: s.contentLength ? +s.contentLength : null,
    }));
  return {
    id: vd.videoId || id,
    title: vd.title || '',
    uploader: vd.author || '',
    duration: vd.lengthSeconds ? +vd.lengthSeconds : null,
    thumbnail: bestThumb(vd.thumbnail && vd.thumbnail.thumbnails),
    url: `https://www.youtube.com/watch?v=${id}`,
    streams,
  };
}

function parsePlaylistPage(data, first) {
  let items;
  let title = '';
  if (first) {
    const tabs = data.contents.twoColumnBrowseResultsRenderer.tabs;
    const tab = (tabs.find((t) => t.tabRenderer && t.tabRenderer.selected) || tabs[0]).tabRenderer;
    items = sectionItems(tab);
    title = headerTitle(data);
  } else {
    items = data.onResponseReceivedActions[0].appendContinuationItemsAction.continuationItems;
  }
  const videos = [];
  let continuation = null;
  for (const c of items || []) {
    if (c.lockupViewModel && c.lockupViewModel.contentType === 'LOCKUP_CONTENT_TYPE_VIDEO') {
      // New YouTube layout.
      const lk = c.lockupViewModel;
      const md = (lk.metadata && lk.metadata.lockupMetadataViewModel) || {};
      const vid = lk.contentId || '';
      if (!vid) continue;
      videos.push({
        id: vid,
        title: (md.title && md.title.content) || '',
        thumbnail: lockupThumb(lk),
        url: `https://www.youtube.com/watch?v=${vid}`,
        duration: lockupDuration(lk),
      });
    } else if (c.playlistVideoRenderer) {
      // Classic layout.
      const p = c.playlistVideoRenderer;
      videos.push({
        id: p.videoId,
        title: ((p.title && p.title.runs) || []).map((r) => r.text).join('') || (p.title && p.title.simpleText) || '',
        thumbnail: bestThumb(p.thumbnail && p.thumbnail.thumbnails),
        url: `https://www.youtube.com/watch?v=${p.videoId}`,
        duration: p.lengthSeconds ? +p.lengthSeconds : null,
      });
    } else if (c.continuationItemRenderer) {
      continuation =
        c.continuationItemRenderer.continuationEndpoint.continuationCommand.token;
    }
  }
  return { title, videos, continuation };
}

async function playlistInfo(id) {
  const ctx = { client: WEB_CLIENT };
  let data = await innertube('browse', { browseId: 'VL' + id, context: ctx }, WEB_UA);
  let title = '';
  const videos = [];
  for (let page = 0; page < 10; page++) {
    const parsed = parsePlaylistPage(data, page === 0);
    if (page === 0) title = parsed.title;
    videos.push(...parsed.videos);
    if (!parsed.continuation) break;
    data = await innertube('browse', { continuation: parsed.continuation, context: ctx }, WEB_UA);
  }
  return {
    playlist_title: title,
    count: videos.length,
    videos: videos.map((v, i) => ({ index: i + 1, ...v })),
  };
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    if (url.searchParams.get('key') !== (env.WORKER_KEY || '')) {
      return json({ detail: 'Not authenticated.' }, 401);
    }
    try {
      if (url.pathname === '/health') return json({ status: 'ok' });
      if (url.pathname === '/video') {
        const id = url.searchParams.get('id') || '';
        if (!id) return json({ detail: 'Missing ?id=' }, 400);
        return json(await videoInfo(id));
      }
      if (url.pathname === '/playlist') {
        const id = url.searchParams.get('id') || '';
        if (!id) return json({ detail: 'Missing ?id=' }, 400);
        return json(await playlistInfo(id));
      }
      return json({ detail: 'Unknown route.' }, 404);
    } catch (e) {
      return json({ detail: String((e && e.message) || e) }, 502);
    }
  },
};
