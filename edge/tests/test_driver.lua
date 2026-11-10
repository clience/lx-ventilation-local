-- Framework contract harness, not a replacement for real-hub testing.
local captured, response, posted, events = nil, nil, nil, {}
local caps = setmetatable({}, {__index=function(t,k)
  local c = {ID=k, commands=setmetatable({}, {__index=function(_,n) return {NAME=n} end})}
  setmetatable(c,{__index=function(_,a) return function(v) return {cap=k,attr=a,value=v} end end})
  rawset(t,k,c); return c
end})
package.preload['st.capabilities']=function() return caps end
package.preload['st.driver']=function() return function(_,opts) captured=opts; return {run=function() end} end end
package.preload['st.json']=function() return {encode=function(v) posted=v; return 'body' end,decode=function() return response end} end
local status=200
local requests=0
local http={request=function(req) requests=requests+1; return 1,status end}
package.preload['cosock']=function() return {asyncify=function() return http end} end
package.preload['cosock.socket']=function() return {} end
package.preload['ltn12']=function() return {source={string=function(v) return v end},sink={table=function(v) return v end}} end
dofile(arg[1] or 'edge/driver/src/init.lua')
local fields, timers, delayed, created = {}, 0, 0, 0
local device={device_network_id='lxvent-lan-main',preferences={bridgeHost='192.168.0.10',lanKey=string.rep('x',32),allowCommands=false},thread={}}
function device:emit_event(e) table.insert(events,e) end
function device:online() self.is_online=true end
function device:offline() self.is_online=false end
function device:get_field(k) return fields[k] end
function device:set_field(k,v) fields[k]=v end
function device.thread:call_on_schedule(_,f) timers=timers+1; return f end
function device.thread:cancel_timer(_) timers=timers-1 end
function device.thread:call_with_delay(_,f) delayed=delayed+1; return f end
local devices={}
local driver={get_devices=function() return devices end,try_create_device=function(_,d)
  assert(d.device_network_id=='lxvent-lan-main' and d.profile=='lxvent-lan'); created=created+1
end}
response={id='lxvent-main',online=true,state={power='on',fan=2,mode='manual',lamp='off',temperature=22,humidity=58,co2=900,pm25=20,tvoc=105,filterPct=44,hexPct=89}}
captured.lifecycle_handlers.init(driver,device)
assert(device.is_online and #events==12 and timers==1)
captured.lifecycle_handlers.infoChanged(driver,device); assert(timers==1)
local before=requests
device.preferences.lanKey=string.rep('0',32)
captured.capability_handlers.refresh.refresh(driver,device)
assert(requests==before and not device.is_online)
device.preferences.lanKey=string.rep('x',32)
local handler=captured.capability_handlers.switch.on
handler(driver,device,{capability='switch',command='on'})
assert(posted==nil,'default must reject commands')
device.preferences.allowCommands=true; response={accepted=true}; status=202
local count=#events
handler(driver,device,{capability='switch',command='on'})
assert(posted.command=='power' and posted.value=='on' and #events==count and delayed==1)
captured.capability_handlers.fanSpeed.setFanSpeed(driver,device,{capability='fanSpeed',args={speed=0}})
assert(posted.command=='power' and posted.value=='off')
captured.capability_handlers.mode.setMode(driver,device,{capability='mode',args={mode='sleep'}})
assert(posted.command=='mode' and posted.value=='sleep')
captured.capability_handlers.rapidCooling.setRapidCooling(driver,device,{capability='rapidCooling',args={rapidCooling='off'}})
assert(posted.command=='lamp' and posted.value=='off')
device.is_online=true; status=403
handler(driver,device,{capability='switch',command='on'})
assert(device.is_online,'read-only rejection must not mark device offline')
status=503; handler(driver,device,{capability='switch',command='on'})
assert(not device.is_online)
status=200; response={id='wrong-device',online=true,state={}}
captured.capability_handlers.refresh.refresh(driver,device); assert(not device.is_online)
device.preferences.deviceId='wrong-device'
captured.capability_handlers.refresh.refresh(driver,device); assert(device.is_online)
captured.lifecycle_handlers.removed(driver,device); assert(timers==0)
captured.discovery(driver); assert(created==1)
devices={device}; captured.discovery(driver); assert(created==1)
print('Lua driver contract checks passed')
