const FILAS_NUM = Array.from({length:33},(_,i)=>String(i+1));

const ESPECIAIS = [
  {key:'coqueiro',label:'Coqueiro',icon:'🌴',cls:'esp-coqueiro'},
  {key:'laje',label:'Laje',icon:'🪨',cls:'esp-laje'},
  {key:'lavador',label:'Lavador',icon:'💧',cls:'esp-lavador'},
  {key:'bomba',label:'Bomba',icon:'⛽',cls:'esp-bomba'},
  {key:'eletricos',label:'Elétricos',icon:'⚡',cls:'esp-eletricos'},
  {key:'fundao',label:'Fundão',icon:'🏭',cls:'esp-fundao'},
];

const EXEMPLO = {
  linhas:[],
  frota:[],
  manutencao:[],
  filas:{},
  especiais:{coqueiro:[],lavador:[],eletricos:[],bomba:[],laje:[],fundao:[]},
  presos:[],
  revisoes:[],
  escala:{ tipo:null, data:'', importadoEm:null, manobra:[], e2:[], ar2:[] }
};

let state;
function migrarDados(lista){
  if(!Array.isArray(lista)) return [];
  return lista
    .map(x => typeof x === 'string' ? {frota:x, linha:''} : x)
    .filter(x => x && x.frota && String(x.frota) !== 'undefined' && String(x.frota) !== 'null');
}

function initState(){
  try {
    const s=localStorage.getItem('sambaiba_v2');
    state=s?JSON.parse(s):JSON.parse(JSON.stringify(EXEMPLO));
  } catch(e) {
    state=JSON.parse(JSON.stringify(EXEMPLO));
  }
  if(!state.linhas) state.linhas=[];
  // Migra linhas antigas (array de strings) para array de objetos e remove entradas inválidas
  state.linhas = state.linhas
    .map(l => typeof l === 'string'
      ? { codigo: l, descricao: '', setor: String(l).startsWith('1') ? 'E2' : 'AR2' }
      : l)
    .filter(l => l && l.codigo && typeof l.codigo === 'string' && l.codigo.trim() !== '');
  if(!state.frota) state.frota=[];
  if(!state.presos) state.presos=[];
  if(!state.manutencao) state.manutencao=[];
  if(!state.revisoes) state.revisoes=[];
  if(!state.filas) state.filas={};
  if(!state.especiais) state.especiais={};
  if(!state.escala) state.escala={ tipo:null, data:'', importadoEm:null, manobra:[], e2:[], ar2:[] };
  if(!state.escala.manobra) state.escala.manobra=[];
  if(!state.escala.e2) state.escala.e2=[];
  if(!state.escala.ar2) state.escala.ar2=[];
  // Garante todas as filas e especiais existem e migra formato antigo
  FILAS_NUM.forEach(f=>{ state.filas[f]=migrarDados(state.filas[f]||[]); });
  ESPECIAIS.forEach(e=>{ state.especiais[e.key]=migrarDados(state.especiais[e.key]||[]); });
  // Garante frota como array de objetos
  state.frota=state.frota.map(o=>typeof o==='string'?{frota:o}:o).filter(o=>o&&o.frota);
  state.frota.sort((a,b)=>Number(a.frota)-Number(b.frota));
}
function save(){localStorage.setItem('sambaiba_v2',JSON.stringify(state));}

// Registra uma linha no catálogo state.linhas de forma segura (sempre como objeto)
function _registrarLinha(codigo) {
  if (!codigo) return;
  const c = String(codigo).trim();
  if (!c) return;
  const existe = state.linhas.find(l => (l.codigo || l) === c);
  if (!existe) state.linhas.push({ codigo: c, descricao: '', setor: '' });
}

function updateClock(){
  const n=new Date();
  document.getElementById('clock').textContent=n.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  document.getElementById('dateline').textContent=n.toLocaleDateString('pt-BR',{weekday:'short',day:'2-digit',month:'short'}).toUpperCase();
}
setInterval(updateClock,1000);updateClock();

let currentPage='patio';
function showPage(id,el){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  el.classList.add('active');currentPage=id;renderAll();
}
function fabAction(){
  if(currentPage==='frota')openModal('modal-onibus');
  else if(currentPage==='alertas')openModal('modal-preso');
  else openModal('modal-onibus');
}

let modalFilaAtual=null;
function openModal(id,filaKey){
  if(filaKey!==undefined)modalFilaAtual=filaKey;
  populateSelects();
  document.getElementById(id).classList.add('open');
  if(id==='modal-alocar'){
    const esp=ESPECIAIS.find(e=>e.key===filaKey);
    document.getElementById('modal-alocar-title').textContent=esp?`Alocar em ${esp.label}`:`Alocar na Fila ${filaKey}`;
    setTimeout(()=>{
      const el=document.getElementById('input-frota-alocar');
      if(el){el.value='';el.focus();}
      document.getElementById('input-linha-alocar').value='';
    },100);
  }
}
function closeModal(id){document.getElementById(id).classList.remove('open');modalFilaAtual=null;}
document.querySelectorAll('.modal-overlay').forEach(m=>{m.addEventListener('click',e=>{if(e.target===m)m.classList.remove('open');});});

// ─── MODAL DE CONFIRMAÇÃO GENÉRICO ───────────────────────────────
let _confirmaCallback = null;
function confirmar(titulo, msg, onSim, onNao) {
  document.getElementById('confirma-titulo').textContent = titulo;
  document.getElementById('confirma-msg').textContent   = msg;
  _confirmaCallback = { onSim: onSim || null, onNao: onNao || null };
  openModal('modal-confirma');
}
function _confirmaResolve(resp) {
  closeModal('modal-confirma');
  const cb = _confirmaCallback;
  _confirmaCallback = null;
  if (resp && cb && cb.onSim) cb.onSim();
  else if (!resp && cb && cb.onNao) cb.onNao();
}

function populateSelects(){
  const opts=state.frota.length
    ?state.frota.map(o=>`<option value="${o.frota}">${o.frota}</option>`).join('')
    :'<option value="">Nenhum ônibus cadastrado</option>';
  ['select-preso','select-revisao'].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML=opts;});
  // Atualiza datalist de linhas (suporta objetos e strings legadas)
  const linhaOpts = state.linhas
    .map(l => typeof l === 'object' ? l.codigo : l)
    .sort((a,b) => String(a).localeCompare(String(b), 'pt-BR', {numeric:true}))
    .map(c => `<option value="${c}">`)
    .join('');
  document.querySelectorAll('#datalist-linhas, #lista-linhas-rapido, #lista-linhas-bloco, #lista-linhas-edit')
    .forEach(el => { if(el) el.innerHTML = linhaOpts; });
}

function cadastrarOnibus(){
  const frota=String(document.getElementById('input-frota').value).trim();
  if(!frota){alert('Informe o número da frota.');return;}
  if(state.frota.find(o=>String(o.frota)===frota)){alert('Frota já cadastrada.');return;}
  state.frota.push({frota});
  state.frota.sort((a,b)=>Number(a.frota)-Number(b.frota));
  document.getElementById('input-frota').value='';
  save();renderAll();closeModal('modal-onibus');
}

function alocarOnibus(){
  const inputFrota = document.getElementById('input-frota-alocar');
  const frota = inputFrota ? String(inputFrota.value).trim() : '';
  const linha=document.getElementById('input-linha-alocar').value.trim();
  if(!frota||!modalFilaAtual)return;
  // Remove de qualquer posição anterior
  FILAS_NUM.forEach(f=>{state.filas[f]=state.filas[f].filter(x=>x.frota!==frota);});
  ESPECIAIS.forEach(e=>{state.especiais[e.key]=state.especiais[e.key].filter(x=>x.frota!==frota);});
  // Salva linha nova se não existir
  _registrarLinha(linha);
  // Calcula próxima posição sem conflito
  const esp=ESPECIAIS.find(e=>e.key===modalFilaAtual);
  const listaAtual = esp ? (state.especiais[modalFilaAtual]||[]) : (state.filas[modalFilaAtual]||[]);
  const maxPos = listaAtual.length > 0 ? Math.max(...listaAtual.map(x=>x.pos||0)) : 0;
  const cadastro = state.frota.find(o => String(o.frota) === String(frota));
  const linhaFinal = linha || (cadastro && cadastro.linha) || '';
  _registrarLinha(linhaFinal);
  const item={frota, linha: linhaFinal, pos: maxPos + 1};
  if(esp)state.especiais[modalFilaAtual].push(item);else state.filas[modalFilaAtual].push(item);
  document.getElementById('input-linha-alocar').value='';
  const ifA = document.getElementById('input-frota-alocar');
  if(ifA) ifA.value='';
  save();renderAll();closeModal('modal-alocar');
}

function removerDaFila(frota,filaKey,isEspecial){
  if(!confirm(`Remover ônibus ${frota} desta posição?`))return;
  if(isEspecial)state.especiais[filaKey]=state.especiais[filaKey].filter(x=>x.frota!==frota);
  else state.filas[filaKey]=state.filas[filaKey].filter(x=>x.frota!==frota);
  save();renderAll();
}

function registrarPreso(){
  const frota=document.getElementById('select-preso').value;
  if(!frota)return;
  state.presos.push({frota,motivo:document.getElementById('input-motivo-preso').value.trim(),hora:new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})});
  document.getElementById('input-motivo-preso').value='';
  save();renderAll();closeModal('modal-preso');
}

function registrarRevisao(){
  const frota=document.getElementById('select-revisao').value;
  if(!frota)return;
  state.revisoes.push({frota,tipo:document.getElementById('select-tipo-revisao').value,desc:document.getElementById('input-desc-revisao').value.trim(),hora:new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})});
  document.getElementById('input-desc-revisao').value='';
  save();renderAll();closeModal('modal-revisao');
}

function removerAlerta(tipo,idx){
  if(!confirm('Marcar como resolvido e remover?'))return;
  if(tipo==='preso')state.presos.splice(idx,1);else state.revisoes.splice(idx,1);
  save();renderAll();
}

function excluirOnibus(frota){
  if(!confirm(`Remover ônibus ${frota} da frota?`))return;
  state.frota=state.frota.filter(o=>String(o.frota)!==String(frota));
  FILAS_NUM.forEach(f=>{state.filas[f]=state.filas[f].filter(x=>x.frota!==frota);});
  ESPECIAIS.forEach(e=>{state.especiais[e.key]=state.especiais[e.key].filter(x=>x.frota!==frota);});
  state.presos=state.presos.filter(p=>p.frota!==frota);
  state.revisoes=state.revisoes.filter(r=>r.frota!==frota);
  save();renderAll();
}

function updateCharCount(){document.getElementById('char-count').textContent=`(${document.getElementById('input-desc-revisao').value.length}/100)`;}

function localizarVeiculo(frota) {
  for(const f of FILAS_NUM) {
    const item = (state.filas[f]||[]).find(x=>String(x.frota)===String(frota));
    if(item) return {onde:'Fila '+f+(item.pos?' · Pos.'+item.pos:''), linha:item.linha||'', tipo:'fila'};
  }
  for(const e of ESPECIAIS) {
    const item = (state.especiais[e.key]||[]).find(x=>String(x.frota)===String(frota));
    if(item) return {onde:e.label, linha:item.linha||'', tipo:'especial'};
  }
  return null;
}

function buscarVeiculo(){
  const search=(document.getElementById('search-patio').value||'').trim();
  const resultado=document.getElementById('resultado-busca');
  if(!search){ resultado.innerHTML=''; return; }

  const frota=search;
  const cadastrado=state.frota.find(o=>String(o.frota)===frota);
  const loc=localizarVeiculo(frota);
  const preso=state.presos.find(p=>String(p.frota)===frota);
  const revisao=state.revisoes.find(r=>String(r.frota)===frota);

  let cor='#334155', icone='🚌', status='', detalhe='';

  if(!cadastrado){
    cor='#7f1d1d'; icone='❌';
    status='Veículo não cadastrado';
    detalhe='O número '+frota+' não existe na frota cadastrada.';
  } else if(preso){
    cor='#7f1d1d'; icone='🔴';
    status='PRESO';
    detalhe=(preso.motivo||'Sem descrição')+(loc?' · '+loc.onde:'');
  } else if(revisao){
    cor='#78350f'; icone='🟡';
    status='AMOSTRAL SPTRANS';
    detalhe=revisao.tipo+(revisao.desc?' — '+revisao.desc:'')+(loc?' · '+loc.onde:'');
  } else if(loc){
    cor='#064e3b'; icone='✅';
    status=loc.onde;
    detalhe=loc.linha?'Linha '+loc.linha:'Linha não informada';
  } else {
    cor='#1e3a5f'; icone='⏳';
    status='Não alocado';
    detalhe='Veículo cadastrado mas ainda não alocado em nenhuma fila.';
  }

  resultado.innerHTML=`
    <div class="card" style="border-left:4px solid ${cor};margin-bottom:10px">
      <div class="card-header" style="background:${cor}22">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="font-size:28px">${icone}</div>
          <div>
            <div style="font-family:var(--mono);font-size:24px;font-weight:800;color:var(--text)">${frota}</div>
            <div style="font-size:14px;font-weight:700;color:var(--accent);margin-top:2px">${status}</div>
          </div>
        </div>
      </div>
      <div style="padding:12px 16px;font-size:14px;color:var(--muted)">${detalhe}</div>
    </div>`;
}

