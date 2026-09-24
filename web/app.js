const $ = s => document.querySelector(s);
let state = {boards: [], cards: []}, selected = localStorage.getItem('kanban-board') || '', provider = 'all', detailId;
const statuses = [['todo','Todo','시작할 작업을 추가하세요.'],['running','In progress','진행 중인 작업이 없습니다.'],['done','Done','완료된 결과가 모이는 곳입니다.'],['failed','Needs attention','확인이 필요한 작업이 없습니다.']];
function el(tag, text, className) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; }
async function api(path, method='GET', data) {
  const response = await fetch('/api/' + path, {method, headers: {'Content-Type':'application/json'}, body: data === undefined ? undefined : JSON.stringify(data)});
  const value = await response.json(); if (!response.ok) throw new Error(value.error || '요청 실패'); return value;
}
function error(message) { $('#error').textContent = message; $('#error').hidden = !message; }
async function refresh() {
  try { state = await api('state'); if (selected && !state.boards.some(b=>b.id===selected)) selected=''; render(); $('#connection').textContent = '● 로컬 연결됨'; error(''); }
  catch (e) { $('#connection').textContent = '연결 끊김'; error('서버 연결을 확인하세요. 마지막으로 조회한 기록을 표시합니다.'); }
}
function selectProject(id) { selected=id; localStorage.setItem('kanban-board', id); render(); }
function render() {
  const project = state.boards.find(b=>b.id===selected);
  $('#board-title').textContent = $('#breadcrumb-name').textContent = project?.name || '전체 프로젝트';
  $('#project-count').textContent = state.boards.length;
  $('#all-projects').classList.toggle('active', !selected);
  $('#projects').replaceChildren(...state.boards.map(b=> { const button = el('button', '◈  '+b.name, 'project'+(b.id===selected?' active':'')); button.title=b.directory; button.onclick=()=>selectProject(b.id); return button; }));
  const query = $('#search').value.toLocaleLowerCase();
  const cards = state.cards.filter(c=>(!selected || c.board_id===selected) && (provider==='all' || c.provider===provider) && c.archived===$('#archived').checked && (c.title+' '+c.prompt+' '+c.result).toLocaleLowerCase().includes(query));
  $('#total').textContent=cards.length; $('#running').textContent=cards.filter(c=>c.status==='running').length; $('#done').textContent=cards.filter(c=>c.status==='done').length;
  $('#board').replaceChildren(...statuses.map(([status,label,empty])=> {
    const column=el('div',null,'column '+status), items=cards.filter(c=>c.status===status || (status==='failed' && c.status==='interrupted'));
    const head=el('div',null,'column-head'); head.append(el('span',null,'status-dot'),el('span',label),el('span',items.length,'column-count')); column.append(head);
    if (!items.length) column.append(el('div',empty,'empty'));
    for(const card of items) {
      const button=el('button',null,'task'), top=el('div',null,'task-meta'), bottom=el('div',null,'task-meta');
      top.append(el('span',card.provider.toUpperCase(),'badge '+card.provider),el('span',state.boards.find(b=>b.id===card.board_id)?.name,'task-project'));
      bottom.append(el('span',new Date(card.updated_at).toLocaleDateString('ko-KR',{month:'short',day:'numeric'})),el('span',card.status==='interrupted'?'중단됨':'↗ 상세 보기'));
      button.append(top,el('h3',card.title),bottom); button.onclick=()=>openDetail(card.id); column.append(button);
    } return column;
  }));
}
function openDetail(id) {
  detailId=id; const card=state.cards.find(c=>c.id===id); if(!card)return;
  $('#detail-title').textContent=card.title;
  const body=$('#detail-body'); body.replaceChildren(el('div',`${card.provider.toUpperCase()} · ${card.status} · ${new Date(card.created_at).toLocaleString('ko-KR')}`,'detail-meta'));
  for(const [label,value] of [['요청',card.prompt || card.title],['결과',card.result || '아직 기록된 결과가 없습니다.'],['사용량',card.usage ? JSON.stringify(card.usage,null,2) : '수집된 사용량 없음']]) body.append(el('h3',label),el('pre',value));
  if(card.status==='todo') body.append(el('h3','Kanban-board 폴더의 터미널에서 실행'),el('pre','python3 -m kanban start '+card.id));
  if(card.status==='running') body.append(el('p','실행 상태는 원래 터미널에서 관리합니다.','hint'));
  $('#archive-card').textContent=card.archived?'보관 해제':'보관하기';
  if(!$('#detail-dialog').open) $('#detail-dialog').showModal();
}
$('#archive-card').onclick=async()=>{ try {const c=state.cards.find(c=>c.id===detailId); await api('cards/'+detailId,'PATCH',{archived:!c.archived}); $('#detail-dialog').close(); await refresh();}catch(e){error(e.message);} };
$('#all-projects').onclick=()=>selectProject('');
$('#add-project').onclick=()=>$('#project-dialog').showModal();
$('#new-card').onclick=()=>{
  if(!state.boards.length){$('#project-dialog').showModal();return;}
  $('#card-project').replaceChildren(...state.boards.map(b=>{const o=el('option',b.name);o.value=b.id;return o;}));
  if(selected)$('#card-project').value=selected; $('#card-dialog').showModal();
};
document.querySelectorAll('.close').forEach(b=>b.onclick=()=>b.closest('dialog').close());
document.querySelectorAll('[data-provider]').forEach(b=>b.onclick=()=>{provider=b.dataset.provider; document.querySelectorAll('[data-provider]').forEach(x=>x.classList.toggle('selected',x===b));render();});
$('#search').oninput=render; $('#archived').onchange=render;
for(const [id,endpoint] of [['project','boards'],['card','cards']]) {
  $('#'+id+'-form').onsubmit=async event=>{
    event.preventDefault();const form=event.target,button=form.querySelector('.primary');button.disabled=true;form.querySelector('.form-error').textContent='';
    try {const value=await api(endpoint,'POST',Object.fromEntries(new FormData(form)));form.closest('dialog').close();form.reset();if(id==='project')selectProject(value.id);await refresh();if(id==='card')openDetail(value.id);}
    catch(e){form.querySelector('.form-error').textContent=e.message;}finally{button.disabled=false;}
  };
}
async function poll(){await refresh();setTimeout(poll,3000);} poll();
