import test from 'node:test';
import assert from 'node:assert/strict';
import { ScanRunner } from '../cts/caddy/panel/public/scanner.js';
import { isIpInConfiguredDhcpRange, networkForIp, validateScanRange } from '../cts/caddy/panel/public/scanner-config.js';

test('scanner forwards only results belonging to the requested addresses', async () => {
  const received = [];
  const runner = new ScanRunner(async () => ({results:[
    {ip:'192.168.1.30',isAlive:true},
    {ip:'192.168.1.31',isAlive:false},
    {ip:'192.168.1.90',isAlive:false}
  ]}));
  const result = await runner.run(['192.168.1.30','192.168.1.31'], {onChunk:items=>received.push(...items)});
  assert.deepEqual(received.map(item=>item.ip),['192.168.1.30','192.168.1.31']);
  assert.equal(result.completed,2);
});

test('cancelled requests do not turn unconfirmed addresses into inactive results', async () => {
  let resolve;
  const received = [];
  const runner = new ScanRunner(() => new Promise(done => {resolve=done;}));
  const pending = runner.run(['192.168.1.30','192.168.1.31'], {onChunk:items=>received.push(...items)});
  runner.cancel();
  resolve({results:[{ip:'192.168.1.30',isAlive:false}]});
  const result = await pending;
  assert.equal(result.cancelled,true);
  assert.equal(result.completed,0);
  assert.deepEqual(received,[]);
});

test('a failed chunk retains previous results without inventing missing responses', async () => {
  const received = [];
  let calls = 0;
  const runner = new ScanRunner(async (action,{data}) => {
    if (++calls === 2) throw new Error('Connection failed');
    return {results:[{ip:data.ips[0],isAlive:true}]};
  });
  await assert.rejects(runner.run(['192.168.1.30','192.168.1.31'],{chunkSize:1,concurrency:1,onChunk:items=>received.push(...items)}),/Connection failed/);
  assert.deepEqual(received,[{ip:'192.168.1.30',isAlive:true}]);
});

test('manual scans classify DHCP against the individual network of each address', () => {
  const configuration = {scanLimit:65536,scanAllowedCidrs:['192.168.1.0/24','192.168.20.0/24'],scanNetworks:[
    {id:'control',name:'Control',cidr:'192.168.1.0/24',dhcpPool:{start:'192.168.1.100',end:'192.168.1.150'}},
    {id:'video',name:'Video',cidr:'192.168.20.0/24',dhcpPool:{start:'192.168.20.50',end:'192.168.20.99'}}
  ]};
  assert.equal(isIpInConfiguredDhcpRange('192.168.1.110',configuration),true);
  assert.equal(isIpInConfiguredDhcpRange('192.168.20.70',configuration),true);
  assert.equal(isIpInConfiguredDhcpRange('192.168.1.70',configuration),false);
  assert.equal(isIpInConfiguredDhcpRange('192.168.20.110',configuration),false);
  assert.equal(isIpInConfiguredDhcpRange('192.168.30.110',configuration),false);
  assert.equal(isIpInConfiguredDhcpRange('invalid',configuration),false);
  assert.deepEqual(validateScanRange('192.168.1.110,192.168.20.70',configuration),['192.168.1.110','192.168.20.70']);
  assert.throws(()=>validateScanRange('192.168.30.110',configuration));
});

test('overlapping networks use the most specific subnet, including networks without DHCP', () => {
  const configuration={scanNetworks:[
    {id:'large',name:'LAN',cidr:'10.0.0.0/8',dhcpPool:{start:'10.0.0.1',end:'10.255.255.254'}},
    {id:'static',name:'Static',cidr:'10.20.0.0/16',dhcpPool:null}
  ]};
  assert.equal(networkForIp('10.20.0.50',configuration).id,'static');
  assert.equal(isIpInConfiguredDhcpRange('10.20.0.50',configuration),false);
  assert.equal(isIpInConfiguredDhcpRange('10.21.0.50',configuration),true);
  assert.throws(()=>validateScanRange('10.0.0.0/8',{...configuration,scanAllowedCidrs:['10.0.0.0/8'],scanLimit:65536}),/65536/);
});
