"""Serves the single-page 'point at any repo' UI at GET /."""

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auto-Debug Agent</title>
<style>
  :root {
    --chocolate-dark: #2E1A0F;
    --chocolate: #4A2C1A;
    --chocolate-med: #6F4426;
    --caramel: #A9702F;
    --off-white: #FAF5EC;
    --cream: #F1E8D8;
    --green: #5B7350;
    --rust: #A34630;
    --amber: #B98029;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--off-white);
    color: var(--chocolate-dark);
    font-family: -apple-system, 'Segoe UI', Inter, sans-serif;
    padding: 40px 20px;
  }
  .wrap { max-width: 640px; margin: 0 auto; }
  h1 {
    font-size: 1.9rem;
    margin: 0 0 4px;
    border-bottom: 2px solid var(--chocolate-med);
    padding-bottom: 14px;
  }
  .sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    color: var(--caramel);
    margin-bottom: 28px;
  }
  label {
    display: block;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--chocolate-med);
    margin-bottom: 6px;
  }
  input[type=text] {
    width: 100%;
    padding: 12px 14px;
    font-size: 1rem;
    border: 1px solid var(--chocolate-med);
    border-radius: 6px;
    background: var(--cream);
    color: var(--chocolate-dark);
    margin-bottom: 16px;
  }
  input[type=text]:focus { outline: 2px solid var(--caramel); }
  button {
    background: var(--chocolate-dark);
    color: var(--off-white);
    border: none;
    padding: 12px 22px;
    border-radius: 6px;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  button:hover:not(:disabled) { background: var(--chocolate); }
  #status {
    margin-top: 24px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    color: var(--chocolate-med);
    min-height: 1.2em;
  }
  .result {
    margin-top: 20px;
    background: var(--cream);
    border: 1px solid var(--chocolate-med);
    border-radius: 8px;
    padding: 20px;
    display: none;
  }
  .verdict {
    display: inline-block;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 4px 10px;
    border-radius: 4px;
    margin-bottom: 12px;
  }
  .verdict.fixed { background: var(--green); color: white; }
  .verdict.failed_to_fix, .verdict.error { background: var(--rust); color: white; }
  .verdict.unsafe { background: var(--amber); color: white; }
  .verdict.no_failures { background: var(--chocolate-med); color: white; }
  .verdict.issue_opened { background: var(--caramel); color: white; }
  .verdict.pr_opened { background: var(--green); color: white; }
  .verdict.still_waiting { background: var(--chocolate-med); color: white; }
  .verdict.stale { background: var(--rust); color: white; }
  .result p { line-height: 1.5; margin: 8px 0; }
  .result a { color: var(--caramel); }
  pre {
    background: var(--chocolate-dark);
    color: var(--off-white);
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.8rem;
    white-space: pre-wrap;
  }
  .hint { font-size: 0.8rem; color: var(--chocolate-med); margin-top: -8px; margin-bottom: 20px; }

  .section-divider {
    border: none;
    border-top: 1px solid var(--chocolate-med);
    opacity: 0.3;
    margin: 40px 0 28px;
  }
  h2 {
    font-size: 1.2rem;
    margin: 0 0 4px;
  }
  .section-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: var(--caramel);
    margin-bottom: 18px;
  }
  .pending-card {
    background: var(--cream);
    border: 1px solid var(--chocolate-med);
    border-radius: 8px;
    padding: 16px 18px;
    margin-bottom: 12px;
  }
  .pending-card .repo-line {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    font-weight: 600;
  }
  .pending-card .test-line {
    font-size: 0.85rem;
    color: var(--chocolate-med);
    margin: 4px 0 10px;
  }
  .pending-card a { color: var(--caramel); font-size: 0.85rem; }
  .pending-card .card-actions {
    margin-top: 12px;
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .btn-small {
    padding: 7px 14px;
    font-size: 0.82rem;
  }
  .btn-secondary {
    background: transparent;
    border: 1px solid var(--chocolate-med);
    color: var(--chocolate-dark);
  }
  .btn-secondary:hover:not(:disabled) { background: var(--cream); }
  .pending-status-tag {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
  }
  .empty-pending {
    font-size: 0.85rem;
    color: var(--chocolate-med);
    font-style: italic;
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>&#9749; Auto-Debug Agent</h1>
  <div class="sub">point it at any repo</div>

  <label for="repo_url">GitHub repo URL</label>
  <input type="text" id="repo_url" placeholder="https://github.com/owner/repo">
  <label for="branch">Branch</label>
  <input type="text" id="branch" placeholder="main" value="main">
  <div class="hint">Clones the repo, installs its dependencies, runs its test suite, and fixes the first failure it finds.</div>

  <button id="run_btn" onclick="runAgent()">Run Agent</button>
  <div id="status"></div>

  <div class="result" id="result">
    <div class="verdict" id="verdict_badge"></div>
    <div id="result_body"></div>
  </div>

  <hr class="section-divider">

  <h2>Pending Approvals</h2>
  <div class="section-sub">fixes verified on external repos, waiting for a maintainer's go-ahead</div>
  <div id="pending_list"></div>
  <button class="btn-small btn-secondary" onclick="checkAllApprovals()" id="check_all_btn">Check All Now</button>
</div>

<script>
async function runAgent() {
  const repoUrl = document.getElementById('repo_url').value.trim();
  const branch = document.getElementById('branch').value.trim() || 'main';
  const btn = document.getElementById('run_btn');
  const status = document.getElementById('status');
  const resultBox = document.getElementById('result');
  const badge = document.getElementById('verdict_badge');
  const body = document.getElementById('result_body');

  if (!repoUrl) {
    status.textContent = 'Enter a repo URL first.';
    return;
  }

  btn.disabled = true;
  resultBox.style.display = 'none';
  status.textContent = 'Cloning repo and running its test suite... this can take a minute or two.';

  try {
    const resp = await fetch('/debug/auto', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: repoUrl, branch: branch }),
    });
    const data = await resp.json();

    status.textContent = '';
    resultBox.style.display = 'block';

    if (!resp.ok) {
      badge.textContent = 'ERROR';
      badge.className = 'verdict error';
      body.innerHTML = '<p>' + (data.detail || 'Something went wrong.') + '</p>';
      return;
    }

    if (data.status === 'no_failures') {
      badge.textContent = 'NO FAILURES';
      badge.className = 'verdict no_failures';
      body.innerHTML = '<p>' + data.message + '</p>';
      return;
    }

    badge.textContent = data.verdict.replace(/_/g, ' ');
    badge.className = 'verdict ' + data.verdict;

    let html = '<p>' + data.message + '</p>';
    html += '<p><strong>Attempts:</strong> ' + data.attempts + '</p>';
    if (data.pr_url) {
      html += '<p><strong>Pull request:</strong> <a href="' + data.pr_url + '" target="_blank">' + data.pr_url + '</a></p>';
    }
    if (data.issue_url) {
      html += '<p><strong>Issue opened:</strong> <a href="' + data.issue_url + '" target="_blank">' + data.issue_url + '</a></p>';
      html += '<p style="color:var(--chocolate-med);font-size:0.85rem;">No changes were pushed. This will appear below under Pending Approvals until a maintainer approves it.</p>';
    }
    if (data.last_fix) {
      html += '<p><strong>Diagnosis:</strong> ' + data.last_fix.explanation + '</p>';
      html += '<pre>' + escapeHtml(data.last_fix.original_snippet) + '\\n\\n\\u2193\\n\\n' + escapeHtml(data.last_fix.fixed_snippet) + '</pre>';
    }
    body.innerHTML = html;
  } catch (err) {
    status.textContent = '';
    resultBox.style.display = 'block';
    badge.textContent = 'ERROR';
    badge.className = 'verdict error';
    body.innerHTML = '<p>Request failed: ' + err.message + '</p>';
  } finally {
    btn.disabled = false;
    loadPending();
  }
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