function renderPatio(){
  const container=document.getElementById('filas-container');

  // Sempre exibe filas normalmente
  container.innerHTML=FILAS_NUM.map(f=>{
    const onibus=state.filas[f];
    const chips=onibus.map(o=>{
      const cadastro = state.frota.find(x=>String(x.frota)===String(o.frota));
      const hora = cadastro&&cadastro.hora ? cadastro.hora : '';
      const isPreso    = state.presos.find(p=>String(p.frota)===String(o.frota));
      const isAmostral = state.revisoes.find(r=>String(r.frota)===String(o.frota));
      const status     = cadastro&&cadastro.status ? cadastro.status : '';
      // PRESO tem prioridade absoluta sobre hora e linha
      let infoLinha = '';
      if(isPreso) {
        infoLinha = `<span class="chip-status chip-status-preso">PRESO</span>`;
      } else if(hora) {
        infoLinha = `<span style="font-size:10px;color:#aaa;font-weight:400">${hora}</span>`;
      } else if(isAmostral) {
        infoLinha = `<span class="chip-status chip-status-amostral">AMOSTRAL</span>`;
      } else if(status === 'manutencao') {
        infoLinha = `<span class="chip-status chip-status-manutencao">MANUTENÇÃO</span>`;
      } else if(status === 'evento') {
        infoLinha = `<span class="chip-status chip-status-evento">EVENTO</span>`;
      } else if(status) {
        infoLinha = `<span class="chip-status" style="background:var(--surface);color:var(--muted)">${status.toUpperCase()}</span>`;
      }
      return `<div class="chip" onclick="abrirEdicaoChip('${o.frota}','${f}',false)">
        <div style="display:flex;flex-direction:column;gap:2px">
          <div style="display:flex;align-items:center;gap:6px">
            <span>${o.frota}</span>
            ${infoLinha}
          </div>
          
        </div>
        ${!isPreso ? `<span style="font-size:10px;color:var(--accent);font-weight:400">${o.pos?'P.'+o.pos:''}${(o.linha||(cadastro&&cadastro.linha))?' L.'+(o.linha||(cadastro&&cadastro.linha)):''}</span>` : ''}
      </div>`;
    }).join('');
    return `<div class="card">
      <div class="card-header">
        <div style="display:flex;align-items:center;gap:10px"><div class="fila-num">${f}</div><div class="fila-label">Fila ${f}</div></div>
        <div style="display:flex;align-items:center;gap:8px"><span class="fila-count">${onibus.length} ôn.</span><button class="btn btn-ghost btn-sm" onclick="openModal('modal-alocar','${f}')">+ Alocar</button></div>
      </div>
      <div class="chips">${chips||'<span style="color:var(--muted);font-size:13px;padding:4px 0">Vazia</span>'}</div>
    </div>`;
  }).join('');
}

function renderEspeciais(){
  document.getElementById('especiais-container').innerHTML=ESPECIAIS.map(e=>{
    const onibus=state.especiais[e.key];
    const chips=onibus.map(o=>{
      const cadastro = state.frota.find(x=>String(x.frota)===String(o.frota));
      const hora = cadastro&&cadastro.hora ? cadastro.hora : '';
      const isPreso    = state.presos.find(p=>String(p.frota)===String(o.frota));
      const isAmostral = state.revisoes.find(r=>String(r.frota)===String(o.frota));
      const status     = cadastro&&cadastro.status ? cadastro.status : '';
      let infoLinha = '';
      if(hora) {
        infoLinha = `<span style="font-size:10px;color:#aaa;font-weight:400">${hora}</span>`;
      } else if(isPreso) {
        infoLinha = `<span class="chip-status chip-status-preso">PRESO</span>`;
      } else if(isAmostral) {
        infoLinha = `<span class="chip-status chip-status-amostral">AMOSTRAL</span>`;
      } else if(status === 'manutencao') {
        infoLinha = `<span class="chip-status chip-status-manutencao">MANUTENÇÃO</span>`;
      } else if(status === 'evento') {
        infoLinha = `<span class="chip-status chip-status-evento">EVENTO</span>`;
      } else if(status) {
        infoLinha = `<span class="chip-status" style="background:var(--surface);color:var(--muted)">${status.toUpperCase()}</span>`;
      }
      return `<div class="chip" onclick="abrirEdicaoChip('${o.frota}','${e.key}',true)">
        <div style="display:flex;flex-direction:column;gap:2px">
          <div style="display:flex;align-items:center;gap:6px">
            <span>${o.frota}</span>
            ${infoLinha}
          </div>
          ${(!isPreso && o.linha)?`<span style="font-size:10px;color:var(--accent);font-weight:400">${o.pos?'P.'+o.pos+' ':''}L.${o.linha}</span>`:''}
        </div>
        <span style="font-size:13px;color:var(--muted)">✎</span>
      </div>`;
    }).join('');
    return `<div class="card">
      <div class="card-header">
        <div style="display:flex;align-items:center;gap:10px">
          <div class="fila-num">${e.icon}</div>
          <div class="fila-label">${e.label}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="fila-count">${onibus.length} ôn.</span>
          <button class="btn btn-ghost btn-sm" onclick="openModal('modal-alocar','${e.key}')">+ Alocar</button>
        </div>
      </div>
      <div class="chips">${chips||'<span style="color:var(--muted);font-size:13px;padding:4px 0">Vazia</span>'}</div>
    </div>`;
  }).join('');
}

function renderAlertas(){
  const lp=document.getElementById('lista-presos');
  const lr=document.getElementById('lista-revisao');
  lp.innerHTML=state.presos.length
    ?state.presos.map((p,i)=>`<div class="alerta-card preso"><div class="alerta-top"><div style="display:flex;align-items:center;gap:10px"><div class="alerta-frota">${p.frota}</div><span class="badge badge-preso">PRESO</span></div><button class="btn btn-ghost btn-sm" onclick="removerAlerta('preso',${i})">✓ Resolver</button></div><div class="alerta-desc">${p.motivo||'Sem descrição'}</div><div class="alerta-meta">Registrado às ${p.hora}</div></div>`).join('')
    :'<div class="empty"><div class="empty-icon">✅</div>Nenhum veículo preso</div>';
  lr.innerHTML=state.revisoes.length
    ?state.revisoes.map((r,i)=>`<div class="alerta-card revisao"><div class="alerta-top"><div style="display:flex;align-items:center;gap:10px"><div class="alerta-frota">${r.frota}</div><span class="badge badge-revisao">SPTRANS</span></div><button class="btn btn-ghost btn-sm" onclick="removerAlerta('revisao',${i})">✓ Resolver</button></div><div class="alerta-desc">${r.desc||'Sem descrição'}</div><div class="alerta-meta">Solicitado às ${r.hora}</div></div>`).join('')
    :'<div class="empty"><div class="empty-icon">✅</div>Nenhum amostral SPTRANS</div>';
}

function renderFrota(){
  const search=(document.getElementById('search-frota').value||'').toLowerCase();
  const container=document.getElementById('frota-container');
  const filtered=state.frota.filter(o=>o&&o.frota&&String(o.frota).toLowerCase().includes(search));
  if(!filtered.length){container.innerHTML=state.frota.length===0?'<div class="empty"><div class="empty-icon">🚌</div>Nenhum ônibus cadastrado.<br>Toque em + para adicionar.</div>':'<div class="empty"><div class="empty-icon">🔍</div>Nenhum resultado</div>';return;}
  function loc(frota){
    for(const f of FILAS_NUM){const item=state.filas[f].find(x=>x.frota===frota);if(item)return'Fila '+f+(item.linha?' · Linha '+item.linha:'');}
    for(const e of ESPECIAIS){const item=state.especiais[e.key].find(x=>x.frota===frota);if(item)return e.label+(item.linha?' · Linha '+item.linha:'');}
    return null;
  }
  container.innerHTML='<div class="card"><div class="card-body">'+filtered.map(o=>{
    const l=loc(o.frota);
    const preso=state.presos.find(p=>p.frota===o.frota);
    const revisao=state.revisoes.find(r=>r.frota===o.frota);
    return `<div class="onibus-item">
      <div>
        <div class="onibus-num">${o.frota}</div>
        <div class="onibus-info">${l?'📍 '+l:'<span style="color:var(--muted)">Não alocado</span>'}</div>
        <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${preso?'<span class="badge badge-preso">PRESO</span>':''}${revisao?'<span class="badge badge-revisao">REVISÃO</span>':''}</div>
      </div>
      <button class="btn btn-danger btn-sm" onclick="excluirOnibus('${o.frota}')">✕</button>
    </div>`;
  }).join('')+'</div></div>';
}

// ─── SUB-ABAS DA FROTA ───────────────────────────────────────────
function showFrotaTab(tab) {
  document.getElementById('frota-painel-veiculos').style.display = tab === 'veiculos' ? 'block' : 'none';
  document.getElementById('frota-painel-linhas').style.display   = tab === 'linhas'   ? 'block' : 'none';
  document.getElementById('ftab-veiculos').classList.toggle('active', tab === 'veiculos');
  document.getElementById('ftab-linhas').classList.toggle('active', tab === 'linhas');
  if (tab === 'linhas') renderLinhas();
}

// ─── CRUD DE LINHAS ──────────────────────────────────────────────
function renderLinhas() {
  const search  = (document.getElementById('search-linhas').value || '').toLowerCase();
  const container = document.getElementById('linhas-container');
  const lista   = state.linhas
    .filter(l => {
      const c = (l.codigo || '').toLowerCase();
      const d = (l.descricao || '').toLowerCase();
      return !search || c.includes(search) || d.includes(search);
    })
    .sort((a, b) => String(a.codigo).localeCompare(String(b.codigo), 'pt-BR', {numeric:true}));

  if (!lista.length) {
    container.innerHTML = state.linhas.length === 0
      ? '<div class="empty"><div class="empty-icon">🗺️</div>Nenhuma linha cadastrada.<br>Toque em + para adicionar.</div>'
      : '<div class="empty"><div class="empty-icon">🔍</div>Nenhum resultado</div>';
    return;
  }

  container.innerHTML = '<div class="card"><div class="card-body">' +
    lista.map(l => `
      <div class="linha-item">
        <div class="linha-info">
          <span class="linha-codigo">${l.codigo}</span>
          <span class="linha-setor ${l.setor === 'E2' ? 'setor-e2' : 'setor-ar2'}">${l.setor}</span>
          ${l.descricao ? `<span class="linha-desc">${l.descricao}</span>` : ''}
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-ghost btn-sm" onclick="abrirEditarLinha('${l.codigo}')">✎</button>
          <button class="btn btn-danger btn-sm" onclick="removerLinha('${l.codigo}')">✕</button>
        </div>
      </div>`
    ).join('') +
  '</div></div>';
}

function abrirNovaLinha() {
  document.getElementById('linha-modal-titulo').textContent = '➕ Nova Linha';
  document.getElementById('input-linha-codigo').value = '';
  document.getElementById('input-linha-descricao').value = '';
  document.getElementById('input-linha-setor').value = 'E2';
  document.getElementById('linha-edit-codigo-original').value = '';
  document.getElementById('input-linha-codigo').removeAttribute('readonly');
  openModal('modal-linha');
}

function abrirEditarLinha(codigo) {
  const linha = state.linhas.find(l => l.codigo === codigo);
  if (!linha) return;
  document.getElementById('linha-modal-titulo').textContent = '✎ Editar Linha';
  document.getElementById('input-linha-codigo').value = linha.codigo;
  document.getElementById('input-linha-descricao').value = linha.descricao || '';
  document.getElementById('input-linha-setor').value = linha.setor || 'E2';
  document.getElementById('linha-edit-codigo-original').value = linha.codigo;
  document.getElementById('input-linha-codigo').setAttribute('readonly', true);
  openModal('modal-linha');
}

function salvarLinha() {
  const codigo    = document.getElementById('input-linha-codigo').value.trim().toUpperCase();
  const descricao = document.getElementById('input-linha-descricao').value.trim();
  const setor     = document.getElementById('input-linha-setor').value;
  const original  = document.getElementById('linha-edit-codigo-original').value;

  if (!codigo) { alert('Informe o código da linha.'); return; }

  if (!original) {
    // Nova linha — verifica duplicata
    if (state.linhas.find(l => l.codigo === codigo)) {
      alert(`Linha ${codigo} já está cadastrada.`); return;
    }
    state.linhas.push({ codigo, descricao, setor });
  } else {
    // Edição — atualiza
    const idx = state.linhas.findIndex(l => l.codigo === original);
    if (idx >= 0) state.linhas[idx] = { codigo: original, descricao, setor };
  }

  save();
  closeModal('modal-linha');
  renderLinhas();
  populateSelects();
}

function removerLinha(codigo) {
  confirmar(
    '🗑️ Remover linha?',
    `Deseja remover a linha ${codigo} do cadastro?`,
    () => {
      state.linhas = state.linhas.filter(l => l.codigo !== codigo);
      save();
      renderLinhas();
      populateSelects();
    }
  );
}

function importarLinhasExcel(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const data = new Uint8Array(e.target.result);
      const wb   = XLSX.read(data, {type:'array'});
      const ws   = wb.Sheets[wb.SheetNames[0]];
      // Lê por posição de coluna (header:1) — ignora o nome do cabeçalho
      // Coluna A (0) = código, B (1) = descrição, C (2) = setor
      const rows = XLSX.utils.sheet_to_json(ws, {header:1, defval:''});

      if (!rows.length) { alert('Nenhuma linha encontrada na planilha.'); return; }

      // Ignora a linha de cabeçalho (linha 0)
      const dados = rows.slice(1);

      let novos = 0, atualizados = 0, ignorados = 0;

      dados.forEach(row => {
        const codigo    = String(row[0] || '').trim().toUpperCase();
        const descricao = String(row[1] || '').trim();
        // Coluna C: aceita "E2", "E 2", "AR2", "AR 2", etc.
        const setorRaw  = String(row[2] || '').toUpperCase().replace(/\s/g,'');
        const setor     = setorRaw.startsWith('AR') ? 'AR2' : 'E2';

        if (!codigo || codigo === 'CODIGO' || codigo === 'CODE') { ignorados++; return; }

        const existente = state.linhas.find(l => l.codigo === codigo);
        if (existente) {
          // Atualiza descrição e setor se já existir
          if (descricao) existente.descricao = descricao;
          existente.setor = setor;
          atualizados++;
        } else {
          state.linhas.push({ codigo, descricao, setor });
          novos++;
        }
      });

      save();
      renderLinhas();
      populateSelects();
      alert(`✔ Importação concluída!\n${novos} novas linhas\n${atualizados} atualizadas${ignorados ? '\n' + ignorados + ' ignoradas (sem código)' : ''}`);
    } catch(err) {
      alert('Erro ao ler o arquivo: ' + err.message);
    }
    input.value = '';
  };
  reader.readAsArrayBuffer(file);
}

function updateStats(){
  let alocados=0;
  FILAS_NUM.forEach(f=>alocados+=(state.filas[f]||[]).length);
  ESPECIAIS.forEach(e=>alocados+=(state.especiais[e.key]||[]).length);
  document.getElementById('stat-total').textContent=state.frota.length;
  document.getElementById('stat-alocados').textContent=alocados;
  document.getElementById('stat-presos').textContent=state.presos.length;
  document.getElementById('stat-revisao').textContent=state.revisoes.length;
}

function renderAll(){renderPatio();renderEspeciais();renderManutencao();renderAlertas();renderFrota();renderManobra();renderPlantaoE2();renderPlantaoAR2();updateStats();popularDatalistsRapido();}


