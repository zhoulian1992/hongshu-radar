/* 红薯雷达 · 本地工作台前端（原生 JS，离线可用） */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (v) => String(v ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v) => (v === null || v === undefined || v === '') ? '—' : Number(v).toLocaleString('zh-CN');
const money = (v) => (v === null || v === undefined) ? '—' : '¥' + Number(v).toFixed(2);

let STATE = null;
let sortBy = 'goods_value';
let logLevel = 'all';
let onlyAction = false;
let currentId = null;
let lastRiskReport = null;

const RISK_LEVEL = {
  red: { t: '高', c: 'red' }, amber: { t: '中', c: 'orange' },
  green: { t: '低', c: 'green' }
};
const LIST_STATUS = {
  matched: '命中', candidates: '近似候选', disguised: '疑似伪装', no_match: '未命中'
};
function riskBadge(rk) {
  if (!rk) return '<span class="tag grey">未体检</span>';
  if (rk.state === 'failed') return '<span class="tag red" title="检查失败／未完成">未完成</span>';
  const lv = RISK_LEVEL[rk.handling_level] || { t: '—', c: 'grey' };
  const st = LIST_STATUS[rk.list_status] || rk.list_status || '';
  return `<span class="tag ${lv.c}" title="${esc(st)} · ${esc(rk.suggested_action || '')}">${lv.t}风险</span>
    <span class="tag grey">${esc(st)}</span>`;
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

let toastTimer;
function toast(msg, isError) {
  const el = $('#toast');
  el.textContent = msg;
  el.style.background = isError ? '#b93f2e' : '#252a33';
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2800);
}

/* ---------------- 看板 ---------------- */
async function loadState(quiet) {
  try {
    STATE = await api('/api/state');
    renderOverview();
    renderBoard();
    renderConfigForm();
    if (!quiet) renderLogs();
  } catch (e) { toast('加载失败：' + e.message, true); }
}

function renderOverview() {
  const o = STATE.overview, s = STATE.scheduler || {};
  $('#kProducts').textContent = num(o.products);
  $('#kActive').textContent = `监控中 ${o.active} · 快照 ${num(o.snapshots)} 条`;
  $('#kToday').textContent = num(o.today_total) + ' 单';
  $('#kRevenue').textContent = money(o.fuzzy_revenue);
  $('#kAlerts').textContent = num(o.alerts);
  const running = s.running;
  $('#runStatus').textContent = s.cooldown_until && s.cooldown_until > new Date().toISOString().replace('T', ' ').slice(0, 19)
    ? '风控冷却中' : (running ? '监控运行中' : '监控已停止');
  $('#runStatus').classList.toggle('off', !running);
  $('#nextRun').textContent = s.next_at ? `下次整点 ${s.next_at.slice(11, 16)}` : '';
  $('#demoBanner').classList.toggle('hidden', !o.demo_mode || sessionStorage.getItem('hideDemo') === '1');
  $('#dbInfo').textContent = `数据库 ${(o.db_size / 1024).toFixed(1)} KB · ${num(o.snapshots)} 条快照 · 商品 ${o.products} 个`;
}

function sortRows(rows) {
  const val = (p) => p[sortBy];
  return rows.sort((a, b) => {
    const x = val(a), y = val(b);
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return sortBy === 'boom_value' ? x - y : y - x;
  });
}

