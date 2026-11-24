import { isIpv4, validateIp } from './validation.js';
import { ipToInt, isIpInDhcpRange, inspectIpRange, expandIpRanges, MAX_SCAN_ADDRESSES } from './scanner.js';

const PRIVATE_RANGES = [[0x0a000000, 0x0affffff], [0xac100000, 0xac1fffff], [0xc0a80000, 0xc0a8ffff]];

function privateIp(ip) {
  const value = ipToInt(ip);
  return value !== null && PRIVATE_RANGES.some(([first, last]) => value >= first && value <= last);
}
function validateCidr(value) {
  if (typeof value !== 'string') throw new Error('Red de escaneo no válida.');
  const parts = value.split('/');
  if (parts.length !== 2 || !isIpv4(parts[0]) || !/^\d{1,2}$/.test(parts[1]) || Number(parts[1]) > 32) throw new Error('Red de escaneo no válida.');
  const size = 2 ** (32 - Number(parts[1]));
  const start = Math.floor(ipToInt(parts[0]) / size) * size;
  const end = start + size - 1;
  const address = number => [24, 16, 8, 0].map(shift => (number >>> shift) & 255).join('.');
  if (!privateIp(address(start)) || !privateIp(address(end))) throw new Error('La red permitida debe ser una subred privada.');
  return value;
}
function coversRange(first, last, allowed) {
  let cursor = first;
  for (const [from, to] of allowed) {
    if (from > cursor) break;
    if (to >= cursor) cursor = to + 1;
    if (cursor > last) return true;
  }
  return false;
}
export function inspectScanRange(range, configuration) {
  const result = inspectIpRange(range, configuration.scanLimit);
  if (result.count === 0) throw new Error(`Introduce un rango IPv4 de hasta ${configuration.scanLimit} direcciones.`);
  const allowed = configuration.scanAllowedCidrs.map(cidr => {
    const [base, prefix] = cidr.split('/');
    const size = 2 ** (32 - Number(prefix));
    const first = Math.floor(ipToInt(base) / size) * size;
    return [first, first + size - 1];
  }).sort((a, b) => a[0] - b[0]);
  if (!result.ranges.every(([first, last]) => coversRange(first, last, PRIVATE_RANGES) && coversRange(first, last, allowed))) {
    throw new Error('El rango debe estar dentro de las redes privadas permitidas.');
  }
  return result;
}
export function validateScanRange(range, configuration) {
  return expandIpRanges(inspectScanRange(range, configuration).ranges);
}
export function isAllowedScanRange(range, configuration) {
  try { inspectScanRange(range, configuration); return true; }
  catch { return false; }
}
export function networkForIp(ip, configuration) {
  const address = ipToInt(ip);
  if (address === null) return null;
  let result = null;
  let prefix = -1;
  for (const network of configuration.scanNetworks) {
    const [base, bits] = network.cidr.split('/');
    const size = 2 ** (32 - Number(bits));
    if (Number(bits) > prefix && Math.floor(address / size) === Math.floor(ipToInt(base) / size)) {
      result = network; prefix = Number(bits);
    }
  }
  return result;
}
export function isIpInConfiguredDhcpRange(ip, configuration) {
  const pool = networkForIp(ip, configuration)?.dhcpPool;
  return Boolean(pool && isIpInDhcpRange(ip, pool.start, pool.end));
}
export function validateScannerConfiguration(value) {
  if (!value || !Number.isInteger(value.configRevision) || value.configRevision < 0 || value.scanLimit !== MAX_SCAN_ADDRESSES || !Array.isArray(value.scanNetworks) || value.scanNetworks.length < 1 || value.scanNetworks.length > 32) throw new Error('La configuración de redes no es válida.');
  const identifiers = new Set();
  const scanNetworks = value.scanNetworks.map(network => {
    if (!network || typeof network.id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(network.id) || identifiers.has(network.id) || typeof network.name !== 'string' || !network.name.trim() || [...network.name].length > 80) throw new Error('Una red compartida no es válida.');
    identifiers.add(network.id);
    const cidr = validateCidr(network.cidr);
    const dhcpPool = network.dhcpPool == null ? null : {start:validateIp(network.dhcpPool.start),end:validateIp(network.dhcpPool.end)};
    if (dhcpPool && ipToInt(dhcpPool.start) > ipToInt(dhcpPool.end)) throw new Error('El pool DHCP no es válido.');
    return {id:network.id,name:network.name.trim(),cidr,dhcpPool};
  });
  return {...value,scanNetworks,scanAllowedCidrs:scanNetworks.map(network=>network.cidr)};
}