function imprimirLista() {
  const now = new Date();
  const ESPECIAIS_LABELS = {
    coqueiro:'Coqueiro', laje:'Laje', lavador:'Lavador',
    bomba:'Bomba', eletricos:'Eletricos', fundao:'Fundao'
  };

  // Monta lista completa ordenada por frota crescente
  let todos = [];
  FILAS_NUM.forEach(f => {
    state.filas[f].forEach(o => {
      const cadastro = state.frota.find(x => String(x.frota) === String(o.frota));
      const linha = o.linha || (cadastro && cadastro.linha) || '';
      todos.push({frota: o.frota, posicao: f, linha, pos: o.pos||'', hora: (cadastro&&cadastro.hora)||'', preso: false});
    });
  });
  ESPECIAIS.forEach(e => {
    state.especiais[e.key].forEach(o => {
      const cadastro = state.frota.find(x => String(x.frota) === String(o.frota));
      const linha = o.linha || (cadastro && cadastro.linha) || '';
      todos.push({frota: o.frota, posicao: ESPECIAIS_LABELS[e.key], linha, pos: o.pos||'', hora: (cadastro&&cadastro.hora)||'', preso: false});
    });
  });
  state.presos.forEach(p => {
    const item = todos.find(x => x.frota === p.frota);
    if(item) { item.situacao = 'PRESO'; }
    else {
      const cadastro = state.frota.find(x => String(x.frota) === String(p.frota));
      todos.push({frota: p.frota, posicao: '', linha: '', hora: (cadastro&&cadastro.hora)||'', situacao: 'PRESO'});
    }
  });
  state.revisoes.forEach(r => {
    const item = todos.find(x => x.frota === r.frota);
    if(item) { item.situacao = 'AMOSTRAL'; }
    else {
      const cadastro = state.frota.find(x => String(x.frota) === String(r.frota));
      todos.push({frota: r.frota, posicao: '', linha: '', hora: (cadastro&&cadastro.hora)||'', situacao: 'AMOSTRAL'});
    }
  });
  todos.sort((a,b) => Number(a.frota) - Number(b.frota));

  // 6 colunas, ~55 itens por coluna (cabe numa A4 retrato)
  const POR_COLUNA = 55;
  const cols = [];
  for(let i = 0; i < todos.length; i += POR_COLUNA) {
    cols.push(todos.slice(i, i + POR_COLUNA));
  }
  if(cols.length === 0) cols.push([]);

  function buildCol(arr) {
    return arr.map(o => {
      let fila, corFila, bgRow;
      if(o.situacao === 'PRESO') {
        fila = 'PRESO'; corFila = 'color:red;font-weight:800'; bgRow = 'background:#fff0f0';
      } else if(o.situacao === 'AMOSTRAL') {
        fila = 'AMOSTRAL'; corFila = 'color:#b45309;font-weight:800'; bgRow = 'background:#fffbeb';
      } else {
        fila = o.posicao + (o.pos ? ' P.' + o.pos : ''); corFila = ''; bgRow = '';
      }
      return '<tr' + (bgRow ? ' style="' + bgRow + '"' : '') + '>' +
        '<td class="print-frota">' + o.frota + '</td>' +
        '<td class="print-fila-col" style="' + corFila + '">' + fila + '</td>' +
        '<td class="print-linha-col">' + (o.linha || '—') + '</td>' +
        '<td class="print-hora-col">' + (o.hora || '') + '</td>' +
        '</tr>';
    }).join('');
  }

  document.getElementById('print-meta').textContent =
    'Gerado em ' + now.toLocaleDateString('pt-BR') + ' às ' +
    now.toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'}) +
    '  ·  Total alocado: ' + todos.length + ' veículos';

  let html = '<div class="print-cols">';
  cols.forEach(col => {
    html += '<table class="print-table"><thead><tr><th>Veíc.</th><th>Fila</th><th>Linha</th><th>Hora</th></tr></thead><tbody>' + buildCol(col) + '</tbody></table>';
  });
  html += '</div>';

  if(state.presos.length || state.revisoes.length) {
    html += '<div class="print-secao">Alertas</div><div class="print-alertas">';
    state.presos.forEach(p => {
      html += '<div class="print-alerta-item"><b>' + p.frota + ' — PRESO</b><br>' + (p.motivo||'') + '</div>';
    });
    state.revisoes.forEach(r => {
      html += '<div class="print-alerta-item"><b>' + r.frota + ' — ' + r.tipo + '</b><br>' + (r.desc||'') + '</div>';
    });
    html += '</div>';
  }

  document.getElementById('print-content').innerHTML = html;
  window.print();
}
function abrirEdicaoChip(frota, filaKey, isEspecial) {
  // Encontra o item atual
  const lista = isEspecial ? state.especiais[filaKey] : state.filas[filaKey];
  const item = lista.find(x => x.frota === frota);
  if(!item) return;

  // Preenche campos
  document.getElementById('edit-frota-original').value = frota;
  document.getElementById('edit-fila-key').value = filaKey;
  document.getElementById('edit-is-especial').value = isEspecial ? '1' : '0';
  document.getElementById('edit-linha').value = item.linha || '';
  document.getElementById('modal-editar-chip-title').textContent = 'Veículo ' + frota;

  // Preenche datalist de linhas
  const dl = document.getElementById('lista-linhas-edit');
  if(dl) dl.innerHTML = state.linhas.map(l=>l.codigo||l).filter(Boolean).sort().map(l=>`<option value="${l}">`).join('');

  // Monta opções de posição
  let opts = '<option value="">— Manter posição atual —</option>';
  FILAS_NUM.forEach(f => { opts += `<option value="fila:${f}">Fila ${f}</option>`; });
  ESPECIAIS.forEach(e => { opts += `<option value="esp:${e.key}">${e.icon} ${e.label}</option>`; });
  document.getElementById('edit-nova-posicao').innerHTML = opts;

  openModal('modal-editar-chip');
}

function salvarEdicaoChip() {
  const frota = document.getElementById('edit-frota-original').value;
  const filaKey = document.getElementById('edit-fila-key').value;
  const isEspecial = document.getElementById('edit-is-especial').value === '1';
  const linha = document.getElementById('edit-linha').value.trim();
  const novaPosicao = document.getElementById('edit-nova-posicao').value;

  // Remove da posição atual
  if(isEspecial) state.especiais[filaKey] = state.especiais[filaKey].filter(x => x.frota !== frota);
  else state.filas[filaKey] = state.filas[filaKey].filter(x => x.frota !== frota);

  // Define nova posição
  const cadastro = state.frota.find(o => String(o.frota) === String(frota));
  const linhaFinal = linha || (cadastro && cadastro.linha) || '';
  const item = {frota, linha: linhaFinal};
  if(novaPosicao.startsWith('fila:')) {
    const f = novaPosicao.replace('fila:','');
    state.filas[f].push(item);
  } else if(novaPosicao.startsWith('esp:')) {
    const k = novaPosicao.replace('esp:','');
    state.especiais[k].push(item);
  } else {
    // Mantém posição atual
    if(isEspecial) state.especiais[filaKey].push(item);
    else state.filas[filaKey].push(item);
  }

  save(); renderAll(); closeModal('modal-editar-chip');
}

function removerChipAtual() {
  const frota = document.getElementById('edit-frota-original').value;
  const filaKey = document.getElementById('edit-fila-key').value;
  const isEspecial = document.getElementById('edit-is-especial').value === '1';
  if(!confirm(`Remover ônibus ${frota} desta posição?`)) return;
  if(isEspecial) state.especiais[filaKey] = state.especiais[filaKey].filter(x => x.frota !== frota);
  else state.filas[filaKey] = state.filas[filaKey].filter(x => x.frota !== frota);
  save(); renderAll(); closeModal('modal-editar-chip');
}

function alocarRapido() {
  const el = document.getElementById('rapido-carro');
  if(!el) return;
  const frota = String(el.value).trim();
  const elFila = document.getElementById('rapido-fila');
  const elLinha = document.getElementById('rapido-linha');
  const filaInput = elFila ? String(elFila.value).trim().toLowerCase() : '';
  const linha = elLinha ? String(elLinha.value).trim() : '';

  if(!frota){ alert('Informe o número do carro.'); return; }
  if(!filaInput){ alert('Informe a fila ou posição.'); return; }

  // Verifica se o carro está cadastrado
  if(!state.frota.find(o=>String(o.frota)===frota)){
    state.frota.push({frota});
    state.frota.sort((a,b)=>Number(a.frota)-Number(b.frota));
  }

  // Detecta se é fila numérica ou posição especial
  const espEncontrado = ESPECIAIS.find(e =>
    e.key === filaInput ||
    e.label.toLowerCase() === filaInput ||
    e.label.toLowerCase().startsWith(filaInput)
  );
  const filaNum = FILAS_NUM.find(f => f === filaInput);

  if(!espEncontrado && !filaNum){
    alert('Fila "'+filaInput+'" não encontrada. Use números de 1 a 33 ou: coqueiro, laje, lavador, bomba, eletricos, fundao');
    return;
  }

  // Remove de qualquer posição anterior
  FILAS_NUM.forEach(f=>{ state.filas[f]=state.filas[f].filter(x=>String(x.frota)!==frota); });
  ESPECIAIS.forEach(e=>{ state.especiais[e.key]=state.especiais[e.key].filter(x=>String(x.frota)!==frota); });

  // Salva linha nova se não existir
  _registrarLinha(linha);

  // Calcula próxima posição
  let proxPos = 1;
  if(espEncontrado) proxPos = (state.especiais[espEncontrado.key]||[]).length + 1;
  else proxPos = (state.filas[filaNum]||[]).length + 1;
  const item = {frota, linha, pos: proxPos};
  if(espEncontrado) state.especiais[espEncontrado.key].push(item);
  else state.filas[filaNum].push(item);

  // Limpa campos e foca no carro para próxima alocação
  ['rapido-carro','rapido-fila','rapido-linha'].forEach(id=>{ const el=document.getElementById(id); if(el) el.value=''; });
  const elFoco = document.getElementById('rapido-carro'); if(elFoco) elFoco.focus();

  save(); renderAll();
}

function popularDatalistsRapido() {
  popularSelectBlocoFila();
  // Filas
  const dlFila = document.getElementById('lista-filas-rapido');
  if(dlFila) {
    let opts = FILAS_NUM.map(f=>`<option value="${f}">Fila ${f}</option>`).join('');
    opts += ESPECIAIS.map(e=>`<option value="${e.key}">${e.label}</option>`).join('');
    dlFila.innerHTML = opts;
  }
  // Linhas
  const dlLinha = document.getElementById('lista-linhas-rapido');
  if(dlLinha) dlLinha.innerHTML = state.linhas.map(l=>l.codigo||l).filter(Boolean).sort().map(l=>`<option value="${l}">`).join('');
  const dlLinhaBloco = document.getElementById('lista-linhas-bloco');
  if(dlLinhaBloco) dlLinhaBloco.innerHTML = state.linhas.map(l=>l.codigo||l).filter(Boolean).sort().map(l=>`<option value="${l}">`).join('');
}

// ─── MODO BLOCO ──────────────────────────────────────────────────
let blocoSentido = 'ida';

function setSentido(s) {
  blocoSentido = s;
  document.getElementById('btn-ida').classList.toggle('active', s === 'ida');
  document.getElementById('btn-volta').classList.toggle('active', s === 'volta');
  atualizarStatusBloco();
}

function popularSelectBlocoFila() {
  const dl = document.getElementById('lista-filas-bloco');
  if(!dl) return;
  let opts = FILAS_NUM.map(f => `<option value="${f}">Fila ${f}</option>`).join('');
  opts += ESPECIAIS.map(e => `<option value="${e.key}">${e.label}</option>`).join('');
  opts += Object.entries(TIPOS_MANUT).map(([k,t]) => `<option value="manut:${k}">Manutenção ${t.label}</option>`).join('');
  dl.innerHTML = opts;
}

function atualizarStatusBloco() {
  const filaInput = (document.getElementById('bloco-fila-input')||{value:''}).value.trim();
  const status = document.getElementById('bloco-status');
  if(!status || !filaInput) { status.textContent = ''; return; }
  const filaVal = resolverFilaInput(filaInput);
  if(!filaVal) { status.textContent = ''; return; }
  const lista = getListaBloco(filaVal);
  const proxPos = blocoSentido === 'ida' ? lista.length + 1 : 1;
  const sentidoLabel = blocoSentido === 'ida' ? '→' : '←';
  status.textContent = filaInput + ' ' + sentidoLabel + ' · ' + lista.length + ' carros · próxima pos.' + proxPos;
}

function getListaBloco(filaVal) {
  if(!filaVal) return [];
  if(filaVal.startsWith('manut:')) {
    const tipo = filaVal.replace('manut:','');
    if(!state.manutencao) state.manutencao = [];
    return state.manutencao.filter(m => m.tipo === tipo);
  }
  if(filaVal.startsWith('esp:')) {
    const k = filaVal.replace('esp:','');
    if(!state.especiais[k]) state.especiais[k] = [];
    return state.especiais[k];
  }
  const f = filaVal.replace('fila:','');
  if(!state.filas[f]) state.filas[f] = [];
  return state.filas[f];
}

function resolverFilaInput(input) {
  const val = input.toLowerCase().trim();
  // Tenta fila numérica
  const filaNum = FILAS_NUM.find(f => f === val);
  if(filaNum) return 'fila:' + filaNum;
  // Tenta posição especial por key ou label
  const esp = ESPECIAIS.find(e =>
    e.key === val ||
    e.label.toLowerCase() === val ||
    e.label.toLowerCase().startsWith(val)
  );
  if(esp) return 'esp:' + esp.key;
  // Tenta manutenção
  if(val === 'manutencao' || val === 'manutenção' || val === 'manut') return 'manut:mecanica';
  const tipoManut = Object.entries(TIPOS_MANUT).find(([k, t]) =>
    k === val || t.label.toLowerCase() === val || t.label.toLowerCase().startsWith(val) ||
    ('manut:'+k) === val
  );
  if(tipoManut) return 'manut:' + tipoManut[0];
  return null;
}

