local Driver = require 'st.driver'
local caps = require 'st.capabilities'
local json = require 'st.json'
local cosock = require 'cosock'
local socket = require 'cosock.socket'
local http = cosock.asyncify 'socket.http'
local ltn12 = require 'ltn12'
http.TIMEOUT = 5
-- One manually configured ventilator per installed driver/hub.
local DNI = 'lxvent-lan-main'

local function request(device, method, path, payload)
  local p = device.preferences
  local host, key = p.bridgeHost or '', p.lanKey or ''
  if not host:match('^%d+%.%d+%.%d+%.%d+$') or #key < 32 or key == string.rep('0',32) then return nil end
  local body, out = payload and json.encode(payload) or '', {}
  local ok, _, code = pcall(http.request, {
    url='http://' .. host .. ':' .. tostring(p.bridgePort or 18765) .. path,
    method=method,
    headers={Authorization='Bearer ' .. key, ['Content-Type']='application/json', ['Content-Length']=tostring(#body)},
    source=ltn12.source.string(body), sink=ltn12.sink.table(out),
    create=function() local s=socket.tcp(); s:settimeout(5); return s end
  })
  if not ok then return nil end
  code=tonumber(code)
  if code ~= 200 and code ~= 202 then return nil, code end
  local decoded, result=pcall(json.decode, table.concat(out))
  if decoded then return result, code end
  return nil, code
end

local function refresh(_, device)
  local result=request(device,'GET','/v1/state')
  if not result or result.id ~= (device.preferences.deviceId or 'lxvent-main') or result.online ~= true then
    device:offline(); return
  end
  device:online()
  local state=result.state or {}
  local function emit(cap, attr, key, unit)
    if state[key] == nil then return end
    local value=unit and {value=state[key],unit=unit} or state[key]
    device:emit_event(caps[cap][attr](value))
  end
  emit('switch','switch','power')
  emit('fanSpeed','fanSpeed','fan')
  emit('mode','mode','mode')
  emit('rapidCooling','rapidCooling','lamp')
  emit('temperatureMeasurement','temperature','temperature','C')
  emit('relativeHumidityMeasurement','humidity','humidity','%')
  emit('carbonDioxideMeasurement','carbonDioxide','co2','ppm')
  emit('fineDustSensor','fineDustLevel','pm25')
  emit('tvocMeasurement','tvocLevel','tvoc','ppb')
  emit('filterState','filterLifeRemaining','filterPct','%')
end

local function command(driver, device, cmd)
  if device.preferences.allowCommands ~= true then return end
  local name, value
  if cmd.capability == 'switch' then name='power'; value=cmd.command
  elseif cmd.capability == 'rapidCooling' then name='lamp'; value=cmd.args.rapidCooling
  elseif cmd.capability == 'mode' then name='mode'; value=cmd.args.mode
  elseif cmd.capability == 'fanSpeed' then
    name='fan'; value=cmd.args.speed
    if value == 0 then name='power'; value='off' end
  end
  if not name then return end
  local result, status=request(device,'POST','/v1/command',{command=name,value=value})
  if not result then
    -- A read-only/validation rejection is not a network outage.
    if not status or status >= 500 then device:offline() end
    return
  end
  -- Never report desired state as confirmed physical state.
  device.thread:call_with_delay(6,function() refresh(driver,device) end)
end

local function init(driver, device)
  -- Do not migrate the profile of an existing device automatically.
  local timer=device:get_field('poll_timer')
  if timer then device.thread:cancel_timer(timer) end
  device:emit_event(caps.mode.supportedModes({'auto','turbo','sleep','manual'}))
  device:emit_event(caps.temperatureMeasurement.temperatureRange({value={minimum=-20,maximum=50,step=1},unit='C'}))
  device:set_field('poll_timer',device.thread:call_on_schedule(10,function() refresh(driver,device) end))
  refresh(driver,device)
end

Driver('lxvent-lan',{
  discovery=function(driver)
    for _,device in pairs(driver:get_devices()) do if device.device_network_id == DNI then return end end
    driver:try_create_device({type='LAN',device_network_id=DNI,label='LX 환기창',profile='lxvent-lan',
      manufacturer='LX',model='Ventilation LAN',vendor_provided_label='LX Vent LAN'})
  end,
  lifecycle_handlers={init=init,infoChanged=init,removed=function(_,device)
    local timer=device:get_field('poll_timer'); if timer then device.thread:cancel_timer(timer) end
  end},
  supported_capabilities={caps.switch,caps.mode,caps.fanSpeed,caps.rapidCooling,caps.fineDustSensor,
    caps.temperatureMeasurement,caps.relativeHumidityMeasurement,caps.carbonDioxideMeasurement,caps.tvocMeasurement,caps.filterState,caps.refresh},
  capability_handlers={
    [caps.switch.ID]={[caps.switch.commands.on.NAME]=command,[caps.switch.commands.off.NAME]=command},
    [caps.mode.ID]={[caps.mode.commands.setMode.NAME]=command},
    [caps.fanSpeed.ID]={[caps.fanSpeed.commands.setFanSpeed.NAME]=command},
    [caps.rapidCooling.ID]={[caps.rapidCooling.commands.setRapidCooling.NAME]=command},
    [caps.refresh.ID]={[caps.refresh.commands.refresh.NAME]=refresh}
  }
}):run()
