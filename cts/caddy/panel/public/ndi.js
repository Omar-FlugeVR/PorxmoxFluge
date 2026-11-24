import { isIpv4 } from './validation.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => value ? new Date(value).toLocaleString('es-ES') : 'Nunca observado';

export function validateNdiSettings(value) {
  if (typeof value.enabled !== 'boolean') throw new Error('El estado NDI no es válido.');
  const ip = String(value.ip || '').trim().toLowerCase();
  let validHost = isIpv4(ip);
  if (ip.includes(':')) { try { validHost = new URL(`http://[${ip}]/`).hostname.startsWith('['); } catch {} }
  else if (!/^[0-9.]+$/.test(ip)) validHost = ip.length <= 253 && ip.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label));
  if ((value.enabled || ip) && !validHost) throw new Error('Introduce una IP o hostname válido, sin protocolo ni puerto.');
  const result = {...value,ip};
  for (const [name,min,max] of [['port',1,65535],['polling_rate',2,300],['timeout',1,15]]) {
    if (!Number.isInteger(value[name]) || value[name] < min || value[name] > max) throw new Error(`${name}: introduce un entero entre ${min} y ${max}.`);
  }
  if (typeof value.groups !== 'string' || new TextEncoder().encode(value.groups).length > 2048 || /[\x00-\x1f\x7f]/.test(value.groups)) throw new Error('Los grupos NDI no son válidos.');
  const groups = [...new Set(value.groups.split(',').map(group => group.trim()).filter(Boolean))];
  if (groups.length > 32 || groups.some(group => [...group].length > 64)) throw new Error('Usa hasta 32 grupos de 64 caracteres como máximo.');
  result.groups = groups.join(',');
  if (!['normal','offline','empty','missing_lib'].includes(result.mock_scenario)) throw new Error('El escenario NDI no es válido.');
  return result;
}

/** Poll only an enabled, visible view. Chained timeouts prevent overlapping requests. */
export class NdiModule {
  constructor({request,save,feedback,onDisabled}) {
    Object.assign(this,{request,save,feedback,onDisabled,view:false,timer:null,controller:null,generation:0,data:null,busy:false,direction:'all'});
    $('ndiForm').addEventListener('submit', event => { event.preventDefault(); void this.saveForm(); });
    $('ndiEnabled').addEventListener('change', () => {
      this.showFields();
      if (!$('ndiEnabled').checked) { this.stop(); void this.disable(); }
    });
    $('ndiTest').onclick = () => { void this.test(); };
    $('ndiRefresh').onclick = () => { this.stop(); void this.poll(); };
    $('ndiClearLog').onclick = () => { void this.clearLog(); };
    $('ndiSearch').addEventListener('input', () => this.renderSources());
    $('ndiDirections').addEventListener('click', event => {
      const button = event.target.closest('[data-ndi-direction]');
      if (!button) return;
      this.direction = button.dataset.ndiDirection;
      $('ndiDirections').querySelectorAll('button').forEach(item => item.classList.toggle('selected',item===button));
      this.renderSources();
    });
    document.addEventListener('visibilitychange', () => { this.stop(); if (this.active()) void this.poll(); });
  }

  configure(settings,mock) {
    const changed = JSON.stringify(settings) !== JSON.stringify(this.settings) || mock !== this.mock;
    this.settings = {...settings}; this.mock = mock;
    const form = $('ndiForm');
    form.elements.enabled.checked = settings.enabled;
    for (const name of ['ip','port','polling_rate','timeout','groups','mock_scenario']) form.elements[name].value = settings[name];
    $('ndiMockField').hidden = !mock;
    $('ndiTab').hidden = !settings.enabled;
    this.showFields();
    if (changed) { this.stop(); this.data = null; this.render(); if (this.active()) void this.poll(); }
    if (!settings.enabled) this.onDisabled();
  }

  showFields() {
    $('ndiToggleLabel').textContent = $('ndiEnabled').checked ? 'Enable' : 'Disable';
    $('ndiFields').hidden = !$('ndiEnabled').checked;
    $('ndiFields').disabled = !$('ndiEnabled').checked;
    if (!$('ndiEnabled').checked) $('ndiTestResult').hidden = true;
  }

