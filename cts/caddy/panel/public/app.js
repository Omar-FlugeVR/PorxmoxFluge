import { ScanRunner } from './scanner.js';
import { validateScanRange, validateScannerConfiguration, isIpInConfiguredDhcpRange } from './scanner-config.js';
import { mergeHosts } from './inventory.js';
import { NdiModule } from './ndi.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const uid = prefix => `${prefix}-${crypto.randomUUID?.() || Math.random().toString(36).slice(2)}`;
let session, inventory, runtime, config, scannerConfig, users;
let view = 'services', selected = new Set(), editorState, categoryDeletion, busy = false;
let scanResults = [], scanFilter = 'alive', scanPage = 0, scanRangeLabel = '', allNetworksRange = '', scanning = false;
const runner = new ScanRunner(request);
const admin = () => session?.role === 'admin';
const ndiModule = new NdiModule({request:(path,options={})=>request('',{...options,path}),save:settings=>saveConfig({ndi:settings}),feedback,
  onDisabled:()=>{if(view==='ndi')switchView('services');}});

async function request(action, options = {}) {
  const method = options.method || 'GET';
  const response = await fetch(options.path || `api.php${method === 'GET' ? `?action=${encodeURIComponent(action)}` : ''}`, {
    method, credentials: 'same-origin', signal: options.signal,
    headers: method === 'POST' ? { 'Content-Type': 'application/json', 'X-CSRF-Token': session?.csrfToken || '' } : {},
    ...(method === 'POST' ? {body: JSON.stringify({action, ...options.data})} : {})
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && action !== 'login') {
      ndiModule.setView(false);
      runner.cancel(); $('app').hidden = true; $('loginScreen').hidden = false;
    }
    const error = new Error(data.message || data.dns_sync?.errors?.join(' ') || 'No se pudo completar la operación.');
    error.status = response.status; error.data = data; throw error;
  }
  return data;
}
const post = (action, data) => request(action, {method: 'POST', data});
function feedback(message, type = '') {
  $('feedback').textContent = message; $('feedback').className = `feedback ${type}`; $('feedback').hidden = !message;
}
function guard(fn) { return async event => { try { await fn(event); } catch (error) { feedback(error.message, 'error'); } }; }
function hosts() { return mergeHosts(inventory.services, inventory.ips); }
function hostServiceAction(host) {
  const label = host.linkedServices.length ? 'Editar servicio' : 'Añadir servicio';
  return `<button class="edit-button" data-host-service="${esc(host.ip)}">${label}</button>`;
}
function domainLabel() { return `.${runtime.baseDomain}`; }
function renderInstallationName() {
  const name = runtime.installationName || '';
  $('installationName').textContent = name;
  $('installationName').title = name;
  $('installationName').hidden = !name;
  document.querySelector('.header-identity').classList.toggle('has-installation', Boolean(name));
}
function renderSync() {
  const sync = runtime.lastSync;
  const current = sync?.ok && sync.revision === inventory.revision && (!config || sync.configRevision === config.configRevision);
  const needsRetry = !current && (typeof sync?.at === 'string' || inventory.revision > 1 || config?.configRevision > 1);
  const text = current ? `Accesos al día · ${new Date(sync.at).toLocaleString('es-ES')}` : needsRetry ? 'Cambios guardados. No se ha podido confirmar la actualización de los accesos.' : 'Los accesos se actualizan automáticamente al guardar.';
  $('syncStatus').textContent = text;
  $('retryButton').hidden = !needsRetry;
  $('footerStatus').textContent = current ? 'Accesos actualizados' : needsRetry ? 'Error al actualizar los accesos' : 'Aplicación automática';
}
async function load() {
  [inventory, runtime] = await Promise.all([request('data'), request('runtime')]);
  selected = new Set(inventory.categories.map(c => c.name));
  if (admin()) {
    [config, scannerConfig, users] = await Promise.all([request('get_config'), request('get_scanner_config'), request('users')]);
    scannerConfig = validateScannerConfiguration(scannerConfig);
  }
  $('username').textContent = session.username;
  document.querySelectorAll('[data-admin]').forEach(el => el.hidden = !admin());
  $('demoBadge').hidden = $('demoNotice').hidden = !runtime.demo;
  $('inspectButton').hidden = !runtime.demo;
  renderInstallationName();
  ndiModule.configure(runtime.ndi,runtime.ndi_mock);
  $('loginScreen').hidden = true; $('app').hidden = false;
  if (!admin() && ['settings','scan'].includes(view)) view = 'services';
  if (admin()) fillSettings();
  switchView(view); renderSync();
}
function switchView(next) {
  if(next==='ndi'&&!runtime.ndi.enabled)next='services';
  view = next;
  document.querySelectorAll('[data-view]').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('directory').hidden = !['services','devices'].includes(view);
  $('scanner').hidden = view !== 'scan'; $('settings').hidden = view !== 'settings';
  $('ndiView').hidden = view !== 'ndi'; ndiModule.setView(view==='ndi');
  $('viewTitle').textContent = view === 'devices' ? 'Equipos del estudio' : 'Servicios del estudio';
  $('search').placeholder = view === 'devices' ? 'Buscar equipo, IP o MAC…' : 'Buscar servicio, IP o dominio…';
  $('addButton').textContent = view === 'devices' ? '+ Añadir equipo' : '+ Añadir servicio';
  $('serviceGrid').hidden = view === 'devices'; $('deviceTable').hidden = view !== 'devices';
  renderDirectory(); if (view === 'scan') renderScan();
}
function renderDirectory() {
  const allHosts = hosts();
  $('serviceCount').textContent = inventory.services.length; $('deviceCount').textContent = allHosts.length;
  const all = inventory.categories.every(c => selected.has(c.name));
  $('categories').innerHTML = `<button data-category="*" class="${all ? 'selected' : ''}">Todos</button>` + inventory.categories.map(c => `<button data-category="${esc(c.name)}" class="${selected.has(c.name) ? 'selected' : ''}">${esc(c.name)}</button>`).join('');
  const query = norm($('search').value);
  const list = (view === 'devices' ? allHosts : inventory.services).filter(item => selected.has(view === 'devices' ? item.tag : item.category) && norm([item.name,item.hostname,item.url,item.ip,item.domain,item.description,item.mac,item.notes,item.tag,item.category].join(' ')).includes(query));
  $('viewCount').textContent = `${list.length} de ${view === 'devices' ? allHosts.length : inventory.services.length}`;
  $('empty').hidden = list.length > 0;
  if (view === 'devices') {
    $('deviceTable').innerHTML = `<table><thead><tr><th>Dirección IP</th><th>Equipo</th><th>Categoría</th><th>Datos adicionales</th><th>Acciones</th></tr></thead><tbody>${list.map(host => `<tr><td><code>${esc(host.ip)}</code>${host.mac ? `<p class="hint">${esc(host.mac)}</p>` : ''}</td><td><strong>${esc(host.hostname)}</strong><p class="hint">${host.linkedServices.length} servicios</p></td><td>${esc(host.tag)}</td><td class="notes">${esc(host.notes)}${host.sshEnabled ? `<p class="hint"><button class="text-button" data-copy="ssh ${esc(host.sshUser || 'root')}@${esc(host.ip)}">Copiar SSH</button></p>` : ''}</td><td><div class="cell-actions"><button class="edit-button" data-copy="${esc(host.ip)}">Copiar IP</button>${admin() ? `<button class="edit-button" data-host="${esc(host.ip)}">Editar</button>${hostServiceAction(host)}${!host.isFromService ? `<button class="edit-button danger-text" data-delete-ip="${esc(host.id)}">Eliminar</button>` : ''}` : ''}</div></td></tr>`).join('')}</tbody></table>`;
    return;
  }
  const groups = new Map(); for (const item of list) { if (!groups.has(item.category)) groups.set(item.category, []); groups.get(item.category).push(item); }
  $('serviceGrid').innerHTML = [...groups].map(([category, items]) => `<section class="group ${items.length > 1 ? 'wide' : ''}"><h2 class="group-heading">${esc(category)} <span>${items.length}</span></h2><div class="cards">${items.map(serviceCard).join('')}</div></section>`).join('');
}
function serviceCard(item) {
  const dns = item.proxyMode === 'dns_only';
  const initials = item.name.split(/\s+/).map(s => s === s.toUpperCase() ? s : s[0]).join('').slice(0,3);
  return `<article class="service-card"><div class="card-top"><span class="service-symbol">${esc(initials)}</span><span class="access-type">${dns ? 'Servicio de red' : 'Interfaz web'}</span></div>${admin() ? `<div class="card-admin"><button class="edit-button" data-edit="${esc(item.id)}">Editar</button></div>` : ''}<h3>${dns ? esc(item.name) : `<a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.name)}</a>`}</h3><p>${esc(item.description || 'Sin descripción')}</p><div class="endpoint">${esc(dns ? item.ip : item.url)}</div><div class="card-actions">${dns ? '<span class="dns-note">DNS directo · sin interfaz web</span>' : `<a class="primary" href="${esc(item.url)}" target="_blank" rel="noopener">Abrir servicio</a>`}${item.domain ? (dns ? `<code class="shortcut">${esc(item.domain)}</code>` : `<a class="shortcut" href="http://${esc(item.domain)}/" target="_blank" rel="noopener">${esc(item.domain)}</a>`) : ''}</div></article>`;
}
function categoryOptions(value = '') { return `<input name="category" list="categoryNames" value="${esc(value)}" required maxlength="120"><datalist id="categoryNames">${inventory.categories.map(c => `<option value="${esc(c.name)}">`).join('')}</datalist>`; }
function renderNetworkSettings() {
  $('networkList').innerHTML = config.scanNetworks.map(network => `<div class="network-row"><div class="network-summary"><strong>${esc(network.name)}</strong><code>${esc(network.cidr)}</code><p class="hint">${network.dhcpPool ? `DHCP: ${esc(network.dhcpPool.start)} – ${esc(network.dhcpPool.end)}` : 'Sin pool DHCP'}</p></div><div class="cell-actions"><button class="edit-button" data-edit-network="${esc(network.id)}">Editar</button><button class="edit-button danger-text" data-delete-network="${esc(network.id)}" ${config.scanNetworks.length === 1 ? 'disabled title="Debes conservar al menos una red"' : ''}>Eliminar</button></div></div>`).join('');
}
function renderScanPresets() {
  const range = $('scanRange').value.trim();
  const allSelected = range === allNetworksRange;
  $('scanPresets').innerHTML = `<button data-all-networks="true" class="${allSelected ? 'selected' : ''}" title="${esc(allNetworksRange)}">Todas las redes</button>` + config.scanNetworks.map(network => `<button data-network-preset="${esc(network.id)}" class="${!allSelected && range === network.cidr ? 'selected' : ''}" title="${esc(network.cidr)}">${esc(network.name)} <code>${esc(network.cidr)}</code></button>`).join('');
  const network = config.scanNetworks.find(item => item.cidr === range);
  $('scanHelp').textContent = allSelected ? `Todas las redes · ${config.scanNetworks.map(item => item.name).join(' ; ')} · Pulsa Iniciar escaneo para comprobarlas juntas. Cada IP utiliza el pool DHCP de su red.` : network ? `${network.name} · ${network.cidr} · ${network.dhcpPool ? `DHCP: ${network.dhcpPool.start} – ${network.dhcpPool.end}` : 'Sin pool DHCP'}` : `Redes permitidas: ${config.scanAllowedCidrs.join(' ; ')} · Cada IP utiliza el pool DHCP de su red.`;
}
function openNetworkDelete(id) {
  const network = config.scanNetworks.find(item => item.id === id);
  if (!network || config.scanNetworks.length === 1) return;
  const revision = config.configRevision;
  const remaining = config.scanNetworks.filter(item => item.id !== id);
  $('confirmTitle').textContent = 'Eliminar red';
  $('confirmText').textContent = `Se quitará «${network.name} · ${network.cidr}» de las redes permitidas y los presets del escáner.`;
  $('confirmAction').textContent = 'Eliminar red';
  $('confirmAction').onclick = guard(async () => {
    $('confirmAction').disabled = true;
    try { await saveConfig({scanNetworks:remaining}, revision); $('confirmDialog').close(); }
    finally { $('confirmAction').disabled = false; }
  });
  $('confirmDialog').showModal();
}
function renderCategorySettings() {
  const equipment = hosts();
  $('categoryList').innerHTML = inventory.categories.map(category => {
    const services = inventory.services.filter(service => service.category === category.name).length;
    const devices = equipment.filter(host => host.tag === category.name).length;
    const last = inventory.categories.length === 1;
    return `<div class="category-row"><div class="category-summary"><strong>${esc(category.name)}</strong><p class="hint">${services} ${services === 1 ? 'servicio' : 'servicios'} · ${devices} ${devices === 1 ? 'equipo' : 'equipos'}</p></div><div class="cell-actions"><button class="edit-button" data-edit-category="${esc(category.id)}">Editar</button><button class="edit-button danger-text" data-delete-category="${esc(category.id)}" ${last ? 'disabled title="Debes conservar al menos una categoría"' : ''}>Eliminar</button></div></div>`;
  }).join('');
  if (inventory.categories.length === 1) $('categoryList').insertAdjacentHTML('beforeend', '<p class="hint category-minimum">Conserva al menos una categoría para organizar los servicios y equipos.</p>');
}
function replaceInventory(next, selectCategoryId = null) {
  const previous = new Map(inventory.categories.map(category => [category.id, category]));
  selected = new Set(next.categories.filter(category => !previous.has(category.id) || selected.has(previous.get(category.id).name) || category.id === selectCategoryId).map(category => category.name));
  inventory = next;
  renderDirectory(); renderSync();
  if (admin()) renderCategorySettings();
  if (view === 'scan') renderScan();
}
function openCategoryDelete(id) {
  const category = inventory.categories.find(item => item.id === id);
  const others = inventory.categories.filter(item => item.id !== id);
  if (!category || !others.length) return;
  const used = inventory.services.some(service => service.category === category.name) || inventory.ips.some(host => host.tag === category.name);
  categoryDeletion = {id, name:category.name, revision:inventory.revision};
  $('categoryDeleteText').textContent = used ? `Los servicios y equipos de «${category.name}» se moverán a la categoría que elijas.` : `Se eliminará «${category.name}». No tiene servicios ni equipos asociados.`;
  $('categoryDeleteError').textContent = '';
  $('categoryReplacementField').hidden = !used;
  $('categoryReplacement').disabled = !used;
  $('categoryReplacement').required = used;
  $('categoryReplacement').innerHTML = '<option value="">Selecciona una categoría</option>' + others.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
  $('categoryDeleteDialog').showModal();
}
async function deleteCategory(event) {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  $('categoryDeleteError').textContent = '';
  try {
    const replacementId = $('categoryReplacement').disabled ? '' : $('categoryReplacement').value;
    const keepSelected = selected.has(categoryDeletion.name);
    const result = await post('delete_category', {id:categoryDeletion.id,revision:categoryDeletion.revision,replacementId});
    replaceInventory(result.inventory, keepSelected ? replacementId : null);
    showPublication(result.dns_sync);
    $('categoryDeleteDialog').close();
  } catch (error) { $('categoryDeleteError').textContent = error.message; }
  finally { button.disabled = false; }
}
function openEditor(type, record = null, extra = {}) {
  if (!admin()) return;
  if (type === 'password' && (!session.isPrimary || !record)) return;
  editorState = {type, record, extra, configRevision:config.configRevision, usersRevision:users.revision}; $('editorError').textContent = '';
  $('editorTitle').textContent = {service:record ? 'Editar servicio' : 'Añadir servicio',device:record ? 'Editar equipo' : 'Registrar equipo',category:record ? 'Editar categoría' : 'Añadir categoría',network:record ? 'Editar red' : 'Añadir red',user:'Añadir usuario',password:`Cambiar contraseña · ${record?.username || ''}`}[type];
  $('editorForm').querySelector('[name=save]').textContent = 'Guardar';
  if (type === 'service') {
    const item = record || {name:extra.hostname || '',url:extra.url || '',domain:'',category:extra.tag || inventory.categories[0]?.name || 'Equipos',description:'',proxyMode:'redirect',ip:extra.ip || ''};
    const suffix = domainLabel(); const short = item.domain.endsWith(suffix) ? item.domain.slice(0,-suffix.length) : item.domain;
    $('editorFields').innerHTML = `<label>Nombre<input name="name" value="${esc(item.name)}" required maxlength="160"></label><div class="form-row"><label>Tipo de acceso<select name="mode"><option value="redirect">Con redirección · puerto</option><option value="dns_only">Sin redirección · solo DNS</option></select></label><label>Categoría${categoryOptions(item.category)}</label></div><label>Nombre en la red<div class="domain-input"><input name="subdomain" value="${esc(short)}" placeholder="mezclador" pattern="[a-z0-9][a-z0-9.-]*" maxlength="100"><span>${esc(suffix)}</span></div><span class="hint">Opcional. Déjalo vacío para guardar solo el acceso.</span></label><label id="urlField">URL de destino y puerto<input name="url" value="${esc(item.url)}" placeholder="http://192.168.1.30:8080" type="url"></label><label id="ipField" hidden>IP del equipo (solo DNS)<input name="ip" value="${esc(item.ip || extra.ip || '')}" placeholder="192.168.1.30"></label><label>Descripción<textarea name="description" maxlength="2048">${esc(item.description)}</textarea></label>${record ? `<button type="button" class="text-button danger-text" id="deleteService">Eliminar servicio</button>` : ''}`;
    $('editorForm').elements.mode.value = item.proxyMode; updateMode();
    $('editorForm').elements.mode.addEventListener('change', updateMode);
    if (record) $('deleteService').onclick = () => { $('editor').close(); askDelete('service', record); };
  } else if (type === 'device') {
    const item = record || {ip:extra.ip || '',hostname:extra.hostname || '',tag:inventory.categories[0]?.name || 'Equipos',mac:extra.mac || '',notes:extra.ports?.length ? `Puertos detectados: ${extra.ports.join(', ')}` : '',sshEnabled:extra.ports?.includes(22) || false,sshUser:'root'};
    $('editorFields').innerHTML = `<label>Nombre del equipo<input name="hostname" value="${esc(item.hostname)}" required maxlength="160"></label><div class="form-row"><label>Dirección IP<input name="ip" value="${esc(item.ip)}" required ${record?.isFromService ? 'readonly' : ''}></label><label>Categoría${categoryOptions(item.tag)}</label></div><label>Dirección MAC<input name="mac" value="${esc(item.mac)}" placeholder="AA:BB:CC:DD:EE:FF"></label><div class="form-row"><label>Acceso SSH<select name="sshEnabled"><option value="false">Desactivado</option><option value="true">Activado</option></select></label><label>Usuario SSH<input name="sshUser" value="${esc(item.sshUser || 'root')}"></label></div><label>Notas<textarea name="notes" maxlength="8192">${esc(item.notes || '')}</textarea></label>`;
    $('editorForm').elements.sshEnabled.value = String(item.sshEnabled);
  } else if (type === 'network') {
    $('editorFields').innerHTML = `<label>Nombre de la red<input name="name" value="${esc(record?.name || '')}" required maxlength="80" placeholder="Por ejemplo, Vídeo"></label><label>Red / CIDR<input name="cidr" value="${esc(record?.cidr || '')}" required maxlength="32" placeholder="192.168.20.0/24"></label><div class="form-row"><label>Pool DHCP desde<input name="dhcpStart" value="${esc(record?.dhcpPool?.start || '')}" placeholder="192.168.20.100" maxlength="15"></label><label>Pool DHCP hasta<input name="dhcpEnd" value="${esc(record?.dhcpPool?.end || '')}" placeholder="192.168.20.200" maxlength="15"></label></div><p class="hint">Deja ambos campos vacíos si esta red no tiene pool DHCP. Se usa para identificar las IP dinámicas; no cambia el router.</p>`;
  } else if (type === 'category') {
    $('editorFields').innerHTML = `<label>Nombre de la categoría<input name="name" value="${esc(record?.name || '')}" required maxlength="120" autocomplete="off" placeholder="Por ejemplo, Iluminación"></label><p class="hint">${record ? 'El nuevo nombre se actualizará también en los servicios y equipos asociados.' : 'La categoría estará disponible en Servicios, Equipos y sus formularios.'}</p>`;
  } else if (type === 'user') {
    $('editorFields').innerHTML = '<label>Nombre de usuario<input name="username" required minlength="3" maxlength="64" autocomplete="off"></label><label>Contraseña<input name="password" type="password" required autocomplete="new-password" minlength="8" maxlength="128"></label><label>Repite la contraseña<input name="confirmPassword" type="password" required autocomplete="new-password" minlength="8" maxlength="128"></label><p class="hint">Al menos 8 caracteres, una mayúscula, una minúscula y un número.</p><label>Permisos<select name="role"><option value="user">Usuario · consultar y abrir accesos</option><option value="admin">Administrador · gestionar y escanear</option></select></label>';
  } else {
    $('editorFields').innerHTML = '<label>Nueva contraseña<input name="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label><label>Repite la nueva contraseña<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label><p class="hint">Al menos 8 caracteres, una mayúscula, una minúscula y un número.</p>';
  }
  $('editor').showModal();
}
function updateMode() {
  const dns = $('editorForm').elements.mode.value === 'dns_only';
  $('urlField').hidden = dns; $('ipField').hidden = !dns;
  $('editorForm').elements.url.required = !dns; $('editorForm').elements.ip.required = dns;
}
async function saveInventory(draft) {
  feedback('Guardando cambios…');
  const result = await post('save_data', {...draft, revision:inventory.revision});
  replaceInventory({...draft, revision:result.revision, updatedAt:result.updatedAt});
  showPublication(result.dns_sync);
}
function showPublication(sync) {
  runtime.lastSync = sync;
  renderSync();
  if (sync.ok) feedback(runtime.demo ? 'Cambios guardados y accesos actualizados en la muestra.' : 'Cambios guardados y accesos actualizados.');
  else feedback(`Los cambios están guardados, pero no se han actualizado todos los accesos: ${(sync.errors || []).join(' ')} Puedes reintentar la actualización desde Configuración.`, 'warning');
}
async function applyInfrastructure() {
  feedback('Reintentando la actualización de los accesos…');
  try {
    const result = await post('sync_infrastructure', {revision:inventory.revision});
    showPublication(result.dns_sync);
    return true;
  } catch (error) {
    showPublication(error.data?.dns_sync || {ok:false,at:new Date().toISOString(),errors:[error.message]});
    return false;
  }
}
async function submitEditor(event) {
  event.preventDefault(); if (busy) return;
  busy = true; $('editorError').textContent = '';
  const buttons = [...$('editorForm').querySelectorAll('button')]; buttons.forEach(b => b.disabled = true);
  try {
    const fields = Object.fromEntries(new FormData($('editorForm'))); const {type, record} = editorState;
    if (['user','password'].includes(type) && fields[type === 'user' ? 'password' : 'newPassword'] !== fields.confirmPassword) throw new Error('Las contraseñas no coinciden.');
    if (type === 'network') {
      const network = {id:record?.id || uid('net'),name:fields.name.trim(),cidr:fields.cidr.trim(),dhcpPool:fields.dhcpStart.trim() || fields.dhcpEnd.trim() ? {start:fields.dhcpStart.trim(),end:fields.dhcpEnd.trim()} : null};
      const networks = structuredClone(config.scanNetworks);
      if (record) networks[networks.findIndex(item => item.id === record.id)] = network; else networks.push(network);
      await saveConfig({scanNetworks:networks}, editorState.configRevision);
    }
    else if (type === 'category') {
      const result = await post('save_category', {id:record?.id || '',name:fields.name,revision:inventory.revision});
      replaceInventory(result.inventory); showPublication(result.dns_sync);
    }
    else if (type === 'user') { await post('create_user', {...fields, revision:editorState.usersRevision, enabled:true}); users = await request('users'); renderUsers(); feedback('Usuario creado.'); }
    else if (type === 'password') { const result = await post('change_password', {...fields,id:record.id,revision:editorState.usersRevision}); session = {...session, ...result}; users = await request('users'); renderUsers(); feedback(`Contraseña de ${record.username} actualizada.`); }
    else {
      const draft = structuredClone(inventory);
      const category = fields.category.trim();
      if (!draft.categories.some(c => c.name === category)) draft.categories.push({id:uid('cat'),name:category,color:'#6db9b2'});
      if (type === 'service') {
        const item = {...record, id:record?.id || uid('srv'), name:fields.name.trim(),url:fields.mode === 'redirect' ? fields.url.trim() : '',ip:fields.mode === 'dns_only' ? fields.ip.trim() : '',domain:fields.subdomain.trim() ? fields.subdomain.trim().toLowerCase() + domainLabel() : '',proxyMode:fields.mode,category,description:fields.description.trim(),mac:record?.mac || '',sshEnabled:record?.sshEnabled || false,sshUser:record?.sshUser || 'root'};
        if (record) draft.services[draft.services.findIndex(s => s.id === record.id)] = item; else draft.services.push(item);
      } else {
        const item = {id:record && !record.isFromService ? record.id : uid('ip'),ip:fields.ip.trim(),hostname:fields.hostname.trim(),tag:category,mac:fields.mac.trim(),notes:fields.notes.trim(),sshEnabled:fields.sshEnabled === 'true',sshUser:fields.sshUser.trim()};
        if (record && !record.isFromService) draft.ips[draft.ips.findIndex(h => h.id === record.id)] = item; else draft.ips.push(item);
        if (record && record.ip !== item.ip) {
          for (const service of draft.services) {
            if (service.proxyMode === 'dns_only' && service.ip === record.ip) service.ip = item.ip;
            else if (service.url) { const url = new URL(service.url); if (url.hostname === record.ip) {url.hostname = item.ip; service.url = url.href.replace(/\/$/,'');} }
          }
        }
      }
      await saveInventory(draft);
    }
    $('editor').close();
  } catch (error) { $('editorError').textContent = error.message; }
  finally { busy = false; buttons.forEach(b => b.disabled = false); }
}
function askDelete(type, record) {
  $('confirmTitle').textContent = type === 'user' ? 'Eliminar usuario' : 'Eliminar del inventario';
  $('confirmText').textContent = `Se eliminará «${record.name || record.hostname || record.username}». ${type === 'user' ? '' : 'Se actualizarán sus accesos en AdGuard y Caddy.'}`;
  $('confirmAction').textContent = type === 'user' ? 'Eliminar usuario' : 'Eliminar';
  $('confirmAction').onclick = guard(async () => {
    $('confirmAction').disabled = true;
    try {
      if (type === 'user') { await post('delete_user', {id:record.id,revision:users.revision}); users = await request('users'); renderUsers(); }
      else { const draft = structuredClone(inventory); const key = type === 'service' ? 'services' : 'ips'; draft[key] = draft[key].filter(item => item.id !== record.id); await saveInventory(draft); }
      $('confirmDialog').close();
    } finally { $('confirmAction').disabled = false; }
  });
  $('confirmDialog').showModal();
}
function fillSettings() {
  ndiModule.configure(runtime.ndi,runtime.ndi_mock);
  $('installationForm').elements.installationName.value = config.installationName;
  const form = $('integrationForm'); for (const name of ['adguardUrl','adguardUser']) form.elements[name].value = config[name]; form.elements.adguardPass.value = '';
  $('panelDomain').textContent = config.dashboardDomain;
  const previousAllNetworksRange = allNetworksRange;
  allNetworksRange = config.scanNetworks.map(network => network.cidr).join(' ; ');
  if (!$('scanRange').value.trim() || $('scanRange').value.trim() === previousAllNetworksRange) $('scanRange').value = allNetworksRange;
  renderNetworkSettings(); renderScanPresets();
  renderUsers(); renderCategorySettings();
}
function renderUsers() {
  $('usersList').innerHTML = users.users.map(user => {
    const current = user.id === session.userId;
    const actions = (session.isPrimary ? `<button class="edit-button" data-user-password="${esc(user.id)}">Cambiar contraseña</button>` : '') + (!user.isPrimary && !current ? `<button class="edit-button" data-toggle-user="${esc(user.id)}">${user.enabled ? 'Desactivar' : 'Activar'}</button><button class="edit-button danger-text" data-delete-user="${esc(user.id)}">Eliminar</button>` : '');
    return `<div class="user-row${current ? ' current-user' : ''}" data-user-id="${esc(user.id)}" ${current ? 'aria-current="true"' : ''}><div class="user-summary"><strong>${esc(user.username)}</strong><span class="hint">${user.role === 'admin' ? 'Administrador' : 'Usuario'}${!user.enabled ? ' · Desactivado' : ''}</span>${user.isPrimary ? '<span class="tag">Cuenta principal</span>' : ''}${current ? '<span class="tag current-user-badge">Sesión actual</span>' : ''}</div>${actions ? `<div class="cell-actions">${actions}</div>` : ''}</div>`;
  }).join('');
}
async function saveConfig(data, revision = config.configRevision) {
  if ('scanNetworks' in data && scanning) runner.cancel();
  feedback('Guardando configuración…');
  const result = await post('save_config', {...data,configRevision:revision});
  config = result.config;
  runtime.installationName = config.installationName;
  runtime.ndi = config.ndi;
  renderInstallationName();
  showPublication(result.dns_sync);
  scannerConfig = validateScannerConfiguration(await request('get_scanner_config')); fillSettings();
  if (scanResults.length) renderScan();
}
async function scan() {
  if (scanning) return;
  const ips = validateScanRange($('scanRange').value.trim(), scannerConfig);
  scanRangeLabel = $('scanRange').value.trim();
  scanResults = []; scanPage = 0; scanning = true;
  $('startScan').hidden = true; $('stopScan').hidden = false; $('scanProgress').hidden = false; $('scanProgress').value = 0; $('scanRange').disabled = true; feedback('');
  $('scanSummary').textContent = `0 de ${ips.length} direcciones`;
  renderScan();
  try {
    const result = await runner.run(ips, {onChunk(items) {scanResults.push(...items.filter(item => typeof item.isAlive === 'boolean')); renderScan();}, onProgress(completed,total) {$('scanSummary').textContent = `${completed} de ${total} direcciones`; $('scanProgress').value = completed / total * 100;}});
    const alive = scanResults.filter(item => item.isAlive === true).length;
    const unanswered = scanResults.filter(item => item.isAlive === false).length;
    $('scanSummary').textContent = `${result.cancelled ? 'Detenido · ' : ''}${alive} activos · ${unanswered} sin respuesta · ${scanResults.length} de ${ips.length} comprobadas`;
  } catch (error) {
    $('scanSummary').textContent = `Escaneo interrumpido · ${scanResults.length} de ${ips.length} comprobadas`;
    throw error;
  } finally { scanning = false; $('startScan').hidden = false; $('stopScan').hidden = true; $('scanRange').disabled = false; renderScan(); }
}
function renderScan() {
  const known = new Map(hosts().map(h => [h.ip,h]));
  // Only returned results from the current scan are evaluated, never missing inventory IPs.
  const list = scanResults.filter(item => {
    if (scanFilter === 'offline') return item.isAlive === false;
    if (scanFilter === 'all') return true;
    if (item.isAlive !== true) return false;
    if (scanFilter === 'new') return !known.has(item.ip);
    if (scanFilter === 'known') return known.has(item.ip);
    if (scanFilter === 'dhcp') return isIpInConfiguredDhcpRange(item.ip,scannerConfig);
    return true;
  });
  if (scanFilter === 'offline') list.sort((a,b) => Number(known.has(b.ip)) - Number(known.has(a.ip)));
  $('scanScope').textContent = `${scanRangeLabel ? `Rango del último escaneo: ${scanRangeLabel}. ` : ''}Solo se evalúan las IP comprobadas. Los equipos fuera del rango o aún sin comprobar quedan sin evaluar.`;
  const pages = Math.max(1,Math.ceil(list.length / 100)); scanPage = Math.min(scanPage,pages-1);
  $('scanTable').innerHTML = `<table><thead><tr><th>Estado</th><th>IP / MAC</th><th>Equipo</th><th>Puertos</th><th>Acciones</th></tr></thead><tbody>${list.slice(scanPage*100,(scanPage+1)*100).map(item => {
    const host = known.get(item.ip); const dynamic = isIpInConfiguredDhcpRange(item.ip,scannerConfig);
    return `<tr><td><span class="state ${item.isAlive ? '' : 'offline'}">${item.isAlive ? 'Activo' : 'Sin respuesta'}</span></td><td><code>${esc(item.ip)}</code><p class="hint">${esc(item.mac)}</p></td><td>${esc(host?.hostname || item.hostname || 'Equipo sin nombre')}${dynamic ? '<p><span class="tag warn">Rango DHCP</span></p>' : ''}</td><td>${item.ports.map(p=>`<span class="tag">${p === 8000 ? 'Companion · ' : p === 8006 ? 'PVE · ' : p === 5959 ? 'NDI · ' : ''}${p}</span>`).join('') || '—'}</td><td><div class="cell-actions">${host ? `<span class="state">Registrado</span><button class="edit-button" data-sync-host="${esc(item.ip)}">Actualizar</button>${hostServiceAction(host)}` : item.isAlive && !dynamic ? `<button class="secondary" data-discovered="${esc(item.ip)}">Registrar equipo</button>` : dynamic ? '<span class="hint">Asignar IP fija antes de registrar</span>' : ''}</div></td></tr>`;
  }).join('') || `<tr><td colspan="5" class="empty">${scanning ? 'Buscando equipos…' : scanResults.length ? 'No hay equipos con este filtro.' : 'Selecciona un rango e inicia el escaneo.'}</td></tr>`}</tbody></table>`;
  $('scanPagination').innerHTML = list.length > 100 ? `<button class="secondary" data-page="-1" ${scanPage === 0 ? 'disabled' : ''}>Anterior</button><span>${scanPage+1} / ${pages}</span><button class="secondary" data-page="1" ${scanPage === pages-1 ? 'disabled' : ''}>Siguiente</button>` : '';
}
async function showApplied() {
  const data = await request('applied');
  // Use the published rules, since the inventory can contain unapplied changes.
  const redirects = new Map();
  for (const [, domain, target] of (data.caddy || '').matchAll(/^http:\/\/(\S+) \{\r?\n\s*redir "([^"\r\n]+)\{uri\}" 302\r?\n\}/gm)) {
    const url = new URL(target);
    const port = url.port || (url.protocol === 'https:' ? '443' : '80');
    redirects.set(domain, `${url.protocol}//${url.hostname}:${port}${url.pathname === '/' ? '' : url.pathname}`);
  }
  $('appliedDns').innerHTML = `<table><thead><tr><th>Nombre DNS</th><th>Dirección DNS</th><th>Redirección · destino y puerto</th></tr></thead><tbody>${data.dns.map(item=>`<tr><td><code>${esc(item.domain)}</code></td><td><code>${esc(item.answer)}</code></td><td>${redirects.has(item.domain) ? `<code>${esc(redirects.get(item.domain))}</code>` : '<span class="muted">Sin redirección · solo DNS</span>'}</td></tr>`).join('')}</tbody></table>`;
  $('appliedCaddy').textContent = data.caddy || 'Todavía no hay reglas aplicadas.'; $('appliedDialog').showModal();
}
function openHostService(ip) {
  const host = hosts().find(h => h.ip === ip); const discovered = scanResults.find(h => h.ip === ip);
  const linked = host?.linkedServices || [];
  if (linked.length === 1) {
    openEditor('service', inventory.services.find(service => service.id === linked[0].id));
    return;
  }
  if (linked.length > 1) {
    $('serviceChoices').innerHTML = linked.map(service => `<button type="button" class="secondary service-choice" data-edit="${esc(service.id)}"><strong>${esc(service.name)}</strong><code>${esc(service.domain || service.url || ip)}</code></button>`).join('');
    $('servicePicker').showModal();
    return;
  }
  const ports = discovered?.ports || []; const port = [8000,8006,8080,443,80].find(p => ports.includes(p));
  const scheme = port === 443 || port === 8006 ? 'https' : 'http';
  openEditor('service', null, {...host,ip,url:`${scheme}://${ip}${port && ![80,443].includes(port) ? ':'+port : ''}`});
}
document.addEventListener('click', guard(async event => {
  const link = event.target.closest('a[href]');
  if (link && runtime?.demo && new URL(link.href).origin !== location.origin) {
    event.preventDefault(); feedback(`Este acceso abriría ${link.href}. En la muestra no se conecta con equipos de tu red.`); return;
  }
  const button = event.target.closest('button'); if (!button) return;
  const d = button.dataset;
  if ('close' in d) button.closest('dialog').close();
  else if (d.view) switchView(d.view);
  else if (d.category) {
    const allSelected = inventory.categories.every(category => selected.has(category.name));
    if (d.category === '*') selected = new Set(allSelected ? [] : inventory.categories.map(category => category.name));
    else if (allSelected) selected = new Set([d.category]);
    else if (selected.has(d.category)) selected.delete(d.category);
    else selected.add(d.category);
    renderDirectory();
  }
  else if (d.edit) {
    if ($('servicePicker').open) $('servicePicker').close();
    openEditor('service', inventory.services.find(s => s.id === d.edit));
  }
  else if (d.host) openEditor('device',hosts().find(h=>h.ip === d.host));
  else if (d.editCategory) openEditor('category', inventory.categories.find(category => category.id === d.editCategory));
  else if (d.deleteCategory) openCategoryDelete(d.deleteCategory);
  else if (d.editNetwork) openEditor('network', config.scanNetworks.find(network => network.id === d.editNetwork));
  else if (d.deleteNetwork) openNetworkDelete(d.deleteNetwork);
  else if (d.userPassword) openEditor('password', users.users.find(user => user.id === d.userPassword));
  else if (d.hostService) openHostService(d.hostService);
  else if (d.discovered) openEditor('device',null,scanResults.find(r=>r.ip === d.discovered));
  else if (d.deleteIp) askDelete('device',inventory.ips.find(h=>h.id === d.deleteIp));
  else if (d.copy) {await navigator.clipboard.writeText(d.copy); feedback('Copiado al portapapeles.');}
  else if (d.scanFilter) {scanFilter = d.scanFilter; scanPage = 0; $('scanFilters').querySelectorAll('button').forEach(b=>b.classList.toggle('selected',b === button)); renderScan();}
  else if (d.allNetworks && !scanning) { $('scanRange').value = allNetworksRange; renderScanPresets(); }
  else if (d.networkPreset) {
    const network = config.scanNetworks.find(item => item.id === d.networkPreset);
    if (network && !scanning) { $('scanRange').value = network.cidr; renderScanPresets(); }
  }
  else if (d.page) {scanPage += Number(d.page); renderScan();}
  else if (d.syncHost) {
    const item = scanResults.find(r=>r.ip === d.syncHost), host = hosts().find(h=>h.ip === d.syncHost), draft = structuredClone(inventory);
    for (const target of [...draft.ips.filter(h=>h.ip === item.ip),...draft.services.filter(s=>s.ip === item.ip || s.url && new URL(s.url).hostname === item.ip)]) {target.mac = item.mac || target.mac; target.sshEnabled = item.ports.includes(22) || target.sshEnabled;}
    await saveInventory(draft); renderScan();
  } else if (d.toggleUser) {const user = users.users.find(u=>u.id === d.toggleUser); await post('update_user',{id:user.id,username:user.username,role:user.role,enabled:!user.enabled,revision:users.revision});users = await request('users'); renderUsers();}
  else if (d.deleteUser) askDelete('user',users.users.find(u=>u.id === d.deleteUser));
}));
const loginForm = $('loginForm');
const loginButton = loginForm.querySelector('button[type=submit]');
const loginError = $('loginError');
const loginRetry = $('loginRetry');
let loginRetryTimer = null;

