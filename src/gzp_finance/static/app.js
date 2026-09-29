const $ = (s) => document.querySelector(s);
const csrf = $('meta[name="csrf-token"]').content;
const euro = new Intl.NumberFormat('es-ES', {style: 'currency', currency: 'EUR'});
let offset = 0, total = 0, selected = null;
let vocabulary = {fields: {}, subtypes: {}};
const NEW_VALUE = '__finance_add_label__';

function selectOptions(field, values, current = '') {
  const select = $(`[data-target="${field}"]`);
  select.replaceChildren(new Option('Sin asignar', ''));
  for (const value of [...new Set([...values, current].filter(Boolean))].sort((a,b) => a.localeCompare(b, 'es', {numeric:true}))) select.add(new Option(value, value));
  select.add(new Option('Añadir otro valor…', NEW_VALUE));
  select.value = current;
}
function fieldValue(field) {
  const control = $(`[data-target="${field}"]`);
  return control.value === NEW_VALUE ? $(`[data-new-value="${field}"]`).value : control.value;
}
function filterSubtypes() {
  const current = fieldValue('target_subtipo');
  const category = fieldValue('target_categoria_general');
  const values = vocabulary.subtypes[category] || vocabulary.fields.target_subtipo || [];
  // Preserve an existing partial/inconsistent label until the user explicitly edits it.
  selectOptions('target_subtipo', values, current);
  $('[data-new-value="target_subtipo"]').hidden = true;
}
for (const select of $('#fields').querySelectorAll('select')) select.onchange = () => {
  const newInput = $(`[data-new-value="${select.name}"]`);
  newInput.hidden = select.value !== NEW_VALUE;
  if (!newInput.hidden) newInput.focus();
  if (select.name === 'target_categoria_general') filterSubtypes();
};

const fold = value => value.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLocaleLowerCase('es');
for (const input of $('#fields').querySelectorAll('input[data-target]')) {
  const list = $('#options-' + input.name);
  let matches = [], active = -1;
  const hide = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
  const choose = index => { if (matches[index] !== undefined) { input.value = matches[index]; hide(); input.focus(); } };
  const highlight = index => {
    active = index;
    for (const [i, option] of [...list.children].entries()) option.setAttribute('aria-selected', String(i === active));
    input.setAttribute('aria-activedescendant', `${list.id}-${active}`);
    list.children[active]?.scrollIntoView({block: 'nearest'});
  };
  const suggest = () => {
    const query = fold(input.value.trim());
    matches = query ? (vocabulary.fields[input.name] || []).filter(value => fold(value).includes(query) && value !== input.value).slice(0, 12) : [];
    active = -1; list.replaceChildren(); input.removeAttribute('aria-activedescendant');
    if (!matches.length) { hide(); return; }
    for (const [index, value] of matches.entries()) {
      const option = node('button', value); option.type = 'button'; option.id = `${list.id}-${index}`;
      option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false'); option.tabIndex = -1;
      option.onmousedown = e => e.preventDefault(); option.onclick = () => choose(index); list.append(option);
    }
    list.hidden = false; input.setAttribute('aria-expanded', 'true');
  };
  input.addEventListener('input', suggest);
  input.addEventListener('focus', suggest);
  input.addEventListener('blur', hide);
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') { if (!list.hidden) { e.preventDefault(); e.stopPropagation(); hide(); } }
    else if (!list.hidden && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      highlight(active < 0 ? (e.key === 'ArrowDown' ? 0 : matches.length - 1) :
        (active + (e.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
    } else if (!list.hidden && e.key === 'Enter') { e.preventDefault(); choose(active < 0 ? 0 : active); }
  });
}

async function api(path, options = {}) {
  options.headers = {...options.headers, 'X-CSRF-Token': csrf};
  if (options.body && !(options.body instanceof FormData)) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, options);
  const body = await res.json();
  if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'No se ha podido guardar. Revisa los campos.');
  return body;
}
function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function notice(message, error = false) { $('#notice').textContent = message; $('#notice').hidden = false; $('#notice').className = error ? 'error' : 'success'; }
function guarded(fn) { return async (...args) => { try { await fn(...args); } catch(e) { notice(e.message, true); } }; }

