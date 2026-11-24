import { extractServiceIp, replaceServiceIp } from './validation.js';

/** Build a read model; standalone host fields take precedence over service fields. */
export function mergeHosts(services, ips) {
  const byIp = new Map(ips.map(item => [item.ip, { ...item, isFromService: false, linkedServices: [] }]));
  for (const service of services) {
    const ip = service.proxyMode === 'dns_only' ? service.ip : extractServiceIp(service.url);
    if (!ip) continue;
    let host = byIp.get(ip);
    if (!host) {
      host = { id: `srv-ip-${service.id}`, ip, hostname: service.name, tag: service.category || 'Servidores', mac: service.mac || '', notes: service.notes ?? service.description ?? '', sshEnabled: service.sshEnabled !== false, sshUser: service.sshUser || 'root', isFromService: true, serviceId: service.id, linkedServices: [] };
      byIp.set(ip, host);
    }
    host.linkedServices.push({ id: service.id, name: service.name, domain: service.domain, url: service.url });
    if (!host.hostname) host.hostname = service.name;
    if (!host.tag) host.tag = service.category;
    if (!host.mac) host.mac = service.mac || '';
  }
  return [...byIp.values()];
}

export function updateHostMetadata(document, ip, { mac, sshEnabled, sshUser, notes }) {
  for (const service of document.services.filter(item => extractServiceIp(item.url) === ip)) Object.assign(service, { mac, sshEnabled, sshUser, ...(notes !== undefined ? { notes } : {}) });
  for (const host of document.ips.filter(item => item.ip === ip)) Object.assign(host, { mac, sshEnabled, sshUser, ...(notes !== undefined ? { notes } : {}) });
}

/** Update a host without splitting services that share its address. Mutates a draft only. */
export function updateIpRecord(document, { editingId, record }) {
  if (editingId?.startsWith('srv-ip-')) {
    const service = document.services.find(item => item.id === editingId.slice(7));
    if (!service) throw new Error('El servicio asociado ya no existe.');
    const previousIp = extractServiceIp(service.url);
    if (record.ip !== previousIp && (document.ips.some(item => item.ip === record.ip) || document.services.some(item => extractServiceIp(item.url) === record.ip))) throw new Error('Esa IP ya está asociada a otro equipo.');
    for (const item of document.services.filter(item => extractServiceIp(item.url) === previousIp)) item.url = replaceServiceIp(item.url, record.ip);
    Object.assign(service, { name: record.hostname, category: record.tag });
    updateHostMetadata(document, record.ip, record);
  } else if (editingId) {
    const host = document.ips.find(item => item.id === editingId);
    if (!host) throw new Error('El registro IP ya no existe.');
    const previousIp = host.ip;
    if (document.ips.some(item => item.id !== editingId && item.ip === record.ip)) throw new Error('Esa dirección IP ya está registrada.');
    if (previousIp !== record.ip && document.services.some(item => extractServiceIp(item.url) === record.ip)) throw new Error('Esa IP ya está asociada a otro equipo.');
    if (previousIp !== record.ip) {
      for (const service of document.services.filter(item => extractServiceIp(item.url) === previousIp)) service.url = replaceServiceIp(service.url, record.ip);
    }
    Object.assign(host, record);
    updateHostMetadata(document, record.ip, record);
  } else {
    if (document.ips.some(item => item.ip === record.ip)) throw new Error('Esa dirección IP ya está registrada.');
    // Keep device names and service names independent. A direct host can share its IP with services.
    document.ips.push(record);
    updateHostMetadata(document, record.ip, record);
  }
}