function showLoginDelay(seconds) {
  if (!Number.isInteger(seconds) || seconds <= 0) return;
  clearInterval(loginRetryTimer);
  const deadline = Date.now() + seconds * 1000;
  loginButton.disabled = true;
  loginError.textContent = 'Acceso bloqueado temporalmente por demasiados intentos.';
  loginRetry.hidden = false;

  const update = () => {
    const remaining = Math.ceil((deadline - Date.now()) / 1000);
    if (remaining <= 0) {
      clearInterval(loginRetryTimer);
      loginRetryTimer = null;
      loginRetry.hidden = true;
      loginError.textContent = '';
      loginButton.disabled = false;
      return;
    }
    const minutes = String(Math.floor(remaining / 60)).padStart(2, '0');
    const seconds = String(remaining % 60).padStart(2, '0');
    loginRetry.textContent = 'Próximo intento en ' + minutes + ':' + seconds + '.';
  };
  loginRetryTimer = setInterval(update, 1000);
  update();
}

loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (loginButton.disabled) return;
  loginButton.disabled = true;
  loginError.textContent = '';
  try {
    session = await post('login', Object.fromEntries(new FormData(loginForm)));
    await load();
  } catch (error) {
    loginError.textContent = error.message;
    if (error.status === 429) showLoginDelay(error.data.retry_after);
  } finally {
    loginButton.disabled = loginRetryTimer !== null;
  }
});
$('logout').onclick = guard(async()=>{
  runner.cancel();
  ndiModule.setView(false);
  await post('logout',{});
  session = await request('session');
  $('app').hidden = true;
  $('loginScreen').hidden = false;
  loginForm.elements.password.value = '';
  showLoginDelay(session.login_retry_after);
});
$('settingsButton').onclick = ()=>switchView('settings');
$('addButton').onclick = ()=>openEditor(view === 'devices' ? 'device' : 'service');
$('search').addEventListener('input',renderDirectory);
$('editorForm').addEventListener('submit',submitEditor);
$('categoryDeleteForm').addEventListener('submit',deleteCategory);
$('addCategoryButton').onclick = () => openEditor('category');
$('addNetworkButton').onclick = () => openEditor('network');
$('scanRange').addEventListener('input', () => { if (config) renderScanPresets(); });
$('installationForm').addEventListener('submit',guard(async event=>{event.preventDefault();await saveConfig(Object.fromEntries(new FormData(event.target)));}));
$('integrationForm').addEventListener('submit',guard(async event=>{event.preventDefault();await saveConfig(Object.fromEntries(new FormData(event.target)));}));
$('startScan').onclick = guard(scan); $('stopScan').onclick = ()=>runner.cancel();
$('retryButton').onclick = guard(async event=>{event.target.disabled = true;try{await applyInfrastructure();}finally{event.target.disabled = false;}});
for (const id of ['showApplied','inspectButton']) $(id).onclick = guard(showApplied);
$('addUserButton').onclick = ()=>openEditor('user');
$('exportButton').onclick = ()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(inventory,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`fluge-inventario-${new Date().toISOString().slice(0,10)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('importButton').onclick = ()=>$('importFile').click();
$('importFile').onchange = guard(async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>4*1024*1024)throw new Error('La copia supera 4 MiB.');const draft=JSON.parse(await file.text());$('confirmTitle').textContent='Importar inventario';$('confirmText').textContent='Se sustituirán los servicios, equipos y categorías por los de la copia. Los accesos se actualizarán automáticamente.';$('confirmAction').textContent='Importar';$('confirmAction').onclick=guard(async()=>{await saveInventory(draft);$('confirmDialog').close();});$('confirmDialog').showModal();}finally{event.target.value='';}});
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)&&!document.querySelector('dialog[open]')){event.preventDefault();$('search').focus();}if(event.key==='Escape'&&event.target===$('search')){$('search').value='';renderDirectory();}});
try {
  session = await request('session');
  if(session.authenticated)await load();
  else showLoginDelay(session.login_retry_after);
} catch(error){loginError.textContent=error.message;}
finally {loginButton.disabled = loginRetryTimer !== null;}