function adicionarBloco() {
  const frota    = String(document.getElementById('bloco-carro').value).trim();
  const filaInput = document.getElementById('bloco-fila-input').value.trim();
  const linha    = String(document.getElementById('bloco-linha').value).trim();

  if (!frota) { document.getElementById('bloco-carro').focus(); return; }
  if (!filaInput) { alert('Informe a fila ou posição.'); document.getElementById('bloco-fila-input').focus(); return; }

  const filaVal = resolverFilaInput(filaInput);
  if (!filaVal) {
    alert('Posição "' + filaInput + '" não encontrada. Use: 1 a 33, coqueiro, laje, lavador, bomba, eletricos ou fundao');
    document.getElementById('bloco-fila-input').focus();
    return;
  }

  // ── Descobre onde o carro já está alocado (se estiver) ──────────
  let filaAtualLabel = null;
  for (const f of FILAS_NUM) {
    if ((state.filas[f] || []).some(x => String(x.frota) === frota)) {
      filaAtualLabel = 'fila ' + f; break;
    }
  }
  if (!filaAtualLabel) {
    for (const e of ESPECIAIS) {
      if ((state.especiais[e.key] || []).some(x => String(x.frota) === frota)) {
        filaAtualLabel = e.label || e.key; break;
      }
    }
  }

  const naFrota = !!state.frota.find(o => String(o.frota) === frota);

  // ── Função que executa a alocação após confirmações ─────────────
  const executar = () => _executarAlocarBloco(frota, filaVal, filaInput, linha, naFrota);

  // ── Verificação 1: carro já alocado em outra posição? ───────────
  if (filaAtualLabel) {
    confirmar(
      '🔄 Substituir alocação?',
      `Carro ${frota} já está na ${filaAtualLabel}. Deseja substituir?`,
      () => {
        // ── Verificação 2 (após confirmar substituição): não cadastrado? ──
        if (!naFrota) {
          confirmar(
            '➕ Cadastrar veículo?',
            `Carro ${frota} não está cadastrado na frota. Deseja cadastrar?`,
            executar   // Sim → cadastra e aloca
            // Não → cancela (sem onNao = fecha modal e não faz nada)
          );
        } else {
          executar();
        }
      }
      // Não substituir → cancela tudo
    );
    return;
  }

  // ── Verificação 2: não está na frota? ───────────────────────────
  if (!naFrota) {
    confirmar(
      '➕ Cadastrar veículo?',
      `Carro ${frota} não está cadastrado na frota. Deseja cadastrar?`,
      executar   // Sim → cadastra e aloca
      // Não → cancela
    );
    return;
  }

  // ── Caminho direto: tudo ok, aloca sem confirmação ──────────────
  executar();
}

function _executarAlocarBloco(frota, filaVal, filaInput, linha, naFrota) {
  // Cadastra na frota se não existir e usuário confirmou
  if (!naFrota) {
    state.frota.push({frota});
    state.frota.sort((a, b) => Number(a.frota) - Number(b.frota));
  }

  // Remove de qualquer posição anterior (garante sem duplicata)
  FILAS_NUM.forEach(f => { state.filas[f] = state.filas[f].filter(x => String(x.frota) !== frota); });
  ESPECIAIS.forEach(e => { state.especiais[e.key] = state.especiais[e.key].filter(x => String(x.frota) !== frota); });

  _registrarLinha(linha);

  const lista = getListaBloco(filaVal);

  // Insere na posição correta conforme sentido
  if (blocoSentido === 'ida') {
    lista.push({frota, linha, pos: lista.length + 1});
  } else {
    lista.unshift({frota, linha, pos: 1});
    lista.forEach((o, i) => o.pos = i + 1);
  }

  // Salva na estrutura correta
  if (filaVal.startsWith('manut:')) {
    const tipo = filaVal.replace('manut:', '');
    if (!state.manutencao) state.manutencao = [];
    state.manutencao = state.manutencao.filter(m => m.tipo !== tipo);
    lista.forEach(m => {
      if (!state.manutencao.find(x => String(x.frota) === String(m.frota) && x.tipo === tipo))
        state.manutencao.push({frota: m.frota, tipo, hora: m.hora || ''});
    });
  } else if (filaVal.startsWith('esp:')) {
    state.especiais[filaVal.replace('esp:', '')] = lista;
  } else {
    state.filas[filaVal.replace('fila:', '')] = lista;
  }

  // Atualiza barra de status do bloco
  const sentidoLabel = blocoSentido === 'ida' ? '→' : '←';
  const proxPos      = blocoSentido === 'ida' ? lista.length + 1 : 1;
  const statusEl     = document.getElementById('bloco-status');
  if (statusEl) statusEl.textContent = filaInput + ' ' + sentidoLabel + ' · ' + lista.length + ' carros · próxima pos.' + proxPos;

  document.getElementById('bloco-carro').value = '';
  document.getElementById('bloco-carro').focus();

  save(); renderAll();
  renderHistoricoBloco(filaVal);
}

function desfazerBloco() {
  const filaInput = document.getElementById('bloco-fila-input').value.trim();
  const filaVal = resolverFilaInput(filaInput);
  if(!filaVal) return;
  const lista = getListaBloco(filaVal);
  if(!lista.length) return;
  if(!confirm('Remover o último carro marcado (' + lista[lista.length-1].frota + ')?')) return;
  lista.pop();
  lista.forEach((o, i) => o.pos = i + 1);
  if(filaVal.startsWith('esp:')) state.especiais[filaVal.replace('esp:','')] = lista;
  else state.filas[filaVal.replace('fila:','')] = lista;
  save(); renderAll();
  atualizarStatusBloco();
  renderHistoricoBloco(filaVal);
}

function renderHistoricoBloco(filaVal) {
  const container = document.getElementById('bloco-historico');
  if(!container) return; // elemento removido da UI, sem impacto
  const lista = getListaBloco(filaVal);
  if(!lista.length) {
    container.innerHTML = '';
    return;
  }
  // Mostra em ordem reversa (último marcado no topo)
  container.innerHTML = [...lista].reverse().map((o, i) => `
    <div class="bloco-item">
      <span class="bloco-item-pos">Pos.${o.pos}</span>
      <span class="bloco-item-frota">${o.frota}</span>
      <span class="bloco-item-linha">${o.linha ? 'L.'+o.linha : '—'}</span>
      <button class="bloco-item-edit" onclick="editarItemBloco('${filaVal}',${o.pos})" title="Editar">✎</button>
    </div>`).join('');
}

function editarItemBloco(filaVal, pos) {
  if(!filaVal) return;
  const lista = getListaBloco(filaVal);
  const item = lista.find(o => o.pos === pos);
  if(!item) return;
  const novaFrota = prompt('Carro na posição '+pos+':', item.frota);
  if(!novaFrota) return;
  const novaLinha = prompt('Linha (deixe em branco para manter):', item.linha||'');
  item.frota = String(novaFrota).trim();
  item.linha = novaLinha !== null ? String(novaLinha).trim() : item.linha;
  if(filaVal.startsWith('esp:')) state.especiais[filaVal.replace('esp:','')] = lista;
  else state.filas[filaVal.replace('fila:','')] = lista;
  save(); renderAll();
  renderHistoricoBloco(filaVal);
}

// ─── AUTOCOMPLETE FILA ───────────────────────────────────────────
const AC_OPCOES = () => {
  const opts = [];
  FILAS_NUM.forEach(f => opts.push({
    label: 'Fila ' + f,
    value: f,
    count: (state.filas[f]||[]).length
  }));
  ESPECIAIS.forEach(e => opts.push({
    label: e.icon + ' ' + e.label,
    value: e.key,
    count: (state.especiais[e.key]||[]).length
  }));
  // Adiciona subtipos de manutenção
  Object.entries(TIPOS_MANUT).forEach(([key, t]) => opts.push({
    label: t.icon + ' Manutenção · ' + t.label,
    value: 'manut:' + key,
    count: (state.manutencao||[]).filter(m=>m.tipo===key).length
  }));
  return opts;
};

let acIdx = -1;

function acFiltrar(val) {
  const list = document.getElementById('ac-list');
  acIdx = -1;
  if(!val.trim()) { list.classList.remove('open'); atualizarStatusBloco(); return; }
  const q = val.toLowerCase();
  const filtradas = AC_OPCOES().filter(o =>
    o.label.toLowerCase().includes(q) ||
    o.value.toLowerCase().includes(q)
  );
  if(!filtradas.length) { list.classList.remove('open'); return; }
  list.innerHTML = filtradas.map((o, i) =>
    `<div class="autocomplete-item" data-value="${o.value}" onmousedown="acSelecionar('${o.value}','${o.label}')">
      ${o.label}<span class="ac-count">${o.count} ôn.</span>
    </div>`
  ).join('');
  list.classList.add('open');
}

function acSelecionar(value, label) {
  document.getElementById('bloco-fila-input').value = value;
  document.getElementById('ac-list').classList.remove('open');
  acIdx = -1;
  atualizarStatusBloco();
  document.getElementById('bloco-linha').focus();
}

function acKeydown(e) {
  const list = document.getElementById('ac-list');
  const items = list.querySelectorAll('.autocomplete-item');
  if(e.key === 'ArrowDown') {
    e.preventDefault();
    acIdx = Math.min(acIdx + 1, items.length - 1);
    items.forEach((el, i) => el.classList.toggle('selected', i === acIdx));
  } else if(e.key === 'ArrowUp') {
    e.preventDefault();
    acIdx = Math.max(acIdx - 1, 0);
    items.forEach((el, i) => el.classList.toggle('selected', i === acIdx));
  } else if(e.key === 'Enter') {
    if(acIdx >= 0 && items[acIdx]) {
      e.preventDefault();
      const val = items[acIdx].dataset.value;
      const label = items[acIdx].textContent.trim();
      acSelecionar(val, label);
    } else {
      list.classList.remove('open');
      document.getElementById('bloco-linha').focus();
    }
  } else if(e.key === 'Escape') {
    list.classList.remove('open');
  }
}

// Fecha autocomplete ao clicar fora
document.addEventListener('click', function(e) {
  const wrap = document.querySelector('.autocomplete-wrap');
  if(wrap && !wrap.contains(e.target)) {
    document.getElementById('ac-list').classList.remove('open');
  }
});

