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
 *   GET /video?id=<youtubeId>       -> {id,title,uploader,duration,thumbnail,url,streams[]}
 *   GET /download?id=<youtubeId>&format=audio|video   -> media bytes (mints + fetches in one invocation, same egress IP => avoids googlevideo IP-lock 403)
 *   GET /stream?url=<encoded>       -> relay bytes for an already-minted googlevideo URL
 *   GET /playlist?id=<playlistId>   -> {playlist_title,count,videos[]}
 */
const INNERTUBE_KEY = 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8';

const CLIENTS = [
  {
    name: 'ANDROID',
    context: {
      clientName: 'ANDROID',
      clientVersion: '21.26.364',
      androidSdkVersion: 30,
      osName: 'Android',
      osVersion: '11',
      hl: 'en',
      gl: 'US',
    },
    ua: 'com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip',
  },
  {
    name: 'WEB',
    context: {
      clientName: 'WEB',
      clientVersion: '2.20260101.00.00',
      hl: 'en',
      gl: 'US',
    },
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  },
  {
    name: 'IOS',
    context: {
      clientName: 'IOS',
      clientVersion: '21.26.3',
      deviceModel: 'iPhone14,3',
      hl: 'en',
      gl: 'US',
    },
    ua: 'com.google.ios.youtube/21.26.3 (iPhone14,3; U; CPU iOS 17_0 like Mac OS X)',
  },
  {
    name: 'MWEB',
    context: {
      clientName: 'MWEB',
      clientVersion: '2.20260101.00.00',
      hl: 'en',
      gl: 'US',
    },
    ua: 'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
  },
];

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
      headers: { 'Content-Type': 'application/json', 'User-Agent': ua },
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

/**
 * Fetch video info via player endpoint, trying each client until one works.
 * YouTube challenges certain clients per-IP, so fallback is critical.
 * When the backend forwards a content-bound PO token (+visitor data), it is
 * injected into every innertube request via serviceIntegrityDimensions.
 */