  formSettings() {
    const form = $('ndiForm');
    return validateNdiSettings({enabled:form.elements.enabled.checked,ip:form.elements.ip.value,
      port:Number(form.elements.port.value),polling_rate:Number(form.elements.polling_rate.value),
      timeout:Number(form.elements.timeout.value),groups:form.elements.groups.value,mock_scenario:form.elements.mock_scenario.value});
  }

  setView(active) { this.view = active; this.stop(); if (this.active()) void this.poll(); }
  active() { return this.settings?.enabled && this.view && !document.hidden; }
  stop() {
    this.generation++;
    clearTimeout(this.timer); this.timer = null;
    this.controller?.abort(); this.controller = null;
    this.testController?.abort(); this.testController = null;
    $('ndiRefresh').disabled = false;
  }

  async poll() {
    if (!this.active() || this.clearingLog) return;
    const generation = this.generation;
    this.controller = new AbortController(); $('ndiRefresh').disabled = true;
    let rate = this.settings.polling_rate;
    try {
      const data = await this.request('api/ndi/status', {signal:this.controller.signal});
      if (generation !== this.generation || !this.active()) return;
      if (!data.enabled) { this.settings.enabled = false; $('ndiTab').hidden = true; this.stop(); this.onDisabled(); return; }
      this.data = data; rate = data.polling_rate; this.render();
    } catch (error) {
      if (error.name !== 'AbortError' && generation === this.generation) {
        this.data = {...(this.data || {}),warning:error.message,stale:true}; this.render();
      }
    } finally {
      if (generation === this.generation) {
        this.controller = null; $('ndiRefresh').disabled = false;
        if (this.active()) this.timer = setTimeout(() => { void this.poll(); }, rate * 1000);
      }
    }
  }

  render() {
    const data = this.data;
    $('ndiState').textContent = !data || data.pending || data.stale ? 'Sin comprobar' : data.online ? 'Online' : 'Offline';
    $('ndiState').className = `ndi-state ${data?.pending || data?.stale ? '' : data?.online ? 'online' : data ? 'offline' : ''}`;
    $('ndiServer').textContent = data?.server || '—';
    $('ndiLatency').textContent = data?.latency_ms == null ? '—' : `${data.latency_ms} ms`;
    $('ndiUpdated').textContent = data?.last_updated ? new Date(data.last_updated).toLocaleString('es-ES') : 'Pendiente de consulta';
    $('ndiSourceCount').textContent = Number.isInteger(data?.sources_count) ? String(data.sources_count) : '—';
    $('ndiReceiverCount').textContent = data?.receivers_observed && !data.stale ? String(data.receivers_count) : '—';
    $('ndiServerSeen').textContent = data?.server_last_seen ? `Última vez disponible: ${date(data.server_last_seen)}` : '';
    $('ndiReceiverNote').textContent = data?.receivers_warning || 'Los receptores deben anunciarse al Discovery Server (NDI 6.2+). La función anunciada no confirma tráfico de vídeo/audio en curso.';
    $('ndiMockBadge').hidden = !this.mock;
    $('ndiWarning').hidden = !data?.warning;
    $('ndiWarning').textContent = data?.warning || '';
    this.renderSources();
    this.renderEvents();
  }

