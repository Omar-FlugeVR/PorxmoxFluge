import { isIpv4 } from './validation.js';
export const MAX_SCAN_ADDRESSES = 65536;
export function ipToInt(ip) {
  if (!isIpv4(ip)) return null;
  return ip.split('.').reduce((n, part) => n * 256 + Number(part), 0);
}
export function intToIp(value) { return [24, 16, 8, 0].map(shift => (value >>> shift) & 255).join('.'); }
export function isIpInDhcpRange(ip, start, end) {
  const values = [ip, start, end].map(ipToInt);
  return !values.includes(null) && values[0] >= Math.min(values[1], values[2]) && values[0] <= Math.max(values[1], values[2]);
}
// Validate and count intervals without allocating one string per address while typing.
export function inspectIpRange(input, limit = MAX_SCAN_ADDRESSES) {
  if (typeof input !== 'string' || !input.trim()) return { ranges: [], count: 0 };
  if (input.length > 8192) throw new Error('El rango es demasiado largo; usa como máximo 8192 caracteres.');
  const ranges = [];
  let covered = [], count = 0;
  const append = (start, end) => {
    if (start === null || end === null) throw new Error('Rango IPv4 no válido.');
    const first = Math.min(start, end), last = Math.max(start, end);
    if (last - first + 1 > limit) throw new Error(`Escanea como máximo ${limit} direcciones cada vez.`);
    // Keep the original segment order while excluding previously requested addresses.
    const additions = [];
    let cursor = first;
    for (const [from, to] of covered) {
      if (to < cursor) continue;
      if (from > last) break;
      if (from > cursor) additions.push([cursor, from - 1]);
      cursor = Math.max(cursor, to + 1);
      if (cursor > last) break;
    }
    if (cursor <= last) additions.push([cursor, last]);
    for (const [from, to] of additions) count += to - from + 1;
    if (count > limit) throw new Error(`Escanea como máximo ${limit} direcciones cada vez.`);
    ranges.push(...additions);
    const merged = [];
    for (const [from, to] of [...covered, [first, last]].sort((a, b) => a[0] - b[0])) {
      const previous = merged.at(-1);
      if (previous && from <= previous[1] + 1) previous[1] = Math.max(previous[1], to);
      else merged.push([from, to]);
    }
    covered = merged;
  };
  for (const segment of input.split(/[,;]+/).map(s => s.trim()).filter(Boolean)) {
    if (segment.includes('/')) {
      const parts = segment.split('/');
      const base = ipToInt(parts[0]);
      if (parts.length !== 2 || !/^\d{1,2}$/.test(parts[1])) throw new Error('CIDR no válido.');
      const mask = Number(parts[1]);
      if (base === null || mask < 0 || mask > 32) throw new Error('CIDR IPv4 no válido.');
      const count = 2 ** (32 - mask);
      const network = Math.floor(base / count) * count;
      append(network + (mask <= 30 ? 1 : 0), network + count - (mask <= 30 ? 2 : 1));
    } else if (segment.includes('-')) {
      const parts = segment.split('-').map(s => s.trim());
      if (parts.length !== 2) throw new Error('Rango IPv4 no válido.');
      const end = parts[1].includes('.') ? parts[1] : `${parts[0].split('.').slice(0, 3).join('.')}.${parts[1]}`;
      append(ipToInt(parts[0]), ipToInt(end));
    } else {
      const value = ipToInt(segment);
      append(value, value);
    }
  }
  return { ranges, count };
}
export function expandIpRanges(ranges) {
  const result = [];
  for (const [first, last] of ranges) {
    for (let value = first; value <= last; value++) result.push(intToIp(value));
  }
  return result;
}
export function parseIpRange(input, limit = MAX_SCAN_ADDRESSES) {
  return expandIpRanges(inspectIpRange(input, limit).ranges);
}

export class ScanRunner {
  constructor(request) { this.request = request; this.generation = 0; this.controller = null; }
  cancel() { this.generation++; this.controller?.abort(); this.controller = null; }
  async run(ips, { onChunk = () => {}, onProgress = () => {}, chunkSize = 16, concurrency = 2 } = {}) {
    this.cancel();
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    let cursor = 0, completed = 0;
    const active = () => generation === this.generation && !controller.signal.aborted;
    const worker = async () => {
      while (active() && cursor < ips.length) {
        const chunk = ips.slice(cursor, cursor + chunkSize);
        cursor += chunk.length;
        const data = await this.request('scan_chunk', { method: 'POST', data: { ips: chunk }, signal: controller.signal });
        if (!active()) return;
        if (!Array.isArray(data.results)) throw new Error('Respuesta de escaneo no válida.');
        // Accept only addresses that belong to the requested chunk.
        onChunk(data.results.filter(item => item && chunk.includes(item.ip)));
        completed += chunk.length;
        onProgress(completed, ips.length);
      }
    };
    try {
      await Promise.all(Array.from({ length: concurrency }, worker));
      return { cancelled: !active(), completed };
    } catch (error) {
      const cancelled = !active() || error.name === 'AbortError';
      if (!cancelled) this.cancel();
      if (cancelled) return { cancelled: true, completed };
      throw error;
    } finally {
      if (this.controller === controller) this.controller = null;
    }
  }
}
