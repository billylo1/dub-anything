#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const videosDir = path.join(__dirname, 'videos');
const outputFile = path.join(videosDir, 'manifest.json');

const VIDEO_EXTS = new Set(['.mp4', '.webm', '.mov', '.m4v', '.ogg', '.mkv']);

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0)} ${sizes[i]}`;
}

function cleanTitle(filename) {
  const base = path.basename(filename, path.extname(filename));
  return base
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\b(Cbc|Ctv|Bbc|Nbc|Abc|Pbs|Tv|Hd|4k|Ai|Uk|Us|Ca)\b/g, (m) => m.toUpperCase());
}

function probeVideo(filePath) {
  try {
    const out = execSync(
      `ffprobe -v quiet -print_format json -show_format -show_streams "${filePath.replace(/"/g, '\\"')}"`,
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }
    );
    const info = JSON.parse(out);
    const durationSec = info.format?.duration ? parseFloat(info.format.duration) : null;
    const videoStream = info.streams?.find((s) => s.codec_type === 'video');
    const width = videoStream?.width || null;
    const height = videoStream?.height || null;
    return { durationSec, width, height };
  } catch {
    return { durationSec: null, width: null, height: null };
  }
}

function formatDuration(sec) {
  if (!sec || Number.isNaN(sec)) return null;
  const total = Math.round(sec);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

const entries = fs.existsSync(videosDir) ? fs.readdirSync(videosDir) : [];
const videos = [];

for (const name of entries) {
  if (name.startsWith('.')) continue;
  const ext = path.extname(name).toLowerCase();
  if (!VIDEO_EXTS.has(ext)) continue;

  const fullPath = path.join(videosDir, name);
  const stat = fs.statSync(fullPath);
  if (!stat.isFile()) continue;

  const { durationSec, width, height } = probeVideo(fullPath);
  videos.push({
    id: name,
    filename: name,
    title: cleanTitle(name),
    url: `videos/${encodeURIComponent(name)}`,
    sizeBytes: stat.size,
    sizeFormatted: formatBytes(stat.size),
    modifiedAt: stat.mtime.toISOString(),
    durationSec,
    durationFormatted: formatDuration(durationSec),
    resolution: width && height ? `${width}x${height}` : null
  });
}

videos.sort((a, b) => a.title.localeCompare(b.title));

const manifest = {
  generatedAt: new Date().toISOString(),
  count: videos.length,
  videos
};

fs.writeFileSync(outputFile, JSON.stringify(manifest, null, 2) + '\n', 'utf8');
console.log(`Wrote ${videos.length} video(s) to ${outputFile}`);