  renderSources() {
    const query = $('ndiSearch').value.trim().normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
    const entities = this.data?.entities || [];
    const sources = entities.filter(source => (this.direction==='all'||source.direction===this.direction) && [source.full_name,source.host,source.stream,source.url].join(' ').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().includes(query));
    $('ndiResultsCount').textContent = `${sources.length} de ${entities.length} registros`;
    $('ndiSources').innerHTML = `<table><thead><tr><th>Equipo / Host</th><th>Función</th><th>Fuente / Entrada</th><th>Dirección NDI</th><th>Disponibilidad</th><th>Última vez visto</th></tr></thead><tbody>${sources.map(source => {
      const available = this.data.stale ? null : source.available;
      const role = source.direction==='receiver'?'Receptor':'Emisor';
      return `<tr data-ndi-entity="${esc(source.id)}"><td data-label="Equipo / Host"><strong>${esc(source.host)}</strong></td><td data-label="Función"><span class="ndi-role">${role}</span>${source.device_role==='both'?`<span class="hint ndi-also">También ${source.direction==='receiver'?'emite':'recibe'}</span>`:''}</td><td data-label="Fuente / Entrada">${esc(source.stream || source.full_name)}</td><td data-label="Dirección NDI"><code>${esc(source.url || '—')}</code></td><td data-label="Disponibilidad"><span class="ndi-availability ${available===true?'available':available===false?'absent':'unknown'}">${available===true?'Disponible':available===false?'No visible':'Sin comprobar'}</span></td><td data-label="Última vez visto"><time datetime="${esc(source.last_seen)}">${esc(date(source.last_seen))}</time></td></tr>`;
    }).join('') || `<tr><td colspan="6" class="empty">${query || this.direction!=='all' ? 'No hay equipos con este filtro.' : this.data ? 'Todavía no hay equipos NDI registrados en este monitor.' : 'Consultando equipos NDI…'}</td></tr>`}</tbody></table>`;
  }

  renderEvents() {
    const events = this.data?.events || [];
    $('ndiEventCount').textContent = `${events.length} eventos · últimos ${this.data?.history_limit || 500}`;
    $('ndiClearLog').disabled = Boolean(this.clearingLog) || !events.length;
    $('ndiEvents').innerHTML = events.map(event => `<li class="ndi-event ${event.level==='warning'?'warning':''}"><time datetime="${esc(event.at)}">${esc(date(event.at))}</time><span>${esc(event.message)}</span></li>`).join('') || `<li class="hint">${this.data ? 'Registro vacío. Los próximos cambios aparecerán aquí.' : 'El registro aparecerá al realizar las primeras consultas.'}</li>`;
  }

  async clearLog() {
    if (this.clearingLog) return;
    this.clearingLog = true; $('ndiClearLog').disabled = true;
    this.stop(); // Discard responses that started before the clear operation.
    $('ndiRefresh').disabled = true;
    try {
      await this.request('api/ndi/log/clear',{method:'POST',data:{}});
      if (this.data) this.data = {...this.data,events:[]};
      this.renderEvents(); this.feedback('Registro NDI limpiado.');
    } catch (error) { this.feedback(error.message,'error'); }
    finally { this.clearingLog = false; this.renderEvents(); $('ndiRefresh').disabled = false; if(this.active())void this.poll(); }
  }

  setBusy(busy) {
    this.busy = busy;
    for (const element of [$('ndiEnabled'),$('ndiSave'),$('ndiTest')]) element.disabled = busy;
  }

  async saveForm() {
    if (this.busy) return;
    try { const settings = this.formSettings(); this.setBusy(true); await this.save(settings); }
    catch (error) { this.feedback(error.message,'error'); }
    finally { this.setBusy(false); }
  }

  async disable() {
    if (this.busy) return;
    this.setBusy(true);
    try { await this.save({...this.settings,enabled:false}); }
    catch (error) { this.feedback(error.message,'error'); this.configure(this.settings,this.mock); if (this.active()) void this.poll(); }
    finally { this.setBusy(false); }
  }

  async test() {
    if (this.busy) return;
    try {
      const settings = this.formSettings();
      this.setBusy(true); $('ndiTestResult').hidden = false; $('ndiTestResult').textContent = 'Comprobando conexión…';
      this.testController = new AbortController();
      const result = await this.request('api/ndi/test',{method:'POST',data:settings,signal:this.testController.signal});
      $('ndiTestResult').textContent = `${result.online ? 'Online' : 'Offline'} · ${result.server}${result.latency_ms == null ? '' : ` · ${result.latency_ms} ms`} · ${result.sources_count} fuentes · ${result.receivers_count || 0} receptores${result.warning ? `\n${result.warning}` : ''}`;
      $('ndiTestResult').className = `ndi-test-result ${result.online && !result.warning ? 'success' : 'warning'}`;
    } catch (error) { if (error.name !== 'AbortError') { $('ndiTestResult').hidden = false; $('ndiTestResult').textContent = error.message; } }
    finally { this.testController = null; this.setBusy(false); }
  }
}
