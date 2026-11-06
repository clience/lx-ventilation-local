# 기기 프로토콜 정리

대상: LX Z:IN 자동환기 `LGH_SMARTVENTIL2021M_G1N` (ESP 계열 Wi-Fi 모듈).
아래는 직접 관찰한 동작을 정리한 것입니다. 펌웨어 버전에 따라 다를 수 있습니다.

## 1. 프로비저닝 (기기 AP 모드)

- 페어링 모드에서 기기는 `lxhsv_xxxxxx` 형태의 SSID로 AP를 엽니다.
- AP 비밀번호: SSID에서 `_` 뒤 부분을 두 번 이어 붙인 문자열 (`lxhsv_ab12cd` → `ab12cdab12cd`).
- AP에 붙으면 기기는 `192.168.1.2`, TCP `8080`에서 기다립니다.

### 프레임

요청과 응답 모두 같은 형식입니다.

```
+----------------+---------------------------+------------------+
| "LGHAUSYS" (8) | JSON 길이 uint32 BE (4)    | JSON (ASCII)     |
+----------------+---------------------------+------------------+
```

- 매직은 ASCII 8바이트 `LGHAUSYS`.
- 길이는 빅엔디언 4바이트. 응답은 길이 뒤에 여분 바이트가 붙을 수 있으니 길이만큼만 잘라서 파싱합니다.
- 문자열은 바이트당 한 글자로 보내므로 SSID·비밀번호는 ASCII로 한정하는 것이 안전합니다.
- 모든 메시지에 `type`(`REQ`/`RES`)과 `code`가 들어갑니다.

### 순서

| 단계 | 요청 | 내용 |
|------|------|------|
| 1 | `{"type":"REQ","code":"LG100"}` | 기기 정보(모델) 조회 |
| 2 | `{"type":"REQ","code":"LG200","deviceId":["<id>"],"apSsid":"…","apPassword":"…","dpsKey":"…","dpsEndpoint":"…","idScope":"…"}` | Wi-Fi와 DPS 정보 전달 |
| 3 | `{"type":"REQ","code":"LG300"}` | 적용. 기기는 AP를 닫고 집 Wi-Fi로 붙음 |

- 각 응답의 `result`가 문자열 `"200"`이면 다음 단계로 갑니다. `"400"`은 실패입니다.
- `deviceId`는 **배열**입니다(원소 1개).
- `dpsEndpoint`는 DPS 호스트 이름입니다. 여기에 에뮬레이터 호스트 이름을 넣습니다.
- `dpsKey`(대칭 키, base64)와 `idScope`는 원래 Azure DPS 인증용입니다. 에뮬레이터는 검사하지 않으므로 임의 값이면 됩니다.
- 그 밖의 코드: `LG600`은 등록된 기기의 Wi-Fi 정보만 바꾸는 요청(`apSsid`, `apPassword`)으로 보이며, 이 저장소는 사용하지 않습니다.

## 2. 연결 (DPS → IoT Hub)

1. 기기는 `dpsEndpoint:8883`에 TLS로 접속합니다. **서버 인증서를 검증하지 않습니다.**
2. MQTT CONNECT의 사용자 이름에 `…/registrations/<deviceId>/…`가 들어 있으면 DPS 세션입니다.
3. 기기가 `$dps/registrations/PUT/iotdps-register/?$rid=N`을 보내면
   `$dps/registrations/res/200/?$rid=N`으로 `status=assigned`, `assignedHub=<호스트>`를 돌려줍니다.
   (`iotdps-get-operationstatus` 폴링에도 같은 내용으로 답합니다.)
4. 기기는 `assignedHub:8883`에 다시 접속하며 사용자 이름은 `<호스트>/<deviceId>/…` 형태입니다.
5. `$iothub/twin/res/#`, 메서드, desired 토픽을 구독하고 `$iothub/twin/GET/?$rid=N`으로 twin을 요청합니다.
6. 이후 `devices/<deviceId>/messages/events/`로 텔레메트리를 보냅니다.