function renderBoard() {
  const kw = $('#search').value.trim().toLowerCase();
  let rows = STATE.products.filter((p) => !kw ||
    `${p.title || ''}${p.shop_name || ''}${p.id}`.toLowerCase().includes(kw));
  if (onlyAction) rows = rows.filter((p) =>
    p.status === '正常' && p.today_delta > 0 && !p.sample_small);
  if ($('#onlySafe') && $('#onlySafe').checked) rows = rows.filter((p) =>
    !(p.risk && p.risk.state === 'ok' && p.risk.handling_level === 'red'));
  rows = sortRows([...rows]);

  const body = $('#boardBody');
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="14" style="padding:36px;text-align:center;color:var(--muted)">
      还没有商品。到「添加商品 / 导入」加一个，先拿一个商品试，确认能读到数据、能正常计算。</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((p) => {
    const tags = [];
    if (p.partial) tags.push('<span class="tag orange">半日口径</span>');
    if (p.sample_small) tags.push('<span class="tag grey">样本过小</span>');
    if (p.delisted_at) tags.push('<span class="tag red">已下架</span>');
    if (p.last_error) tags.push('<span class="tag red" title="' + esc(p.last_error) + '">采集异常</span>');
    if (p.imprecise) tags.push('<span class="tag orange" title="最近读数 ' + esc(p.imprecise_raw || '') + '，只有模糊值，不参与差值计算">读数不精确</span>');
    if (p.status === '待采集') tags.push('<span class="tag grey">待采集</span>');
    if (!tags.length) tags.push('<span class="tag green">正常</span>');
    return `<tr data-id="${esc(p.id)}">
      <td class="title-cell"><div class="title-main">${esc(p.title)}</div>
        <div class="title-sub">${esc(p.id.slice(0, 16))} · ${esc(p.shop_name || '')}</div></td>
      <td>${esc(p.category || '—')}</td>
      <td class="num">${p.price !== null && p.price !== undefined ? money(p.price).replace('¥', '') : '—'}</td>
      <td class="num">${num(p.current_sold)}</td>
      <td class="num strong-num">${num(p.today_delta)}</td>
      <td class="num">${num(p.yesterday_delta)}</td>
      <td class="num">${num(p.hour_delta)}</td>
      <td class="num">${p.streak ? p.streak + ' 天' : '—'}</td>
      <td class="num strong-num">${p.boom_value !== null ? p.boom_value : '—'}</td>
      <td class="num strong-num">${p.goods_value !== null ? p.goods_value.toFixed(2) : '—'}</td>
      <td class="num">${p.fuzzy_revenue !== null && p.fuzzy_revenue !== undefined ? p.fuzzy_revenue.toFixed(2) : '—'}</td>
      <td><span class="tag ${p.research_done >= 7 ? 'green' : 'grey'}">${p.research_done}/7</span></td>
      <td>${riskBadge(p.risk)}</td>
      <td>${tags.join('')}<div class="title-sub" title="${esc(p.note)}">${esc((p.note || '').slice(0, 22))}</div></td>
    </tr>`;
  }).join('');
  $$('#boardBody tr[data-id]').forEach((tr) =>
    tr.addEventListener('click', () => openDetail(tr.dataset.id)));
}