function exportarDados() {
  const dados = JSON.stringify(state, null, 2);
  const blob = new Blob([dados], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'patio_sambaiba_' + new Date().toLocaleDateString('pt-BR').replace(/\//g,'-') + '.json';
  a.click();
  URL.revokeObjectURL(url);
}

function importarDados(input) {
  const file = input.files[0];
  if(!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const dados = JSON.parse(e.target.result);
      if(!dados.frota) { alert('Arquivo inválido.'); return; }
      if(!confirm('Isso vai substituir todos os dados atuais. Confirma?')) return;
      state = dados;
      // Mesmas migrações do initState — garante consistência ao importar de outro dispositivo
      if(!state.linhas) state.linhas = [];
      state.linhas = state.linhas
        .map(l => typeof l === 'string'
          ? { codigo: l, descricao: '', setor: String(l).startsWith('1') ? 'E2' : 'AR2' }
          : l)
        .filter(l => l && l.codigo && typeof l.codigo === 'string' && l.codigo.trim() !== '');
      if(!state.frota) state.frota = [];
      if(!state.presos) state.presos = [];
      if(!state.revisoes) state.revisoes = [];
      if(!state.manutencao) state.manutencao = [];
      if(!state.filas) state.filas = {};
      if(!state.especiais) state.especiais = {};
      if(!state.escala) state.escala = { tipo:null, data:'', importadoEm:null, manobra:[], e2:[], ar2:[] };
      if(!state.escala.manobra) state.escala.manobra = [];
      if(!state.escala.e2) state.escala.e2 = [];
      if(!state.escala.ar2) state.escala.ar2 = [];
      FILAS_NUM.forEach(f => { state.filas[f] = migrarDados(state.filas[f] || []); });
      ESPECIAIS.forEach(esp => { state.especiais[esp.key] = migrarDados(state.especiais[esp.key] || []); });
      state.frota = state.frota.map(o => typeof o === 'string' ? {frota:o} : o).filter(o => o && o.frota);
      state.frota.sort((a,b) => Number(a.frota) - Number(b.frota));
      save();
      renderAll();
      alert('Dados importados com sucesso! ' + state.frota.length + ' veículos carregados.');
    } catch(err) {
      alert('Erro ao importar: ' + err.message);
    }
    input.value = '';
  };
  reader.readAsText(file);
}

// ─── IMPORTAR ESCALA ─────────────────────────────────────────────

function setEscalaTab(tab, el) {
  document.querySelectorAll('.escala-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.escala-panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('escala-panel-' + tab).classList.add('active');
}

// Detecta qual coluna é carro, linha e horário pelo conteúdo
function detectarColunas(linhas) {
  // Pega até 5 linhas de amostra para detectar
  const amostra = linhas.slice(0, 5).map(l => l.split(/	|;|,/).map(c => c.trim()));
  const ncols = Math.max(...amostra.map(r => r.length));
  let colCarro = -1, colLinha = -1, colHora = -1;

  for(let c = 0; c < ncols; c++) {
    const vals = amostra.map(r => r[c] || '').filter(v => v);
    // Carro: número de 4 dígitos
    if(colCarro < 0 && vals.some(v => /^\d{4}$/.test(v))) colCarro = c;
    // Hora: padrão HH:MM ou número como 4.30 ou 5:30
    if(colHora < 0 && vals.some(v => /^\d{1,2}[:h]\d{2}$/.test(v) || /^\d{1,2}\.\d{2}$/.test(v))) colHora = c;
    // Linha: alfanumérico como 4100, 271A, 119C
    if(colLinha < 0 && vals.some(v => /^\d{3,4}[A-Z]?$/.test(v) && !/^\d{4}$/.test(v))) colLinha = c;
  }
  // Se linha não detectada, pega a coluna restante
  if(colLinha < 0) {
    for(let c = 0; c < ncols; c++) {
      if(c !== colCarro && c !== colHora) { colLinha = c; break; }
    }
  }
  return {colCarro, colLinha, colHora};
}

function parsearLinhasEscala(texto) {
  const linhas = texto.split('\n').map(l => l.trim()).filter(l => l.length > 2);
  if(!linhas.length) return [];
  const {colCarro, colLinha, colHora} = detectarColunas(linhas);
  const STATUS_CONHECIDOS = ['PRESO','AMOSTRAL','MANUTENCAO','MANUTENÇÃO','EVENTO'];
  const resultado = [];
  for(const linha of linhas) {
    const cols = linha.split(/\t|;|,/).map(c => c.trim());
    const carro = colCarro >= 0 ? cols[colCarro] : '';
    if(!carro || !/^\d{3,4}$/.test(carro)) continue;

    // Verifica se alguma coluna contém um status conhecido
    let status = '';
    let linhaBus = '';
    let hora = '';

    for(const col of cols) {
      const upper = col.toUpperCase().trim();
      // Só aceita status se for texto exato — evita falsos positivos
      if(STATUS_CONHECIDOS.includes(upper)) {
        status = upper === 'MANUTENCAO' || upper === 'MANUTENÇÃO' ? 'manutencao' : upper.toLowerCase();
        continue;
      }
      // Hora: padrão H:MM ou HH:MM
      if(/^\d{1,2}[:h]\d{2}$/.test(col) || /^\d{1,2}\.\d{2}$/.test(col)) {
        hora = col; continue;
      }
      // Linha: alfanumérico tipo 178T, 271C, 1156
     if(col !== carro && /^\d{3,4}[A-Z]?$/.test(col)) {
        linhaBus = col; continue;
      }
    }

    resultado.push({carro, linha: linhaBus, hora, status});
  }
  return resultado;
}

function previewEscalaColar() {
  const texto = document.getElementById('escala-texto').value;
  const dados = parsearLinhasEscala(texto);
  const preview = document.getElementById('escala-preview-colar');
  if(!dados.length) { preview.style.display = 'none'; return; }
  preview.style.display = 'block';
  preview.innerHTML = '<div style="font-size:11px;color:var(--muted);margin-bottom:6px">' + dados.length + ' veículos detectados:</div>' +
    dados.slice(0, 8).map(d =>
      '<div class="escala-preview-item">' +
      '<span class="ep-carro">' + d.carro + '</span>' +
      '<span class="ep-linha">' + (d.linha || '—') + '</span>' +
      '<span class="ep-hora">' + (d.hora || '') + '</span>' +
      '</div>'
    ).join('') +
    (dados.length > 8 ? '<div style="color:var(--muted);font-size:11px;padding:4px 0">... e mais ' + (dados.length-8) + ' veículos</div>' : '');
}

function aplicarEscalaColar() {
  const texto = document.getElementById('escala-texto').value;
  const dados = parsearLinhasEscala(texto);
  if(!dados.length) { alert('Nenhum dado reconhecido. Verifique o formato.'); return; }
  closeModal('modal-escala');
  verificarEAplicarEscala(dados);
}

// ─── DETECÇÃO AUTOMÁTICA DE TIPO DE ESCALA ───────────────────────
function detectarTipoEscala(rawLinhas) {
  // Detecta se é Manobra ou Plantão (E2+AR2).
  // A separação E2 / AR2 é sempre feita pelo prefixo do número do veículo:
  //   1xxx → E2 (Centro) | 2xxx → AR2 (Bairro)
  const texto = rawLinhas.slice(0, 8).flat().join(' ').toUpperCase();
  // Palavras-chave de plantão no cabeçalho
  if (texto.includes('AR2') || texto.includes('(E2)') ||
      texto.includes('E2 ') || texto.includes(' E2') ||
      texto.includes('PLANTÃO') || texto.includes('PLANTAO')) {
    return 'plantao';
  }
  // Fallback: se houver veículos com 4 dígitos (1xxx ou 2xxx) é plantão
  const nums = rawLinhas.flat()
    .map(v => parseInt(String(v).trim()))
    .filter(v => !isNaN(v) && v >= 1000 && v < 10000);
  if (nums.length) return 'plantao';
  return 'manobra';
}

function calcularStatsEscala(dados) {
  const total = dados.length;
  const presos = dados.filter(d =>
    (d.status && d.status.toLowerCase() === 'preso') ||
    (d.linha && d.linha.toUpperCase() === 'PRESO')
  ).length;
  const escalados = dados.filter(d => d.hora && d.hora.trim() !== '' && !( (d.status && d.status.toLowerCase() === 'preso') || (d.linha && d.linha.toUpperCase() === 'PRESO') )).length;
  const reserva = total - escalados - presos;
  return { total, escalados, presos, reserva };
}

// ─── PARSERS POR TIPO DE ESCALA ───────────────────────────────────
function parsearManobra(dados) {
  return dados.map(d => {
    const isPreso = (d.status && d.status.toLowerCase() === 'preso') ||
                    (d.linha && d.linha.toUpperCase() === 'PRESO');
    const status = isPreso ? 'preso' : (d.hora && d.hora.trim() ? 'escalado' : 'reserva');
    return {
      carro: String(d.carro),
      hora: isPreso ? '' : (d.hora || ''),
      linha: isPreso ? '' : (d.linha || ''),
      status
    };
  }).sort((a, b) => Number(a.carro) - Number(b.carro));
}

function parsearPlantao(dados) {
  // Agrupa por linha, ordena por horário dentro de cada grupo
  const toMin = h => { if(!h||!h.trim()) return 9999; const p=h.split(':'); return parseInt(p[0])*60+(parseInt(p[1])||0); };
  const porLinha = {};
  dados
    .filter(d => d.carro && d.linha && d.linha.toUpperCase() !== 'PRESO' && String(d.carro).trim())
    .forEach(d => {
      const linha = String(d.linha).trim();
      if (!porLinha[linha]) porLinha[linha] = [];
      porLinha[linha].push({ carro: String(d.carro), hora: d.hora || '', mot: '', cob: '' });
    });
  return Object.entries(porLinha)
    .map(([linha, veiculos]) => ({
      linha,
      tabelas: veiculos.length,
      veiculos: veiculos.sort((a, b) => toMin(a.hora) - toMin(b.hora))
    }))
    .sort((a, b) => a.linha.localeCompare(b.linha, 'pt-BR', { numeric:true }));
}

// ─── MODAL DE PREVIEW DE ESCALA ───────────────────────────────────
function abrirPreviewEscala(dados, rawLinhas) {
  const tipo = detectarTipoEscala(rawLinhas);
  const stats = calcularStatsEscala(dados);
  window._escalaTemp = dados;
  window._tipoEscalaTemp = tipo;

  // Conta quantos veículos vão para cada setor (pelo prefixo)
  const countE2  = dados.filter(d => String(d.carro).startsWith('1')).length;
  const countAR2 = dados.filter(d => String(d.carro).startsWith('2')).length;

  // Badge do tipo detectado
  const isManobra = tipo === 'manobra';
  const nomeBadge = isManobra ? '📋 ESCALA DE MANOBRA' : '🚍 E2 + 🚌 AR2 — SEPARAÇÃO AUTOMÁTICA';
  const corBadge  = isManobra ? 'var(--accent4)' : '#1a3a6b';
  document.getElementById('preview-tipo-badge').innerHTML =
    `<div style="background:${corBadge};color:#fff;border-radius:8px;padding:10px 14px;font-family:var(--mono);font-size:13px;font-weight:800;letter-spacing:1px">${nomeBadge}</div>` +
    (!isManobra && (countE2 || countAR2) ? `<div style="display:flex;gap:8px;margin-top:8px">
      <span style="background:#1a3a6b;color:#fff;border-radius:6px;padding:4px 10px;font-size:12px;font-family:var(--mono)">🚍 E2: ${countE2} veíc.</span>
      <span style="background:var(--accent);color:#fff;border-radius:6px;padding:4px 10px;font-size:12px;font-family:var(--mono)">🚌 AR2: ${countAR2} veíc.</span>
    </div>` : '');

  // Stats em mini-cards
  document.getElementById('preview-stats').innerHTML =
    `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px">
      <div class="stat-box"><div class="stat-num" style="font-size:18px">${stats.total}</div><div class="stat-label">Total</div></div>
      <div class="stat-box"><div class="stat-num" style="font-size:18px;color:var(--accent3)">${stats.escalados}</div><div class="stat-label">Escalados</div></div>
      <div class="stat-box"><div class="stat-num" style="font-size:18px;color:var(--accent2)">${stats.presos}</div><div class="stat-label">Presos</div></div>
      <div class="stat-box"><div class="stat-num" style="font-size:18px;color:var(--muted)">${stats.reserva}</div><div class="stat-label">Reserva</div></div>
    </div>`;

  // Seleciona o tipo detectado no dropdown
  document.getElementById('preview-tipo-select').value = tipo;
  openModal('modal-preview-escala');
}

function confirmarPreviewEscala() {
  const dados = window._escalaTemp;
  if (!dados || !dados.length) return;
  const tipo = document.getElementById('preview-tipo-select').value;
  window._tipoEscalaTemp = tipo;
  closeModal('modal-preview-escala');
  window._escalaTemp = null;
  verificarEAplicarEscala(dados, tipo);
}

// ─── RENDERS DE ESCALA POR SETOR ─────────────────────────────────
let _manobraFiltro = 'todos';
function filtrarManobra(filtro, btn) {
  _manobraFiltro = filtro;
  document.querySelectorAll('.manobra-filtro').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderManobra();
}

function abrirManobra() {
  renderManobra();
  openModal('modal-manobra');
}

function renderManobra() {
  const grid    = document.getElementById('manobra-grid');
  const statsEl = document.getElementById('manobra-stats');
  const vaziEl  = document.getElementById('manobra-vazio');
  if (!grid) return;

  const dados = state.escala && state.escala.tipo === 'manobra' && state.escala.manobra.length
    ? state.escala.manobra : null;

  if (!dados) {
    grid.innerHTML = '';
    if (statsEl) statsEl.innerHTML = '';
    if (vaziEl) vaziEl.style.display = 'block';
    return;
  }
  if (vaziEl) vaziEl.style.display = 'none';

  // Stats banner
  const tot = dados.length;
  const esc = dados.filter(d => d.status === 'escalado').length;
  const pre = dados.filter(d => d.status === 'preso').length;
  const res = dados.filter(d => d.status === 'reserva').length;
  if (statsEl) statsEl.innerHTML =
    `<div class="manobra-stats-bar">
      <span class="ms-item total">${tot} frota</span>
      <span class="ms-item escalado">🟢 ${esc}</span>
      <span class="ms-item preso">🔴 ${pre}</span>
      <span class="ms-item reserva">⚫ ${res}</span>
      ${state.escala.data ? '<span class="ms-item data">📅 ' + state.escala.data + '</span>' : ''}
    </div>`;

  // Grid filtrado
  const visíveis = _manobraFiltro === 'todos' ? dados : dados.filter(d => d.status === _manobraFiltro);
  if (!visíveis.length) { grid.innerHTML = '<div class="empty" style="padding:20px 0">Nenhum veículo neste filtro.</div>'; return; }

  grid.innerHTML = visíveis.map(d => {
    const cls   = `manobra-card ${d.status}`;
    const info  = d.status === 'escalado' ? d.hora : d.status === 'preso' ? 'PRESO' : '—';
    const linha = d.linha ? `<div class="manobra-linha">${d.linha}</div>` : '';
    return `<div class="${cls}" onclick="detalheManobra('${d.carro}')">
      <div class="manobra-num">${d.carro}</div>
      <div class="manobra-hora">${info}</div>
      ${linha}
    </div>`;
  }).join('');
}

function detalheManobra(carro) {
  const item = (state.escala.manobra || []).find(d => d.carro === String(carro));
  if (!item) return;
  const st = { escalado:'🟢 Escalado', preso:'🔴 PRESO', reserva:'⚫ Reserva' };
  alert(`Carro ${item.carro}\n${st[item.status] || item.status}${item.hora ? '\nHorário: ' + item.hora : ''}${item.linha ? '\nLinha: ' + item.linha : ''}`);
}

function renderPlantaoE2() {
  const container = document.getElementById('e2-container');
  const metaEl    = document.getElementById('e2-meta');
  if (!container) return;
  const dados = state.escala && state.escala.e2 && state.escala.e2.length ? state.escala.e2 : null;
  if (!dados) {
    container.innerHTML = '<div class="empty" style="padding:30px 0"><div class="empty-icon">🚍</div>Nenhuma escala E2 importada.<br>Use ⋮ → Importar Escala e selecione um arquivo de Plantão E2.</div>';
    if (metaEl) metaEl.textContent = 'Nenhuma escala importada';
    return;
  }
  const total = dados.reduce((s, g) => s + g.tabelas, 0);
  if (metaEl) metaEl.textContent = `${total} veículos · ${dados.length} linhas · ${state.escala.data || ''}`;
  _renderPlantaoGrupos(container, dados, 'e2');
}

function renderPlantaoAR2() {
  const container = document.getElementById('ar2-container');
  const metaEl    = document.getElementById('ar2-meta');
  if (!container) return;
  const dados = state.escala && state.escala.ar2 && state.escala.ar2.length ? state.escala.ar2 : null;
  if (!dados) {
    container.innerHTML = '<div class="empty" style="padding:30px 0"><div class="empty-icon">🚌</div>Nenhuma escala AR2 importada.<br>Use ⋮ → Importar Escala e selecione um arquivo de Plantão AR2.</div>';
    if (metaEl) metaEl.textContent = 'Nenhuma escala importada';
    return;
  }
  const total = dados.reduce((s, g) => s + g.tabelas, 0);
  if (metaEl) metaEl.textContent = `${total} veículos · ${dados.length} linhas · ${state.escala.data || ''}`;
  _renderPlantaoGrupos(container, dados, 'ar2');
}

function _renderPlantaoGrupos(container, grupos, setor) {
  const filtro = (document.getElementById(`search-${setor}`) || {}).value || '';
  const fl = filtro.trim().toLowerCase();
  const visiveis = fl
    ? grupos.filter(g => g.linha.toLowerCase().includes(fl) || g.veiculos.some(v => v.carro.includes(fl)))
    : grupos;

  if (!visiveis.length) {
    container.innerHTML = '<div class="empty" style="padding:20px 0">Nenhum resultado encontrado.</div>';
    return;
  }

  container.innerHTML = visiveis.map(g => {
    const id = `plantao-${setor}-${g.linha}`;
    const rows = fl
      ? g.veiculos.filter(v => v.carro.includes(fl) || g.linha.toLowerCase().includes(fl))
      : g.veiculos;
    return `<div class="plantao-grupo">
      <div class="plantao-grupo-header" onclick="togglePlantaoGrupo('${id}')">
        <div style="display:flex;align-items:center;gap:10px">
          <span class="plantao-linha-badge">${g.linha}</span>
          <span class="plantao-tabelas">${g.tabelas} tabelas</span>
        </div>
        <span class="plantao-chevron" id="chev-${id}">▼</span>
      </div>
      <div class="plantao-grupo-body" id="${id}">
        <div class="plantao-row plantao-row-header">
          <span>Carro</span><span>Hora</span><span>Motorista</span><span>Cobrador</span>
        </div>
        ${rows.map((v, i) => `
          <div class="plantao-row ${i%2===0?'':'par'}">
            <span class="plantao-carro">${v.carro}</span>
            <span class="plantao-hora">${v.hora || '—'}</span>
            <input class="plantao-input" placeholder="MOT" value="${v.mot||''}"
              oninput="salvarMOT('${setor}','${g.linha}',${i},this.value)">
            <input class="plantao-input" placeholder="COB" value="${v.cob||''}"
              oninput="salvarCOB('${setor}','${g.linha}',${i},this.value)">
          </div>`).join('')}
      </div>
    </div>`;
  }).join('');
}

function togglePlantaoGrupo(id) {
  const el = document.getElementById(id);
  const chev = document.getElementById('chev-' + id);
  if (!el) return;
  const aberto = el.style.display !== 'none';
  el.style.display = aberto ? 'none' : 'block';
  if (chev) chev.textContent = aberto ? '▶' : '▼';
}

function filtrarPlantao(setor) {
  if (setor === 'e2') renderPlantaoE2();
  else renderPlantaoAR2();
}

function salvarMOT(setor, linha, idx, val) {
  const g = (state.escala[setor] || []).find(x => x.linha === linha);
  if (g && g.veiculos[idx]) { g.veiculos[idx].mot = val; save(); }
}
function salvarCOB(setor, linha, idx, val) {
  const g = (state.escala[setor] || []).find(x => x.linha === linha);
  if (g && g.veiculos[idx]) { g.veiculos[idx].cob = val; save(); }
}

// ─── VERIFICAÇÃO DE NOVOS VEÍCULOS ANTES DE APLICAR ESCALA ──────
function verificarEAplicarEscala(dados, tipo) {
  const frotaSet = new Set(state.frota.map(o => String(o.frota)));

  // Filtra veículos que não estão na frota (ignora PRESO pois já serão tratados)
  const naoNaCadastro = dados.filter(d => {
    const frota = String(d.carro);
    const isPreso = (d.status && d.status.toLowerCase() === 'preso') ||
                    (d.linha && d.linha.toUpperCase() === 'PRESO');
    return !frotaSet.has(frota) && !isPreso && frota && frota !== 'NaN';
  });

  if (naoNaCadastro.length === 0) {
    // Todos conhecidos — aplica direto sem passar pela confirmação
    aplicarEscala(dados, new Set(), tipo);
    return;
  }

  // Guarda os dados para usar após confirmação
  window._escalaParaAplicar = dados;
  window._tipoEscalaParaAplicar = tipo;

  // Monta lista no modal
  const lista = document.getElementById('lista-novos-veiculos');
  lista.innerHTML = naoNaCadastro.map(d => `
    <label class="novo-veiculo-item">
      <input type="checkbox" class="novo-veiculo-check" value="${d.carro}" checked>
      <span class="novo-veiculo-num">${d.carro}</span>
      <span class="novo-veiculo-info">${d.linha && d.linha.toUpperCase() !== 'PRESO' ? d.linha : '—'}${d.hora ? ' · ' + d.hora : ''}</span>
    </label>
  `).join('');

  document.getElementById('novos-count').textContent = naoNaCadastro.length;
  openModal('modal-novos-veiculos');
}

function selecionarTodosNovos(marcar) {
  document.querySelectorAll('.novo-veiculo-check').forEach(c => c.checked = marcar);
}

function confirmarNovosVeiculos() {
  const dados = window._escalaParaAplicar;
  if (!dados) return;

  // Coleta quais o usuário NÃO quer cadastrar
  const naoCadastrar = new Set();
  document.querySelectorAll('.novo-veiculo-check').forEach(c => {
    if (!c.checked) naoCadastrar.add(c.value);
  });

  const tipo = window._tipoEscalaParaAplicar || null;
  closeModal('modal-novos-veiculos');
  window._escalaParaAplicar = null;
  window._tipoEscalaParaAplicar = null;
  aplicarEscala(dados, naoCadastrar, tipo);
}

// ─────────────────────────────────────────────────────────────────
function aplicarEscala(dados, naoCadastrar = new Set(), tipo = null) {
  let novos = 0, atualizados = 0, presos = 0, ignorados = 0;
  dados.forEach(d => {
    const frota = String(d.carro);
    const isPreso = (d.status && d.status.toLowerCase() === 'preso') || (d.linha && d.linha.toUpperCase() === 'PRESO');
    const linha = isPreso ? '' : (d.linha || '');

    // Cadastra ou atualiza na frota
    const existente = state.frota.find(o => String(o.frota) === frota);
    if(!existente) {
      // Veículo não cadastrado: respeita a decisão do usuário
      if(naoCadastrar.has(frota)) {
        ignorados++;
        return; // pula sem cadastrar nem alocar
      }
      state.frota.push({frota, linha, hora: d.hora, status: d.status||''});
      novos++;
    } else {
      if(!isPreso) existente.linha = linha;
      existente.hora = d.hora;
      existente.status = d.status || existente.status || '';
      atualizados++;
    }

    // Registra como PRESO automaticamente
    if(isPreso) {
      const jaExiste = state.presos.find(p => String(p.frota) === frota);
      if(!jaExiste) {
        state.presos.push({
          frota,
          motivo: 'Registrado via escala',
          hora: new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})
        });
        presos++;
      }
    }

    // Atualiza linha nas filas onde o carro já está alocado
    if(linha) {
      FILAS_NUM.forEach(f => {
        const item = (state.filas[f]||[]).find(x => String(x.frota) === frota);
        if(item) item.linha = linha;
      });
      ESPECIAIS.forEach(e => {
        const item = (state.especiais[e.key]||[]).find(x => String(x.frota) === frota);
        if(item) item.linha = linha;
      });
      _registrarLinha(linha);
    }
  });
  state.frota.sort((a,b) => Number(a.frota) - Number(b.frota));

  // Grava na estrutura state.escala de acordo com o tipo detectado
  if (tipo) {
    const data = new Date().toLocaleDateString('pt-BR');
    const dadosValidos = dados.filter(d => !naoCadastrar.has(String(d.carro)));
    state.escala.data = data;
    state.escala.importadoEm = new Date().toISOString();
    if (tipo === 'manobra') {
      state.escala.tipo = 'manobra';
      state.escala.manobra = parsearManobra(dadosValidos);
      state.escala.e2  = [];
      state.escala.ar2 = [];
    } else {
      // Plantão (E2 ou AR2): divide sempre pelo prefixo do número do veículo
      // Carros 1xxx → E2 (Centro) | Carros 2xxx → AR2 (Bairro)
      const dadosE2  = dadosValidos.filter(d => String(d.carro).startsWith('1'));
      const dadosAR2 = dadosValidos.filter(d => String(d.carro).startsWith('2'));
      state.escala.tipo    = 'plantao';
      state.escala.manobra = [];
      state.escala.e2      = parsearPlantao(dadosE2);
      state.escala.ar2     = parsearPlantao(dadosAR2);
    }
  }

  save(); renderAll();

  // Monta resumo final
  let resumo = dados.length + ' veículos processados!\n'
    + novos + ' novos cadastrados\n'
    + atualizados + ' atualizados\n'
    + presos + ' marcados como PRESO';
  if (ignorados > 0) resumo += '\n' + ignorados + ' ignorados (não cadastrados)';
  alert(resumo);
}

