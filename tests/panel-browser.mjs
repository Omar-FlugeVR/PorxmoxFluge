// Optional real-browser QA. Start an isolated demo and Chromium with a debug port.
// node --experimental-websocket tests/panel-browser.mjs http://127.0.0.1:8788 http://127.0.0.1:9236 OUTPUT_DIR
import fs from 'node:fs';
import path from 'node:path';
const [base = 'http://127.0.0.1:8788', cdp = 'http://127.0.0.1:9236', output, stateDirectory] = process.argv.slice(2);
let targets;
for (let n = 0; n < 100; n++) {
  try { targets = await (await fetch(cdp + '/json')).json(); if (targets.some(t => t.type === 'page')) break; } catch {}
  await new Promise(r => setTimeout(r, 100));
}
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise((resolve,reject) => {ws.onopen=resolve;ws.onerror=reject;});
let serial = 0; const pending = new Map(), exceptions = [];
ws.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
  if (pending.has(message.id)) {const [resolve,reject] = pending.get(message.id); pending.delete(message.id); message.error ? reject(message.error) : resolve(message.result);}
};
const call = (method,params={}) => new Promise((resolve,reject) => {const id=++serial;pending.set(id,[resolve,reject]);ws.send(JSON.stringify({id,method,params}));});
const evaluate = async expression => {const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const waitFor = async expression => {for(let n=0;n<150;n++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,70));}const state=await evaluate(`({hidden:document.hidden,ndiState:document.getElementById('ndiState')?.textContent,ndiWarning:document.getElementById('ndiWarning')?.textContent,requests:window.ndiStatusRequests,feedback:document.getElementById('feedback')?.textContent})`);throw new Error('Timeout: '+expression+' '+JSON.stringify(state)+' exceptions='+JSON.stringify(exceptions));};
const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
const fill = (selector,value) => evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
const assert = (condition,message) => {if(!condition)throw new Error(message);};
const checkRefreshTimeout = async (id, endpoint, busyLabel, messageId) => {
  await waitFor(`!document.getElementById('${id}').disabled`);
  await evaluate(`(() => {
    const originalFetch=window.fetch, originalTimeout=AbortSignal.timeout;
    window.refreshRequests=0;
    AbortSignal.timeout=ms=>originalTimeout.call(AbortSignal,Math.min(ms,500));
    window.fetch=(input,options)=>{
      const target=${JSON.stringify(endpoint)};
      if(!String(input).includes(target)&&!options?.body?.includes(target))return originalFetch(input,options);
      window.refreshRequests++;
      return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true}));
    };
    window.restoreRefresh=()=>{window.fetch=originalFetch;AbortSignal.timeout=originalTimeout;};
  })()`);
  try {
    await click(`#${id}`);
    assert(await evaluate(`(()=>{const b=document.getElementById('${id}');return b.disabled&&b.textContent===${JSON.stringify(busyLabel)}&&getComputedStyle(b).cursor==='not-allowed';})()`), `${id} should be disabled and show the pending state`);
    await click(`#${id}`);
    assert(await evaluate(`window.refreshRequests===1`), `${id} should ignore a second click`);
    await waitFor(`!document.getElementById('${id}').disabled&&document.getElementById('${messageId}').textContent.includes('tardado demasiado')`);
  } finally {
    await evaluate(`window.restoreRefresh()`);
  }
};
const screenshot = async (name,selector) => {if(!output)return;fs.mkdirSync(output,{recursive:true});const clip=selector?await evaluate(`(()=>{const box=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:box.x+scrollX,y:box.y+scrollY,width:box.width,height:box.height,scale:1};})()`):undefined;const result=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,...(clip?{clip}:{})});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));};
try {
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setFocusEmulationEnabled',{enabled:true});await call('Page.bringToFront');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1200,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base});
  await waitFor(`document.querySelector('#loginForm button[type=submit]')&&!document.querySelector('#loginForm button[type=submit]').disabled`);
  assert(await evaluate(`document.querySelector('#loginForm [name=username]').value===''&&document.querySelector('#loginForm [name=password]').value===''`),'Login fields must start empty');
  if (stateDirectory) {
    const button = '#loginForm button[type=submit]';
    const blocked = `document.querySelector('#loginRetry')?.hidden===false&&document.querySelector('${button}').disabled`;
    await fill('#loginForm [name=username]', 'admin');
    await fill('#loginForm [name=password]', 'incorrecta');
    for (let index = 0; index < 4; index++) {
      await click(button);
      await waitFor(`!document.querySelector('${button}').disabled&&document.getElementById('loginError').textContent.includes('incorrectos')`);
    }
    await click(button);
    await waitFor(blocked);
    const firstTick = await evaluate("document.getElementById('loginRetry').textContent");
    assert(/Próximo intento en 0[01]:[0-5]\d\./.test(firstTick), 'The login timer must show minutes and seconds');
    await waitFor(`document.getElementById('loginRetry').textContent!==${JSON.stringify(firstTick)}`);
    await call('Page.reload');
    await waitFor(blocked);
    const attemptsPath = path.join(stateDirectory, 'login-attempts.json');
    const attempts = JSON.parse(fs.readFileSync(attemptsPath, 'utf8'));
    const entry = Object.values(attempts).find(value => value.blockedUntil > Date.now() / 1000);
    assert(entry, 'The browser test must have an active server lockout');
    entry.blockedUntil = Math.floor(Date.now() / 1000) + 4;
    fs.writeFileSync(attemptsPath, JSON.stringify(attempts));
    await call('Page.reload');
    await waitFor(blocked);
    await waitFor(`document.getElementById('loginRetry').hidden&&!document.querySelector('${button}').disabled`);
    console.log('Login lockout countdown, reload and re-enabled submit passed.');
  }
  await fill('#loginForm [name=username]','admin');await fill('#loginForm [name=password]','FlugeDemo2026');
  await click('#loginForm button[type=submit]');
  await waitFor(`!document.getElementById('app').hidden && document.querySelectorAll('.service-card').length === 5`);
  await click('[data-view=updates]');
  await waitFor(`document.querySelectorAll('#updatesRows tr').length === 5`);
  assert(await evaluate(`document.getElementById('updatesCheckedAt').textContent.includes('Pendiente')`), 'Missing update report should be shown as pending');
  const progressVisible = `document.getElementById('updateProgress').getClientRects().length > 0`;
  assert(!await evaluate(progressVisible), 'The progress bar must occupy no space when idle');
  if (stateDirectory) {
    const installedRelease = {version:'0.0.1',type:'test',update_required:false};
    const latestRelease = {version:'0.0.2',type:'test',update_required:true};
    const panelReport = {checkedAt:new Date(Date.now()-60000).toISOString(),services:{
      panel:{installed:'a'.repeat(40),latest:null,installedRelease,latestRelease:installedRelease,status:'current',canUpdate:false},
      companion:{installed:'5.1.3',latest:'5.1.3',status:'current'},
      ndi:{installed:'SDK 6.3.1',status:'manual'},
    }};
    const updatesFile = path.join(stateDirectory, 'updates.json');
    const updateRequestFile = path.join(stateDirectory, 'update-request.json');
    assert(!fs.existsSync(updateRequestFile), 'Opening Updates must only read the cached report');
    fs.writeFileSync(updatesFile, JSON.stringify(panelReport));
    await waitFor(`!document.getElementById('checkUpdates').disabled`);
    await evaluate(`(()=>{
      const fetch = window.fetch;
      window.fetch = async (input,options) => {
        if (options?.body?.includes('check_updates')) await new Promise(resolve=>setTimeout(resolve,400));
        return fetch(input,options);
      };
    })()`);
    await click('#checkUpdates');
    assert(await evaluate(`document.getElementById('checkUpdates').disabled&&document.getElementById('checkUpdates').textContent==='Comprobando…'&&(${progressVisible})`), 'Searching must show progress and disable the button');
    await waitFor(`!document.getElementById('checkUpdates').disabled&&document.getElementById('updatesRows').textContent.includes('0.0.1 (test)')&&document.getElementById('updateJobStatus').textContent.includes('Búsqueda completada')`);
    assert(JSON.parse(fs.readFileSync(updateRequestFile)).role==='check'&&JSON.parse(fs.readFileSync(updatesFile)).checkedAt!==panelReport.checkedAt, 'Manual search must queue a check and refresh the report');
    assert(await evaluate(`document.getElementById('panelUpdateNotice').hidden&&!document.querySelector('#updatesRows button')&&!(${progressVisible})`), 'A current report must have no update buttons or visible progress bar');
    Object.assign(panelReport.services.panel,{latest:'b'.repeat(40),tag:'panel-v0.0.2',latestRelease:{...latestRelease,update_required:false},status:'update',canUpdate:true});
    fs.writeFileSync(updatesFile, JSON.stringify(panelReport));
    await click('#checkUpdates');
    await waitFor(`document.getElementById('panelUpdateNotice').hidden&&document.querySelector('[data-update-role=panel]')?.disabled===false&&!document.getElementById('checkUpdates').disabled`);
    const futureRelease = {...latestRelease,version:'0.0.3',minimum_version:'0.0.2'};
    Object.assign(panelReport.services.panel,{latest:'c'.repeat(40),tag:'panel-v0.0.3',latestRelease:{...futureRelease,update_required:false},
      status:'incompatible',canUpdate:false,compatibility:'La versión intermedia 0.0.2 debe estar publicada.'});
    fs.writeFileSync(updatesFile, JSON.stringify(panelReport));
    await click('#checkUpdates');
    await waitFor(`document.getElementById('panelUpdateNotice').hidden&&document.getElementById('updatesRows').textContent.includes('Ruta de actualización no disponible')&&!document.querySelector('[data-update-role=panel]')&&!document.getElementById('checkUpdates').disabled`);
    panelReport.services.panel.latestRelease = futureRelease;
    fs.writeFileSync(updatesFile, JSON.stringify(panelReport));
    await click('#checkUpdates');
    await waitFor(`!document.getElementById('panelUpdateNotice').hidden&&document.getElementById('panelUpdateNotice').textContent.includes('debe estar publicada')&&!document.querySelector('[data-update-role=panel]')&&!document.getElementById('checkUpdates').disabled`);
    delete panelReport.services.panel.compatibility;
    Object.assign(panelReport.services.panel,{status:'update',canUpdate:true,
      upgradePath:[{version:'0.0.2',commit:'b'.repeat(40)},{version:'0.0.3',commit:'c'.repeat(40)}]});
    fs.writeFileSync(updatesFile, JSON.stringify(panelReport));
    await click('#checkUpdates');
    await waitFor(`document.getElementById('updatesRows').textContent.includes('Actualización requerida')`);
    assert(await evaluate(`document.getElementById('updatesRows').textContent.includes('0.0.3 (test)')&&!document.getElementById('updatesRows').textContent.includes('aaaaaaaa')&&!document.querySelector('#updatesRows a')`), 'Update report should display public version labels without change links');
    await click('[data-view=services]');
    assert(await evaluate(`!document.getElementById('panelUpdateNotice').hidden`), 'The required update must be visible outside the updates view');
    await click('[data-view=updates]');
    await click('[data-update-role=panel]');
    await waitFor(`document.getElementById('updateDialog').open`);
    assert(await evaluate(`document.getElementById('updateDialogText').textContent.includes('0.0.2 → 0.0.3')`), 'The confirmation must show the full compatibility path');
    await click('#updateCancel');
    assert(await evaluate(`!document.getElementById('updateDialog').open&&!document.querySelector('[data-update-role=panel]').disabled`), 'Cancelling must leave the selected update available');
    await evaluate(`(()=>{const fetch=window.fetch;window.fetch=async (input,options)=>{if(options?.body?.includes('update_service'))await new Promise(r=>setTimeout(r,400));return fetch(input,options);};})()`);
    await click('[data-update-role=panel]');await click('#updateAccept');
    assert(await evaluate(`(${progressVisible})&&!document.getElementById('updateProgress').hasAttribute('value')`), 'The progress bar must appear as soon as the update is confirmed');
    await waitFor(`document.getElementById('updateJobStatus').textContent.includes('Actualización completada')&&!document.querySelector('[data-update-role=panel]')&&!(${progressVisible})&&document.getElementById('panelUpdateNotice').hidden`);
  }
  await checkRefreshTimeout('checkUpdates', 'check_updates', 'Comprobando…', 'feedback');
  await click('[data-view=services]');
  assert(await evaluate(`document.getElementById('scanRange').value==='192.168.1.0/24'&&document.querySelector('[data-all-networks]').classList.contains('selected')`),'Scanner must initially select all configured networks');
  assert(await evaluate(`document.getElementById('applyButton') === null && document.getElementById('retryButton').hidden`),'Normal workflow must not require manual publication');
  await click('[data-view=settings]');await fill('#installationForm [name=installationName]','Plató principal · Fluge');await click('#installationForm button[type=submit]');
  await waitFor(`document.getElementById('installationName').textContent==='Plató principal · Fluge'&&!document.getElementById('installationName').hidden`);
  await call('Page.reload');await waitFor(`!document.getElementById('app').hidden&&document.getElementById('installationName').textContent==='Plató principal · Fluge'`);
  console.log('Installation name updates immediately and persists after reloading.');
  await screenshot('panel-services-desktop');
  assert(await evaluate(`document.getElementById('categories').textContent.includes('Vídeo')`),'UTF-8 category text corrupted');
  await fill('#search','bitfocus');assert(await evaluate(`document.querySelectorAll('.service-card').length`)===1,'Search failed');await fill('#search','');
  await click('#addButton');assert(await evaluate(`document.querySelectorAll('#editorForm button[type=submit]').length`)===1,'Editor must have one save action');await fill('#editorForm [name=name]','Prueba QA');await fill('#editorForm [name=subdomain]','prueba-qa');await fill('#editorForm [name=url]','http://192.168.1.45:8080');await fill('#editorForm [name=description]','Servicio para verificar el alta');await click('#saveButton');
  await waitFor(`!document.getElementById('editor').open && document.querySelectorAll('.service-card').length === 6`);
  await click('#showApplied');await waitFor(`document.getElementById('appliedDialog').open`);
  assert(await evaluate(`document.getElementById('appliedCaddy').textContent.includes('http://192.168.1.45:8080{uri}')`),'Caddy route missing');
  assert(await evaluate(`(()=>{const rows=[...document.querySelectorAll('#appliedDns tbody tr')];const destination=domain=>rows.find(row=>row.cells[0].textContent===domain)?.cells[2].textContent;return destination('prueba-qa.lan')==='http://192.168.1.45:8080'&&destination('companion.lan')==='http://192.168.1.15:8000'&&destination('proxmox.lan')==='https://192.168.1.10:8006'&&destination('adguard.lan')==='http://192.168.1.11:80'&&destination('ndi.lan')==='Sin redirección · solo DNS'&&rows.find(row=>row.cells[0].textContent==='companion.lan').cells[1].textContent==='192.168.1.12';})()`),'Applied rules must distinguish the DNS address from redirect destinations and show explicit/default ports');
  await screenshot('panel-applied-rules');await click('#appliedDialog [data-close]');
  await call('Page.reload');await waitFor(`!document.getElementById('app').hidden && document.querySelectorAll('.service-card').length === 6`);
  assert(await evaluate(`document.getElementById('serviceGrid').textContent.includes('virtualización')`),'UTF-8 did not survive saving');
  const id = await evaluate(`(async () => (await (await fetch('api.php?action=data')).json()).services.find(s=>s.name==='Prueba QA').id)()`);
  await click(`[data-edit="${id}"]`);await fill('#editorForm [name=url]','https://192.168.1.46:8443');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  assert(await evaluate(`(async()=> (await (await fetch('api.php?action=applied')).json()).caddy.includes('https://192.168.1.46:8443{uri}'))()`),'Editing did not publish automatically');
  await click('#showApplied');await waitFor(`document.getElementById('appliedDialog').open`);assert(await evaluate(`[...document.querySelectorAll('#appliedDns tbody tr')].find(row=>row.cells[0].textContent==='prueba-qa.lan').cells[2].textContent==='https://192.168.1.46:8443'`),'Applied redirect port did not update after editing');await click('#appliedDialog [data-close]');
  await click(`[data-edit="${id}"]`);await click('#deleteService');await click('#confirmAction');await waitFor(`!document.getElementById('confirmDialog').open && document.querySelectorAll('.service-card').length === 5`);
  assert(await evaluate(`(async()=>{const applied=await(await fetch('api.php?action=applied')).json();return !applied.caddy.includes('prueba-qa.lan')&&!applied.dns.some(e=>e.domain==='prueba-qa.lan');})()`),'Deleting did not publish automatically');
  console.log('Single Save automatically publishes creation, editing and deletion; DNS/302 and persistence passed.');
  await click('[data-view=devices]');await click('#addButton');await fill('#editorForm [name=hostname]','Sin respuesta QA');await fill('#editorForm [name=ip]','192.168.1.40');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  await click('#addButton');await fill('#editorForm [name=hostname]','Fuera de rango QA');await fill('#editorForm [name=ip]','192.168.1.90');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  const beforeScanRevision=await evaluate(`(async()=>(await(await fetch('api.php?action=data')).json()).revision)()`);
  await evaluate(`(()=>{
    const fetch = window.fetch;
    window.fetch = (input, options) => {
      if (options?.body?.includes('scan_chunk') && JSON.parse(options.body).ips[0] === '192.168.1.33') {
        return new Promise(resolve => { window.resumeScan = () => { window.fetch = fetch; resolve(fetch(input, options)); }; });
      }
      return fetch(input, options);
    };
  })()`);
  await click('[data-view=scan]');
  await fill('#scanRange','192.168.1.1-50');
  await click('#startScan');
  await waitFor(`Boolean(window.resumeScan&&document.querySelector('[data-discovered="192.168.1.30"]'))`);
  assert(await evaluate(`!document.getElementById('stopScan').hidden&&[...document.querySelectorAll('#scanTable button')].every(button=>button.disabled)`), 'Inventory actions must stay disabled while partial scan results arrive');
  await click('[data-view=devices]');
  assert(await evaluate(`document.getElementById('addButton').disabled&&document.querySelector('[data-delete-ip="panel-landing-device"]').disabled&&document.querySelector('[data-host="192.168.1.12"]').disabled`), 'Moving to Devices must preserve the scan lock');
  await click('[data-view=services]');
  assert(await evaluate(`document.getElementById('addButton').disabled&&document.querySelector('[data-edit="panel-landing"]').disabled`), 'Moving to Services must preserve the scan lock');
  await click('[data-view=scan]');
  await evaluate(`window.resumeScan()`);
  await waitFor(`!document.getElementById('startScan').hidden&&document.querySelector('[data-discovered="192.168.1.30"]')?.disabled===false`);
  assert(await evaluate(`document.querySelector('[data-host-service="192.168.1.12"]').textContent==='Editar servicio'&&!document.getElementById('addButton').disabled`), 'The landing must be registered with its own service and all actions re-enabled after the scan');
  await click('[data-scan-filter=offline]');assert(await evaluate(`document.getElementById('scanTable').textContent.includes('Sin respuesta QA')&&!document.getElementById('scanTable').textContent.includes('Fuera de rango QA')`),'Out-of-range equipment was classified as inactive');assert(await evaluate(`document.querySelector('#scanTable tbody tr').textContent.includes('192.168.1.40')`),'Registered nonresponding equipment should appear first');
  await fill('#scanRange','192.168.1.90-95');await click('#startScan');assert(await evaluate(`!document.getElementById('scanTable').textContent.includes('Sin respuesta QA')`),'Previous inactive results must clear when a new scan starts');await waitFor(`!document.getElementById('startScan').hidden&&document.getElementById('scanScope').textContent.includes('192.168.1.90-95')`);assert(await evaluate(`document.getElementById('scanTable').textContent.includes('Fuera de rango QA')&&!document.getElementById('scanTable').textContent.includes('Sin respuesta QA')`),'Changing range retained stale inactive results');
  await fill('#scanRange','192.168.1.10-16');await click('#startScan');await waitFor(`!document.getElementById('startScan').hidden&&document.getElementById('scanScope').textContent.includes('192.168.1.10-16')`);assert(await evaluate(`!document.getElementById('scanTable').textContent.includes('Fuera de rango QA')&&!document.getElementById('scanTable').textContent.includes('Sin respuesta QA')`),'Unscanned registered equipment was marked inactive');
  await click('[data-scan-filter=all]');assert(await evaluate(`document.querySelectorAll('#scanTable tbody tr').length`)===7,'All results must stay inside the requested range');
  assert(await evaluate(`(async()=>(await(await fetch('api.php?action=data')).json()).revision)()`)===beforeScanRevision,'Scanning must not change saved inventory');
  await fill('#scanRange','192.168.1.1-50');await click('[data-scan-filter=alive]');await click('#startScan');await waitFor(`!document.getElementById('startScan').hidden && document.querySelector('[data-discovered="192.168.1.30"]')`);
  console.log('Inactive results respect each scan range, prioritize registered equipment and leave out-of-range inventory untouched.');
  await screenshot('panel-scanner-desktop');
  assert(await evaluate(`document.querySelector('[data-host-service="192.168.1.15"]').textContent`)==='Editar servicio','Registered web service still offers creation');
  await click('[data-host-service="192.168.1.15"]');assert(await evaluate(`document.getElementById('editorTitle').textContent`)==='Editar servicio','Existing service editor did not open');assert(await evaluate(`document.querySelector('#editorForm [name=url]').value`)==='http://192.168.1.15:8000','Existing web URL not loaded');await click('#editor [data-close]');
  await click('[data-host-service="192.168.1.16"]');assert(await evaluate(`document.querySelector('#editorForm [name=mode]').value`)==='dns_only','DNS service editor did not load its type');assert(await evaluate(`document.querySelector('#editorForm [name=ip]').value`)==='192.168.1.16','DNS address not loaded');await click('#editor [data-close]');
  await click('[data-discovered="192.168.1.30"]');assert(await evaluate(`document.querySelector('#editorForm [name=mac]').value.length`)===17,'Discovered MAC missing');await fill('#editorForm [name=hostname]','Mezclador QA');await click('#editorForm [name=save]');await waitFor(`!document.getElementById('editor').open && document.querySelector('[data-host-service="192.168.1.30"]')`);
  assert(await evaluate(`document.querySelector('[data-host-service="192.168.1.30"]').textContent`)==='Añadir servicio','Equipment without services must still offer creation');
  await click('[data-host-service="192.168.1.30"]');assert(await evaluate(`document.querySelector('#editorForm [name=url]').value`)==='http://192.168.1.30:8080','Port suggestion failed');await fill('#editorForm [name=subdomain]','mezclador-qa');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  assert(await evaluate(`document.querySelector('[data-host-service="192.168.1.30"]').textContent`)==='Editar servicio','Scanner action did not update after adding a service');
  await click('[data-view=devices]');assert(await evaluate(`document.getElementById('deviceTable').textContent.includes('Mezclador QA')`),'Device not registered');
  assert(await evaluate(`document.querySelector('#deviceTable [data-host-service="192.168.1.30"]').textContent`)==='Editar servicio','Equipment directory still offers creation');
  await click('#deviceTable [data-host-service="192.168.1.30"]');assert(await evaluate(`document.querySelector('#editorForm [name=subdomain]').value`)==='mezclador-qa','Equipment action did not open the associated service');await click('#editor [data-close]');
  await click('[data-view=services]');await click('#addButton');await fill('#editorForm [name=name]','Segundo servicio QA');await fill('#editorForm [name=url]','http://192.168.1.30:8081');await fill('#editorForm [name=subdomain]','segundo-qa');await click('#editorForm [name=save]');await waitFor(`!document.getElementById('editor').open`);
  await click('[data-view=devices]');await click('#deviceTable [data-host-service="192.168.1.30"]');await waitFor(`document.getElementById('servicePicker').open`);assert(await evaluate(`document.querySelectorAll('#serviceChoices [data-edit]').length`)===2,'Missing service choices for shared IP');
  await evaluate(`[...document.querySelectorAll('#serviceChoices [data-edit]')].find(b=>b.textContent.includes('Segundo servicio QA')).click()`);await waitFor(`document.getElementById('editor').open && !document.getElementById('servicePicker').open`);assert(await evaluate(`document.querySelector('#editorForm [name=url]').value`)==='http://192.168.1.30:8081','Service choice opened the wrong record');await click('#editor [data-close]');
  console.log('Add/edit actions passed for no service, existing web/DNS services and multiple services on one IP.');
  console.log('Discovery, known/new hosts, registration and web-service suggestion passed.');
  await click('[data-view=settings]');assert(await evaluate(`document.querySelector('#accountsPanel [data-user-password]')!==null&&document.querySelector('#inventoryBackupPanel [data-user-password]')===null&&document.getElementById('passwordButton')===null`),'Account editing belongs in user rows in Accounts, not inventory backup');
  await click('#networkList [data-edit-network]');await fill('#editorForm [name=dhcpEnd]','192.168.1.240');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  await waitFor(`(async()=>{const config=await(await fetch('api.php?action=get_config')).json();const runtime=await(await fetch('api.php?action=runtime')).json();return config.scanNetworks[0].dhcpPool.end==='192.168.1.240'&&runtime.lastSync.ok&&runtime.lastSync.configRevision===config.configRevision;})()`);
  await click('#addNetworkButton');await fill('#editorForm [name=name]','Vídeo QA');await fill('#editorForm [name=cidr]','192.168.20.0/24');await fill('#editorForm [name=dhcpStart]','192.168.20.50');await fill('#editorForm [name=dhcpEnd]','192.168.20.99');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  const videoNetworkId=await evaluate(`(async()=>(await(await fetch('api.php?action=get_config')).json()).scanNetworks.find(n=>n.cidr==='192.168.20.0/24').id)()`);
  await click(`[data-edit-network="${videoNetworkId}"]`);await fill('#editorForm [name=name]','Vídeo principal QA');await fill('#editorForm [name=dhcpEnd]','192.168.20.90');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  await click('[data-view=scan]');await click(`[data-network-preset="${videoNetworkId}"]`);assert(await evaluate(`document.getElementById('scanRange').value==='192.168.20.0/24'&&document.getElementById('scanHelp').textContent.includes('192.168.20.90')&&!document.getElementById('startScan').hidden`),'Preset must load its network and pool without starting a scan');
  await fill('#scanRange','192.168.1.105,192.168.20.70,192.168.20.110');await click('#startScan');await waitFor(`!document.getElementById('startScan').hidden&&document.getElementById('scanScope').textContent.includes('192.168.20.110')`);await click('[data-scan-filter=dhcp]');assert(await evaluate(`document.querySelectorAll('#scanTable tbody tr').length===2&&document.getElementById('scanTable').textContent.includes('192.168.1.105')&&document.getElementById('scanTable').textContent.includes('192.168.20.70')&&!document.getElementById('scanTable').textContent.includes('192.168.20.110')`),'Mixed manual range used the wrong DHCP pool');
  await click('[data-view=settings]');await click('#addNetworkButton');await fill('#editorForm [name=name]','Iluminación QA');await fill('#editorForm [name=cidr]','192.168.30.0/24');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  const lightingNetworkId=await evaluate(`(async()=>(await(await fetch('api.php?action=get_config')).json()).scanNetworks.find(n=>n.cidr==='192.168.30.0/24').id)()`);
  await call('Page.reload');await waitFor(`!document.getElementById('app').hidden&&document.querySelectorAll('#scanPresets [data-network-preset]').length===3`);await click('[data-view=scan]');
  const allNetworksRange='192.168.1.0/24 ; 192.168.20.0/24 ; 192.168.30.0/24';
  assert(await evaluate(`document.getElementById('scanRange').value===${JSON.stringify(allNetworksRange)}&&document.querySelector('[data-all-networks]').classList.contains('selected')`),'All networks must be selected by default after reloading');
  await click(`[data-network-preset="${lightingNetworkId}"]`);assert(await evaluate(`document.getElementById('scanHelp').textContent.includes('Sin pool DHCP')`),'Network without DHCP must be usable as a preset');
  await click('[data-all-networks]');assert(await evaluate(`document.getElementById('scanRange').value===${JSON.stringify(allNetworksRange)}&&!document.getElementById('startScan').hidden`),'All networks must fill a semicolon-separated range');
  await click('[data-scan-filter=alive]');await click('#startScan');await waitFor(`!document.getElementById('startScan').hidden&&document.getElementById('scanSummary').textContent.includes('762 de 762 comprobadas')`);
  assert(await evaluate(`['192.168.1.30','192.168.20.70','192.168.30.50'].every(ip=>document.getElementById('scanTable').textContent.includes(ip))&&document.getElementById('scanScope').textContent.includes(${JSON.stringify(allNetworksRange)})`),'Combined scan must discover devices in every configured network');
  await screenshot('panel-scanner-all-networks');
  await click('[data-view=settings]');await click(`[data-delete-network="${videoNetworkId}"]`);await click('#confirmAction');await waitFor(`!document.getElementById('confirmDialog').open`);assert(await evaluate(`document.querySelectorAll('#scanPresets [data-network-preset]').length===2&&!document.querySelector('[data-network-preset="${videoNetworkId}"]')`),'Deleted network remains in scanner presets');
  assert(await evaluate(`document.getElementById('scanRange').value==='192.168.1.0/24 ; 192.168.30.0/24'&&document.querySelector('[data-all-networks]').classList.contains('selected')`),'All-network selection must follow changes to configured networks');
  console.log('All networks are selected by default, joined with semicolons and scanned together without missing any subnet.');
  console.log('Multiple named CIDR networks, individual/optional DHCP pools, preset selection, persistence and account placement passed.');
  assert(await evaluate(`document.getElementById('retryButton').hidden`),'Successful settings save must not expose a manual apply step');
  await evaluate(`(async()=>{const inventory=await(await fetch('api.php?action=data')).json();inventory.services.find(s=>s.domain==='mezclador-qa.lan').url='http://192.168.1.30:8082';const transfer=new DataTransfer();transfer.items.add(new File([JSON.stringify(inventory)],'inventory.json',{type:'application/json'}));const input=document.getElementById('importFile');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await waitFor(`document.getElementById('confirmDialog').open`);await click('#confirmAction');await waitFor(`!document.getElementById('confirmDialog').open`);
  assert(await evaluate(`(async()=>(await(await fetch('api.php?action=applied')).json()).caddy.includes('http://192.168.1.30:8082{uri}'))()`),'Import did not publish automatically');
  if(stateDirectory){
    const failure=path.join(stateDirectory,'demo-dns-settings.json');
    fs.writeFileSync(failure,JSON.stringify({enabled:false}));
    try {
      await click('[data-view=services]');await click('[data-edit="seed-companion"]');await fill('#editorForm [name=description]','Guardado con fallo DNS simulado');await click('#saveButton');await waitFor(`!document.getElementById('editor').open&&!document.getElementById('retryButton').hidden`);
      assert(await evaluate(`document.getElementById('feedback').classList.contains('warning')`),'Publication failure must be visible without losing the save');
    }finally{fs.unlinkSync(failure);}
    await click('[data-view=settings]');await click('#retryButton');await waitFor(`document.getElementById('retryButton').hidden`);
  }
  console.log('Settings and imports publish automatically; retry is reserved for failed publication.');
  await click('[data-view=settings]');await click('#addCategoryButton');await fill('#editorForm [name=name]','Categoría QA');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  const categoryId=await evaluate(`(async()=>(await(await fetch('api.php?action=data')).json()).categories.find(c=>c.name==='Categoría QA').id)()`);
  assert(await evaluate(`document.querySelector('[data-category="Categoría QA"]') !== null`),'New category missing from filters');
  await click('[data-view=services]');await click('#addButton');assert(await evaluate(`[...document.querySelectorAll('#categoryNames option')].some(o=>o.value==='Categoría QA')`),'New category missing from service form');await click('#editor [data-close]');
  const secondId=await evaluate(`(async()=>(await(await fetch('api.php?action=data')).json()).services.find(s=>s.domain==='segundo-qa.lan').id)()`);
  await click(`[data-edit="${secondId}"]`);await fill('#editorForm [name=category]','Categoría QA');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  await click('[data-view=devices]');await click('[data-host="192.168.1.30"]');assert(await evaluate(`[...document.querySelectorAll('#categoryNames option')].some(o=>o.value==='Categoría QA')`),'New category missing from equipment form');await fill('#editorForm [name=category]','Categoría QA');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  await click('[data-category="Categoría QA"]');await click('[data-view=settings]');await click(`[data-edit-category="${categoryId}"]`);await fill('#editorForm [name=name]','Categoría renovada QA');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  assert(await evaluate(`document.querySelector('[data-category="Categoría QA"]')===null&&document.querySelector('[data-category="Categoría renovada QA"]').classList.contains('selected')`),'Category rename did not update or preserve the active filter');
  assert(await evaluate(`(async()=>{const data=await(await fetch('api.php?action=data')).json();return data.services.find(s=>s.domain==='segundo-qa.lan').category==='Categoría renovada QA'&&data.ips.find(h=>h.ip==='192.168.1.30').tag==='Categoría renovada QA';})()`),'Category rename did not update both inventory references');
  await click(`[data-delete-category="${categoryId}"]`);await waitFor(`document.getElementById('categoryDeleteDialog').open`);assert(await evaluate(`document.getElementById('categoryReplacement').required`),'Used category deletion must require a destination');await fill('#categoryReplacement','cat-0');await click('#categoryDeleteForm button[type=submit]');await waitFor(`!document.getElementById('categoryDeleteDialog').open`);
  assert(await evaluate(`(async()=>{const data=await(await fetch('api.php?action=data')).json();return data.categories.every(c=>c.name!=='Categoría renovada QA')&&data.services.find(s=>s.domain==='segundo-qa.lan').category==='Infraestructura'&&data.ips.find(h=>h.ip==='192.168.1.30').tag==='Infraestructura'&&data.services.length===7;})()`),'Deleting category did not safely reassign its services and equipment');
  await click('#addCategoryButton');await fill('#editorForm [name=name]','Vacía QA');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);const emptyId=await evaluate(`(async()=>(await(await fetch('api.php?action=data')).json()).categories.find(c=>c.name==='Vacía QA').id)()`);await click(`[data-delete-category="${emptyId}"]`);assert(await evaluate(`document.getElementById('categoryReplacementField').hidden`),'Empty category should not require reassignment');await click('#categoryDeleteForm button[type=submit]');await waitFor(`!document.getElementById('categoryDeleteDialog').open`);
  await call('Page.reload');await waitFor(`!document.getElementById('app').hidden&&document.querySelectorAll('.service-card').length===7`);assert(await evaluate(`!document.getElementById('categoryList').textContent.includes('Categoría renovada QA')&&!document.getElementById('categoryList').textContent.includes('Vacía QA')`),'Category changes did not persist');
  await click('[data-view=settings]');await screenshot('panel-settings-categories');
  console.log('Category creation, form choices, renaming, active filters, safe deletion and persistence passed.');
  assert(await evaluate(`document.querySelectorAll('#usersList .current-user').length===1&&document.querySelector('#usersList .current-user').dataset.userId==='independent-admin'&&document.querySelector('[data-user-id="independent-admin"] [data-toggle-user]')===null&&document.querySelector('[data-user-id="independent-admin"] [data-delete-user]')===null`),'Primary account must be highlighted and protected from disabling/deletion');
  await click('#addUserButton');assert(await evaluate(`document.querySelectorAll('#editorFields input[type=password]').length===2`),'Creating a user must require password confirmation');await fill('#editorForm [name=username]','operatorqa');await fill('#editorForm [name=role]','admin');await fill('#editorForm [name=password]','OperatorDemo1');await fill('#editorForm [name=confirmPassword]','MismatchDemo1');await click('#saveButton');await waitFor(`document.getElementById('editorError').textContent.includes('no coinciden')`);assert(await evaluate(`document.getElementById('editor').open&&!document.getElementById('usersList').textContent.includes('operatorqa')`),'Mismatched passwords created an account');await fill('#editorForm [name=confirmPassword]','OperatorDemo1');await click('#saveButton');await waitFor(`!document.getElementById('editor').open&&document.getElementById('usersList').textContent.includes('operatorqa')`);
  const operatorId=await evaluate(`(async()=>(await(await fetch('api.php?action=users')).json()).users.find(user=>user.username==='operatorqa').id)()`);
  assert(await evaluate(`document.querySelector('[data-user-id="${operatorId}"] [data-user-password]')!==null&&document.querySelector('[data-user-id="independent-admin"] [data-user-password]')!==null`),'Every user must have a password button in its own row for the primary administrator');
  await click(`[data-user-password="${operatorId}"]`);assert(await evaluate(`document.querySelector('#editorForm [name=currentPassword]')===null&&document.querySelectorAll('#editorFields input[type=password]').length===2&&document.getElementById('editorTitle').textContent.includes('operatorqa')`),'Reset must identify the selected account and only ask for the new password twice');await fill('#editorForm [name=newPassword]','OperatorChanged1');await fill('#editorForm [name=confirmPassword]','MismatchDemo1');await click('#saveButton');await waitFor(`document.getElementById('editorError').textContent.includes('no coinciden')`);await fill('#editorForm [name=confirmPassword]','OperatorChanged1');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);
  await click('[data-user-password="independent-admin"]');await fill('#editorForm [name=newPassword]','PrimaryChanged1');await fill('#editorForm [name=confirmPassword]','PrimaryChanged1');await click('#saveButton');await waitFor(`!document.getElementById('editor').open`);assert(await evaluate(`(async()=>{const session=await(await fetch('api.php?action=session')).json();return session.authenticated&&session.isPrimary&&session.username==='admin';})()`),'Changing the primary password must preserve its current session');
  await screenshot('panel-accounts-primary','#accountsPanel');
  await click('#logout');await waitFor(`document.getElementById('app').hidden&&!document.getElementById('loginScreen').hidden`);await fill('#loginForm [name=username]','operatorqa');await fill('#loginForm [name=password]','OperatorChanged1');await click('#loginForm button[type=submit]');await waitFor(`!document.getElementById('app').hidden&&document.getElementById('username').textContent==='operatorqa'`);await click('[data-view=settings]');
  assert(await evaluate(`document.querySelector('#usersList .current-user').dataset.userId==='${operatorId}'&&document.querySelectorAll('#usersList .current-user').length===1&&document.querySelector('#usersList [data-user-password]')===null&&!document.querySelector('#usersList .current-user').textContent.includes('Cuenta principal')`),'Secondary administrator must be highlighted without being able to change passwords or being labelled as the primary account');
  await screenshot('panel-accounts-secondary','#accountsPanel');
  await click('#logout');await waitFor(`document.getElementById('app').hidden&&!document.getElementById('loginScreen').hidden`);await fill('#loginForm [name=username]','admin');await fill('#loginForm [name=password]','PrimaryChanged1');await click('#loginForm button[type=submit]');await waitFor(`!document.getElementById('app').hidden&&document.getElementById('username').textContent==='admin'`);
  console.log('Password confirmation, protected primary account, row-specific resets, primary-only permissions and current-user highlighting passed.');
  await evaluate(`(()=>{window.ndiStatusRequests=0;const original=window.fetch;window.fetch=(input,options)=>{if(String(input).includes('api/ndi/status'))window.ndiStatusRequests++;return original(input,options);};})()`);
  await click('[data-view=settings]');assert(await evaluate(`document.getElementById('ndiTab').hidden&&document.getElementById('ndiFields').hidden&&!document.getElementById('ndiEnabled').checked`),'Disabled NDI must hide its tab and fields');await evaluate(`new Promise(resolve=>setTimeout(resolve,2200))`);assert(await evaluate(`window.ndiStatusRequests===0`),'Disabled NDI must not make polling requests');
  await click('#ndiEnabled');await fill('#ndiForm [name=ip]','192.168.10.20');await fill('#ndiForm [name=polling_rate]','2');await fill('#ndiForm [name=groups]','Public,camaras');await click('#ndiTest');await waitFor(`!document.getElementById('ndiTest').disabled&&document.getElementById('ndiTestResult').textContent.includes('Online')`);assert(await evaluate(`document.getElementById('ndiTestResult').textContent.includes('5 fuentes')&&document.getElementById('ndiTab').hidden`),'Connection test must show detected sources without enabling or saving the module');
  await fill('#ndiForm [name=mock_scenario]','offline');await click('#ndiTest');await waitFor(`!document.getElementById('ndiTest').disabled&&document.getElementById('ndiTestResult').textContent.includes('Offline')`);assert(await evaluate(`document.getElementById('ndiTestResult').textContent.includes('timeout')`),'Offline connection test must explain the failure');await fill('#ndiForm [name=mock_scenario]','normal');await click('#ndiSave');await waitFor(`!document.getElementById('ndiTab').hidden&&!document.getElementById('ndiSave').disabled`);
  await call('Page.reload');await waitFor(`!document.getElementById('app').hidden&&!document.getElementById('ndiTab').hidden`);assert(await evaluate(`document.querySelector('#ndiForm [name=ip]').value==='192.168.10.20'&&document.querySelector('#ndiForm [name=polling_rate]').value==='2'&&document.querySelector('#ndiForm [name=groups]').value==='Public,camaras'`),'NDI settings must persist across reloads');
  await evaluate(`(()=>{window.ndiStatusRequests=0;const original=window.fetch;window.fetch=(input,options)=>{if(String(input).includes('api/ndi/status'))window.ndiStatusRequests++;return original(input,options);};})()`);
  await click('#ndiTab');await waitFor(`document.getElementById('ndiState').textContent==='Online'&&document.querySelectorAll('#ndiSources tbody tr').length===7`);assert(await evaluate(`document.getElementById('ndiSourceCount').textContent==='5'&&document.getElementById('ndiReceiverCount').textContent==='2'&&document.getElementById('ndiSources').textContent.includes('VMIX-MAIN')&&document.getElementById('ndiSources').textContent.includes('192.168.10.65:5962')&&document.querySelectorAll('#ndiSources time[datetime]').length===7&&document.getElementById('ndiEvents').textContent.includes('Receptor detectado')`),'NDI must show sender/receiver roles, latest sightings and a persistent activity log');
  assert(await evaluate(`getComputedStyle(document.querySelector('.ndi-role.sender')).color==='rgb(145, 195, 250)'&&getComputedStyle(document.querySelector('.ndi-role.receiver')).color==='rgb(145, 220, 160)'`), 'NDI must render senders in blue and receivers in green');
  await click('[data-ndi-direction=receiver]');assert(await evaluate(`document.querySelectorAll('#ndiSources tbody tr').length===2&&document.getElementById('ndiSources').textContent.includes('También emite')`),'Receiver filter must identify hosts that also send');await click('[data-ndi-direction=sender]');assert(await evaluate(`document.querySelectorAll('#ndiSources tbody tr').length===5`),'Sender filter failed');await click('[data-ndi-direction=all]');await screenshot('panel-ndi-desktop');await screenshot('panel-ndi-history','#ndiView');
  await fill('#ndiSearch','escenario');assert(await evaluate(`document.querySelectorAll('#ndiSources tbody tr').length===1&&document.getElementById('ndiSources').textContent.includes('CAM-02')`),'NDI stream filter failed');await fill('#ndiSearch','192.168.10.65');assert(await evaluate(`document.getElementById('ndiSources').textContent.includes('PROPRESENTER')&&!document.getElementById('ndiSources').textContent.includes('CAM-01')`),'NDI address filter failed');await fill('#ndiSearch','');
  const ndiInitialUpdate=await evaluate(`document.getElementById('ndiUpdated').textContent`);await waitFor(`window.ndiStatusRequests>=2&&document.getElementById('ndiUpdated').textContent!==${JSON.stringify(ndiInitialUpdate)}`);
  await evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));`);const ndiBackgroundRequests=await evaluate(`window.ndiStatusRequests`);await evaluate(`new Promise(resolve=>setTimeout(resolve,2300))`);assert(await evaluate(`window.ndiStatusRequests`)===ndiBackgroundRequests,'A background browser tab must stop NDI polling');await evaluate(`delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));`);await waitFor(`window.ndiStatusRequests>${ndiBackgroundRequests}`);
  await click('[data-view=services]');const ndiHiddenRequests=await evaluate(`window.ndiStatusRequests`);await evaluate(`new Promise(resolve=>setTimeout(resolve,2300))`);assert(await evaluate(`window.ndiStatusRequests`)===ndiHiddenRequests,'Leaving the NDI view must stop polling');
  for (const scenario of ['offline','empty','missing_lib','normal']) {
    const count = {normal:'5',empty:'0',offline:'—',missing_lib:'—'}[scenario];
    await click('[data-view=settings]');await fill('#ndiForm [name=mock_scenario]',scenario);await click('#ndiSave');await waitFor(`!document.getElementById('ndiSave').disabled`);await click('#ndiTab');await waitFor(`document.getElementById('ndiSourceCount').textContent===${JSON.stringify(count)}&&document.getElementById('ndiState').textContent===${JSON.stringify(scenario==='offline'?'Offline':'Online')}`);
    assert(await evaluate(`document.getElementById('ndiWarning').hidden`)===!['offline','missing_lib'].includes(scenario),`NDI warning presentation failed for ${scenario}`);
    const availability=scenario==='normal'?'available':scenario==='empty'?'absent':'unknown';
    assert(await evaluate(`document.querySelectorAll('#ndiSources .ndi-availability.${availability}').length===${scenario==='missing_lib'?6:7}`),`NDI must retain previous devices with accurate observation state for ${scenario}`);
    assert(await evaluate(`document.querySelectorAll('[data-ndi-forget]').length===${scenario==='empty'?7:0}`),'NDI must only offer removal for confirmed absent entries');
    if (scenario==='empty') {
      assert(await evaluate(`document.getElementById('ndiEvents').textContent.includes('Deja de estar visible')&&document.getElementById('ndiReceiverNote').textContent.includes('Allow Receiver Advertising')`),'Missing devices must be logged and zero advertised receivers must explain how to advertise');
      await click('[data-ndi-forget]');
      await click('#confirmDialog .quiet');
      assert(await evaluate(`document.querySelectorAll('[data-ndi-forget]').length===7`),'Cancelling must retain all entries');
      await click('[data-ndi-forget]');
      await click('#confirmAction');
      await waitFor(`!document.getElementById('ndiRefresh').disabled&&document.querySelectorAll('[data-ndi-forget]').length===6&&document.getElementById('ndiEvents').textContent.includes('Deja de estar visible')`);
      await call('Page.reload');
      await waitFor(`!document.getElementById('ndiTab').hidden&&!document.getElementById('app').hidden`);
      await evaluate(`(()=>{window.ndiStatusRequests=0;const original=window.fetch;window.fetch=(input,options)=>{if(String(input).includes('api/ndi/status'))window.ndiStatusRequests++;return original(input,options);};})()`);
      await click('#ndiTab');
      await waitFor(`document.querySelectorAll('[data-ndi-forget]').length===6`);
    }
  }
  assert(await evaluate(`document.getElementById('ndiEvents').textContent.includes('visible de nuevo')`),'Recovered devices must appear in the log');
  await click('[data-view=settings]');await fill('#ndiForm [name=mock_scenario]','missing_lib');await click('#ndiTest');
  await waitFor(`!document.getElementById('ndiInstallFromTest').hidden&&document.getElementById('ndiTestResult').textContent.includes('Fuentes sin comprobar')`);
  await click('#ndiInstallFromTest');await waitFor(`document.getElementById('ndiRuntimeDialog').open&&!document.getElementById('ndiRuntimeAccept').disabled`);
  await click('#ndiRuntimeCancel');assert(await evaluate(`!document.getElementById('ndiRuntimeDialog').open&&!document.getElementById('ndiInstallFromTest').hidden`),'Cancelling the licence must preserve the missing-runtime warning');
  await click('#ndiInstallFromTest');await waitFor(`!document.getElementById('ndiRuntimeAccept').disabled`);
  await click('#ndiRuntimeAccept');await waitFor(`!document.getElementById('ndiRuntimeDialog').open&&document.getElementById('ndiInstallFromTest').hidden&&document.getElementById('ndiTestResult').textContent.includes('5 fuentes')`);
  await click('#ndiTab');await waitFor(`document.getElementById('ndiSourceCount').textContent==='5'`);
  await checkRefreshTimeout('ndiRefresh', 'api/ndi/refresh', 'Consultando…', 'ndiWarning');
  const busyMs = await evaluate(`new Promise(resolve => {
    const button = document.getElementById('ndiRefresh');
    const startedAt = performance.now();
    const observer = new MutationObserver(() => {
      if (button.disabled) return;
      observer.disconnect();
      resolve(performance.now() - startedAt);
    });
    observer.observe(button, {attributes:true, attributeFilter:['disabled']});
    button.click();
  })`);
  assert(busyMs >= 995, `A quick manual NDI refresh must stay disabled for one second (observed ${busyMs} ms)`);
  await waitFor(`document.getElementById('ndiState').textContent==='Online'`);
  await click('#ndiClearLog');await waitFor(`document.getElementById('ndiEventCount').textContent.startsWith('0 eventos')&&document.getElementById('ndiClearLog').disabled`);assert(await evaluate(`document.querySelectorAll('#ndiSources tbody tr').length===7&&document.querySelectorAll('#ndiSources time[datetime]').length===7&&document.getElementById('ndiEvents').textContent.includes('Registro vacío')`),'Clearing the log must preserve all devices and their latest sightings');await click('#ndiRefresh');await waitFor(`!document.getElementById('ndiRefresh').disabled`);assert(await evaluate(`document.getElementById('ndiEventCount').textContent.startsWith('0 eventos')`),'Refresh must not resurrect cleared events');
  for(const width of [320,390,768,1440]) {await call('Emulation.setDeviceMetricsOverride',{width,height:1200,deviceScaleFactor:1,mobile:false});await click('#ndiTab');assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),`NDI view overflows at ${width}px`);if(width===390)await screenshot('panel-ndi-mobile');await click('[data-view=settings]');assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),`Enabled NDI settings overflow at ${width}px`);}
  await screenshot('panel-ndi-settings','#ndiForm');await click('#ndiEnabled');await waitFor(`document.getElementById('ndiTab').hidden&&!document.getElementById('ndiEnabled').disabled`);const ndiDisabledRequests=await evaluate(`window.ndiStatusRequests`);await evaluate(`new Promise(resolve=>setTimeout(resolve,2300))`);assert(await evaluate(`window.ndiStatusRequests`)===ndiDisabledRequests,'Disabling NDI must stop every polling request');assert(await evaluate(`(async()=>JSON.stringify(await(await fetch('api/ndi/status')).json())==='{"enabled":false}')()`),'Disabled endpoint must return only enabled=false');
  console.log('NDI settings, connection tests, live polling, source filtering, all mock scenarios, responsive layout and zero polling while disabled/hidden passed.');
  for (const width of [320,390,768,1440]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:1200,deviceScaleFactor:1,mobile:false});
    const identity=await evaluate(`(()=>{const logo=document.querySelector('.brand img').getBoundingClientRect(),block=document.querySelector('.header-identity').getBoundingClientRect(),title=document.querySelector('.header-identity h1').getBoundingClientRect(),name=document.getElementById('installationName').getBoundingClientRect();return {logoHeight:logo.height,blockHeight:block.height,logoTop:logo.top,blockTop:block.top,titleBottom:title.bottom,nameTop:name.top,nameBottom:name.bottom,blockBottom:block.bottom};})()`);
    assert(Math.abs(identity.logoHeight-identity.blockHeight)<.2&&Math.abs(identity.logoTop-identity.blockTop)<.2&&identity.titleBottom<=identity.nameTop&&identity.nameBottom<=identity.blockBottom,`Header identity does not fit the logo at ${width}px: ${JSON.stringify(identity)}`);
    for (const section of ['services','devices','scan','settings']) {
      await click(`[data-view=${section}]`);
      const metrics=await evaluate(`({width:innerWidth,scroll:document.documentElement.scrollWidth})`);
      assert(metrics.scroll<=metrics.width,`Overflow at ${width} ${section}: ${JSON.stringify(metrics)}`);
      if (width===320 && section==='settings') await screenshot('panel-accounts-mobile','#accountsPanel');
    }
    console.log(`All four views fit at ${width}px.`);
  }
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:1000,deviceScaleFactor:1,mobile:false});await click('[data-view=services]');await screenshot('panel-services-mobile');
  await evaluate(`document.getElementById('installationName').textContent='Nombre de instalación muy largo para comprobar la cabecera en una pantalla móvil'`);
  assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth&&document.getElementById('installationName').getBoundingClientRect().height<=document.querySelector('.brand img').getBoundingClientRect().height`),'Long installation name overflows the header');
  assert(exceptions.length===0,'Browser exceptions: '+JSON.stringify(exceptions));
  console.log('Real-browser checks passed without JavaScript exceptions.');
} finally {ws.close();}
