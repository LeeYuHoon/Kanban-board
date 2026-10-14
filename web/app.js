const $ = selector => document.querySelector(selector);
const providers = {claude: 'Claude Code', codex: 'Codex'};
const labels = {todo: '대기', running: '진행 중', done: '완료', failed: '실패', interrupted: '중단', unknown: '상태 확인'};
const columns = [
  ['running', '진행 중', '에이전트가 시작하면 여기에 나타나요.'],
  ['done', '완료', '완성된 결과가 모이는 곳입니다.'],
  ['failed', '확인 필요', '실패하거나 중단된 작업이 없습니다.']
];
let selected = '';
try { selected = localStorage.getItem('kankan-project') || ''; } catch (_) { /* Optional persistence. */ }
let state = {boards: [], cards: []}, provider = 'all', detailId, connected = false, refreshing;
let historyId, historyRevision, historyNext, historyBusy, historyRequest = 0;
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function api(path, method = 'GET', data) {
  const response = await fetch('/api/' + path, {
    method, headers: {'Content-Type': 'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(10000)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '요청을 처리하지 못했습니다.');
  return value;
}
function error(message) { $('#error').textContent = message; $('#error').hidden = !message; }
let toastTimer;
function toast(message) {
  (document.querySelector('dialog[open]') || document.body).append($('#toast'));
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3500);
}
async function refresh() {
  if (refreshing) { await refreshing; return refresh(); }
  refreshing = (async () => {
    try {
      state = await api('state'); connected = true; groupSessions(); loadCatalog(state.catalog_version);
      renderCollection(); renderObsidian(); renderSearch();
      if (selected && !visibleBoards().some(board => board.id === selected)) selected = '';
      render();
      if ($('#detail-dialog').open) renderDetail();
      $('#connection').textContent = '● 로컬 연결됨'; error('');
    } catch (_) {
      connected = false; $('#connection').textContent = '○ 연결 끊김';
      $('#scan-sessions').disabled = $('#toggle-collection').disabled = true;
      $('#obsidian-settings').disabled = $('#obsidian-sync').disabled = true;
      error('서버 연결을 확인하세요. 마지막으로 조회한 기록을 표시합니다.');
    }
    $('#add-project').disabled = $('#welcome-project').disabled = !connected;
  })();
  try { await refreshing; } finally { refreshing = null; }
}
function selectProject(id) {
  selected = id;
  try { localStorage.setItem('kankan-project', id); } catch (_) { /* Optional persistence. */ }
  render(); renderObsidian(); renderSearch();
}
function renderCollection() {
  const collection = state.collection || {enabled: false, available: false};
  const available = collection.available !== false;
  $('#collection-status').textContent = !available ? '자동 수집 비활성화' : !collection.enabled ? '자동 수집 일시정지' : collection.running ? '세션을 수집하고 있습니다…' : '자동 수집 켜짐';
  const last = collection.last_scan ? new Date(collection.last_scan).toLocaleTimeString('ko-KR') : '첫 확인 대기 중';
  $('#collection-description').textContent = available
    ? `최근 ${collection.days || 7}일 · 세션 파일 ${collection.files || 0}개 · ${last}${collection.errors ? ' · 일부 기록을 읽지 못했습니다.' : ''}`
    : '이 서버는 자동 수집 없이 실행 중입니다.';
  $('#toggle-collection').textContent = collection.enabled ? '일시정지' : '수집 재개';
  $('#toggle-collection').disabled = !available;
  $('#scan-sessions').disabled = !available || !collection.enabled || collection.running;
}
async function changeCollection(data) {
  $('#scan-sessions').disabled = $('#toggle-collection').disabled = true;
  try {
    await api('collection', 'POST', data); await refresh();
    toast(data.scan ? '세션을 다시 확인합니다.' : data.enabled ? '자동 수집을 재개했습니다.' : '자동 수집을 일시정지했습니다.');
  } catch (err) { toast(err.message); renderCollection(); }
}
$('#scan-sessions').onclick = () => changeCollection({scan: true});
function obsidianTime(value) { return value ? new Date(value).toLocaleString('ko-KR') : ''; }
const modeLabels = {off: '연동 안 함', auto: '자동 저장', selected: '선택 저장'};
const modeHelp = {off: '이 프로젝트는 Obsidian에 저장하지 않습니다.', auto: '모든 프롬프트를 자동으로 저장합니다.',
  selected: '카드의 프롬프트에서 ‘Obsidian에 저장’을 누른 것만 저장합니다.'};
function projectConfig(boardId) { return state.obsidian?.configs?.[boardId] || {mode: 'off'}; }
function projectStatus(boardId) { return (state.obsidian?.projects || []).find(item => item.board_id === boardId); }
function resultText(result) {
  if (!result) return '';
  return `새 노트 ${result.created || 0} · 갱신 ${result.updated || 0} · 변경 없음 ${result.unchanged || 0}`
    + (result.skipped ? ` · 선택 없음 ${result.skipped}` : '') + (result.merged ? ` · 상위 노트로 통합 ${result.merged}` : '')
    + (result.conflicts ? ` · 충돌 ${result.conflicts}` : '') + (result.errors ? ` · 실패 ${result.errors}` : '');
}
function shortTime(value) { return value ? new Date(value).toLocaleString('ko-KR', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : ''; }
function projectBrief(item) {
  if (!item) return '아직 동기화하지 않았습니다.';
  return [item.vault_name, '마지막 성공 ' + (shortTime(item.last_success_at) || '없음'), `노트 ${item.notes}개`].join(' · ');
}
function projectSummary(item) {
  if (!item) return '아직 동기화하지 않았습니다.';
  return [`${item.vault_name} / Kankan`, '마지막 성공 ' + (obsidianTime(item.last_success_at) || '없음'), `노트 ${item.notes}개`,
    item.last_finished_at ? '최근 결과: ' + resultText(item.result) : ''].filter(Boolean).join(' · ');
}
function renderObsidian() {
  const o = state.obsidian || {available: false};
  const busy = o.running || o.queued, project = selected && visibleBoards().find(board => board.id === selected);
  let status, description, detail = '', problem = '';
  if (o.available === false) {
    status = 'Obsidian 연동 사용 불가'; description = '이 서버는 Obsidian 내보내기 없이 실행 중입니다.';
  } else if (project) {
    const config = projectConfig(project.id), item = projectStatus(project.id);
    if (config.mode === 'off') {
      status = `Obsidian 연동 안 함 · ${project.name}`;
      description = '‘연동 설정’에서 자동 저장(모든 프롬프트) 또는 선택 저장(고른 프롬프트만)을 켤 수 있습니다.';
    } else {
      status = busy ? 'Obsidian에 동기화하는 중…' : item?.error ? `Obsidian 동기화 실패 · ${project.name}` : `Obsidian ${modeLabels[config.mode]} · ${project.name}`;
      description = projectBrief(item);
      detail = modeHelp[config.mode] + ' ' + projectSummary(item);
      if (item?.error) problem = `${item.error} (${obsidianTime(item.last_finished_at)}) — ${item.hint || '원인을 해결한 뒤 다시 동기화하세요.'}`;
    }
  } else if (!o.connected) {
    status = 'Obsidian 연동 꺼짐';
    description = '프로젝트마다 저장 방식(자동 / 선택)과 Vault를 정합니다. 설정 전에는 파일을 쓰지 않습니다.';
  } else {
    status = busy ? 'Obsidian에 동기화하는 중…' : o.error ? 'Obsidian 동기화 실패 (일부 프로젝트)' : `Obsidian 연동 · 프로젝트 ${o.connected}개`;
    description = o.projects.map(item => `${item.name}: ${item.mode_label}`).join(' · ') + ' · 마지막 성공 ' + (shortTime(o.last_success_at) || '없음');
    if (o.error) problem = `${o.error} — ${o.hint || '원인을 해결한 뒤 다시 동기화하세요.'}`;
  }
  if (!problem && o.conflicts?.length) problem = `편집된 노트 ${o.conflicts.length}개는 덮어쓰지 않았습니다. 최신 내용은 ‘(Kankan 최신본)’ 사본에 있습니다. ‘연동 설정’에서 처리 방법을 고를 수 있습니다.`;
  // Compact sidebar block: long text is clamped, the full text stays available on hover and in the dialog.
  const failed = project ? projectStatus(project.id)?.error : o.error;
  $('.obsidian-panel').dataset.state = busy ? 'busy' : failed ? 'error' : (project ? projectConfig(project.id).mode !== 'off' : o.connected) ? 'ok' : 'off';
  $('#obsidian-status').textContent = status; $('#obsidian-description').textContent = description;
  $('#obsidian-description').title = detail || description;
  $('#obsidian-error').textContent = problem; $('#obsidian-error').title = problem; $('#obsidian-error').hidden = !problem;
  $('#obsidian-error').classList.toggle('notice', !(project ? projectStatus(project.id)?.error : o.error));
  $('#obsidian-settings').disabled = o.available === false;
  $('#obsidian-sync').disabled = !o.connected || busy;
  if ($('#obsidian-dialog').open) { renderProjectStatuses(); renderConflicts(); }
}
async function changeObsidian(data, message) {
  try { await api('obsidian', 'POST', data); await refresh(); toast(message); return true; }
  catch (err) { toast(err.message); await refresh(); return false; }
}
function projectRow(board) {
  const config = projectConfig(board.id);
  const form = el('form', null, 'obsidian-project'); form.dataset.board = board.id;
  const head = el('div', null, 'op-head'); head.append(el('strong', board.name), el('span', board.directory, 'detail-meta'));
  const modes = el('div', null, 'mode-group'); modes.setAttribute('role', 'radiogroup'); modes.setAttribute('aria-label', board.name + ' 저장 방식');
  for (const mode of ['off', 'auto', 'selected']) {
    const label = el('label', null, 'mode-option'), input = el('input');
    input.type = 'radio'; input.name = 'mode'; input.value = mode; input.checked = config.mode === mode;
    label.append(input, el('span', modeLabels[mode])); modes.append(label);
  }
  const vault = el('label', null, 'vault-label'), input = el('input');
  input.name = 'vault'; input.placeholder = '/Users/name/Documents/MyVault'; input.autocomplete = 'off'; input.spellcheck = false;
  input.value = config.vault || state.obsidian?.default_vault || '';
  vault.append(el('span', 'Vault 폴더 경로'), input);
  const help = el('p', modeHelp[config.mode], 'hint op-help'), status = el('p', '', 'op-status'), error = el('p', '', 'form-error');
  error.setAttribute('role', 'alert');
  const save = el('button', '저장', 'primary small-button op-save');
  modes.onchange = () => { help.textContent = modeHelp[form.elements.mode.value]; };
  form.onsubmit = async event => {
    event.preventDefault(); save.disabled = true; error.textContent = '';
    const mode = form.elements.mode.value, path = input.value.trim();
    try {
      await api('obsidian', 'POST', path ? {project: board.id, mode, vault: path} : {project: board.id, mode});
      await refresh();
      toast(mode === 'off' ? `${board.name}: Obsidian 연동을 껐습니다. 기존 노트는 그대로 남습니다.`
        : `${board.name}: ${modeLabels[mode]}으로 저장했습니다.` + (mode === 'selected' ? ' 카드의 프롬프트에서 ‘Obsidian에 저장’을 누르세요.' : ''));
    } catch (err) { error.textContent = err.message; }
    finally { save.disabled = false; }
  };
  form.append(head, modes, vault, help, status, error, save);
  return form;
}
function renderProjectStatuses() {
  for (const form of $('#obsidian-projects').querySelectorAll('.obsidian-project')) {
    const config = projectConfig(form.dataset.board), item = projectStatus(form.dataset.board);
    const input = form.elements.vault;
    // Offer the vault just used for another project, without touching a field being edited.
    if (!input.value && document.activeElement !== input && state.obsidian?.default_vault) input.value = state.obsidian.default_vault;
    form.querySelector('.op-status').textContent = config.mode === 'off' ? '' : (item?.error ? '⚠ ' + item.error + ' — ' + (item.hint || '') : projectSummary(item));
    form.querySelector('.op-status').classList.toggle('failed', !!item?.error);
    form.classList.toggle('connected', config.mode !== 'off');
  }
}
function renderConflicts() {
  const o = state.obsidian || {}, box = $('#obsidian-conflicts'), conflicts = o.conflicts || [];
  const signature = JSON.stringify(conflicts);
  if (box.dataset.signature === signature) return;
  box.dataset.signature = signature; box.hidden = !conflicts.length;
  box.replaceChildren(el('h3', `편집 충돌 ${conflicts.length}건`), el('p', '편집본은 그대로 두었습니다. 계속 편집본을 쓰려면 그대로 두세요(최신 내용은 사본에 갱신됩니다). ‘Kankan 노트로 복원’은 편집본을 ‘(편집본 날짜)’ 파일로 보존한 뒤 원래 노트를 최신 내용으로 되돌립니다.', 'hint'));
  for (const item of conflicts) {
    const row = el('div', null, 'conflict-row'), paths = el('div');
    paths.append(el('span', item.project + ' · 편집됨 · ' + item.path, 'conflict-path'), el('span', '최신본 · ' + item.conflict_path, 'conflict-path'));
    const button = el('button', 'Kankan 노트로 복원', 'small-button');
    button.type = 'button';
    button.onclick = async () => { button.disabled = true; if (!(await changeObsidian({resolve: item.key}, '편집본을 보존하고 노트를 복원했습니다.'))) button.disabled = false; };
    row.append(paths, button); box.append(row);
  }
}
$('#obsidian-settings').onclick = () => {
  // The project open in the sidebar comes first; every visible project can be set separately.
  const boards = visibleBoards().slice().sort((a, b) => (b.id === selected) - (a.id === selected));
  $('#obsidian-projects').replaceChildren(...boards.map(projectRow));
  $('#obsidian-conflicts').dataset.signature = ''; renderProjectStatuses(); renderConflicts();
  $('#obsidian-dialog').showModal();
};
$('#obsidian-sync').onclick = () => { $('#obsidian-sync').disabled = true; changeObsidian({sync: true}, 'Obsidian 동기화를 시작했습니다.'); };
$('#toggle-collection').onclick = () => changeCollection({enabled: !state.collection.enabled});
// Agent-launched sessions (codex exec, Claude SDK) are shown inside the session that started them.
let childrenByRoot = new Map();
function groupSessions() {
  childrenByRoot = new Map();
  for (const card of state.cards) {
    if (!card.root_id) continue;
    if (!childrenByRoot.has(card.root_id)) childrenByRoot.set(card.root_id, []);
    childrenByRoot.get(card.root_id).push(card);
  }
  for (const list of childrenByRoot.values()) list.sort((a, b) => a.created_at.localeCompare(b.created_at));
}
function boardOf(card) { return card.home_board_id || card.board_id; }
function boardName(card) { return state.boards.find(board => board.id === boardOf(card))?.name; }
function visibleBoards() {
  // Hide projects that only hold agent-launched sessions (e.g. worktrees) or were left empty
  // when a session changed folders; projects the user registered stay visible even when empty.
  const shown = new Set(state.cards.filter(card => !card.root_id).map(boardOf));
  const used = new Set(state.cards.map(card => card.board_id));
  return state.boards.filter(board => shown.has(board.id) || (!used.has(board.id) && board.origin === 'manual'));
}
// ---------- Search (all prompts and answers, by text and/or period) ----------
const search = {q: '', target: 'all', scope: 'project', period: 'all', from: '', to: '', view: 'cards', result: null, error: '', seq: 0};
try { search.view = localStorage.getItem('kankan-search-view') === 'prompts' ? 'prompts' : 'cards'; } catch (_) { /* Optional. */ }
const periodLabels = {all: '전체', today: '오늘', '7d': '최근 7일', '30d': '최근 30일', custom: '직접 지정'};
function searchActive() { return !!(search.q.trim() || search.period !== 'all'); }
function localDay(date) { const day = new Date(date); day.setHours(0, 0, 0, 0); return day; }
function dateValue(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }
function shortDay(value) { return new Date(value).toLocaleDateString('ko-KR', {month: 'short', day: 'numeric'}); }
function periodRange() {
  // Local calendar days; the server compares prompt start times in UTC.
  const today = localDay(new Date()), tomorrow = new Date(today.getTime() + 86400000);
  const back = days => ({since: new Date(today.getTime() - (days - 1) * 86400000), until: tomorrow});
  const range = {today: back(1), '7d': back(7), '30d': back(30)}[search.period];
  if (range) return range;
  if (search.period === 'custom') {
    const parse = value => value ? new Date(value + 'T00:00:00') : null;
    const since = parse(search.from), end = parse(search.to);
    return {since, until: end ? new Date(end.getTime() + 86400000) : null};
  }
  return {since: null, until: null};
}
function highlight(text, terms) {
  // Plain-text highlighting with <mark> nodes; never parsed as HTML.
  const fragment = document.createDocumentFragment(), source = String(text ?? '');
  const wanted = (terms || []).filter(Boolean).map(term => term.toLocaleLowerCase());
  if (!wanted.length) { fragment.append(source); return fragment; }
  const lower = source.toLocaleLowerCase();
  let index = 0;
  while (index < source.length) {
    let next = -1, length = 0;
    for (const term of wanted) {
      const at = lower.indexOf(term, index);
      if (at !== -1 && (next === -1 || at < next || (at === next && term.length > length))) { next = at; length = term.length; }
    }
    if (next === -1) { fragment.append(source.slice(index)); break; }
    if (next > index) fragment.append(source.slice(index, next));
    fragment.append(el('mark', source.slice(next, next + length)));
    index = next + length;
  }
  return fragment;
}
function visibleCards() {
  const everywhere = searchActive() && search.scope === 'all';
  return state.cards.filter(card => !card.root_id && (!selected || everywhere || boardOf(card) === selected)
    && (provider === 'all' || card.provider === provider) && card.archived === $('#archived').checked);
}
// Identifies the query a result belongs to, so a previous result is never presented as the current one.
function searchKey() {
  const range = periodRange();
  return JSON.stringify([search.q.trim(), search.target, range.since?.toISOString() || '', range.until?.toISOString() || '']);
}
let searchTimer;
function scheduleSearch(delay = 300) { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, delay); }
async function runSearch() {
  clearTimeout(searchTimer);
  const seq = ++search.seq;
  if (!searchActive()) { search.result = null; search.error = ''; renderSearch(); render(); return; }
  const range = periodRange(), key = searchKey(), params = new URLSearchParams({q: search.q.trim(), target: search.target});
  if (range.since) params.set('since', range.since.toISOString());
  if (range.until) params.set('until', range.until.toISOString());
  try {
    const value = await api('search?' + params);
    if (seq !== search.seq) return;
    search.result = value; search.resultKey = key; search.error = '';
  } catch (err) {
    if (seq !== search.seq) return;
    search.result = {cards: {}, matches: {}, items: [], total: 0, terms: []}; search.resultKey = key; search.error = err.message;
  }
  renderSearch(); render();
  if ($('#detail-dialog').open) { const card = state.cards.find(item => item.id === detailId); if (card) loadHistory(card); }
}
function renderSearch() {
  const active = searchActive();
  document.querySelectorAll('[data-period]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.period === search.period)));
  document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === search.view)));
  const range = periodRange();
  if (search.period !== 'custom') {
    $('#search-from').value = range.since ? dateValue(range.since) : '';
    $('#search-to').value = range.until ? dateValue(new Date(range.until.getTime() - 86400000)) : '';
  }
  $('#search-scope').disabled = !selected;
  $('#period-toggle').textContent = `기간 · ${periodLabels[search.period]} ▾`;
  $('#search-summary').hidden = !active;
  if (!active) return;
  const shown = new Set(visibleCards().map(card => card.id)), found = search.result?.cards || {};
  const cards = Object.keys(found).filter(id => shown.has(id)), prompts = cards.reduce((sum, id) => sum + found[id].count, 0);
  const parts = [];
  if (search.q.trim()) parts.push(`‘${search.q.trim()}’`);
  if (search.period !== 'all') parts.push(search.period === 'custom' ? `${search.from || '처음'} ~ ${search.to || '오늘'}` : periodLabels[search.period]);
  $('#search-count').textContent = search.error ? search.error
    : !search.result || search.resultKey !== searchKey() ? parts.join(' · ') + ' · 검색 중…'
    : `${parts.join(' · ')} · 카드 ${cards.length}개 · 일치 프롬프트 ${prompts}개` + (search.result.truncated && search.view === 'prompts' ? ' (목록은 최근 300개)' : '');
  $('#search-count').classList.toggle('failed', !!search.error);
}
function renderSearchResults() {
  const box = $('#search-results'), show = searchActive() && search.view === 'prompts';
  box.hidden = !show;
  if (!show) return;
  const shown = new Set(visibleCards().map(card => card.id)), items = (search.result?.items || []).filter(item => shown.has(item.root_id));
  const signature = JSON.stringify([items, search.result?.terms]);
  if (box.dataset.signature === signature) return;
  box.dataset.signature = signature;
  const nodes = [];
  let day = '';
  for (const item of items) {
    const label = new Date(item.started_at).toLocaleDateString('ko-KR', {month: 'long', day: 'numeric', weekday: 'short'});
    if (label !== day) { day = label; nodes.push(el('div', label, 'result-day')); }
    const root = state.cards.find(card => card.id === item.root_id);
    const article = el('article', null, 'result'), head = el('div', null, 'result-head');
    head.append(el('span', providers[item.provider], 'badge ' + item.provider),
      el('span', conversationTitle(root?.prompt || '').split('\n')[0].slice(0, 60) + (item.child ? ' · 하위 세션' : ''), 'result-title'),
      el('span', `· ${boardName(root || {}) || ''} · ${new Date(item.started_at).toLocaleTimeString('ko-KR', {hour: 'numeric', minute: '2-digit'})} · 프롬프트 ${item.number}`, 'detail-meta'));
    const open = el('button', '카드 열기 →', 'small-button open'); open.type = 'button';
    open.onclick = () => openDetail(item.card_id, item.turn_key);
    head.append(open);
    const request = el('p'); request.append(highlight(item.prompt, search.result.terms));
    article.append(head, el('h4', '요청'), request);
    if (item.answer) { const answer = el('p'); answer.append(highlight(item.answer, search.result.terms)); article.append(el('h4', '답변'), answer); }
    nodes.push(article);
  }
  if (!items.length) nodes.push(el('div', '조건에 맞는 프롬프트가 없습니다.', 'filter-empty'));
  box.replaceChildren(...nodes);
}
function resetSearch() {
  Object.assign(search, {q: '', target: 'all', period: 'all', from: '', to: ''});
  $('#search').value = ''; $('#search-target').value = 'all';
  runSearch();
}
$('#search').oninput = () => { search.q = $('#search').value; renderSearch(); scheduleSearch(); };
$('#search').onkeydown = event => { if (event.key === 'Escape' && $('#search').value) { event.preventDefault(); $('#search').value = ''; search.q = ''; runSearch(); } };
$('#search-target').onchange = () => { search.target = $('#search-target').value; runSearch(); };
$('#search-scope').onchange = () => { search.scope = $('#search-scope').value; renderSearch(); render(); };
document.querySelectorAll('[data-period]').forEach(button => { button.onclick = () => {
  search.period = button.dataset.period;
  if (search.period === 'custom') { search.from = $('#search-from').value; search.to = $('#search-to').value; $('#search-from').focus(); }
  renderSearch(); runSearch();
}; });
for (const id of ['#search-from', '#search-to']) $(id).onchange = () => {
  search.period = 'custom'; search.from = $('#search-from').value; search.to = $('#search-to').value; renderSearch(); runSearch();
};
document.querySelectorAll('[data-view]').forEach(button => { button.onclick = () => {
  search.view = button.dataset.view;
  try { localStorage.setItem('kankan-search-view', search.view); } catch (_) { /* Optional. */ }
  renderSearch(); render();
}; });
$('#search-reset').onclick = resetSearch;
$('#period-toggle').onclick = () => {
  const open = !$('#search-panel').classList.contains('period-open');
  $('#search-panel').classList.toggle('period-open', open); $('#period-toggle').setAttribute('aria-expanded', String(open));
};
document.addEventListener('keydown', event => {
  if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey || document.querySelector('dialog[open]')) return;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable) return;
  event.preventDefault(); $('#search').focus();
});
function render() {
  const boards = visibleBoards();
  const project = boards.find(board => board.id === selected);
  $('#board-title').textContent = $('#breadcrumb-name').textContent = project?.name || '전체 프로젝트';
  $('#board-description').textContent = project?.directory || '두 에이전트의 작업을, 하나의 흐름으로.';
  $('#project-count').textContent = boards.length;
  $('#all-projects').classList.toggle('active', !selected);
  $('#all-projects').setAttribute('aria-pressed', String(!selected));
  $('#welcome').hidden = boards.length > 0;
  // Preserve sidebar buttons between polls so keyboard focus is not lost.
  const signature = JSON.stringify([selected, boards]);
  if ($('#projects').dataset.signature !== signature) {
    $('#projects').dataset.signature = signature;
    $('#projects').replaceChildren(...boards.map(board => {
      const button = el('button', null, 'project' + (board.id === selected ? ' active' : ''));
      button.append(el('span', board.name.slice(0, 1).toUpperCase(), 'project-avatar'), el('span', board.name, 'project-name'));
      button.title = board.directory; button.setAttribute('aria-pressed', String(board.id === selected));
      button.onclick = () => selectProject(board.id); return button;
    }));
  }
  const searching = searchActive(), found = search.result?.cards || {};
  const cards = visibleCards().filter(card => !searching || found[card.id]);
  $('#total').textContent = cards.length;
  $('#running').textContent = cards.filter(card => card.status === 'running').length;
  $('#done').textContent = cards.filter(card => card.status === 'done').length;
  $('#filter-empty').hidden = cards.length > 0 || !(searching || provider !== 'all' || $('#archived').checked);
  $('#board').hidden = searching && search.view === 'prompts';
  renderSearchResults();
  const boardSignature = JSON.stringify([cards, cards.map(card => childrenByRoot.get(card.id)?.length || 0), selected, $('#archived').checked,
    searching && cards.map(card => found[card.id]), search.result?.terms]);
  if ($('#board').dataset.signature === boardSignature) return;
  $('#board').dataset.signature = boardSignature;
  $('#board').replaceChildren(...columns.map(([status, label, empty]) => {
    const column = el('div', null, 'column ' + status);
    const items = cards.filter(card => card.status === status || (status === 'failed' && ['interrupted', 'unknown', 'todo'].includes(card.status)));
    const head = el('div', null, 'column-head');
    head.append(el('span', null, 'status-dot'), el('h2', label), el('span', items.length, 'column-count'));
    column.append(head);
    if (!items.length) column.append(el('div', $('#archived').checked ? '보관된 작업이 없습니다.' : empty, 'empty'));
    for (const card of items) {
      const button = el('button', null, 'task'), top = el('div', null, 'task-meta'), bottom = el('div', null, 'task-meta');
      top.append(el('span', providers[card.provider], 'badge ' + card.provider), el('span', labels[card.status], 'task-status'));
      bottom.append(el('span', boardName(card), 'task-project'), el('span', new Date(card.updated_at).toLocaleDateString('ko-KR', {month: 'short', day: 'numeric'})));
      button.append(top, el('h3', card.title));
      if (card.source === 'session') button.append(el('span', '↻ 자동 수집', 'auto-label'));
      const launched = childrenByRoot.get(card.id)?.length;
      if (launched) button.append(el('span', `⚙ 에이전트 자동 실행 ${launched}개 포함`, 'auto-label child-label'));
      if (card.obsidian_picks?.length && projectConfig(boardOf(card)).mode === 'selected') button.append(el('span', `★ Obsidian 선택 ${card.obsidian_picks.length}개`, 'auto-label pick-label'));
      else if (card.launch === 'automated') button.append(el('span', '⚙ 에이전트 자동 실행 · 시작한 세션 미확인', 'auto-label child-label'));
      const hit = searching && found[card.id];
      if (hit) {
        button.append(el('span', `일치 프롬프트 ${hit.count}개` + (hit.children ? ` · 하위 세션 ${hit.children}개 포함` : ''), 'match-badge'));
        const snippet = el('p', null, 'snippet');
        snippet.append('“', highlight(hit.first.answer || hit.first.prompt, search.result.terms), '” · ' + shortDay(hit.first.started_at));
        button.append(snippet);
      } else if (card.prompt) button.append(el('p', card.prompt, 'task-preview'));
      button.append(bottom); button.onclick = () => openDetail(card.id); column.append(button);
    }
    return column;
  }));
}
function openProject() {
  $('#project-form').reset(); $('#project-form .form-error').textContent = ''; $('#project-dialog').showModal();
}
const expandedTurns = new Map(), openLaunched = new Set();
let historyMode = '', historyMatchOnly = true, focusTurn = null;
function openDetail(id, turnKey = null) {
  expandedTurns.clear(); openLaunched.clear(); historyMode = ''; historyMatchOnly = true; focusTurn = turnKey;
  if (turnKey) expandedTurns.set(turnKey, true);
  detailId = id; historyId = null; historyRevision = null; historyNext = null; historyRequest++;
  $('#history-panel').replaceChildren(); $('#history-panel').hidden = true;
  $('#detail-body').dataset.signature = ''; renderDetail();
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
  $('#detail-dialog').scrollTop = 0;
}
async function loadHistory(card, more = false) {
  const keys = search.result?.matches?.[card.id], matchOnly = !!(searchActive() && keys && historyMatchOnly);
  const mode = JSON.stringify([matchOnly, searchActive() && keys, search.result?.terms]);
  if (!more && historyMode !== mode) { historyMode = mode; historyId = null; historyRevision = null; }
  const revision = JSON.stringify([mode, card.turn_count, card.source_updated_at, card.observed_at,
    card.obsidian_picks, projectConfig(boardOf(card)).mode,
    (childrenByRoot.get(card.id) || []).map(child => [child.id, child.status, child.updated_at])]);
  if (!more && historyId === card.id && historyRevision === revision) return;
  const busyKey = card.id + revision;
  if (historyBusy === busyKey) return;
  historyBusy = busyKey;
  const request = ++historyRequest;
  const cursor = more ? historyNext : null;
  const panel = $('#history-panel'); panel.hidden = false;
  try {
    const value = await api('cards/' + card.id + '/history' + (matchOnly ? '?keys=' + encodeURIComponent(JSON.stringify(keys))
      : cursor ? '?before=' + encodeURIComponent(cursor) : ''));
    if (request !== historyRequest || detailId !== card.id) return;
    const preserveCursor = !more && historyId === card.id && panel.querySelectorAll('.history-turn').length > 30;
    if (historyId !== card.id) {
      const heading = el('div', null, 'history-heading'), tools = el('div', null, 'history-tools');
      for (const [label, open] of [['모두 펼치기', true], ['모두 접기', false]]) {
        const button = el('button', label, 'small-button'); button.type = 'button';
        button.onclick = () => panel.querySelectorAll('.history-turn').forEach(node => setTurnOpen(node, open));
        tools.append(button);
      }
      if (searchActive() && keys) {
        // Search open: matching prompts are expanded and highlighted; '일치만 보기' hides the rest.
        const only = el('label', null, 'archive-filter match-only'), box = el('input');
        box.type = 'checkbox'; box.checked = historyMatchOnly;
        box.onchange = () => { historyMatchOnly = box.checked; loadHistory(card); };
        only.append(box, ` 일치만 보기 (${keys.length})`); tools.prepend(only);
        for (const key of keys) if (!expandedTurns.has(key)) expandedTurns.set(key, true);
      }
      heading.append(el('h3', ''), tools);
      panel.replaceChildren(heading, el('p', '프롬프트를 눌러 요청 · 답변 · 사용 내역을 펼치거나 접습니다. 최신 요청부터 표시합니다.', 'hint'));
    }
    panel.querySelector('h3').textContent = `프롬프트 기록 (${card.turn_count || value.items.length})`;
    panel.querySelector('.history-more')?.remove();
    panel.querySelector('.history-error')?.remove();
    const existing = new Map([...panel.querySelectorAll('.history-turn')].map(node => [node.dataset.key, node]));
    for (const turn of value.items) {
      const old = existing.get(turn.turn_key);
      const launched = launchedBy(card, turn.turn_key);
      const choosing = projectConfig(boardOf(card)).mode === 'selected' && !card.root_id;
      const picked = (card.obsidian_picks || []).includes(turn.turn_key);
      const signature = JSON.stringify([turn, launched.map(child => [child.id, child.status, child.updated_at]), choosing, picked]);
      if (old?.dataset.signature === signature) continue;
      const details = el('article', null, 'history-turn'); details.dataset.key = turn.turn_key;
      details.dataset.signature = signature; details.dataset.started = turn.started_at;
      const header = el('button', null, 'turn-header turn-toggle'); header.type = 'button';
      const summary = el('span', null, 'turn-summary');
      summary.append(el('strong', '프롬프트'), el('span', conversationTitle(turn.prompt).split('\n')[0].slice(0, 140), 'turn-preview'));
      const side = el('span', null, 'turn-side');
      if (launched.length) side.append(el('span', `⚙ 하위 세션 ${launched.length}`, 'turn-badge'));
      side.append(el('span', `${labels[turn.status] || turn.status} · ${new Date(turn.started_at).toLocaleString('ko-KR')}`, 'detail-meta'), el('span', '', 'turn-chevron'));
      header.append(summary, side);
      header.onclick = () => setTurnOpen(details, details.dataset.open !== 'true');
      const body = el('div', null, 'turn-body');
      const session = el('div', null, 'turn-session');
      session.append(el('span', '세션 · ' + (turn.session_id || card.session_id || '기록 없음')));
      const requestId = el('span', '요청 ID · ' + turn.turn_key.slice(0, 12));
      requestId.title = turn.turn_key; session.append(requestId);
      const terms = searchActive() ? search.result?.terms || [] : [];
      const request = el('pre', null, 'turn-request'), answer = el('pre', null, 'turn-answer');
      request.append(highlight(turn.prompt, terms));
      answer.append(highlight(turn.result || (turn.status === 'running' ? '답변을 작성하고 있습니다.' : '이 요청에 대해 기록된 최종 답변이 없습니다.'), turn.result ? terms : []));
      if (keys?.includes(turn.turn_key)) details.classList.add('matched');
      body.append(session, el('h4', '요청'), request, el('h4', '답변'), answer);
      if (turn.result) {
        const copy = el('button', '답변 복사', 'small-button turn-copy');
        copy.onclick = () => copyText(turn.result); body.append(copy);
      }
      body.append(activityPanel(turn.activity, card.provider));
      if (launched.length) body.append(launchedPanel(launched, turn.turn_key));
      const head = el('div', null, 'turn-head'); head.append(header);
      if (choosing) head.append(pickButton(card, turn.turn_key, picked));
      details.append(head, body);
      setTurnOpen(details, expandedTurns.get(turn.turn_key) || false);
      if (old) old.replaceWith(details);
      else {
        const next = [...panel.querySelectorAll('.history-turn')].find(node =>
          node.dataset.started < turn.started_at || (node.dataset.started === turn.started_at && node.dataset.key < turn.turn_key));
        if (next) panel.insertBefore(details, next); else panel.append(details);
      }
    }
    historyId = card.id; historyRevision = revision;
    if (focusTurn) {
      const node = panel.querySelector(`.history-turn[data-key="${CSS.escape(focusTurn)}"]`);
      if (node) { setTurnOpen(node, true); node.scrollIntoView({block: 'start'}); focusTurn = null; }
    }
    if (!preserveCursor) historyNext = value.next;
    if (historyNext) {
      const button = el('button', '이전 대화 더 보기', 'history-more');
      button.onclick = () => { button.disabled = true; loadHistory(card, true); };
      panel.append(button);
    }
  } catch (_) {
    if (request !== historyRequest || detailId !== card.id) return;
    panel.querySelector('.history-error')?.remove();
    panel.append(el('p', '대화 이력을 불러오지 못했습니다. 잠시 후 다시 확인합니다.', 'history-error'));
    const moreButton = panel.querySelector('.history-more'); if (moreButton) moreButton.disabled = false;
    historyRevision = null;
  } finally { if (historyBusy === busyKey) historyBusy = null; }
}
// 선택 저장: only prompts picked here are written to the project's Obsidian note.
function pickButton(card, turnKey, picked) {
  const button = el('button', picked ? '★ Obsidian 저장 선택됨' : '☆ Obsidian에 저장', 'small-button turn-pick' + (picked ? ' picked' : ''));
  button.type = 'button'; button.setAttribute('aria-pressed', String(picked));
  button.title = picked ? '선택을 해제합니다. 이미 저장된 노트는 지우지 않습니다.' : '이 프롬프트(와 여기서 실행된 하위 세션)를 Obsidian 노트에 저장합니다.';
  button.onclick = async () => {
    button.disabled = true;
    try {
      await api('obsidian', 'POST', {card_id: card.id, turn_key: turnKey, picked: !picked});
      await refresh();
      toast(picked ? '선택을 해제했습니다. 이미 저장된 노트는 지우지 않습니다.' : '이 프롬프트를 Obsidian에 저장합니다.');
    } catch (err) { toast(err.message); button.disabled = false; }
  };
  return button;
}
function setTurnOpen(node, open) {
  node.dataset.open = String(open); expandedTurns.set(node.dataset.key, open);
  node.querySelector('.turn-toggle').setAttribute('aria-expanded', String(open));
  node.querySelector('.turn-body').hidden = !open;
}
// Descendants grouped under the top-level prompt that (directly or through another run) launched them.
function launchedBy(card, turnKey) {
  const byId = new Map(state.cards.map(item => [item.id, item]));
  return (childrenByRoot.get(card.id) || []).filter(child => {
    let node = child;
    for (let depth = 0; node && node.parent_id !== card.id && depth < 20; depth++) node = byId.get(node.parent_id);
    return node?.parent_turn_key === turnKey;
  });
}
function launchedPanel(children, turnKey) {
  const panel = el('section', null, 'launched-panel'), list = el('div', null, 'launched-list');
  const toggle = el('button', null, 'small-button launched-toggle'); toggle.type = 'button';
  // Open state survives live re-renders while child sessions are still running.
  const show = open => {
    list.hidden = !open; toggle.setAttribute('aria-expanded', String(open));
    if (open) openLaunched.add(turnKey); else openLaunched.delete(turnKey);
    toggle.textContent = `⚙ 에이전트가 실행한 하위 세션 ${children.length}개 ${open ? '접기' : '보기'}`;
    if (open && !list.childElementCount) list.append(...children.map(child => {
      const row = el('button', null, 'launched-row'); row.type = 'button';
      row.append(el('span', providers[child.provider], 'badge ' + child.provider), el('span', conversationTitle(child.prompt).split('\n')[0].slice(0, 120), 'launched-title'),
        el('span', `${labels[child.status] || child.status} · ${new Date(child.created_at).toLocaleString('ko-KR')}`, 'detail-meta'));
      row.onclick = () => openDetail(child.id); return row;
    }));
  };
  toggle.onclick = () => show(list.hidden);
  show(openLaunched.has(turnKey));
  panel.append(toggle, el('p', '이 프롬프트를 처리하는 동안 에이전트가 codex exec 등으로 자동 실행한 세션입니다. 눌러서 각 세션의 요청과 답변을 확인하세요.', 'hint'), list);
  return panel;
}
function conversationTitle(prompt) {
  const text = (prompt || '').replace(/<image\b[^>]*>[\s\S]*?<\/image>/gi, '').replace(/\[Image #\d+\]/gi, '').trim();
  return text || '이미지 첨부 요청';
}
function activityGroups(activity = {}, provider) {
  const mcp = [], tools = [];
  for (const name of activity.tools || []) {
    const match = name.match(/(?:^|\.)mcp__([^]+?)__(.+)$/);
    if (match) mcp.push({label: match[1] + ' / ' + match[2], title: name, kind: 'mcp', name: 'mcp__' + match[1] + '__' + match[2], server: match[1]});
    else tools.push({label: name, title: name, kind: 'tool', name, provider});
  }
  const names = (key, kind) => (activity[key] || []).map(label => ({label, title: label, kind, name: label, provider}));
  return [
    ['skills', '스킬', [...names('skills', 'skill'), ...names('skill_references', 'skill_reference').map(item => ({...item, label: item.label + ' · 참조'}))]],
    ['mcp', 'MCP', mcp],
    ['agents', '에이전트', [{label: providers[provider] || provider, title: '이 요청의 실행 에이전트', kind: 'main_agent', name: providers[provider] || provider, provider},
      ...names('agents', 'agent').map(item => ({...item, label: item.label + ' · 하위'}))]],
    ['tools', '일반 도구', tools], ['models', '모델', names('models')]
  ];
}
function activityRows(activity, provider, compact) {
  const container = el('span', null, compact ? 'activity-overview' : 'activity-groups');
  for (const [key, label, items] of activityGroups(activity, provider)) {
    if (compact && ['tools', 'models'].includes(key)) continue;
    const row = el('span', null, 'activity-row activity-' + key);
    row.append(el('span', label, 'activity-label'));
    const values = el('span', null, 'activity-values');
    const shown = compact ? items.slice(0, 3) : items;
    for (const item of shown) {
      const chip = el('span', item.label, 'activity-chip');
      if (item.kind) {
        // Description on hover / focus / tap (see showTip); no native title so it does not double up.
        Object.assign(chip.dataset, {kind: item.kind, name: item.name, owner: item.provider || '', server: item.server || ''});
        chip.tabIndex = 0; chip.setAttribute('aria-describedby', 'tip');
      } else chip.title = item.title;
      values.append(chip);
    }
    if (compact && items.length > shown.length) values.append(el('span', '+' + (items.length - shown.length) + ' 더 보기', 'activity-more'));
    if (!items.length) values.append(el('span', '확인된 기록 없음', 'activity-empty'));
    row.append(values); container.append(row);
  }
  return container;
}
// Descriptions Kankan found for skills, agents and MCP tools (transcript listings, SKILL.md files).
let catalogData = null, catalogVersion = null;
async function loadCatalog(version, force = false) {
  if (version === catalogVersion && !force) return;
  catalogVersion = version;
  try { catalogData = await api('catalog'); } catch (_) { catalogVersion = null; }
}
const sourceLabels = {transcript: 'Claude Code가 받은 목록', skill_file: '설치된 SKILL.md'};
// Public directories with a verified search URL. Private names (project skills, named teammates,
// Codex sub-agents, internal ids) are never sent to them.
const directories = {
  augment: ['AugmentClaude에서 찾기', term => 'https://augmentclaude.com/search?q=' + encodeURIComponent(term)],
  // Smithery lists MCP servers and skills (no agents), each with its own search.
  smithery: ['Smithery MCP에서 찾기', term => 'https://smithery.ai/search?q=' + encodeURIComponent(term)],
  smitherySkills: ['Smithery 스킬에서 찾기', term => 'https://smithery.ai/skills?q=' + encodeURIComponent(term)]
};
const builtInAgents = new Set(['general-purpose', 'Explore', 'Plan', 'claude', 'statusline-setup', 'claude-code-guide']);
function searchLinks(term, sites = ['augment']) {
  term = term.includes(':') ? term.split(':').pop() : term;  // plugin:name -> name
  return sites.map(site => ({label: directories[site][0], url: directories[site][1](term)}));
}
function describeChip({kind, name, owner: provider, server}) {
  const cat = catalogData || {}, entry = (group, key) => cat[group]?.[key];
  const note = (text, source) => ({text, source: source ? sourceLabels[source] || source : ''});
  if (kind === 'main_agent') return {title: name, kindLabel: '실행 에이전트', parts: [note('이 프롬프트를 처리한 에이전트입니다. 아래 하위 에이전트·도구 기록은 이 에이전트의 세션 로그에서 확인한 것입니다.')]};
  if (kind === 'skill' || kind === 'skill_reference') {
    const found = entry('skill', name), parts = [];
    if (kind === 'skill_reference') parts.push(note('스킬 파일(SKILL.md)을 읽거나 언급한 기록입니다. 실제로 실행했는지는 로그로 확인되지 않습니다.'));
    parts.push(found ? note(found.description, found.source) : note('설명을 찾지 못했습니다. 로그의 스킬 목록과 설치된 SKILL.md에 이 이름이 없습니다.'));
    const private_ = entry('skill_scope', name);
    if (private_) parts.push(note('이 프로젝트 전용 스킬이라 외부 사이트 검색 링크를 제공하지 않습니다.'));
    return {title: name, kindLabel: kind === 'skill' ? '스킬 실행' : '스킬 참조', parts, links: private_ ? [] : searchLinks(name, ['augment', 'smitherySkills'])};
  }
  if (kind === 'agent') {
    const direct = entry('agent', name), alias = entry('agent_alias', name)?.description, typed = alias && entry('agent', alias);
    // Search by agent type, never by a teammate's own name; Claude Code built-in types are not listed elsewhere.
    const type = direct ? name : alias, links = type && !builtInAgents.has(type) ? searchLinks(type) : [];
    if (direct) return {title: name, kindLabel: '하위 에이전트' + (builtInAgents.has(name) ? ' · Claude Code 기본' : ''), parts: [note(direct.description, direct.source)], links};
    if (alias) return {title: name, kindLabel: `하위 에이전트 · ${alias} 유형`, parts: [typed ? note(typed.description, typed.source) : note(`${alias} 유형으로 실행된 에이전트입니다. 이 유형의 설명은 기록에 없습니다.`)], links};
    if (provider === 'codex') return {title: name, kindLabel: 'Codex 하위 에이전트', parts: [note('Codex가 spawn_agent로 만든 하위 에이전트입니다. 맡긴 역할 내용은 로그에 암호화되어 있어 표시할 수 없습니다.')]};
    if (/^a[0-9a-f]{16}$/.test(name)) return {title: name, kindLabel: '하위 에이전트', parts: [note('Claude 내부 에이전트 ID입니다. 실행할 때의 이름이 로그에 남지 않아 어떤 에이전트인지 확인되지 않습니다.')]};
    return {title: name, kindLabel: '하위 에이전트', parts: [note('설명을 찾지 못했습니다. 로그의 에이전트 목록에 이 이름이 없습니다.')]};
  }
  if (kind === 'tool') return {title: name, kindLabel: '일반 도구 · ' + (providers[provider] || provider || '기본') + ' 기본 도구', parts: []};
  if (kind === 'mcp') {
    const tool = entry('mcp_tool', name), host = entry('mcp_server', server), parts = [];
    if (tool) parts.push(note(tool.description, tool.source));
    // Recorded server text is the instruction block given to the agent, labelled as such.
    if (host) parts.push(note('에이전트에게 전달된 서버 안내: ' + host.description, host.source));
    if (!parts.length) parts.push(note('설명을 찾지 못했습니다. 로그에 이 MCP 서버·도구의 설명이 기록되지 않았습니다.'));
    // claude.ai connectors are named claude_ai_<Service>; search by the service name.
    const term = server.replace(/^claude_ai_/, '').replace(/_/g, ' ');
    return {title: name.replace(/^mcp__/, '').replace('__', ' / '), kindLabel: 'MCP 서버 / 도구', parts, links: searchLinks(term, ['smithery', 'augment'])};
  }
  return null;
}
let tipTarget = null, tipPinned = false;
// The user's own description per item (saved locally; an empty save deletes it).
const userKinds = {tool: 'tool', skill: 'skill', skill_reference: 'skill', mcp: 'mcp', agent: 'agent'};
function userNote(chip) {
  const kind = userKinds[chip.dataset.kind];
  return kind ? {kind, name: chip.dataset.name, saved: catalogData?.user?.[kind]?.[chip.dataset.name]} : null;
}
function showTip(chip, pinned = false, editing = false) {
  const info = describeChip(chip.dataset);
  if (!info) return;
  const tip = $('#tip'); tipTarget = chip; tipPinned = pinned;
  (chip.closest('dialog') || document.body).append(tip);
  const body = info.parts.map(part => { const node = el('p', part.text); if (part.source) node.append(el('span', ' · 출처: ' + part.source, 'tip-source')); return node; });
  const links = el('div', null, 'tip-links');
  for (const link of info.links || []) {
    const anchor = el('a', link.label + ' ↗'); anchor.href = link.url;
    anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; links.append(anchor);
  }
  tip.replaceChildren(el('strong', info.title), el('span', info.kindLabel, 'tip-kind'), ...body);
  const mine = userNote(chip);
  if (mine) tip.append(userSection(chip, mine, info, pinned, editing));
  if (links.childElementCount) tip.append(links);
  tip.classList.toggle('pinned', pinned);
  tip.hidden = false;
  const rect = chip.getBoundingClientRect(), box = tip.getBoundingClientRect();
  const left = Math.min(Math.max(8, rect.left), innerWidth - box.width - 8);
  const below = rect.bottom + 8 + box.height <= innerHeight;
  tip.style.left = left + 'px'; tip.style.top = (below ? rect.bottom + 8 : Math.max(8, rect.top - box.height - 8)) + 'px';
}
let tipTimer;
function userSection(chip, mine, info, pinned, editing) {
  const box = el('div', null, 'tip-user');
  if (editing) {
    const form = el('form', null, 'tip-editor'), area = el('textarea');
    area.maxLength = 2000; area.rows = 4; area.value = mine.saved?.text || '';
    area.placeholder = '이 항목이 무엇인지 직접 적어 두세요. 빈칸으로 저장하면 삭제됩니다.';
    area.setAttribute('aria-label', info.title + ' 설명');
    const actions = el('div', null, 'tip-actions'), save = el('button', '저장', 'small-button primary'), cancel = el('button', '취소', 'small-button');
    cancel.type = 'button'; cancel.onclick = () => showTip(chip, true);
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled = true;
      try {
        const value = await api('descriptions', 'POST', {kind: mine.kind, name: mine.name, text: area.value});
        await loadCatalog(null, true);
        toast(value.deleted ? '내 설명을 삭제했습니다.' : '내 설명을 저장했습니다.');
        if (chip.isConnected) showTip(chip, true); else hideTip();
      } catch (err) { toast(err.message); save.disabled = false; }
    };
    actions.append(save, cancel); form.append(area, el('p', '빈칸으로 저장하면 내 설명이 삭제됩니다. 기록된 설명은 그대로 남습니다.', 'tip-hint'), actions);
    box.append(form);
    setTimeout(() => area.focus(), 0);
    return box;
  }
  if (mine.saved) {
    const node = el('p', mine.saved.text, 'tip-mine'); node.prepend(el('b', '내 설명 · ')); box.append(node);
  } else if (!info.parts.length || info.parts.every(part => !part.source)) {
    box.append(el('p', '설명이 없습니다. ' + (pinned ? '아래에서 작성해 주세요.' : '클릭해서 작성해 주세요.'), 'tip-empty'));
  }
  if (pinned) {
    const edit = el('button', mine.saved ? '내 설명 수정' : '설명 작성', 'small-button tip-edit');
    edit.type = 'button'; edit.onclick = () => showTip(chip, true, true);
    box.append(edit);
  } else box.append(el('p', '클릭하면 내 설명을 ' + (mine.saved ? '수정' : '작성') + '할 수 있습니다.', 'tip-hint'));
  return box;
}
function hideTip() { clearTimeout(tipTimer); tipTarget = null; tipPinned = false; $('#tip').hidden = true; }
// The tooltip stays while the pointer moves from the chip onto it, so its links can be clicked.
const inTip = node => node && $('#tip').contains(node);
document.addEventListener('mouseover', event => {
  const chip = event.target.closest?.('.activity-chip[data-kind]');
  if (chip || inTip(event.target)) clearTimeout(tipTimer);
  if (chip && chip !== tipTarget && !tipPinned) showTip(chip);
});
document.addEventListener('mouseout', event => {
  if (!tipTarget || tipPinned || tipTarget.contains(event.relatedTarget) || inTip(event.relatedTarget)) return;
  clearTimeout(tipTimer); tipTimer = setTimeout(hideTip, 250);
});
document.addEventListener('focusin', event => {
  const chip = event.target.closest?.('.activity-chip[data-kind]');
  if (chip && chip !== tipTarget) showTip(chip); else if (!chip && !inTip(event.target) && !tipPinned) hideTip();
});
// Click (or Enter / Space) pins the box so the description can be written or edited.
document.addEventListener('click', event => {
  const chip = event.target.closest?.('.activity-chip[data-kind]');
  // A button inside the box may already be replaced (e.g. '설명 작성' swaps in the editor): not an outside click.
  if (chip) showTip(chip, true); else if (tipTarget && event.target.isConnected && !inTip(event.target)) hideTip();
});
document.addEventListener('keydown', event => {
  const chip = event.target.closest?.('.activity-chip[data-kind]');
  if (chip && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); showTip(chip, true); }
});
document.addEventListener('keydown', event => { if (event.key === 'Escape' && tipTarget) { hideTip(); event.stopPropagation(); } }, true);
// Scrolling keeps the tooltip attached to its chip; it closes once the chip is gone or off screen.
document.addEventListener('scroll', () => {
  if (!tipTarget) return;
  const rect = tipTarget.getBoundingClientRect();
  if (!tipTarget.isConnected || rect.bottom < 0 || rect.top > innerHeight) hideTip();
  else if (!$('#tip').querySelector('.tip-editor')) showTip(tipTarget, tipPinned);
}, true);
function activityPanel(activity, provider) {
  const panel = el('section', null, 'activity-panel');
  panel.append(el('h4', '스킬 / MCP / 에이전트'), activityRows(activity, provider, false));
  panel.append(el('p', '로그에 기록된 호출 기준입니다. ‘참조’는 스킬 파일을 언급하거나 읽은 기록입니다. 항목에 마우스를 올리면 설명이 보이고, 클릭하면 내 설명을 작성·수정할 수 있습니다.', 'hint'));
  return panel;
}
function renderDetail() {
  const card = state.cards.find(item => item.id === detailId);
  if (!card) { $('#detail-dialog').close(); return; }
  const body = $('#detail-body'), signature = JSON.stringify(card);
  if (card.source === 'session' || card.turn_count) loadHistory(card);
  renderObsidianLink(card);
  if (body.dataset.signature === signature) return;
  body.dataset.signature = signature; $('#detail-title').textContent = card.source === 'session' ? conversationTitle(card.prompt).slice(0, 140) : card.title;
  const meta = el('div', null, 'detail-meta');
  meta.append(el('span', providers[card.provider], 'badge ' + card.provider), el('span', labels[card.status]), el('span', boardName(card)));
  body.replaceChildren(meta);
  if (card.source === 'session') body.append(el('p', '↻ 자동 수집 · ' + (card.status_note || '원래 세션에서 작업을 계속하세요.'), 'guide-note'));
  if (card.parent_id && state.cards.some(item => item.id === card.parent_id)) {
    const back = el('button', '← 이 세션을 실행한 상위 세션 열기', 'small-button parent-link'); back.type = 'button';
    back.onclick = () => openDetail(card.parent_id);
    body.append(el('p', '⚙ 에이전트가 자동 실행한 하위 세션입니다. 보드에는 상위 세션 안에 묶여 표시됩니다.', 'hint'), back);
  }
  const pending = card.status === 'running' ? '에이전트가 작업 중입니다. 실행이 끝나면 결과가 자동으로 표시됩니다.' : '아직 기록된 결과가 없습니다.';
  if (!(card.source === 'session' || card.turn_count)) {
  for (const [label, value] of [['요청', card.prompt || card.title], ['결과', card.result || pending]]) {
    const heading = el('div', null, 'detail-section-heading'); heading.append(el('h3', label));
    if (label === '결과' && card.result) { const copy = el('button', '결과 복사', 'small-button'); copy.onclick = () => copyText(card.result); heading.append(copy); }
    body.append(heading, el('pre', value));
  }
  body.append(activityPanel(card.activity, card.provider));
  }
  const usage = el('details', null, 'usage'); usage.append(el('summary', '사용량 · 실행 정보'));
  usage.append(el('pre', card.usage ? JSON.stringify(card.usage, null, 2) : '수집된 사용량 없음'));
  const facts = [['생성', card.created_at], ['시작', card.started_at], ['종료', card.finished_at]];
  for (const [label, value] of facts) if (value) usage.append(el('p', label + ' · ' + new Date(value).toLocaleString('ko-KR'), 'detail-meta'));
  if (card.session_id) usage.append(el('p', '세션 · ' + card.session_id, 'detail-meta'));
  if (card.source_path) usage.append(el('p', '원본 · ' + card.source_path, 'detail-meta'));
  if (card.observed_at) usage.append(el('p', '수집 · ' + new Date(card.observed_at).toLocaleString('ko-KR'), 'detail-meta'));
  if (card.usage?.scope) usage.append(el('p', card.usage.scope === 'session_total' ? '사용량 범위: 세션 누적' : '사용량 범위: 최근 모델 응답', 'detail-meta'));
  body.append(usage);
  $('#archive-card').textContent = card.archived ? '보관 해제' : '보관하기';
}
function renderObsidianLink(card) {
  const link = $('#open-obsidian'), note = $('#obsidian-note');
  const top = state.cards.find(item => item.id === card.root_id) || card;
  const config = projectConfig(boardOf(top)), exportable = card.source === 'session' || card.status !== 'todo';
  link.hidden = !card.obsidian?.url;
  if (card.obsidian?.url) { link.href = card.obsidian.url; link.title = card.obsidian.path; }
  let text = '';
  if (exportable && config.mode !== 'off') {
    text = `이 프로젝트: Obsidian ${modeLabels[config.mode]} — ${modeHelp[config.mode]}`;
    if (config.mode === 'selected' && !card.root_id) text += ` 선택한 프롬프트 ${(card.obsidian_picks || []).length}개.`;
    if (card.root_id) text += ' 이 하위 세션은 상위 세션 노트에 함께 저장됩니다.';
    text += card.obsidian ? ' 노트 · ' + card.obsidian.path + (card.obsidian.conflict_path ? ` (편집됨 · 최신 내용: ${card.obsidian.conflict_path})` : '')
      : config.mode === 'selected' ? '' : ' 아직 동기화되지 않았습니다.';
  }
  note.textContent = text; note.hidden = !text;
}
async function copyText(value) {
  try { await navigator.clipboard.writeText(value); toast('클립보드에 복사했습니다.'); }
  catch (_) { toast('복사할 수 없습니다. 표시된 내용을 선택해서 복사하세요.'); }
}
$('#archive-card').onclick = async () => {
  const button = $('#archive-card'); button.disabled = true;
  try {
    const card = state.cards.find(item => item.id === detailId);
    await api('cards/' + detailId, 'PATCH', {archived: !card.archived});
    $('#detail-dialog').close(); await refresh(); toast(card.archived ? '보관을 해제했습니다.' : '작업을 보관했습니다.');
  } catch (err) { toast(err.message); } finally { button.disabled = false; }
};
$('#all-projects').onclick = () => selectProject('');
$('#add-project').onclick = $('#welcome-project').onclick = openProject;
$('#open-guide').onclick = () => $('#guide-dialog').showModal();
document.querySelectorAll('.close, .cancel').forEach(button => { button.onclick = () => button.closest('dialog').close(); });
function setProvider(value) {
  provider = value;
  document.querySelectorAll('[data-provider]').forEach(button => {
    const active = button.dataset.provider === provider;
    button.classList.toggle('selected', active); button.setAttribute('aria-pressed', String(active));
  });
  render();
}
document.querySelectorAll('[data-provider]').forEach(button => { button.onclick = () => setProvider(button.dataset.provider); });
$('#archived').onchange = render;
$('#reset-filters').onclick = () => { resetSearch(); $('#archived').checked = false; setProvider('all'); };
$('#project-form').onsubmit = async event => {
  event.preventDefault();
  const form = event.target, button = form.querySelector('.primary'); button.disabled = true;
  form.querySelector('.form-error').textContent = '';
  try {
    const value = await api('boards', 'POST', Object.fromEntries(new FormData(form)));
    form.closest('dialog').close();
    selected = value.id;
    await refresh(); selectProject(selected); setProvider(provider);
    toast('프로젝트를 추가했습니다. 이 폴더에서 Codex나 Claude Code를 사용하면 카드가 자동으로 생깁니다.');
  } catch (err) { form.querySelector('.form-error').textContent = err.message; }
  finally { button.disabled = false; }
};
async function poll() { await refresh(); if (searchActive()) await runSearch(); setTimeout(poll, 3000); }
poll();

// Require both ends of the gesture on the backdrop, preserving text selection.
document.querySelectorAll('dialog').forEach(dialog => {
  let startedOutside = false;
  const outside = event => {
    const rect = dialog.getBoundingClientRect();
    return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
  };
  dialog.addEventListener('pointerdown', event => { startedOutside = event.button === 0 && outside(event); });
  dialog.addEventListener('click', event => {
    if (startedOutside && outside(event)) dialog.close();
    startedOutside = false;
  });
  dialog.addEventListener('pointercancel', () => { startedOutside = false; });
  dialog.addEventListener('close', () => { startedOutside = false; });
});