// Importar Excel/CSV — com detecção automática de tipo
function importarExcel(input) {
  const file = input.files[0];
  if(!file) return;
  const reader = new FileReader();

  if(file.name.endsWith('.csv')) {
    reader.onload = function(e) {
      const texto = e.target.result;
      const rawLinhas = texto.split('\n').map(l => l.split(','));
      const dados = parsearLinhasEscala(texto);
      if(!dados.length) { alert('Nenhum dado reconhecido no CSV.'); return; }
      closeModal('modal-escala');
      abrirPreviewEscala(dados, rawLinhas);
    };
    reader.readAsText(file, 'UTF-8');
  } else {
    // XLSX — usa SheetJS via CDN
    reader.onload = function(e) {
      try {
        const data = new Uint8Array(e.target.result);
        const wb = XLSX.read(data, {type: 'array'});
        const ws = wb.Sheets[wb.SheetNames[0]];
        // Lê como array de arrays para detecção
        const rawLinhas = XLSX.utils.sheet_to_json(ws, {header:1, defval:''});
        const csv = XLSX.utils.sheet_to_csv(ws);
        const dados = parsearLinhasEscala(csv);
        if(!dados.length) { alert('Nenhum dado reconhecido. Verifique as colunas.'); return; }
        closeModal('modal-escala');
        abrirPreviewEscala(dados, rawLinhas);
      } catch(err) {
        alert('Erro ao ler Excel: ' + err.message + '\nTente salvar como CSV e importar novamente.');
      }
    };
    reader.readAsArrayBuffer(file);
  }
  input.value = '';
}

// ─── SUBMENU ─────────────────────────────────────────────────────
// ─── MANUTENÇÃO ──────────────────────────────────────────────────
const TIPOS_MANUT = {
  eletrica:       {label:'Elétrica',      icon:'⚡', cor:'#06b6d4'},
  funilaria:      {label:'Funilaria',     icon:'🔨', cor:'#f97316'},
  mecanica:       {label:'Mecânica',      icon:'🔧', cor:'#f97316'},
  'ar-condicionado': {label:'Ar-cond.',   icon:'❄️', cor:'#3b82f6'},
};

function renderManutencao() {
  const container = document.getElementById('manutencao-container');
  if(!container) return;

  // Agrupa por tipo
  const grupos = {};
  Object.keys(TIPOS_MANUT).forEach(t => { grupos[t] = []; });
  (state.manutencao||[]).forEach(m => {
    if(grupos[m.tipo]) grupos[m.tipo].push(m);
  });

  const chips = (state.manutencao||[]).map(m => {
    const t = TIPOS_MANUT[m.tipo] || {label: m.tipo, icon:'🔧', cor:'#f97316'};
    return `<div class="chip" onclick="removerManutencao('${m.frota}','${m.tipo}')">
      <div style="display:flex;flex-direction:column;gap:2px">
        <span>${m.frota}</span>
        <span style="font-size:10px;color:${t.cor};font-weight:700">${t.icon} ${t.label}</span>
      </div>
      <span style="font-size:13px;color:var(--muted)">×</span>
    </div>`;
  }).join('');

  container.innerHTML = `<div class="card" style="border-top:3px solid #f97316">
    <div class="card-header">
      <div style="display:flex;align-items:center;gap:10px">
        <div class="fila-num">🔧</div>
        <div class="fila-label">Manutenção</div>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="fila-count">${(state.manutencao||[]).length} ôn.</span>
        <button class="btn btn-ghost btn-sm" onclick="openModal('modal-manutencao')">+ Adicionar</button>
      </div>
    </div>
    <div class="chips">${chips||'<span style="color:var(--muted);font-size:13px;padding:4px 0">Vazia</span>'}</div>
  </div>`;
}

function adicionarManutencao() {
  const frota = String(document.getElementById('input-frota-manut').value).trim();
  const tipo  = document.getElementById('select-tipo-manut').value;
  if(!frota) { document.getElementById('input-frota-manut').focus(); return; }
  if(!state.manutencao) state.manutencao = [];
  // Evita duplicata do mesmo carro no mesmo tipo
  if(state.manutencao.find(m => String(m.frota)===frota && m.tipo===tipo)) {
    alert('Este veículo já está nessa categoria de manutenção.');
    return;
  }
  // Cadastra se não existir
  if(!state.frota.find(o=>String(o.frota)===frota)) {
    state.frota.push({frota});
    state.frota.sort((a,b)=>Number(a.frota)-Number(b.frota));
  }
  state.manutencao.push({frota, tipo, hora: new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})});
  document.getElementById('input-frota-manut').value = '';
  save(); renderAll(); closeModal('modal-manutencao');
}

function removerManutencao(frota, tipo) {
  if(!confirm(`Remover ônibus ${frota} da manutenção (${TIPOS_MANUT[tipo]?.label||tipo})?`)) return;
  state.manutencao = (state.manutencao||[]).filter(m=>!(String(m.frota)===String(frota) && m.tipo===tipo));
  save(); renderAll();
}

// Autocomplete de frota no modal manutenção
let acFrotaManutIdx = -1;
function acFrotaManutFiltrar(val) {
  const list = document.getElementById('ac-frota-manut-list');
  acFrotaManutIdx = -1;
  if(!val || val.length < 1) { list.classList.remove('open'); return; }
  const filtradas = state.frota.filter(o=>String(o.frota).startsWith(String(val))).slice(0,8);
  if(!filtradas.length) { list.classList.remove('open'); return; }
  list.innerHTML = filtradas.map(o =>
    `<div class="autocomplete-item" data-value="${o.frota}" onmousedown="acFrotaManutSelecionar('${o.frota}')">
      <span style="font-weight:800">${o.frota}</span>
      <span class="ac-count">${o.hora?'· '+o.hora:''}</span>
    </div>`
  ).join('');
  list.classList.add('open');
}
function acFrotaManutSelecionar(frota) {
  document.getElementById('input-frota-manut').value = frota;
  document.getElementById('ac-frota-manut-list').classList.remove('open');
  document.getElementById('select-tipo-manut').focus();
}
function acFrotaManutKeydown(e) {
  const list = document.getElementById('ac-frota-manut-list');
  const items = list.querySelectorAll('.autocomplete-item');
  if(e.key==='ArrowDown'){e.preventDefault();acFrotaManutIdx=Math.min(acFrotaManutIdx+1,items.length-1);items.forEach((el,i)=>el.classList.toggle('selected',i===acFrotaManutIdx));}
  else if(e.key==='ArrowUp'){e.preventDefault();acFrotaManutIdx=Math.max(acFrotaManutIdx-1,0);items.forEach((el,i)=>el.classList.toggle('selected',i===acFrotaManutIdx));}
  else if(e.key==='Enter'){if(acFrotaManutIdx>=0&&items[acFrotaManutIdx]){e.preventDefault();acFrotaManutSelecionar(items[acFrotaManutIdx].dataset.value);}else{list.classList.remove('open');document.getElementById('select-tipo-manut').focus();}}
  else if(e.key==='Escape'){list.classList.remove('open');}
}

// ─── AUTOCOMPLETE FROTA NO MODAL ALOCAR ─────────────────────────
let acFrotaIdx = -1;

function acFrotaFiltrar(val) {
  const list = document.getElementById('ac-frota-list');
  acFrotaIdx = -1;
  if(!val || val.length < 1) { list.classList.remove('open'); return; }
  const q = String(val);
  const filtradas = state.frota
    .filter(o => String(o.frota).startsWith(q))
    .slice(0, 8);
  if(!filtradas.length) { list.classList.remove('open'); return; }
  list.innerHTML = filtradas.map(o => {
    const hora = o.hora ? ' · ' + o.hora : '';
    const linha = o.linha ? ' · L.' + o.linha : '';
    return `<div class="autocomplete-item" data-value="${o.frota}" onmousedown="acFrotaSelecionar('${o.frota}')">
      <span style="font-weight:800">${o.frota}</span>
      <span class="ac-count">${hora}${linha}</span>
    </div>`;
  }).join('');
  list.classList.add('open');
}