async function loadTransactions() {
  const params = new URLSearchParams({bank: $('#filter-bank').value, status: $('#filter-status').value, offset, limit: 50});
  const result = await api(`/api/transactions?${params}`); total = result.total;
  const tbody = $('#transactions'); tbody.replaceChildren();
  for (const tx of result.items) {
    const tr = node('tr'); const date = node('td');
    date.append(node('strong', new Date(tx.bank_date + 'T12:00:00').toLocaleDateString('es-ES')), node('small', tx.source_bank));
    const classification = node('td');
    for (const f of ['target_tipo_transaccion', 'target_categoria_general', 'target_subtipo']) {
      if (tx.values[f]) {
        const p = tx.provenance[f];
        classification.append(node('div', `${tx.values[f]}${p ? ` · ${p.engine === 'manual' ? 'manual' : p.engine === 'rule' ? 'regla' : 'ML'}${p.engine === 'ml' ? ` ${Math.round(p.confidence * 100)}%` : ''}` : ''}`, 'classification'));
      }
    }
    if (!classification.childNodes.length) classification.append(node('span', 'Sin propuesta', 'muted'));
    const state = node('td'); state.append(node('span', tx.status, `badge ${tx.confirmed ? 'confirmed' : ''}`));
    const action = node('td'), button = node('button', tx.confirmed ? 'Editar' : 'Revisar', 'secondary');
    button.onclick = guarded(() => edit(tx.id)); action.append(button);
    tr.append(date, node('td', tx.concept_raw), node('td', euro.format(tx.amount_eur), 'amount'), classification, state, action); tbody.append(tr);
  }
  if (!result.items.length) { const tr = node('tr'), td = node('td', 'No hay movimientos aquí. Puedes importar un extracto o cambiar los filtros.', 'empty'); td.colSpan = 6; tr.append(td); tbody.append(tr); }
  $('#count').textContent = total ? `${offset + 1}–${Math.min(offset + 50, total)} de ${total} movimientos` : '0 movimientos';
  $('#previous').disabled = offset === 0; $('#next').disabled = offset + 50 >= total;
}
async function edit(id) {
  [selected, vocabulary] = await Promise.all([api(`/api/transactions/${id}`), api('/api/vocabulary')]);
  $('#editor-title').textContent = selected.transaction.concept_raw;
  $('#editor-summary').textContent = `${selected.transaction.source_bank} · ${selected.transaction.bank_date} · ${euro.format(selected.transaction.amount_eur)}`;
  const form = $('#confirmation'); form.reset(); $('#editor-error').hidden = true; $('#custom-label').hidden = true;
  for (const input of $('#fields').querySelectorAll('[data-target]')) {
    const values = vocabulary.fields[input.name] || [];
    if (input.tagName === 'SELECT') {
      selectOptions(input.name, values, selected.values[input.name] || '');
      $(`[data-new-value="${input.name}"]`).hidden = true;
    } else {
      input.value = selected.values[input.name] || '';
      const list = $('#options-' + input.name); list.replaceChildren(); list.hidden = true;
      input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant');
    }
    const p = selected.provenance[input.name];
    const suggestions = selected.predictions.filter(x => x.field === input.name && x.engine === 'ml');
    const suggestion = suggestions.at(-1);
    $(`[data-origin="${input.name}"]`).textContent = p ? `${p.engine === 'rule' ? 'Regla' : p.engine === 'manual' ? 'Manual' : 'ML'} · ${Math.round(p.confidence * 100)}%${p.rule_id ? ` · ${p.rule_id}` : ''}` : suggestion ? `Sugerencia ML: ${suggestion.value} · ${Math.round(suggestion.confidence * 100)}%` : 'Sin resolver; puede quedar vacío';
  }
  filterSubtypes();
  form.elements.custom_match.value = JSON.stringify({source_bank: selected.transaction.source_bank, concept_key: selected.transaction.concept_key}, null, 2);
  $('#evidence').textContent = JSON.stringify({bank: selected.transaction, predictions: selected.predictions}, null, 2);
  $('#editor').showModal();
}
$('#confirmation').onsubmit = async (event) => {
  event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]'); submit.disabled = true;
  try {
    const values = Object.fromEntries([...$('#fields').querySelectorAll('[data-target]')].map(input => [input.name, fieldValue(input.name)]));
    const mode = form.elements.rule_mode.value;
    await api(`/api/transactions/${selected.transaction.id}/confirm`, {method: 'POST', body: {values, revision: selected.revision, training: form.elements.training.checked, rule_mode: mode, custom_match: mode === 'custom' ? JSON.parse(form.elements.custom_match.value) : null}});
    $('#editor').close(); notice('Clasificación confirmada.'); await loadTransactions();
  } catch(e) { $('#editor-error').hidden = false; $('#editor-error').textContent = e.message; }
  finally { submit.disabled = false; }
};
$('#confirmation').elements.rule_mode.onchange = (e) => { $('#custom-label').hidden = e.target.value !== 'custom'; };
$('#close-editor').onclick = $('#cancel-editor').onclick = () => $('#editor').close();
$('#upload').onsubmit = guarded(async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true;
  try { const r = await api('/api/imports', {method: 'POST', body: new FormData(event.currentTarget)});
    $('#import-result').hidden = false; $('#import-result').textContent = `${r.inserted} movimientos nuevos\n${r.duplicates} duplicados\n${r.grouped_fills} fills agrupados\n${r.skipped_non_cash} migraciones sin efectivo omitidas${r.same_file ? '\nEste extracto ya estaba importado.' : ''}`;
    notice('Extracto procesado. Ya puedes revisar sus movimientos.'); offset = 0; await loadTransactions();
  } finally { button.disabled = false; }
});
async function loadRules() {
  const [rules, candidates] = await Promise.all([api('/api/rules'), api('/api/candidates')]);
  $('#rules-list').replaceChildren(); $('#candidates-list').replaceChildren();
  for (const r of rules) {
    const card = node('article', undefined, 'card rule'); card.append(node('h3', r.match.concept_key || r.match.bank_type || r.match.source_bank), node('p', `${r.source} · soporte ${r.support} · prioridad ${r.priority}`, 'hint'), node('pre', JSON.stringify({match: r.match, campos: r.set}, null, 2)));
    const toggle = node('button', r.enabled ? 'Desactivar' : 'Activar', 'secondary');
    toggle.onclick = guarded(async () => { toggle.disabled = true; await api(`/api/rules/${encodeURIComponent(r.id)}/enabled`, {method: 'POST', body: {enabled: !r.enabled}}); await loadRules(); }); card.append(toggle); $('#rules-list').append(card);
  }
  if (!rules.length) $('#rules-list').append(node('p', 'Todavía no hay reglas cargadas.', 'muted'));
  for (const c of candidates) {
    if (rules.some(r => r.enabled && JSON.stringify(r.match) === JSON.stringify(c.match) && Object.entries(c.set).every(([k,v]) => r.set[k] === v))) continue;
    const card = node('article', undefined, 'card rule'); card.append(node('h3', c.match.concept_key), node('p', `${c.match.source_bank} · ${c.support} movimientos consistentes`, 'hint'), node('pre', JSON.stringify({match: c.match, campos: c.set}, null, 2)));
    const button = node('button', 'Confirmar como regla'); button.onclick = guarded(async () => { button.disabled = true; await api('/api/candidates/accept', {method: 'POST', body: c}); await loadRules(); }); card.append(button); $('#candidates-list').append(card);
  }
  if (!$('#candidates-list').childNodes.length) $('#candidates-list').append(node('p', 'No hay propuestas nuevas con suficiente repetición.', 'muted'));
}
for (const tab of document.querySelectorAll('.tab')) tab.onclick = guarded(async () => {
  for (const p of document.querySelectorAll('.panel')) p.hidden = p.id !== tab.dataset.panel;
  for (const t of document.querySelectorAll('.tab')) t.classList.toggle('active', t === tab);
  if (tab.dataset.panel === 'review') await loadTransactions(); if (tab.dataset.panel === 'rules') await loadRules();
});
for (const id of ['filter-bank', 'filter-status']) $('#' + id).onchange = guarded(async () => { offset = 0; await loadTransactions(); });
$('#previous').onclick = guarded(async () => { offset = Math.max(0, offset - 50); await loadTransactions(); });
$('#next').onclick = guarded(async () => { offset += 50; await loadTransactions(); });
guarded(loadTransactions)();