async function videoInfo(id, poToken, visitorData) {
  let lastErr = null;
  const attempts = [...CLIENTS, CLIENTS[0]];
  for (let i = 0; i < attempts.length; i++) {
    const c = attempts[i];
    if (i === CLIENTS.length) {
      // Second pass on ANDROID — brief pause to absorb transient challenge.
      await new Promise((r) => setTimeout(r, 1500));
    }
    try {
      const body = { videoId: id, context: { client: c.context } };
      if (poToken) {
        body.context.client.visitorData = visitorData || '';
        body.serviceIntegrityDimensions = { poToken };
      }
      console.log(`[yt-app] videoInfo: ${c.name} poToken=${poToken ? 'yes' : 'no'}`);
      const data = await innertube('player', body, c.ua);
      const status = data.playabilityStatus || {};
      if (status.status && status.status !== 'OK') {
        lastErr = `${c.name}: ${status.reason || status.status}`;
        console.log(`[yt-app] videoInfo: ${c.name} -> ${lastErr}`);
        continue;
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
    } catch (e) {
      lastErr = `${c.name}: ${e.message || e}`;
    }
  }
  throw new Error(`All player clients failed (${attempts.length} attempts): ${lastErr}`);
}

/**
 * Pick the best stream for a requested format.
 * - 'audio': prefer audio-only adaptive stream (largest bytes = best bitrate);
 *            fall back to a progressive mp4 (audio track usable by backend).
 * - 'video': prefer highest-quality progressive mp4, then largest non-audio.
 */
function pickStream(streams, format) {
  if (!streams || !streams.length) return null;
  const isMp4 = (s) => /video\/mp4|; codecs="avc/.test(s.mime || '');
  const isMp4Audio = (s) => (s.mime || '').startsWith('audio/mp4');
  const qualityP = (s) => {
    const m = /(\d+)p/.exec(s.quality || '');
    return m ? +m[1] : 0;
  };
  if (format === 'audio') {
    const auds = streams
      .filter((s) => (s.mime || '').startsWith('audio/'))
      .sort((a, b) => (b.size || 0) - (a.size || 0));
    if (auds.length) return auds[0];
    // No audio-only stream -> fall back to lowest-bitrate progressive mp4.
    const prog = streams.filter(isMp4).sort((a, b) => (a.size || 0) - (b.size || 0));
    if (prog.length) return prog[0];
    return null;
  }
  const prog = streams
    .filter((s) => isMp4(s) || /video\/(mp4|webm)/.test(s.mime || ''))
    .sort((a, b) => qualityP(b) - qualityP(a) || (b.size || 0) - (a.size || 0));
  if (prog.length) return prog[0];
  const any = streams
    .filter((s) => !(s.mime || '').startsWith('audio/'))
    .sort((a, b) => qualityP(b) - qualityP(a) || (b.size || 0) - (a.size || 0));
  return any[0] || (isMp4Audio(streams[0]) ? streams[0] : null);
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
  const ctx = { client: CLIENTS[1].context }; // WEB
  let data = await innertube('browse', { browseId: 'VL' + id, context: ctx }, CLIENTS[1].ua);
  let title = '';
  const videos = [];
  for (let page = 0; page < 10; page++) {
    const parsed = parsePlaylistPage(data, page === 0);
    if (page === 0) title = parsed.title;
    videos.push(...parsed.videos);
    if (!parsed.continuation) break;
    data = await innertube('browse', { continuation: parsed.continuation, context: ctx }, CLIENTS[1].ua);
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
        const poToken = url.searchParams.get('po_token') || '';
        const visitorData = url.searchParams.get('visitor_data') || '';
        return json(await videoInfo(id, poToken, visitorData));
      }
      if (url.pathname === '/playlist') {
        const id = url.searchParams.get('id') || '';
        if (!id) return json({ detail: 'Missing ?id=' }, 400);
        return json(await playlistInfo(id));
      }
      if (url.pathname === '/stream') {
        const target = url.searchParams.get('url') || '';
        if (!target) return json({ detail: 'Missing ?url=' }, 400);
        // Stream a googlevideo media URL. The stream URLs from our /video
        // endpoint are IP-locked to Cloudflare, so the backend cannot fetch
        // them directly - only this Worker (same egress IP) can. We relay
        // the raw bytes back with a streaming response.
        const mediaRes = await fetch(target, {
          headers: {
            'User-Agent':
              'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            Referer: 'https://www.youtube.com/',
          },
        });
        if (!mediaRes.ok && !mediaRes.body) {
          return json({ detail: `media fetch failed: ${mediaRes.status}` }, 502);
        }
        return new Response(mediaRes.body, {
          status: mediaRes.status,
          headers: {
            'Content-Type': mediaRes.headers.get('Content-Type') || 'application/octet-stream',
            'Content-Length': mediaRes.headers.get('Content-Length') || '',
            ...CORS,
          },
        });
      }
      if (url.pathname === '/download') {
        // ONE combined invocation: mint the playable stream list AND fetch the
        // chosen media bytes from the SAME Cloudflare egress IP. googlevideo
        // stream URLs are signed to the extracting IP, so a separate /stream
        // request (different colo/IP) gets 403. Doing both here sidesteps that.
        const id = url.searchParams.get('id') || '';
        const format = url.searchParams.get('format') || 'video';
        if (!id) return json({ detail: 'Missing ?id=' }, 400);
        const poToken = url.searchParams.get('po_token') || '';
        const visitorData = url.searchParams.get('visitor_data') || '';
        console.log(`[yt-app] /download id=${id} format=${format} po=${poToken ? 'yes' : 'no'}`);
        const info = await videoInfo(id, poToken, visitorData);
        const picked = pickStream(info.streams || [], format);
        if (!picked) {
          return json({ detail: `No playable stream for format=${format}` }, 502);
        }
        const mediaRes = await fetch(picked.url, {
          headers: {
            'User-Agent':
              'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            Referer: 'https://www.youtube.com/',
            ...(request.headers.get('Range')
              ? { Range: request.headers.get('Range') }
              : {}),
          },
        });
        if (!mediaRes.ok && !mediaRes.body) {
          return json({ detail: `media fetch failed: ${mediaRes.status} (${picked.mime})` }, 502);
        }
        const safeTitle = (info.title || 'video').replace(/[^\w\- ]+/g, '').trim() || 'video';
        const ext = format === 'audio'
          ? (picked.mime || '').startsWith('audio/mp4') ? 'm4a' : (picked.mime || '').includes('webm') ? 'webm' : 'mp4'
          : (picked.mime || '').includes('webm') ? 'webm' : 'mp4';
        return new Response(mediaRes.body, {
          status: mediaRes.status,
          headers: {
            'Content-Type': mediaRes.headers.get('Content-Type') || 'application/octet-stream',
            'Content-Length': mediaRes.headers.get('Content-Length') || '',
            'Content-Disposition': format === 'audio'
              ? `attachment; filename="${safeTitle}.${ext}"`
              : `attachment; filename="${safeTitle}.${ext}"`,
            'Accept-Ranges': 'bytes',
            ...CORS,
          },
        });
      }
      return json({ detail: 'Unknown route.' }, 404);
    } catch (e) {
      return json({ detail: String((e && e.message) || e) }, 502);
    }
  },
};
