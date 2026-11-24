/** Shared frontend boundary validation. The backend validates independently. */
const BAD_URL = /[;{}#\\$`'"\s\x00-\x1f\x7f]/;
const ID = /^[A-Za-z0-9_-]{1,100}$/;
const SSH_USER = /^[a-z_][a-z0-9_-]{0,31}\$?$/;
const HEX_COLOR = /^#[\da-f]{6}$/i;
function text(value, label, max = 2000, required = false) {
  if (value === undefined || value === null) value = '';
  if (typeof value !== 'string' || new TextEncoder().encode(value).length > max || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value)) throw new Error(`${label}: texto no válido.`);
  const result = value.trim();
  if (required && !result) throw new Error(`${label}: campo obligatorio.`);
  return result;
}
export function isIpv4(value) {
  if (typeof value !== 'string' || !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) return false;
  return value.split('.').every(part => String(Number(part)) === part && Number(part) <= 255);
}
export function validateIp(value) {
  const result = text(value, 'Dirección IP', 15, true);
  if (!isIpv4(result)) throw new Error('Introduce una dirección IPv4 válida, sin CIDR.');
  return result;
}
export function validateDirectoryAddress(value) {
  const result = text(value, 'Dirección de inventario', 40, true);
  const match = result.match(/^((?:\d{1,3}\.){3}\d{1,3})(?:-(\d{1,3}))?(?:\/(\d{1,2}))?$/);
  if (!match || !isIpv4(match[1])) throw new Error('Introduce una IPv4, subred o rango válido.');
  if (match[2] !== undefined && (Number(match[2]) > 255 || Number(match[2]) < Number(match[1].split('.')[3]))) throw new Error('El final del rango debe estar entre el primer octeto y 255.');
  if (match[3] !== undefined && Number(match[3]) > 32) throw new Error('El prefijo CIDR debe estar entre 0 y 32.');
  return result;
}
export function normalizeDomain(value, { shorthand = false, empty = true } = {}) {
  let result = text(value, 'Dominio', 253).toLowerCase();
  if (!result && empty) return '';
  if (shorthand && result && !result.includes('.')) result += '.casa';
  if (!result || result.length > 253 || !result.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))) throw new Error('Dominio no válido; usa solo el nombre DNS, sin protocolo ni ruta.');
  return result;
}
export function normalizeServiceUrl(value) {
  let result = text(value, 'URL', 2048, true);
  if (BAD_URL.test(result)) throw new Error('La URL contiene caracteres no permitidos.');
  if (!/^https?:\/\//i.test(result)) {
    if (result.includes('://') || /^(javascript|data|vbscript|file):/i.test(result) || result.startsWith('/') || result.startsWith('.')) throw new Error('Usa una URL http:// o https:// válida.');
    result = `${/:(8006|9443|443)(?:\/|$)/.test(result) ? 'https' : 'http'}://${result}`;
  }
  let url;
  try { url = new URL(result); } catch { throw new Error('La URL no es válida.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !url.hostname) throw new Error('Usa una URL HTTP o HTTPS sin credenciales.');
  if (!isIpv4(url.hostname) && !url.hostname.startsWith('[')) normalizeDomain(url.hostname, { empty: false });
  return url.href;
}
export function validateMac(value) {
  const result = text(value, 'MAC', 17).replace(/-/g, ':').toUpperCase();
  if (result && !/^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(result)) throw new Error('La MAC debe tener seis pares hexadecimales.');
  return result;
}
export function validateSshUser(value) {
  const result = text(value || 'root', 'Usuario SSH', 33);
  if (!SSH_USER.test(result)) throw new Error('Usuario SSH no válido.');
  return result;
}
export function validateCustomIcon(value) {
  if (typeof value !== 'string') throw new Error('Icono no válido.');
  const result = text(value, 'Icono', 131072);
  if (!result) return '';
  if (/^https?:\/\//i.test(result)) return normalizeServiceUrl(result);
  if (/^data:image\/(png|jpeg|webp|gif);base64,[a-z0-9+/]+={0,2}$/i.test(result)) return result;
  if (result.startsWith('data:')) throw new Error('El icono debe ser una imagen PNG, JPEG, GIF o WebP.');
  return text(result, 'Icono', 128);
}
function identifier(value, label) {
  if (typeof value !== 'string' || !ID.test(value)) throw new Error(`${label}: identificador no válido.`);
  return value;
}
function unique(items, key, label, ignoreEmpty = false) {
  const seen = new Set();
  for (const item of items) {
    const value = item[key];
    if (ignoreEmpty && !value) continue;
    if (seen.has(value)) throw new Error(`${label}: valor duplicado.`);
    seen.add(value);
  }
}
export function validateDocument(value, { allowLegacy = false, defaultCategories = [] } = {}) {
  if (allowLegacy && Array.isArray(value)) value = { services: value, ips: [], categories: defaultCategories };
  if (allowLegacy && value && typeof value === 'object' && Array.isArray(value.services)) value = { ...value, ips: value.ips ?? [], categories: value.categories ?? defaultCategories };
  if (!value || typeof value !== 'object' || !Array.isArray(value.services) || !Array.isArray(value.ips) || !Array.isArray(value.categories)) throw new Error('El documento debe contener las listas de servicios, IPs y categorías.');
  if (value.services.length > 2000 || value.ips.length > 2000 || value.categories.length > 2000) throw new Error('El documento supera el límite de registros.');
  const categories = value.categories.map(c => {
    if (!c || typeof c !== 'object' || !HEX_COLOR.test(c.color)) throw new Error('Categoría o color no válido.');
    return { id: identifier(c.id, 'Categoría'), name: text(c.name, 'Nombre categoría', 120, true), color: c.color.toLowerCase() };
  });
  const services = value.services.map(s => {
    if (!s || typeof s !== 'object') throw new Error('Servicio no válido.');
    if (s.sshEnabled !== undefined && typeof s.sshEnabled !== 'boolean') throw new Error('Estado SSH no válido.');
    if (s.proxyMode !== undefined && !['proxy', 'redirect'].includes(s.proxyMode)) throw new Error('Modo de proxy no válido.');
    if (!/^[a-zA-Z0-9_-]*$/.test(s.icon ?? 'server')) throw new Error('Identificador de icono no válido.');
    return { id: identifier(s.id, 'Servicio'), name: text(s.name, 'Nombre del servicio', 160, true), url: normalizeServiceUrl(s.url), domain: normalizeDomain(s.domain), proxyMode: s.proxyMode || 'redirect', category: text(s.category ?? 'Servidores', 'Categoría', 120), description: text(s.description, 'Descripción', 2048), notes: text(s.notes, 'Notas', 8192), icon: text(s.icon || 'server', 'Icono', 64), customIcon: validateCustomIcon(s.customIcon || ''), mac: validateMac(s.mac), sshEnabled: s.sshEnabled !== false, sshUser: validateSshUser(s.sshUser) };
  });
  const ips = value.ips.map(i => {
    if (!i || typeof i !== 'object') throw new Error('Registro IP no válido.');
    if (i.sshEnabled !== undefined && typeof i.sshEnabled !== 'boolean') throw new Error('Estado SSH no válido.');
    return { id: identifier(i.id, 'IP'), ip: validateDirectoryAddress(i.ip), hostname: text(i.hostname, 'Nombre del equipo', 253), tag: text(i.tag ?? 'Servidores', 'Categoría', 120), mac: validateMac(i.mac), notes: text(i.notes, 'Notas', 8192), sshEnabled: i.sshEnabled !== false, sshUser: validateSshUser(i.sshUser) };
  });
  unique(categories, 'id', 'Categorías');
  unique(categories.map(c => ({ name: c.name.toLowerCase() })), 'name', 'Categorías');
  unique(services, 'id', 'Servicios');
  unique(services, 'domain', 'Dominios', true);
  unique(ips, 'id', 'IPs');
  unique(ips, 'ip', 'IPs');
  return { services, ips, categories };
}
export function extractServiceIp(value) {
  try { const host = new URL(normalizeServiceUrl(value)).hostname; return isIpv4(host) ? host : null; } catch { return null; }
}
export function replaceServiceIp(value, ip) {
  const url = new URL(normalizeServiceUrl(value));
  url.hostname = validateIp(ip);
  return url.href;
}