/* ---------------- 试算 ---------------- */
async function runTrial() {
  try {
    const r = await post('/api/trial', {
      price: +$('#tPrice').value || 0, sold: +$('#tSold').value || 0,
      delta: +$('#tDelta').value || 0, partial: $('#tPartial').checked,
    });
    $('#rBoom').textContent = r.boom_value !== null ? r.boom_value : '—';
    $('#rValue').textContent = r.goods_value !== null ? r.goods_value.toFixed(2) : '—';
    $('#rRevenue').textContent = r.fuzzy_revenue.toFixed(2);
    $('#rWarnings').innerHTML = r.warnings.map((w) => `<div class="warning">${esc(w)}</div>`).join('');
    $('#rNote').textContent = r.note;
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 日志 ---------------- */
async function renderLogs() {
  try {
    const r = await api(`/api/logs?level=${logLevel}&limit=300`);
    $('#logsBody').innerHTML = r.logs.length ? r.logs.map((l) => {
      const color = { error: 'red', warn: 'orange', success: 'green', info: 'grey' }[l.level] || 'grey';
      return `<tr><td>${esc(l.ts)}</td><td><span class="tag ${color}">${esc(l.level)}</span></td>
        <td>${esc(l.source)}</td><td>${esc(l.title || l.product_id || '—')}</td>
        <td title="${esc(l.detail || '')}">${esc(l.message)}</td></tr>`;
    }).join('') : '<tr><td colspan="5" style="text-align:center;padding:30px;color:var(--muted)">暂无日志</td></tr>';
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 商品详情 ---------------- */
async function openDetail(id) {
  currentId = id;
  try {
    const d = await api('/api/product/' + encodeURIComponent(id));
    const p = d.product;
    $('#mTitle').textContent = p.title;
    $('#mSub').textContent = `${p.id} · ${p.shop_name || '未知店铺'} · ${p.note || ''}`;
    const items = [
      ['累计已售', num(p.current_sold)], ['今日增量', num(p.today_delta)],
      ['昨日', num(p.yesterday_delta)], ['上小时', num(p.hour_delta)],
      ['爆品值', p.boom_value ?? '—'], ['商品价值', p.goods_value ?? '—'],
      ['连续出单', p.streak + ' 天'], ['研判进度', p.research_done + '/7'],
    ];
    $('#mMetrics').innerHTML = items.map(([k, v]) =>
      `<div class="metric"><span>${k}</span><strong>${esc(v)}</strong></div>`).join('');
    const rk = (STATE.products.find((x) => x.id === id) || {}).risk;
    const riskBox = $('#mRisk');
    riskBox.className = 'risk-inline ' + ((rk && rk.handling_level) || '');
    riskBox.innerHTML = rk
      ? `<b>上架前风险：${esc((RISK_LEVEL[rk.handling_level] || { t: '—' }).t)}风险 · ${esc(LIST_STATUS[rk.list_status] || rk.list_status || '')}</b>
         <div class="muted small">${esc(rk.suggested_action || '')} ｜ 检查于 ${esc(rk.checked_at || '')} ｜ 词库 ${esc(rk.db_date || '')}</div>
         <div class="muted small">版权归属未核验、销售授权待核验；名单未命中不等于可售。</div>
         <button id="btnGoRisk" class="link">查看完整处置卡 →</button>`
      : `<b>尚未做上架前风险体检</b>
         <div class="muted small">可查版权名、伪装写法、平台暗语与违禁词，结果不是法律结论。</div>
         <button id="btnGoRisk" class="link">去体检 →</button>`;
    riskBox.querySelector('#btnGoRisk').onclick = () => {
      $('#modal').classList.add('hidden');
      const tab = $$('.tab').find((x) => x.dataset.tab === 'risk');
      if (tab) tab.click();
      if (!rk) {
        $('#riskInput').value = p.title;
        runRiskCheck({ candidates: [{ id: p.id, title: p.title }] }, '单品体检');
      } else {
        showRiskById(rk.candidate_id || id);
      }
    };
    $('#chartHour').innerHTML = lineChart(d.hourly);
    $('#chartDay').innerHTML = barChart(d.daily);

    let research = {};
    try { research = typeof p.research_json === 'string' ? JSON.parse(p.research_json || '{}') : (p.research_json || {}); } catch (e) { }
    $('#researchList').innerHTML = (d.research_items || STATE.research_items).map((it) => `
      <div class="research-item">
        <input type="checkbox" id="rs_${it.key}" ${research[it.key] ? 'checked' : ''}>
        <label for="rs_${it.key}"><b>${esc(it.label)}</b>
          <div class="hint">${esc(it.hint)}</div></label>
      </div>`).join('');

    $('#mPrice').value = p.price ?? '';
    $('#mNotes').value = p.notes_count ?? '';
    $('#mCompetitors').value = p.competitor_count ?? '';
    $('#mSeason').value = p.season_tag || '';
    $('#snapList').innerHTML = (d.snapshots || []).slice().reverse().slice(0, 40).map((s) =>
      `<span class="snap ${s.usable ? '' : 'unusable'}" title="${esc(s.sold_raw || '')}">
        ${esc(s.captured_at.slice(5, 16))} · ${esc(s.sold_raw ?? s.sold ?? '—')}</span>`).join('') || '<span class="muted small">暂无快照</span>';
    $('#modal').classList.remove('hidden');
  } catch (e) { toast(e.message, true); }
}

function lineChart(points) {
  const W = 480, H = 150, pad = 22;
  const vals = points.map((p) => p.delta);
  const max = Math.max(1, ...vals.filter((v) => v !== null));
  const x = (i) => pad + i * (W - pad * 2) / Math.max(1, points.length - 1);
  const y = (v) => H - pad - (v / max) * (H - pad * 2);
  let grid = '';
  for (let i = 0; i <= 3; i++) {
    const gy = pad + i * (H - pad * 2) / 3;
    grid += `<line x1="${pad}" y1="${gy}" x2="${W - pad}" y2="${gy}" stroke="#eef0f3"/>
      <text x="2" y="${gy + 3}" font-size="9" fill="#9aa0a8">${Math.round(max * (3 - i) / 3)}</text>`;
  }
  let seg = [], paths = '', dots = '';
  points.forEach((p, i) => {
    if (p.delta === null) { if (seg.length) paths += poly(seg); seg = []; return; }
    seg.push(`${x(i)},${y(p.delta)}`);
    dots += `<circle cx="${x(i)}" cy="${y(p.delta)}" r="2.6" fill="#ff2442"/>`;
  });
  if (seg.length) paths += poly(seg);
  const labels = points.filter((_, i) => i % 4 === 0).map((p, k) =>
    `<text x="${x(k * 4)}" y="${H - 4}" font-size="9" fill="#9aa0a8">${esc(p.hour)}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${paths}${dots}${labels}</svg>`;
}
function poly(seg) {
  return `<polyline points="${seg.join(' ')}" fill="none" stroke="#ff2442" stroke-width="2"
    stroke-linejoin="round" stroke-linecap="round"/>`;
}
function barChart(points) {
  const W = 340, H = 150, pad = 22;
  const max = Math.max(1, ...points.map((p) => p.delta || 0));
  const bw = (W - pad * 2) / points.length * 0.58;
  const gap = (W - pad * 2) / points.length;
  let bars = '';
  points.forEach((p, i) => {
    const h = p.delta ? (p.delta / max) * (H - pad * 2) : 0;
    const px = pad + i * gap + (gap - bw) / 2;
    bars += `<rect x="${px}" y="${H - pad - h}" width="${bw}" height="${h}" rx="3"
      fill="${p.partial ? '#f0c46a' : '#0f9d76'}" opacity="${p.delta ? .9 : .25}"/>
      <text x="${px + bw / 2}" y="${H - 6}" font-size="9" fill="#9aa0a8" text-anchor="middle">${esc(p.date)}</text>
      ${p.delta ? `<text x="${px + bw / 2}" y="${H - pad - h - 3}" font-size="9" fill="#5b626c" text-anchor="middle">${p.delta}</text>` : ''}`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#e8eaee"/>${bars}</svg>`;
}

async function saveResearch() {
  if (!currentId) return;
  const research = {};
  $$('#researchList input[type=checkbox]').forEach((el) =>
    research[el.id.replace('rs_', '')] = el.checked);
  try {
    await post('/api/product/' + encodeURIComponent(currentId), {
      research_json: research,
      price: $('#mPrice').value === '' ? null : +$('#mPrice').value,
      notes_count: $('#mNotes').value === '' ? null : +$('#mNotes').value,
      competitor_count: $('#mCompetitors').value === '' ? null : +$('#mCompetitors').value,
      season_tag: $('#mSeason').value,
    });
    toast('研判与字段已保存');
    await loadState(true);
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 上架前风险体检 ---------------- */
function shortHash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36).slice(0, 10);
}
function parseRiskInput(text) {
  const t = (text || '').trim();
  if (!t) return [];
  if (t.startsWith('[') || t.startsWith('{')) {
    const parsed = JSON.parse(t);
    const arr = Array.isArray(parsed) ? parsed : (parsed.candidates || []);
    return arr.map((x) => typeof x === 'string' ? { title: x, id: 'cand-' + shortHash(x) } : x);
  }
  return t.split('\n').map((l) => l.trim()).filter(Boolean)
    .map((title) => ({ title, id: 'cand-' + shortHash(title) }));
}

async function runRiskCheck(body, label) {
  const msg = $('#riskMsg');
  msg.className = 'hint-box';
  msg.textContent = '正在本地查询（不上传任何内容）…';
  try {
    const rep = await post('/api/risk/check', body);
    lastRiskReport = rep;
    msg.className = 'hint-box ok';
    msg.textContent = `${label || '体检完成'}：共 ${rep.summary.total} 条，新查 ${rep.summary.checked}，复用缓存 ${rep.summary.cached}。${rep.notice}`;
    await renderRiskResults(rep);
    await loadState(true);
  } catch (e) {
    msg.className = 'hint-box err';
    msg.textContent = '检查失败／未完成：' + e.message + '（不会当成「未命中」）';
  }
}

function riskCountChips(counts) {
  const labels = { matched: '命中', candidates: '近似候选', disguised: '疑似伪装',
                   no_match: '未命中', failed: '检查未完成' };
  return Object.keys(labels).map((k) =>
    `<span class="risk-chip"><strong>${counts[k] || 0}</strong>${labels[k]}</span>`).join('');
}

function renderRiskResults(rep) {
  $('#riskSummary').textContent = `本次 ${rep.summary.total} 条`;
  $('#riskCounts').innerHTML = riskCountChips(rep.counts || {});
  const items = (rep.results || []).map((r) => {
    const hits = [];
    (r.checks || []).forEach((c) => (c.matches || []).forEach((m) => {
      if (m.name) hits.push(m.name);
    }));
    return {
      id: (r.candidate || {}).id, title: (r.candidate || {}).title,
      list_status: r.list_status,
      handling_level: (r.handling || {}).level, lint_level: (r.lint || {}).level,
      suggested_action: r.suggested_action, hits: hits.slice(0, 2),
      state: r.state || 'ok', from_cache: r.from_cache,
    };
  });
  renderRiskTable(items);
}

function renderRiskTable(items) {
  const body = $('#riskBody');
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="5" style="padding:26px;text-align:center;color:var(--muted)">'
      + '还没有体检结果。粘贴标题，或点「体检全部监控商品」。</td></tr>';
    return;
  }
  body.innerHTML = items.map((it) => {
    const lv = RISK_LEVEL[it.handling_level] || { t: '—', c: 'grey' };
    const st = it.state === 'failed' ? '检查未完成' : (LIST_STATUS[it.list_status] || it.list_status || '—');
    return `<tr data-id="${esc(it.id || '')}">
      <td class="title-cell"><div class="title-main">${esc(it.title || it.id)}</div>
        <div class="title-sub">${it.from_cache ? '缓存' : '本次查询'} · ${esc((it.id || '').slice(0, 18))}</div></td>
      <td><span class="tag ${it.state === 'failed' ? 'red' : 'grey'}">${esc(st)}</span></td>
      <td><span class="tag ${lv.c}">${esc(lv.t)}风险</span></td>
      <td><span class="tag ${RISK_LEVEL[it.lint_level] ? RISK_LEVEL[it.lint_level].c : 'grey'}">${esc(it.lint_level || '—')}</span></td>
      <td class="title-cell"><div>${esc((it.hits || []).join('、') || '—')}</div>
        <div class="title-sub">${esc(it.suggested_action || '')}</div></td>
    </tr>`;
  }).join('');
  $$('#riskBody tr[data-id]').forEach((tr) =>
    tr.addEventListener('click', () => showRiskById(tr.dataset.id)));
}

async function showRiskById(cid) {
  if (!cid) return;
  try {
    const d = await api('/api/risk/product/' + encodeURIComponent(cid));
    const r = d.result;
    if (!r) { $('#riskDetail').classList.add('hidden'); toast('这条没有完整结果（可能尚未体检）'); return; }
    const h = r.handling || {}, lint = r.lint || {};
    const ul = (arr) => (arr && arr.length)
      ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`
      : '<p class="muted small">—</p>';
    $('#riskDetail').innerHTML = `
      <h3>${esc((r.candidate || {}).title || cid)} · ${esc((RISK_LEVEL[h.level] || { t: '—' }).t)}风险（${h.score === undefined ? '—' : h.score} 分）</h3>
      <div class="muted small">${esc(h.headline || '')} ｜ 名单状态 ${esc(LIST_STATUS[r.list_status] || r.list_status || '')} ｜ 标题体检 ${esc(lint.level || '—')}</div>
      <h4>这意味着</h4>${ul(h.meaning)}
      <h4>可行做法</h4>${ul(h.actions)}
      <h4>上架前要拿到的材料</h4>${ul(h.docs)}
      <h4>换个方向</h4>${ul(h.alternatives)}
      <h4>合规自查</h4>${ul(h.checklist)}
      <div class="notice">${esc(r.guidance || '')}<br>${esc(h.notice || '')}<br>
        版权状态：${esc(r.copyright_status || 'unverified')}（未核验）｜授权状态：${esc(r.authorization_status || 'needs_verification')}（待核验）</div>`;
    $('#riskDetail').classList.remove('hidden');
  } catch (e) { toast(e.message, true); }
}

async function renderRiskRows() {
  try {
    const d = await api('/api/risk/rows');
    $('#riskSummary').textContent = `已缓存 ${d.rows.length} 条`;
    $('#riskCounts').innerHTML = riskCountChips(d.counts || {});
    const items = d.rows.map((row) => {
      let hits = [];
      try { hits = (JSON.parse(row.hits || '[]') || []).map((h) => h.name).filter(Boolean); }
      catch (e) { hits = []; }
      return { id: row.candidate_id, title: row.title, list_status: row.list_status,
               handling_level: row.handling_level, lint_level: row.lint_level,
               suggested_action: row.suggested_action, hits: hits.slice(0, 2),
               state: row.state, from_cache: true };
    });
    renderRiskTable(items);
  } catch (e) { toast(e.message, true); }
}

async function loadRiskStatus() {
  try {
    const s = await api('/api/risk/status');
    if (!s.available) {
      $('#riskVendor').innerHTML = '<b style="color:#e05a47">词库未就绪</b><br>' +
        esc((s.missing || []).slice(0, 2).join('、'));
      $('#riskMsg').className = 'hint-box err';
      $('#riskMsg').textContent = '缺少词库文件，无法体检；系统不会把「没查」当成「未命中」。';
      return;
    }
    $('#riskVendor').innerHTML = `词库 <b>${esc(s.db_date || '—')}</b> · ${num(s.records)} 条名称<br>` +
      `已缓存 ${num(s.cache.count)} 条 · ${esc(s.license)} 许可`;
    $('#riskDisclaimer').textContent = s.disclaimer;
    await renderRiskRows();
  } catch (e) {
    $('#riskVendor').textContent = '状态读取失败：' + e.message;
  }
}

/* ---------------- 设置 ---------------- */
function renderConfigForm() {
  if (!STATE) return;
  const cfg = lastConfig || {};
  if (cfg.interval_minutes) $('#sInterval').value = cfg.interval_minutes;
}
let lastConfig = null;
async function loadConfig() {
  try {
    lastConfig = await api('/api/config');
    $('#sInterval').value = lastConfig.interval_minutes;
    $('#sSource').value = lastConfig.default_source;
    $('#sCommand').value = lastConfig.fetch_command || '';
    const n = lastConfig.notify || {};
    $('#sNotifyType').value = n.type || 'wecom';
    $('#sNotifyEnabled').checked = !!n.enabled;
    $('#sWebhook').placeholder = n.webhook ? '已配置（留空表示不修改）' : 'https://...';
    $('#mcpText').textContent = await (await fetch('/api/mcp')).text();
  } catch (e) { /* 配置接口失败不阻塞看板 */ }
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $$('.tab').forEach((t) => t.addEventListener('click', () => {
    $$('.tab').forEach((x) => x.classList.toggle('active', x === t));
    $$('.panel').forEach((p) => p.classList.toggle('active', p.id === 'tab-' + t.dataset.tab));
    if (t.dataset.tab === 'logs') renderLogs();
    if (t.dataset.tab === 'settings') loadConfig();
    if (t.dataset.tab === 'risk') loadRiskStatus();
  }));
  $('#search').addEventListener('input', renderBoard);
  $('#onlyAction').addEventListener('change', (e) => { onlyAction = e.target.checked; renderBoard(); });
  $('#onlySafe').addEventListener('change', renderBoard);
  $$('.seg[data-sort]').forEach((b) => b.addEventListener('click', () => {
    $$('.seg[data-sort]').forEach((x) => x.classList.toggle('active', x === b));
    sortBy = b.dataset.sort; renderBoard();
  }));
  $$('.seg[data-level]').forEach((b) => b.addEventListener('click', () => {
    $$('.seg[data-level]').forEach((x) => x.classList.toggle('active', x === b));
    logLevel = b.dataset.level; renderLogs();
  }));

  ['#tPrice', '#tSold', '#tDelta'].forEach((s) => $(s).addEventListener('input', runTrial));
  $('#tPartial').addEventListener('change', runTrial);
  $$('.btn[data-preset]').forEach((b) => b.addEventListener('click', () => {
    const [sold, delta, price] = b.dataset.preset.split(',');
    $('#tSold').value = sold; $('#tDelta').value = delta; $('#tPrice').value = price;
    runTrial();
  }));

  $('#btnRefresh').addEventListener('click', () => { loadState(); toast('已刷新'); });
  $('#btnCollect').addEventListener('click', async () => {
    toast('正在采集一轮…');
    try {
      const r = await post('/api/collect', {});
      toast(r.busy ? r.reason : `采集完成：成功 ${r.summary?.ok ?? 0}，失败 ${r.summary?.failed ?? 0}，模糊值 ${r.summary?.imprecise ?? 0}`);
      await loadState();
    } catch (e) { toast(e.message, true); }
  });
  $('#btnExport').addEventListener('click', () => window.open('/api/export', '_blank'));
  $('#btnCloseBanner').addEventListener('click', () => {
    sessionStorage.setItem('hideDemo', '1'); $('#demoBanner').classList.add('hidden');
  });

  $('#btnAdd').addEventListener('click', async () => {
    try {
      const r = await post('/api/products', {
        text: $('#addText').value, category: $('#addCategory').value,
        price: $('#addPrice').value === '' ? null : +$('#addPrice').value,
        source: $('#addSource').value,
      });
      $('#addResult').className = 'hint-box ok';
      $('#addResult').textContent = `新增 ${r.added} · 已存在 ${r.duplicate} · 无效 ${r.invalid} · 跳过 ${r.skipped}`;
      await loadState(true); toast('已加入监控队列');
    } catch (e) { $('#addResult').className = 'hint-box err'; $('#addResult').textContent = e.message; }
  });
  $('#btnProbe').addEventListener('click', async () => {
    try {
      const r = await post('/api/products', {
        text: $('#addText').value, source: $('#addSource').value, probe: true,
      });
      $('#addResult').className = 'hint-box';
      $('#addResult').innerHTML = `<b>解析 ${r.parsed} 条，无效 ${r.invalid} 条（未写入数据库）</b><br>` +
        (r.preview || []).map((p) => `${esc(p.id.slice(0, 12))}：${p.ok
          ? `${esc(p.title || '—')} · ${esc(p.sold_raw || '—')} · ${p.precise ? '精确读数' : '读数不精确'}`
          : `失败 ${esc(p.reason || '')}`}`).join('<br>');
    } catch (e) { toast(e.message, true); }
  });
  $('#btnTemplate').addEventListener('click', () => {
    $('#csvText').value = '商品名称,商品链接,分类,客单价,笔记数,竞争家数,时间节点,店铺\n'
      + '假期安全教育PPT,https://www.xiaohongshu.com/goods-detail/66a1b2c3d4e5f60718293a4b,教师资料,9.9,96,3,假期前,示例小店\n'
      + '健身知识库,,健身,19.9,1200,28,,示例小店';
  });
  $('#btnImport').addEventListener('click', async () => {
    try {
      const r = await post('/api/import', { csv: $('#csvText').value, source: 'manual' });
      toast(`已解析 ${r.parsed} 行，新增 ${r.added}，已存在 ${r.duplicate}`);
      await loadState(true);
    } catch (e) { toast(e.message, true); }
  });

  $('#btnRiskCheck').addEventListener('click', () => {
    let c;
    try { c = parseRiskInput($('#riskInput').value); }
    catch (e) {
      $('#riskMsg').className = 'hint-box err';
      $('#riskMsg').textContent = 'JSON 解析失败：' + e.message;
      return;
    }
    if (!c.length) {
      $('#riskMsg').className = 'hint-box err';
      $('#riskMsg').textContent = '先粘贴至少一个商品标题，或点「体检全部监控商品」。';
      return;
    }
    runRiskCheck({ candidates: c }, '批量体检');
  });
  $('#btnRiskAll').addEventListener('click', () => runRiskCheck({}, '全量体检'));
  $('#btnRiskForce').addEventListener('click', () => {
    let c = [];
    try { c = parseRiskInput($('#riskInput').value); } catch (e) { c = []; }
    runRiskCheck(c.length ? { candidates: c, force: true } : { force: true }, '强制重查');
  });
  $('#btnRiskExport').addEventListener('click', () => window.open('/api/risk/export', '_blank'));
  $('#btnRiskClear').addEventListener('click', async () => {
    if (!confirm('清空全部体检结果缓存？商品与销量数据不受影响。')) return;
    await api('/api/risk/cache', { method: 'DELETE' });
    $('#riskDetail').classList.add('hidden');
    await loadRiskStatus();
    await loadState(true);
    toast('体检缓存已清空');
  });

  $('#btnClearLogs').addEventListener('click', async () => {
    if (!confirm('清空全部运行日志？')) return;
    await api('/api/logs', { method: 'DELETE' }); renderLogs();
  });
  $('#btnSaveConfig').addEventListener('click', async () => {
    const body = {
      interval_minutes: +$('#sInterval').value || 60,
      default_source: $('#sSource').value,
      fetch_command: $('#sCommand').value,
      notify: { enabled: $('#sNotifyEnabled').checked, type: $('#sNotifyType').value },
    };
    if ($('#sWebhook').value) body.notify.webhook = $('#sWebhook').value;
    try {
      await post('/api/config', body);
      $('#configResult').className = 'hint-box ok';
      $('#configResult').textContent = '已保存；周期改动从下一个自然整点生效';
      $('#sWebhook').value = '';
      await loadConfig(); await loadState(true);
    } catch (e) { toast(e.message, true); }
  });
  $('#btnResetDemo').addEventListener('click', async () => {
    if (!confirm('清空所有演示商品及其快照？真实商品不受影响。')) return;
    await post('/api/demo', { count: 0, reset: true });
    await loadState(); toast('演示数据已清空');
  });
  $('#btnCleanup').addEventListener('click', async () => {
    const r = await post('/api/cleanup', { keep_days: 90, log_days: 30 });
    toast(`清理完成：快照 ${r.snapshots_removed} 条，日志 ${r.logs_removed} 条，保留基线 ${r.baselines_kept} 条`);
    await loadState(true);
  });

  $('#btnCloseModal').addEventListener('click', () => $('#modal').classList.add('hidden'));
  $('#modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') $('#modal').classList.add('hidden');
  });
  $('#btnSaveResearch').addEventListener('click', saveResearch);
  $('#btnToggleActive').addEventListener('click', async () => {
    const p = STATE.products.find((x) => x.id === currentId);
    await post('/api/product/' + encodeURIComponent(currentId), { active: p.active ? 0 : 1 });
    toast(p.active ? '已暂停监控' : '已恢复监控');
    $('#modal').classList.add('hidden'); await loadState(true);
  });
  $('#btnDeleteProduct').addEventListener('click', async () => {
    if (!confirm('删除该商品及全部历史快照？删除后不会再自动加入。')) return;
    await api('/api/product/' + encodeURIComponent(currentId), { method: 'DELETE' });
    $('#modal').classList.add('hidden'); await loadState(true); toast('已删除');
  });

  setInterval(() => { if ($('#modal').classList.contains('hidden')) loadState(true); }, 60000);
}

bind();
loadState();
loadConfig();
runTrial();