async function loadPending() {
  const listEl = document.getElementById('pending_list');
  try {
    const resp = await fetch('/pending');
    const data = await resp.json();
    const items = data.pending || [];

    if (items.length === 0) {
      listEl.innerHTML = '<div class="empty-pending">Nothing waiting on approval right now.</div>';
      return;
    }

    listEl.innerHTML = items.map(pf => `
      <div class="pending-card" data-repo="${escapeHtml(pf.repo)}" data-issue="${pf.issue_number}">
        <div class="repo-line">${escapeHtml(pf.repo)} #${pf.issue_number}</div>
        <div class="test-line">${escapeHtml(pf.failure.test_name)} &middot; ${escapeHtml(pf.failure.exception_type)}</div>
        <a href="${pf.issue_url}" target="_blank">${pf.issue_url}</a>
        <div class="card-actions">
          <button class="btn-small btn-secondary" onclick="checkOneApproval('${escapeHtml(pf.repo)}', ${pf.issue_number}, this)">Check Approval</button>
          <span class="pending-status-tag" style="display:none;"></span>
        </div>
      </div>
    `).join('');
  } catch (err) {
    listEl.innerHTML = '<div class="empty-pending">Could not load pending fixes: ' + err.message + '</div>';
  }
}

async function checkOneApproval(repo, issueNumber, btnEl) {
  btnEl.disabled = true;
  const tag = btnEl.nextElementSibling;
  tag.style.display = 'inline';
  tag.textContent = 'checking...';
  try {
    const resp = await fetch('/approvals/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo: repo }),
    });
    const data = await resp.json();
    const result = (data.results || []).find(r => r.issue_number === issueNumber);
    if (!result) {
      tag.textContent = 'no result';
    } else if (result.status === 'pr_opened') {
      tag.textContent = 'PR opened!';
      tag.className = 'pending-status-tag verdict pr_opened';
      loadPending();
    } else if (result.status === 'still_waiting') {
      tag.textContent = 'still waiting';
      tag.className = 'pending-status-tag verdict still_waiting';
    } else if (result.status === 'stale') {
      tag.textContent = 'stale, removed';
      tag.className = 'pending-status-tag verdict stale';
      loadPending();
    } else {
      tag.textContent = result.status || 'error';
      tag.className = 'pending-status-tag verdict error';
    }
  } catch (err) {
    tag.textContent = 'error: ' + err.message;
  } finally {
    btnEl.disabled = false;
  }
}

async function checkAllApprovals() {
  const btn = document.getElementById('check_all_btn');
  btn.disabled = true;
  btn.textContent = 'Checking...';
  try {
    await fetch('/approvals/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
  } catch (err) {
    // swallow -- loadPending() below will just show whatever state we have
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check All Now';
    loadPending();
  }
}

loadPending();
</script>
</body>
</html>
"""