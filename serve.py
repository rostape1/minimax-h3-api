"""Tiny local web UI for run_job.py.

Run:  python serve.py   →  open http://localhost:8000

Jobs are serialised through a queue — each job starts only after the previous
one finishes, keeping the RunPod worker warm between generations.
"""

import base64
import csv
import datetime
import http.server
import json
import os
import queue as _queue
import signal
import socketserver
import subprocess
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

import run_job

PORT = 8000
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "generated")
os.makedirs(OUTPUT_DIR, exist_ok=True)

PROMPTS_CSV = os.path.join(os.path.dirname(__file__), "prompts.csv")
PROMPTS_LOCK = threading.Lock()


def log_prompt(image_name, prompt, duration):
    with PROMPTS_LOCK:
        new_file = not os.path.exists(PROMPTS_CSV)
        with open(PROMPTS_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["timestamp", "image_name", "prompt", "duration"])
            w.writerow([datetime.datetime.now().isoformat(timespec="seconds"), image_name, prompt, duration])


def read_past_prompts():
    if not os.path.exists(PROMPTS_CSV):
        return []
    with PROMPTS_LOCK:
        with open(PROMPTS_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    rows.reverse()  # most recent first
    seen, result = set(), []
    for row in rows:
        p = row.get("prompt", "").strip()
        if p and p not in seen:
            seen.add(p)
            result.append({
                "prompt": p,
                "image_name": row.get("image_name", ""),
                "timestamp": row.get("timestamp", ""),
            })
    return result

# job_id -> {status, video_path, error, prompt_preview, duration}
JOBS = {}
JOBS_LOCK = threading.Lock()

# Ordered list of all submitted job_ids (for /queue_list)
QUEUE_ORDER = []
QUEUE_ORDER_LOCK = threading.Lock()

# Single-consumer queue — guarantees serial execution
JOB_QUEUE = _queue.Queue()


HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>MiniMax H3 &mdash; I2V</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  [hidden] { display: none !important; }
  .hidden { display: none !important; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif;
    background: #0a0a0a;
    color: #e2e2e2;
    max-width: 680px;
    margin: 0 auto;
    padding: 3em 1.5em 5em;
    min-height: 100vh;
  }

  h1 {
    font-size: 1.05em;
    font-weight: 500;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: #555;
    margin: 0 0 2.5em;
  }
  h2 {
    margin: 0;
    font-size: .75em;
    font-weight: 500;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: #555;
  }
  label {
    display: block;
    margin: 1.4em 0 .4em;
    font-size: .78em;
    font-weight: 500;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #666;
  }

  textarea, input[type=file], input[type=number] {
    width: 100%;
    background: #111;
    border: 1px solid #222;
    border-radius: 8px;
    color: #ddd;
    font-family: inherit;
    font-size: .95em;
    padding: .75em 1em;
    transition: border-color .15s, box-shadow .15s;
    outline: none;
  }
  textarea {
    min-height: 7em;
    resize: vertical;
    line-height: 1.55;
  }
  textarea:focus, input[type=file]:focus, input[type=number]:focus {
    border-color: #7c6ff7;
    box-shadow: 0 0 0 3px rgba(124,111,247,.12);
  }
  textarea::placeholder { color: #3a3a3a; }
  input[type=number] { max-width: 140px; }

  .file-wrap {
    position: relative;
    display: flex;
    align-items: center;
    gap: .75em;
    background: #111;
    border: 1px solid #222;
    border-radius: 8px;
    padding: .65em 1em;
    cursor: pointer;
    transition: border-color .15s;
  }
  .file-wrap:hover { border-color: #333; }
  .file-wrap input[type=file] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer;
    padding: 0; border: none; background: none;
  }
  .file-icon { font-size: 1.1em; flex-shrink: 0; }
  .file-label { font-size: .88em; color: #555; }
  .file-label.has-file { color: #aaa; }

  #preview, #q-preview {
    margin-top: .7em;
    border-radius: 8px;
    max-width: 240px;
    display: block;
    border: 1px solid #1e1e1e;
  }

  .btn {
    display: inline-flex;
    align-items: center;
    gap: .5em;
    padding: .75em 1.5em;
    font-size: .9em;
    font-weight: 500;
    letter-spacing: .02em;
    background: linear-gradient(135deg, #7c6ff7, #5b50e0);
    color: #fff;
    border: 0;
    border-radius: 8px;
    cursor: pointer;
    transition: opacity .15s, transform .1s, box-shadow .15s;
    box-shadow: 0 2px 12px rgba(124,111,247,.25);
  }
  .btn:hover { opacity: .9; box-shadow: 0 4px 20px rgba(124,111,247,.35); }
  .btn:active { transform: scale(.98); }
  .btn:disabled { background: #1e1e1e; color: #444; box-shadow: none; cursor: not-allowed; }
  .btn-sm { padding: .5em 1em; font-size: .82em; }
  .btn-ghost {
    background: transparent;
    border: 1px solid #2a2a2a;
    color: #555;
    box-shadow: none;
  }
  .btn-ghost:hover { border-color: #444; color: #888; box-shadow: none; }

  #status {
    margin-top: 1.5em;
    padding: .9em 1.1em;
    background: #111;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    white-space: pre-wrap;
    font-family: ui-monospace, "SF Mono", "Fira Code", monospace;
    font-size: .82em;
    color: #666;
    line-height: 1.5;
  }
  #result video {
    width: 100%;
    margin-top: 1.4em;
    border-radius: 10px;
    border: 1px solid #1e1e1e;
  }

  .queue-section {
    margin-top: 3em;
    border-top: 1px solid #161616;
    padding-top: 2em;
  }
  .queue-header {
    display: flex;
    align-items: center;
    gap: .9em;
    margin-bottom: 1.2em;
  }
  .add-btn {
    width: 24px; height: 24px;
    font-size: 1em; line-height: 1; padding: 0;
    background: #161616;
    border: 1px solid #2a2a2a;
    color: #555;
    border-radius: 6px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: border-color .15s, color .15s;
  }
  .add-btn:hover { border-color: #7c6ff7; color: #7c6ff7; }

  .add-panel {
    background: #0e0e0e;
    border: 1px solid #1e1e1e;
    border-radius: 10px;
    padding: 1.3em 1.4em 1.4em;
    margin-bottom: 1.2em;
  }
  .add-panel label { margin-top: 1em; }
  .add-panel label:first-child { margin-top: 0; }
  .add-panel textarea { min-height: 4em; }
  .add-panel .form-actions { display: flex; gap: .6em; margin-top: 1.2em; }

  .queue-empty { color: #333; font-size: .85em; }

  .queue-item {
    display: flex;
    align-items: center;
    gap: .7em;
    padding: .7em 1em;
    border-radius: 8px;
    border-left: 2px solid #222;
    background: #0e0e0e;
    margin-bottom: .4em;
    font-size: .85em;
    transition: background .15s;
  }
  .queue-item.s-pending { border-left-color: #2a2a2a; }
  .queue-item.s-running { border-left-color: #7c6ff7; background: #100f1a; }
  .queue-item.s-done    { border-left-color: #22c55e; background: #0a110e; }
  .queue-item.s-error   { border-left-color: #ef4444; background: #130a0a; }

  .badge {
    font-size: .7em;
    padding: .2em .6em;
    border-radius: 4px;
    font-weight: 500;
    letter-spacing: .04em;
    white-space: nowrap;
    background: #161616;
    color: #444;
    border: 1px solid #222;
  }
  .badge.running { background: #1a1730; color: #7c6ff7; border-color: #2e2860; }
  .badge.done    { background: #0d1f14; color: #22c55e; border-color: #163324; }
  .badge.error   { background: #1f0d0d; color: #ef4444; border-color: #3a1515; }

  .badge.running::before {
    content: '';
    display: inline-block;
    width: 5px; height: 5px;
    background: #7c6ff7;
    border-radius: 50%;
    margin-right: .4em;
    animation: pulse 1.2s ease-in-out infinite;
    vertical-align: middle;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: .3; }
  }

  .q-num { color: #2a2a2a; font-size: .78em; min-width: 1.4em; }
  .q-prompt { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #555; }
  .q-duration {
    font-size: .7em; color: #333;
    border: 1px solid #222; border-radius: 4px;
    padding: .1em .5em; white-space: nowrap;
  }
  .q-play {
    font-size: .82em; color: #7c6ff7;
    text-decoration: none; white-space: nowrap;
    padding: .2em .6em;
    border: 1px solid #2e2860;
    border-radius: 4px;
    transition: background .15s;
  }
  .q-play:hover { background: #1a1730; }
  .q-err {
    font-size: .75em; color: #ef4444;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px;
  }

  .history-panel {
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    background: #0d0d0d;
    margin-top: .45em;
    overflow: hidden;
  }
  #history-list {
    max-height: 210px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: #222 transparent;
  }
  .history-item {
    display: flex;
    align-items: center;
    gap: .6em;
    padding: .52em .85em;
    border-bottom: 1px solid #161616;
  }
  .history-item:last-child { border-bottom: none; }
  .history-item:hover { background: #111; }
  .history-text {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: .82em;
    color: #555;
  }
  .history-use {
    background: none;
    border: 1px solid #2a2a2a;
    color: #7c6ff7;
    border-radius: 4px;
    padding: .2em .55em;
    font-size: .82em;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
    transition: background .15s, border-color .15s;
    line-height: 1.4;
  }
  .history-use:hover { background: #1a1730; border-color: #7c6ff7; }
  .history-toggle {
    background: none;
    border: 1px solid #222;
    color: #444;
    border-radius: 5px;
    padding: .18em .55em;
    font-size: .72em;
    cursor: pointer;
    transition: color .15s, border-color .15s;
    line-height: 1.4;
  }
  .history-toggle:hover { color: #777; border-color: #333; }

  .divider { border: none; border-top: 1px solid #161616; margin: 1.8em 0; }

  .staged-item {
    display: flex;
    align-items: center;
    gap: .7em;
    padding: .65em 1em;
    border-radius: 8px;
    border-left: 2px solid #2a2a2a;
    background: #0e0e0e;
    margin-bottom: .4em;
    font-size: .85em;
  }
  .staged-thumb {
    width: 32px; height: 32px;
    border-radius: 4px;
    object-fit: cover;
    border: 1px solid #222;
    flex-shrink: 0;
  }
  .staged-prompt { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #555; }
  .staged-remove {
    background: none; border: none; color: #333; font-size: 1.1em;
    cursor: pointer; padding: 0 .2em; line-height: 1;
    transition: color .15s;
  }
  .staged-remove:hover { color: #ef4444; }
</style>
</head>
<body>
  <h1>MiniMax H3 &mdash; Image to Video</h1>

  <form id="form">
    <div style="display:flex;align-items:center;justify-content:space-between;margin:1.4em 0 .4em">
      <label style="margin:0">Prompt</label>
      <button type="button" id="history-toggle" class="history-toggle">History &#x25BE;</button>
    </div>
    <textarea id="prompt" placeholder='describe the shots, motion, and accompanying audio (dialogue, SFX, music)' required></textarea>
    <div id="history-panel" class="history-panel" style="display:none">
      <input type="text" id="history-search" placeholder="Search prompt history…" style="width:100%;background:#0d0d0d;border:none;border-bottom:1px solid #1e1e1e;color:#ddd;font-size:.82em;padding:.55em .85em;outline:none;">
      <div id="history-list"></div>
    </div>

    <label>Input image</label>
    <div class="file-wrap" id="file-wrap">
      <input type="file" id="image" accept="image/png,image/jpeg" required>
      <span class="file-icon">&#128247;</span>
      <span class="file-label" id="file-label">Choose image&hellip;</span>
    </div>
    <img id="preview" hidden>

    <label>Duration (seconds, up to ~15)</label>
    <input type="number" id="duration" value="5" min="1" max="15" step="0.5">

    <button type="submit" id="submit" class="btn" style="margin-top:1.8em">
      &#9654;&nbsp; Generate
    </button>
  </form>

  <div id="status" hidden></div>

  <!-- ── Queue ── -->
  <div class="queue-section">
    <div class="queue-header">
      <h2>Queue</h2>
      <button class="add-btn" id="add-btn" type="button" title="Add item" onclick="var p=document.getElementById('add-panel');p.style.display=p.style.display==='block'?'none':'block'">+</button>
    </div>

    <div class="add-panel" id="add-panel" style="display:none">
      <form id="add-form">
        <label>Prompt</label>
        <textarea id="q-prompt" placeholder="Prompt for next generation" required></textarea>

        <label>Input image</label>
        <div class="file-wrap" id="q-file-wrap">
          <input type="file" id="q-image" accept="image/png,image/jpeg" required>
          <span class="file-icon">&#128247;</span>
          <span class="file-label" id="q-file-label">Choose image&hellip;</span>
        </div>
        <img id="q-preview" hidden>

        <label>Duration (seconds, up to ~15)</label>
        <input type="number" id="q-duration" value="5" min="1" max="15" step="0.5">

        <div class="form-actions">
          <button type="submit" id="add-submit" class="btn btn-sm">Stage item</button>
          <button type="button" id="cancel-btn" class="btn btn-sm btn-ghost">Cancel</button>
        </div>
      </form>
    </div>

    <!-- staged (not yet sent) -->
    <div id="staged-list"></div>

    <!-- server queue (submitted jobs) -->
    <div id="queue-list"></div>
  </div>

<script>
const $ = id => document.getElementById(id);
const STAGED_KEY = 'staged_v1';

const esc = s => (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const readB64 = file => new Promise((res, rej) => {
  const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.onerror = rej; r.readAsDataURL(file);
});

// ── Image previews ──
$("image").addEventListener("change", e => {
  const f = e.target.files[0]; if (!f) return;
  $("file-label").textContent = f.name; $("file-label").classList.add("has-file");
  $("preview").src = URL.createObjectURL(f); $("preview").hidden = false;
});
$("q-image").addEventListener("change", e => {
  const f = e.target.files[0]; if (!f) return;
  $("q-file-label").textContent = f.name; $("q-file-label").classList.add("has-file");
  $("q-preview").src = URL.createObjectURL(f); $("q-preview").hidden = false;
});

$("cancel-btn").addEventListener("click", () => {
  $("add-panel").style.display = "none";
  $("add-form").reset();
  $("q-preview").hidden = true;
  $("q-file-label").textContent = "Choose image…";
  $("q-file-label").classList.remove("has-file");
});

// ── Staged queue (persisted in localStorage) ──
let staged = [];
try { staged = JSON.parse(localStorage.getItem(STAGED_KEY) || '[]'); } catch { staged = []; }
const saveStaged = () => localStorage.setItem(STAGED_KEY, JSON.stringify(staged));

function renderStaged() {
  const el = $("staged-list");
  if (!staged.length) { el.innerHTML = ''; return; }
  el.innerHTML = staged.map((item, i) => `
    <div class="staged-item">
      <img class="staged-thumb" src="data:image/png;base64,${item.b64}">
      <span class="staged-prompt" title="${esc(item.prompt)}">${esc(item.prompt)}</span>
      <span class="q-duration">${item.duration}s</span>
      <span class="badge">staged</span>
      <button class="staged-remove" onclick="removeStaged(${i})" title="Remove">&#x2715;</button>
    </div>`).join('');
}
function removeStaged(i) { staged.splice(i, 1); saveStaged(); renderStaged(); }

function stageItem(prompt, b64, name, duration) {
  staged.push({ prompt, b64, name, duration });
  saveStaged(); renderStaged();
  pollQueue();  // try to drain immediately
}

// ── Add-panel form: stage only ──
$("add-form").addEventListener("submit", async e => {
  e.preventDefault();
  const prompt = $("q-prompt").value.trim();
  const file = $("q-image").files[0];
  if (!prompt || !file) return;
  $("add-submit").disabled = true; $("add-submit").textContent = "Reading…";
  const b64 = await readB64(file);
  stageItem(prompt, b64, file.name, parseFloat($("q-duration").value) || 5);
  $("add-panel").style.display = "none"; $("add-form").reset();
  $("q-preview").hidden = true; $("q-file-label").textContent = "Choose image…";
  $("q-file-label").classList.remove("has-file");
  $("add-submit").disabled = false; $("add-submit").textContent = "Stage item";
});

// ── Main form: also just stages → drain happens via poller ──
$("form").addEventListener("submit", async e => {
  e.preventDefault();
  const prompt = $("prompt").value.trim();
  const file = $("image").files[0];
  if (!prompt || !file) return;
  $("submit").disabled = true;
  try {
    const b64 = await readB64(file);
    stageItem(prompt, b64, file.name, parseFloat($("duration").value) || 5);
    $("form").reset();
    $("preview").hidden = true;
    $("file-label").textContent = "Choose image…";
    $("file-label").classList.remove("has-file");
  } finally {
    $("submit").disabled = false;
  }
});

// ── Server queue rendering + drain ──
let draining = false;

function renderQueue(jobs) {
  currentJobs = jobs;
  const el = $("queue-list");
  if (!jobs.length) { el.innerHTML = '<div class="queue-empty">No submitted jobs.</div>'; return; }
  el.innerHTML = jobs.map((j, i) => {
    const sClass = `s-${j.status}`;
    const action = j.status === 'done'
      ? `<a class="q-play" href="/video?job_id=${j.job_id}" target="_blank">Play</a>
         <a class="q-play" href="/last_frame?job_id=${j.job_id}" download>Frame</a>`
      : j.status === 'error'
        ? `<span class="q-err" title="${esc(j.error)}">${esc(j.error || 'error')}</span>`
        : '';
    return `<div class="queue-item ${sClass}">
      <span class="q-num">${i + 1}</span>
      <span class="q-prompt" title="${esc(j.prompt_preview)}">${esc(j.prompt_preview)}</span>
      <span class="q-duration">${j.duration}s</span>
      <span class="badge ${j.status}">${j.status}</span>
      ${action}
      <button type="button" class="history-use" title="Use this prompt" onclick="useQueuePrompt(${i})">Use prompt</button>
    </div>`;
  }).join('');
}

async function drainNext() {
  if (draining || !staged.length) return;
  draining = true;
  try {
    while (staged.length) {
      const item = staged[0];
      try {
        const r = await fetch('/generate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: item.prompt, image_b64: item.b64, image_name: item.name, duration: item.duration }),
        });
        if (!r.ok) {
          $("status").hidden = false;
          $("status").textContent = `Submit failed (HTTP ${r.status}) — staged items kept.`;
          break;
        }
        staged.shift(); saveStaged(); renderStaged();
      } catch (err) {
        $("status").hidden = false;
        $("status").textContent = `Submit error: ${err.message} — staged items kept.`;
        break;
      }
    }
    pollQueue();
  } finally {
    draining = false;
  }
}

async function pollQueue() {
  try {
    const { jobs } = await fetch('/queue_list').then(r => r.json());
    renderQueue(jobs);
    if (staged.length) drainNext();
  } catch {}
}

renderStaged();
pollQueue();
setInterval(pollQueue, 5000);

// ── Prompt history ──
let pastPrompts = [];
let currentJobs = [];

function renderHistory() {
  const el = $('history-list');
  const query = $('history-search').value.trim().toLowerCase();
  const filtered = query
    ? pastPrompts.filter(p => p.prompt.toLowerCase().includes(query))
    : pastPrompts;
  if (!filtered.length) {
    el.innerHTML = `<div style="padding:.7em 1em;font-size:.82em;color:#333;">${pastPrompts.length ? 'No matches.' : 'No history yet.'}</div>`;
    return;
  }
  el.innerHTML = filtered.map((p) => {
    const i = pastPrompts.indexOf(p);
    const short = p.prompt.replace(/\\n/g, ' ').slice(0, 130) + (p.prompt.length > 130 ? '…' : '');
    return `<div class="history-item">
      <span class="history-text" title="${esc(p.prompt)}">${esc(short)}</span>
      <button type="button" class="history-use" onclick="useHistoryPrompt(${i})">›</button>
    </div>`;
  }).join('');
}

async function loadPastPrompts() {
  try {
    const { prompts } = await fetch('/past_prompts').then(r => r.json());
    pastPrompts = prompts;
    renderHistory();
  } catch {}
}

function useHistoryPrompt(i) {
  $('prompt').value = pastPrompts[i].prompt;
  $('history-panel').style.display = 'none';
  $('history-toggle').textContent = 'History ▾';
  $('prompt').focus();
}

function useQueuePrompt(i) {
  const job = currentJobs[i];
  if (!job) return;
  $('prompt').value = job.prompt;
  $('prompt').focus();
  $('prompt').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

$('history-search').addEventListener('input', renderHistory);

$('history-toggle').addEventListener('click', () => {
  const panel = $('history-panel');
  const opening = panel.style.display === 'none';
  panel.style.display = opening ? 'block' : 'none';
  $('history-toggle').textContent = opening ? 'History ▴' : 'History ▾';
  if (opening) loadPastPrompts();
});
</script>
</body>
</html>
"""


def run_job_background(job_id, prompt, image_path, duration):
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        output_path = os.path.join(OUTPUT_DIR, f"{job_id}.mp4")
        run_job.run_private_job(
            run_job.DEFAULT_WORKFLOW,
            image_path,
            output_path,
            prompt=prompt,
            duration=duration,
        )
        with JOBS_LOCK:
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                JOBS[job_id]["status"] = "done"
                JOBS[job_id]["video_path"] = output_path
            else:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = "No video produced (check terminal)"
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
    finally:
        try:
            os.remove(image_path)
        except OSError:
            pass


def queue_processor():
    """Single background thread — processes one job at a time in submission order."""
    while True:
        job_id, prompt, image_path, duration = JOB_QUEUE.get()
        run_job_background(job_id, prompt, image_path, duration)


def enqueue_job(prompt, image_b64, image_name, duration):
    job_id = uuid.uuid4().hex
    image_path = os.path.join(OUTPUT_DIR, f"{job_id}_{os.path.basename(image_name)}")
    with open(image_path, "wb") as f:
        f.write(base64.b64decode(image_b64))
    preview = prompt[:80] + ("…" if len(prompt) > 80 else "")
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "video_path": None,
            "error": None,
            "prompt": prompt,
            "prompt_preview": preview,
            "duration": duration,
        }
    with QUEUE_ORDER_LOCK:
        QUEUE_ORDER.append(job_id)
    log_prompt(image_name, prompt, duration)
    JOB_QUEUE.put((job_id, prompt, image_path, duration))
    return job_id


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access log

    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/past_prompts":
            self._json(200, {"prompts": read_past_prompts()})
            return

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode())
            return

        if parsed.path == "/status":
            qs = parse_qs(parsed.query)
            job_id = (qs.get("job_id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json(404, {"error": "unknown job"})
                return
            self._json(200, {"status": job["status"], "error": job["error"]})
            return

        if parsed.path == "/queue_list":
            with QUEUE_ORDER_LOCK:
                order = list(QUEUE_ORDER)
            with JOBS_LOCK:
                jobs = [
                    {
                        "job_id": jid,
                        "status": JOBS[jid]["status"],
                        "prompt": JOBS[jid]["prompt"],
                        "prompt_preview": JOBS[jid]["prompt_preview"],
                        "duration": JOBS[jid]["duration"],
                        "error": JOBS[jid]["error"],
                    }
                    for jid in order if jid in JOBS
                ]
            self._json(200, {"jobs": jobs})
            return

        if parsed.path == "/video":
            qs = parse_qs(parsed.query)
            job_id = (qs.get("job_id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or not job.get("video_path") or not os.path.exists(job["video_path"]):
                self.send_error(404, "video not ready")
                return
            with open(job["video_path"], "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/last_frame":
            qs = parse_qs(parsed.query)
            job_id = (qs.get("job_id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or not job.get("video_path") or not os.path.exists(job["video_path"]):
                self.send_error(404, "video not ready")
                return
            png_path = os.path.join(OUTPUT_DIR, f"{job_id}_last.png")
            if not os.path.exists(png_path):
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-sseof", "-0.5", "-i", job["video_path"],
                         "-update", "1", "-frames:v", "1", png_path],
                        check=True, capture_output=True,
                    )
                except FileNotFoundError:
                    self.send_error(500, "ffmpeg not installed")
                    return
                except subprocess.CalledProcessError as e:
                    self.send_error(500, f"ffmpeg failed: {e.stderr.decode()[:200]}")
                    return
            with open(png_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Disposition", f'attachment; filename="{job_id}_last.png"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404)

    def do_POST(self):
        if self.path != "/generate":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode())
        prompt = (body.get("prompt") or "").strip()
        image_b64 = body.get("image_b64") or ""
        image_name = body.get("image_name") or "upload.png"
        duration = float(body.get("duration") or 5.0)
        if not prompt or not image_b64:
            self._json(400, {"error": "prompt and image required"})
            return

        job_id = enqueue_job(prompt, image_b64, image_name, duration)
        self._json(200, {"job_id": job_id})


def free_port(port):
    """Kill any process already listening on `port` (e.g. a stale serve.py)."""
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return
    pids = [int(p) for p in out.split() if p.isdigit() and int(p) != os.getpid()]
    if not pids:
        return
    print(f"Port {port} is in use by pid(s) {pids} — killing.")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(20):
        time.sleep(0.25)
        still = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout
        if not still.strip():
            return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)


def main():
    free_port(PORT)
    threading.Thread(target=queue_processor, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Serving on http://localhost:{PORT}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping.")


if __name__ == "__main__":
    main()