function acFrotaSelecionar(frota) {
  const input = document.getElementById('input-frota-alocar');
  if(input) input.value = frota;
  document.getElementById('ac-frota-list').classList.remove('open');
  acFrotaIdx = -1;
  // Preenche linha automaticamente se o carro tiver linha na escala
  const cadastro = state.frota.find(o => String(o.frota) === String(frota));
  if(cadastro && cadastro.linha) {
    const inputLinha = document.getElementById('input-linha-alocar');
    if(inputLinha && !inputLinha.value) inputLinha.value = cadastro.linha;
  }
  document.getElementById('input-linha-alocar').focus();
}

function acFrotaKeydown(e) {
  const list = document.getElementById('ac-frota-list');
  const items = list.querySelectorAll('.autocomplete-item');
  if(e.key === 'ArrowDown') {
    e.preventDefault();
    acFrotaIdx = Math.min(acFrotaIdx + 1, items.length - 1);
    items.forEach((el, i) => el.classList.toggle('selected', i === acFrotaIdx));
  } else if(e.key === 'ArrowUp') {
    e.preventDefault();
    acFrotaIdx = Math.max(acFrotaIdx - 1, 0);
    items.forEach((el, i) => el.classList.toggle('selected', i === acFrotaIdx));
  } else if(e.key === 'Enter') {
    if(acFrotaIdx >= 0 && items[acFrotaIdx]) {
      e.preventDefault();
      acFrotaSelecionar(items[acFrotaIdx].dataset.value);
    } else {
      list.classList.remove('open');
      document.getElementById('input-linha-alocar').focus();
    }
  } else if(e.key === 'Escape') {
    list.classList.remove('open');
  }
}

// Fecha autocomplete de frota ao clicar fora
document.addEventListener('click', function(e) {
  const input = document.getElementById('input-frota-alocar');
  const list = document.getElementById('ac-frota-list');
  if(list && input && !input.contains(e.target) && !list.contains(e.target)) {
    list.classList.remove('open');
  }
});

function toggleSubmenu(id) {
  const el = document.getElementById(id);
  const arrow = document.getElementById('arrow-' + id);
  const isOpen = el.classList.contains('open');
  // Fecha todos os submenus
  document.querySelectorAll('.menu-dots-submenu').forEach(s => s.classList.remove('open'));
  document.querySelectorAll('.menu-dots-arrow').forEach(a => a.classList.remove('open'));
  if(!isOpen) {
    el.classList.add('open');
    if(arrow) arrow.classList.add('open');
  }
}

// ─── ZERAR PÁTIO ─────────────────────────────────────────────────
function zerarPatio() {
  if(!confirm('Zerar Pátio: remove todos os ônibus das filas e posições. Os cadastros de frota e escala são mantidos. Confirma?')) return;
  FILAS_NUM.forEach(f => { state.filas[f] = []; });
  ESPECIAIS.forEach(e => { state.especiais[e.key] = []; });
  state.manutencao = [];
  save(); renderAll();
  alert('Pátio zerado com sucesso!');
}

// ─── ZERAR ESCALA ─────────────────────────────────────────────────
function zerarEscala() {
  if(!confirm('Zerar Escala: remove linhas, horários e status de todos os veículos. As alocações no pátio são mantidas. Confirma?')) return;
  state.frota.forEach(o => { o.linha = ''; o.hora = ''; o.status = ''; });
  state.presos = [];
  state.revisoes = [];
  state.escala = { tipo:null, data:'', importadoEm:null, manobra:[], e2:[], ar2:[] };
  // Limpa linha nas filas também
  FILAS_NUM.forEach(f => { (state.filas[f]||[]).forEach(o => { o.linha = ''; }); });
  ESPECIAIS.forEach(e => { (state.especiais[e.key]||[]).forEach(o => { o.linha = ''; }); });
  save(); renderAll();
  alert('Escala zerada com sucesso!');
}

// ─── VER ESCALA COMPLETA ─────────────────────────────────────────
function abrirEscalaCompleta() {
  renderEscalaCompleta('');
  openModal('modal-escala-completa');
}

function filtrarEscalaCompleta() {
  const q = document.getElementById('search-escala-completa').value;
  renderEscalaCompleta(q);
}

function renderEscalaCompleta(busca) {
  const q = busca.toLowerCase().trim();
  const lista = state.frota
    .filter(o => o.hora || o.linha || o.status)
    .filter(o => !q || String(o.frota).includes(q) || (o.linha||'').toLowerCase().includes(q) || (o.status||'').toLowerCase().includes(q))
    .sort((a,b) => Number(a.frota) - Number(b.frota));

  const container = document.getElementById('escala-completa-content');
  if(!lista.length) {
    container.innerHTML = '<div class="empty"><div class="empty-icon">📅</div>Nenhum dado de escala encontrado</div>';
    return;
  }

  container.innerHTML = `<div style="font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:8px">${lista.length} veículos escalados</div>
    <table class="escala-table">
      <thead><tr><th>Frota</th><th>Hora</th><th>Linha</th></tr></thead>
      <tbody>` +
    lista.map(o => {
      const isPreso    = state.presos.find(p=>String(p.frota)===String(o.frota));
      const isAmostral = state.revisoes.find(r=>String(r.frota)===String(o.frota));
      const st = isPreso ? 'preso' : isAmostral ? 'amostral' : (o.status||'');
      let linha;
      if(st === 'preso')         linha = '<span style="color:red;font-weight:800">PRESO</span>';
      else if(st === 'amostral') linha = '<span style="color:#b45309;font-weight:800">AMOSTRAL</span>';
      else                       linha = o.linha || '—';
      return `<tr>
        <td class="escala-frota">${o.frota}</td>
        <td>${o.hora||'—'}</td>
        <td>${linha}</td>
      </tr>`;
    }).join('') +
    '</tbody></table>';
}