### 연결 유지

- 소켓 idle 타임아웃은 CONNECT에 적힌 keepalive의 2배 이상, 최소 600초로 둡니다. 너무 짧으면 서버가 살아 있는
  연결을 끊습니다.
- 에뮬레이터는 idle 상태에서 0.5초마다 `cmd.json`을 확인해 명령을 바로 보냅니다.
- 에뮬레이터를 재시작하면 기기가 다시 붙기까지 약 40초 걸립니다.

## 3. 텔레메트리

메시지 종류별로 따로 옵니다. 값은 예시입니다.

```json
{"sensor":{"airCondition":{"co2":650,"pm2_5":8,"tvocs":120,"temperature":22,"humidity":45}}}
{"sensor":{"filter":{"filterLifeTime":2000,"filterUsageTime":300,"cleanLifeTime":500,"cleanUsageTime":100}}}
{"sensor":{"heatExchanger":{"lifeTime":20000,"usageTime":1500}}}
{"sensor":{"power":{"status":"on"}}}
{"sensorStatus":{"lamp":{"style":"all"},"fan":{"fanSpeed":"2"},"mode":"5","timer":{"status":"off","hour":0,"remainHour":0}}}
```

- `co2`는 ppm, `pm2_5`는 µg/m³, `tvocs`는 **지수(index)**이며 ppb가 아닙니다.
- 기기가 꺼져 있어도 공기질 값은 계속 올라옵니다.
- 명령을 받으면 바뀐 부분을 `{"operation":"updated","sensor":{...}}` 형태로 되돌려 보냅니다.
  모드는 이때 `{"mode":{"currentMode":3}}`처럼 객체로 올 수 있습니다.

### 소모품 남은수명

```
남은수명(%) = floor(100 - usage / life * 100)
```

사용률이 100% 이상(남은수명 0%)이면 교체가 필요합니다. 필터(`filter*`), 청소(`clean*`), 열교환기 소자
(`heatExchanger`)에 같은 식을 씁니다.

### 공기질 등급

| 항목 | 좋음 | 보통 | 나쁨 | 매우 나쁨 |
|------|------|------|------|-----------|
| CO2 (ppm) | ≤ 700 | ≤ 1000 | ≤ 1500 | > 1500 |
| PM2.5 | ≤ 15 | ≤ 35 | ≤ 75 | > 75 |
| TVOC 지수 | < 200 | ≤ 300 | ≤ 400 | > 400 |

## 4. 모드 코드

| 코드 | 의미 | 비고 |
|------|------|------|
| 0 | 꺼짐 | |
| 1 | 강제배기 | 이 기종 화면에 없음 |
| 2 | 자동 | |
| 3 | 터보 | |
| 4 | 취침 | |
| 5 | 풍량(수동) | 풍량 1~3단과 함께 씀 |
| 6 | 급기 | |

## 5. 명령 (desired twin)

에뮬레이터는 `$iothub/twin/PATCH/properties/desired/?$version=2` 토픽으로 아래 JSON을 보냅니다.
`cmd.json`에는 `{"desired": <이 JSON>}`을 씁니다.

```json
{"sensor":{"power":{"status":"on"}}}            // on | off
{"sensor":{"fan":{"fanSpeed":"2"}}}             // 문자열 "1"~"3"
{"sensor":{"lamp":{"style":"all"}}}             // none | all | display
{"sensor":{"mode":"2"}}                         // 모드 코드, 문자열
{"sensor":{"timer":{"status":"on","hour":3}}}   // hour 1~8
```

여러 항목을 한 번에 보내도 됩니다(`{"sensor":{"power":{…},"mode":"3"}}`). 브리지는 아직 전달되지 않은
`cmd.json`이 있으면 거기에 합쳐서 씁니다.
