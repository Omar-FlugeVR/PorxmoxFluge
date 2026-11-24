import test from 'node:test';
import assert from 'node:assert/strict';
import { validateNdiSettings } from '../cts/caddy/panel/public/ndi.js';

const settings={enabled:true,ip:'ndi.example.local',port:5959,polling_rate:5,timeout:3,groups:'Public,camaras',mock_scenario:'normal'};
test('NDI settings accept hostnames, IPv4/IPv6 and preserve group names',()=>{
  for(const ip of ['ndi.example.local','localhost','192.168.10.20','::1','2001:db8::20']) assert.equal(validateNdiSettings({...settings,ip}).ip,ip);
  assert.equal(validateNdiSettings({...settings,groups:' Public, camaras, Public '}).groups,'Public,camaras');
  assert.equal(validateNdiSettings({...settings,enabled:false,ip:''}).enabled,false);
});
test('NDI settings reject invalid hosts, ranges and groups before making requests',()=>{
  for(const patch of [{ip:''},{ip:'http://ndi.local'},{ip:'ndi.local:5959'},{ip:'999.1.1.1'},{ip:'bad..local'},
    {port:0},{port:65536},{port:'5959'},{polling_rate:1},{polling_rate:301},{timeout:0},{timeout:16},
    {enabled:'true'},{groups:'Public,\ncamaras'},{groups:'x'.repeat(65)},{mock_scenario:'invalid'}]) assert.throws(()=>validateNdiSettings({...settings,...patch}),JSON.stringify(patch));
});