// ─── RELATÓRIOS EXCEL ─────────────────────────────────────────────
function exportarExcel(tipo) {
  let dados = [], cabecalho = [], nome = '';

  if(tipo === 'patio') {
    nome = 'Patio_' + new Date().toLocaleDateString('pt-BR').replace(/\//g,'-');
    cabecalho = ['Frota','Fila','Posição','Linha','Status'];
    FILAS_NUM.forEach(f => {
      (state.filas[f]||[]).forEach(o => {
        const cad = state.frota.find(x=>String(x.frota)===String(o.frota));
        const isPreso = state.presos.find(p=>String(p.frota)===String(o.frota));
        const isAm = state.revisoes.find(r=>String(r.frota)===String(o.frota));
        const st = isPreso?'PRESO':isAm?'AMOSTRAL':(cad&&cad.status?cad.status.toUpperCase():'');
        dados.push([o.frota,'Fila '+f, o.pos||'', o.linha||'', st]);
      });
    });
    ESPECIAIS.forEach(e => {
      (state.especiais[e.key]||[]).forEach(o => {
        const cad = state.frota.find(x=>String(x.frota)===String(o.frota));
        const isPreso = state.presos.find(p=>String(p.frota)===String(o.frota));
        const isAm = state.revisoes.find(r=>String(r.frota)===String(o.frota));
        const st = isPreso?'PRESO':isAm?'AMOSTRAL':(cad&&cad.status?cad.status.toUpperCase():'');
        dados.push([o.frota, e.label, o.pos||'', o.linha||'', st]);
      });
    });
    dados.sort((a,b)=>Number(a[0])-Number(b[0]));

  } else if(tipo === 'escala') {
    nome = 'Escala_' + new Date().toLocaleDateString('pt-BR').replace(/\//g,'-');
    cabecalho = ['Frota','Hora','Linha','Status'];
    state.frota
      .filter(o => o.hora || o.linha || o.status)
      .sort((a,b)=>Number(a.frota)-Number(b.frota))
      .forEach(o => {
        const isPreso = state.presos.find(p=>String(p.frota)===String(o.frota));
        const isAm = state.revisoes.find(r=>String(r.frota)===String(o.frota));
        const st = isPreso?'PRESO':isAm?'AMOSTRAL':(o.status?o.status.toUpperCase():'');
        dados.push([o.frota, o.hora||'', o.linha||'', st]);
      });

  } else if(tipo === 'alertas') {
    nome = 'Alertas_' + new Date().toLocaleDateString('pt-BR').replace(/\//g,'-');
    cabecalho = ['Frota','Tipo','Descrição','Registrado às'];
    state.presos.forEach(p => dados.push([p.frota,'PRESO',p.motivo||'',p.hora||'']));
    state.revisoes.forEach(r => dados.push([r.frota,'AMOSTRAL',r.desc||'',r.hora||'']));
    (state.manutencao||[]).forEach(m => dados.push([m.frota,'MANUTENÇÃO',(TIPOS_MANUT[m.tipo]?.label||m.tipo),m.hora||'']));
    dados.sort((a,b)=>Number(a[0])-Number(b[0]));

  } else if(tipo === 'frota') {
    nome = 'Frota_' + new Date().toLocaleDateString('pt-BR').replace(/\//g,'-');
    cabecalho = ['Frota','Linha','Hora','Status','Posição no Pátio'];
    state.frota.sort((a,b)=>Number(a.frota)-Number(b.frota)).forEach(o => {
      let loc = 'Não alocado';
      for(const f of FILAS_NUM) { if((state.filas[f]||[]).find(x=>String(x.frota)===String(o.frota))) { loc='Fila '+f; break; } }
      for(const e of ESPECIAIS) { if((state.especiais[e.key]||[]).find(x=>String(x.frota)===String(o.frota))) { loc=e.label; break; } }
      const isPreso = state.presos.find(p=>String(p.frota)===String(o.frota));
      const isAm = state.revisoes.find(r=>String(r.frota)===String(o.frota));
      const st = isPreso?'PRESO':isAm?'AMOSTRAL':(o.status?o.status.toUpperCase():'');
      dados.push([o.frota, o.linha||'', o.hora||'', st, loc]);
    });
  }

  // Gera CSV e força download
  const bom = '﻿'; // BOM para Excel reconhecer UTF-8
  const csv = bom + [cabecalho, ...dados].map(row =>
    row.map(v => '"' + String(v).replace(/"/g,'""') + '"').join(';')
  ).join('\r\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = nome + '.csv';
  a.click(); URL.revokeObjectURL(url);
}

// ─── IMPRIMIR POR MÓDULO ─────────────────────────────────────────
function imprimirRelatorio(tipo) {
  // Reutiliza imprimirLista para pátio, gera HTML para os outros
  if(tipo === 'patio') { imprimirLista(); return; }

  const now = new Date();
  const meta = 'Gerado em ' + now.toLocaleDateString('pt-BR') + ' às ' + now.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  let html = '';

  if(tipo === 'escala') {
    const lista = state.frota.filter(o=>o.hora||o.linha||o.status).sort((a,b)=>Number(a.frota)-Number(b.frota));
    html = '<h2 style="margin:0 0 4px;font-size:14px">Escala do Dia</h2><p style="font-size:10px;color:#888;margin:0 0 8px">'+meta+' · '+lista.length+' veículos</p>';
    html += '<table style="width:100%;border-collapse:collapse;font-size:11px"><thead><tr style="background:#000;color:#fff"><th style="padding:4px 6px;text-align:left">Frota</th><th style="padding:4px 6px;text-align:left">Hora</th><th style="padding:4px 6px;text-align:left">Linha</th><th style="padding:4px 6px;text-align:left">Status</th></tr></thead><tbody>';
    lista.forEach((o,i) => {
      const isPreso = state.presos.find(p=>String(p.frota)===String(o.frota));
      const isAm = state.revisoes.find(r=>String(r.frota)===String(o.frota));
      const st = isPreso?'PRESO':isAm?'AMOSTRAL':(o.status?o.status.toUpperCase():'—');
      const bg = i%2===0?'':'background:#f5f5f5';
      html += `<tr style="${bg}"><td style="padding:3px 6px;font-weight:800">${o.frota}</td><td style="padding:3px 6px">${o.hora||'—'}</td><td style="padding:3px 6px">${o.linha||'—'}</td><td style="padding:3px 6px;color:${isPreso?'red':isAm?'#b45309':'#333'};font-weight:700">${st}</td></tr>`;
    });
    html += '</tbody></table>';

  } else if(tipo === 'alertas') {
    html = '<h2 style="margin:0 0 4px;font-size:14px">Alertas Ativos</h2><p style="font-size:10px;color:#888;margin:0 0 8px">'+meta+'</p>';
    if(state.presos.length) {
      html += '<h3 style="font-size:12px;margin:8px 0 4px;border-left:3px solid red;padding-left:6px">Veículos Presos</h3>';
      state.presos.forEach(p => { html += `<div style="padding:4px 0;font-size:11px;border-bottom:1px solid #eee"><b>${p.frota}</b> — ${p.motivo||'—'}</div>`; });
    }
    if(state.revisoes.length) {
      html += '<h3 style="font-size:12px;margin:8px 0 4px;border-left:3px solid #b45309;padding-left:6px">Amostrais SPTRANS</h3>';
      state.revisoes.forEach(r => { html += `<div style="padding:4px 0;font-size:11px;border-bottom:1px solid #eee"><b>${r.frota}</b> — ${r.tipo||''} ${r.desc||''}</div>`; });
    }
    if(!state.presos.length && !state.revisoes.length) html += '<p style="font-size:11px;color:#888">Nenhum alerta ativo.</p>';

  } else if(tipo === 'frota') {
    const lista = state.frota.sort((a,b)=>Number(a.frota)-Number(b.frota));
    html = '<h2 style="margin:0 0 4px;font-size:14px">Frota Completa</h2><p style="font-size:10px;color:#888;margin:0 0 8px">'+meta+' · '+lista.length+' veículos</p>';
    html += '<table style="width:100%;border-collapse:collapse;font-size:11px"><thead><tr style="background:#000;color:#fff"><th style="padding:4px 6px;text-align:left">Frota</th><th style="padding:4px 6px;text-align:left">Linha</th><th style="padding:4px 6px;text-align:left">Hora</th><th style="padding:4px 6px;text-align:left">Pátio</th></tr></thead><tbody>';
    lista.forEach((o,i) => {
      let loc = '—';
      for(const f of FILAS_NUM) { if((state.filas[f]||[]).find(x=>String(x.frota)===String(o.frota))) { loc='Fila '+f; break; } }
      for(const e of ESPECIAIS) { if((state.especiais[e.key]||[]).find(x=>String(x.frota)===String(o.frota))) { loc=e.label; break; } }
      const bg = i%2===0?'':'background:#f5f5f5';
      html += `<tr style="${bg}"><td style="padding:3px 6px;font-weight:800">${o.frota}</td><td style="padding:3px 6px">${o.linha||'—'}</td><td style="padding:3px 6px">${o.hora||'—'}</td><td style="padding:3px 6px">${loc}</td></tr>`;
    });
    html += '</tbody></table>';
  }

  document.getElementById('print-content').innerHTML = html;
  window.print();
}

function toggleMenuDots(){
  document.getElementById('menu-dots-dropdown').classList.toggle('open');
}
// Fecha menu ao clicar fora
document.addEventListener('click', function(e){
  const menu = document.getElementById('menu-dots');
  if(menu && !menu.contains(e.target)){
    document.getElementById('menu-dots-dropdown').classList.remove('open');
  }
});

function resetarSistema(){
  if(!confirm('RESETAR SISTEMA: Apaga TUDO — frota, filas, escala e alertas. Esta ação não pode ser desfeita. Confirma?')) return;
  const senha = prompt('Digite a senha de administrador para continuar:');
  if(senha === null) return;
  if(senha !== '0000') { alert('Senha incorreta. Operação cancelada.'); return; }
  try { localStorage.removeItem('sambaiba_v2'); } catch(e){}
  state = JSON.parse(JSON.stringify(EXEMPLO));
  FILAS_NUM.forEach(f=>{if(!state.filas[f])state.filas[f]=[];});
  ESPECIAIS.forEach(e=>{if(!state.especiais[e.key])state.especiais[e.key]=[];});
  save();
  renderAll();
  alert('Dados apagados com sucesso!');
}

// ─────────────────────────────────────────────────────────────────
// IMPORTAÇÃO EXCEL COMPLETO (E2 + AR2 + MANOBRA/PRESO)
// ─────────────────────────────────────────────────────────────────

// Dados temporários da prévia — só aplicados se o usuário confirmar
let _escalaCompletaTemp = null;

function previewEscalaCompleta(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const data = new Uint8Array(e.target.result);
      const wb = XLSX.read(data, {type:'array', cellDates:true});

      // Helper: formata hora (Date ou string)
      function fmtHora(v) {
        if (!v && v !== 0) return '';
        if (typeof v === 'string') {
          const u = v.trim().toUpperCase();
          if (u === 'PRESO' || u === 'EVENTO') return u;
          return v.trim();
        }
        if (v instanceof Date) {
          const h = String(v.getHours()).padStart(2,'0');
          const m = String(v.getMinutes()).padStart(2,'0');
          return `${h}:${m}`;
        }
        return String(v).trim();
      }

      // Helper: valida carro (3-4 dígitos)
      function valCarro(v) {
        if (!v) return null;
        const s = String(Math.round(Number(v)));
        return /^\d{3,4}$/.test(s) ? s : null;
      }

      // Detecta tipo da aba pelo conteúdo das primeiras linhas
      function detectarTipoAba(ws) {
        const rows = XLSX.utils.sheet_to_json(ws, {header:1, defval:'', raw:false});
        const cabecalho = rows.slice(0,3).flat().join(' ').toUpperCase();
        if (cabecalho.includes('E2')) return 'e2';
        if (cabecalho.includes('AR2')) return 'ar2';
        if (cabecalho.includes('MANOBRA') || cabecalho.includes('PRESO')) return 'manobra';
        if (cabecalho.includes('CONFIGURA')) return 'configuracao';
        return null;
      }

      // Lê aba de plantão (E2 ou AR2): 3 grupos (cols 0,1,2 / 6,7,8 / 12,13,14)
      function lerPlantao(ws) {
        const rows = XLSX.utils.sheet_to_json(ws, {header:1, defval:'', cellDates:true});
        const grupos = [[0,1,2],[6,7,8],[12,13,14]];
        const resultado = [];
        rows.slice(3).forEach(row => {
          grupos.forEach(([ci,hi,li]) => {
            const carro = valCarro(row[ci]);
            if (!carro) return;
            const hora = fmtHora(row[hi]);
            if (!hora || hora === 'TABELAS' || hora === 'EVENTO') return;
            const linha = String(row[li] || '').trim();
            resultado.push({carro, hora, linha});
          });
        });
        return resultado;
      }

      // Lê aba de manobra/preso: 6 grupos de (carro, hora) — cols 0,1 / 3,4 / 6,7 / 9,10 / 12,13 / 15,16
      function lerManobra(ws) {
        const rows = XLSX.utils.sheet_to_json(ws, {header:1, defval:'', cellDates:true});
        const grupos = [[0,1],[3,4],[6,7],[9,10],[12,13],[15,16]];
        const manobra = [], presos = [];
        rows.slice(1).forEach(row => {
          grupos.forEach(([ci,hi]) => {
            const carro = valCarro(row[ci]);
            if (!carro) return;
            const hora = fmtHora(row[hi]);
            if (hora === 'PRESO') { presos.push(carro); return; }
            if (hora && hora !== 'EVENTO') manobra.push({carro, hora, linha:''});
          });
        });
        return {manobra, presos};
      }

      // Lê aba de configuração: 4 grupos de (carro, hora, linha) — cols 0,1,2 / 4,5,6 / ...
      // Detecta grupos pelo cabeçalho (CARRO = início de grupo)
      function lerConfiguracao(ws) {
        const rows = XLSX.utils.sheet_to_json(ws, {header:1, defval:'', cellDates:true});
        // Encontra colunas CARRO no cabeçalho
        const header = rows[0] || [];
        const grupos = [];
        header.forEach((h,i) => {
          if (String(h).toUpperCase().trim() === 'CARRO') {
            grupos.push([i, i+1, i+2]); // CARRO, HORA, LINHA
          }
        });
        if (!grupos.length) grupos.push([0,1,2],[4,5,6],[8,9,10],[12,13,14]);
        const e2=[], ar2=[], manobra=[], presos=[];
        rows.slice(1).forEach(row => {
          grupos.forEach(([ci,hi,li]) => {
            const carro = valCarro(row[ci]);
            if (!carro) return;
            const hora = fmtHora(row[hi]);
            if (hora === 'PRESO') { presos.push(carro); return; }
            if (!hora || hora === 'EVENTO') return;
            const linha = String(row[li] || '').trim();
            const item = {carro, hora, linha};
            if (carro.startsWith('1')) e2.push(item);
            else if (carro.startsWith('2')) ar2.push(item);
          });
        });
        return {e2, ar2, manobra, presos};
      }

      // Processa todas as abas
      const resultado = {e2:[], ar2:[], manobra:[], presos:[], abas:[], avisos:[]};

      wb.SheetNames.forEach(nome => {
        const ws = wb.Sheets[nome];
        const tipo = detectarTipoAba(ws);
        resultado.abas.push({nome, tipo});

        if (tipo === 'e2') {
          resultado.e2 = resultado.e2.concat(lerPlantao(ws));
        } else if (tipo === 'ar2') {
          resultado.ar2 = resultado.ar2.concat(lerPlantao(ws));
        } else if (tipo === 'manobra') {
          const {manobra, presos} = lerManobra(ws);
          resultado.manobra = resultado.manobra.concat(manobra);
          resultado.presos  = resultado.presos.concat(presos);
        } else if (tipo === 'configuracao') {
          const conf = lerConfiguracao(ws);
          // Configuração só usa se não tiver E2/AR2 separados
          resultado._confE2  = conf.e2;
          resultado._confAR2 = conf.ar2;
          if (!resultado.manobra.length) resultado.manobra = conf.manobra;
          if (!resultado.presos.length)  resultado.presos  = conf.presos;
        } else {
          resultado.avisos.push(`Aba "${nome}" não reconhecida — ignorada`);
        }
      });

      // Se não achou E2/AR2 mas tem configuração, usa ela
      if (!resultado.e2.length && resultado._confE2)  resultado.e2  = resultado._confE2;
      if (!resultado.ar2.length && resultado._confAR2) resultado.ar2 = resultado._confAR2;

      // Remove duplicatas de presos
      resultado.presos = [...new Set(resultado.presos)];

      if (!resultado.e2.length && !resultado.ar2.length && !resultado.manobra.length) {
        alert('Nenhum dado reconhecido no arquivo. Verifique se as abas têm os cabeçalhos corretos (E2, AR2, Manobra).');
        input.value = '';
        return;
      }

      // Guarda temporariamente
      _escalaCompletaTemp = resultado;

      // Monta prévia
      let statsHtml = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
          <div style="background:var(--surface2);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:var(--primary)">${resultado.e2.length}</div>
            <div style="font-size:11px;color:var(--muted)">veículos E2</div>
          </div>
          <div style="background:var(--surface2);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:#3b82f6">${resultado.ar2.length}</div>
            <div style="font-size:11px;color:var(--muted)">veículos AR2</div>
          </div>
          <div style="background:var(--surface2);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:#f59e0b">${resultado.manobra.length}</div>
            <div style="font-size:11px;color:var(--muted)">veículos Manobra</div>
          </div>
          <div style="background:var(--surface2);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:800;color:#ef4444">${resultado.presos.length}</div>
            <div style="font-size:11px;color:var(--muted)">Presos</div>
          </div>
        </div>
        <div style="font-size:11px;color:var(--muted)">
          Abas lidas: ${resultado.abas.map(a => `<b>${a.nome}</b> (${a.tipo || '?'})`).join(', ')}
        </div>`;

      // Amostra E2
      let amostrasHtml = '';
      if (resultado.e2.length) {
        amostrasHtml += `<div style="margin-bottom:6px"><b>Amostra E2:</b> ` +
          resultado.e2.slice(0,4).map(d=>`${d.carro} ${d.hora} ${d.linha}`).join(' · ') + `</div>`;
      }
      if (resultado.ar2.length) {
        amostrasHtml += `<div style="margin-bottom:6px"><b>Amostra AR2:</b> ` +
          resultado.ar2.slice(0,4).map(d=>`${d.carro} ${d.hora} ${d.linha}`).join(' · ') + `</div>`;
      }
      if (resultado.presos.length) {
        amostrasHtml += `<div><b>Presos (primeiros):</b> ` +
          resultado.presos.slice(0,6).join(', ') + (resultado.presos.length>6?' ...':'') + `</div>`;
      }

      let avisosHtml = '';
      if (resultado.avisos.length) {
        avisosHtml = resultado.avisos.map(a =>
          `<div style="font-size:11px;color:#f59e0b;background:var(--surface2);border-radius:6px;padding:6px 10px;margin-bottom:4px">⚠️ ${a}</div>`
        ).join('');
      }

      document.getElementById('preview-completo-stats').innerHTML = statsHtml;
      document.getElementById('preview-completo-amostras').innerHTML = amostrasHtml;
      document.getElementById('preview-completo-avisos').innerHTML = avisosHtml;

      closeModal('modal-escala');
      openModal('modal-preview-completo');

    } catch(err) {
      alert('Erro ao ler o arquivo: ' + err.message);
    }
    input.value = '';
  };
  reader.readAsArrayBuffer(file);
}

function confirmarEscalaCompleta() {
  if (!_escalaCompletaTemp) { alert('Dados da prévia não encontrados. Tente importar novamente.'); return; }
  try {
    const r = _escalaCompletaTemp;

    // Garante que state.escala existe (segurança)
    if (!state.escala) state.escala = { tipo:null, data:'', importadoEm:null, manobra:[], e2:[], ar2:[] };
    if (!state.escala.manobra) state.escala.manobra = [];
    if (!state.escala.e2) state.escala.e2 = [];
    if (!state.escala.ar2) state.escala.ar2 = [];

    // 1. Aplica escala
    state.escala.tipo        = 'plantao';
    state.escala.manobra     = r.manobra;
    state.escala.e2          = parsearPlantao(r.e2);
    state.escala.ar2         = parsearPlantao(r.ar2);
    state.escala.data        = new Date().toLocaleDateString('pt-BR');
    state.escala.importadoEm = new Date().toISOString();

    // 2. Adiciona presos sem duplicar (state.presos é array de objetos {frota, motivo, hora})
    const horaAgora = new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
    r.presos.forEach(carro => {
      const c = String(carro);
      const jaExiste = state.presos.find(p => String(p.frota || p) === c);
      if (!jaExiste) {
        state.presos.push({ frota: c, motivo: 'Importado da escala', hora: horaAgora });
      }
    });

    // 3. Atualiza frota: hora, linha, e adiciona novos veículos
    // Monta lista plana de todos os veículos com seus dados
    const todosDados = [
      ...r.e2.map(d => ({ frota: String(d.carro), hora: d.hora, linha: d.linha || '' })),
      ...r.ar2.map(d => ({ frota: String(d.carro), hora: d.hora, linha: d.linha || '' })),
      ...r.manobra.map(d => ({ frota: String(d.carro), hora: d.hora, linha: '' })),
      ...r.presos.map(c => ({ frota: String(c), hora: '', linha: '' }))
    ];

    let carrosNovos = 0;
    todosDados.forEach(d => {
      if (!d.frota || !/^\d{3,4}$/.test(d.frota)) return;
      const existente = state.frota.find(o => String(o.frota) === d.frota);
      if (!existente) {
        state.frota.push({ frota: d.frota, linha: d.linha, hora: d.hora, status: '' });
        carrosNovos++;
      } else {
        // Atualiza hora sempre; atualiza linha só se tiver valor
        existente.hora = d.hora;
        if (d.linha) existente.linha = d.linha;
      }
      // Atualiza linha nos chips das filas onde o carro já está alocado
      if (d.linha) {
        FILAS_NUM.forEach(f => {
          const item = (state.filas[f]||[]).find(x => String(x.frota) === d.frota);
          if (item) item.linha = d.linha;
        });
        ESPECIAIS.forEach(e => {
          const item = (state.especiais[e.key]||[]).find(x => String(x.frota) === d.frota);
          if (item) item.linha = d.linha;
        });
        // Registra linha no cadastro de linhas (se ainda não existir)
        const linhaObj = state.linhas.find(l => l.codigo === d.linha);
        if (!linhaObj && d.linha) {
          const setor = d.frota.startsWith('1') ? 'E2' : 'AR2';
          state.linhas.push({ codigo: d.linha, descricao: '', setor });
        }
      }
    });
    state.frota.sort((a,b) => Number(a.frota) - Number(b.frota));

    _escalaCompletaTemp = null;
    closeModal('modal-preview-completo');
    save();
    renderAll();
    alert(`✔ Escala importada!\nE2: ${r.e2.length} veículos\nAR2: ${r.ar2.length} veículos\nManobra: ${r.manobra.length} veículos\nPresos: ${r.presos.length}\nAdicionados à frota: ${carrosNovos}`);
  } catch(err) {
    alert('Erro ao confirmar importação: ' + err.message + '\n\nAbra o console (F12) para mais detalhes.');
    console.error('confirmarEscalaCompleta erro:', err);
  }
}

function cancelarEscalaCompleta() {
  _escalaCompletaTemp = null;
  closeModal('modal-preview-completo');
}

initState();renderAll();