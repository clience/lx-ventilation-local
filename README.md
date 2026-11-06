# lx-ventilation-local

**English summary.** Run an LX Z:IN "자동환기" window ventilator (model `LGH_SMARTVENTIL2021M_G1N`) without the
manufacturer's cloud. The device talks Azure IoT DPS → IoT Hub over MQTT/TLS and does not validate the server
certificate, so a tiny self-hosted DPS + IoT Hub emulator can take its place. A bridge mirrors telemetry to a
SmartThings virtual device and forwards power / fan / lamp / mode changes back. Python standard library only,
two containers. Unofficial; use only on your own device.

---

LX Z:IN 자동환기 환기창(모델 `LGH_SMARTVENTIL2021M_G1N`)을 제조사 클라우드 없이 집 안 서버로 쓰기 위한
도구 모음입니다. SmartThings 앱에서 공기질을 보고 전원·풍량·조명·모드를 바꿀 수 있습니다.

## 동작 구조

```
 ┌──────────────┐  MQTT/TLS :8883   ┌──────────────────────┐   data/raw.log   ┌──────────┐  REST  ┌─────────────┐
 │  환기창 (ESP) │ ────────────────▶ │ emulator             │ ───────────────▶ │  bridge  │ ─────▶ │ SmartThings │
 │              │ ◀──────────────── │ (DPS + IoT Hub 흉내)  │ ◀─────────────── │          │ ◀───── │ 가상 디바이스 │
 └──────────────┘  desired twin     └──────────────────────┘   data/cmd.json  └──────────┘ 1s 폴링 └─────────────┘
        ▲
        │ 최초 1회: 기기 AP(lxhsv_xxxxxx)에 붙어 Wi-Fi·dpsEndpoint 주입 (provisioning/provision.py)
```

1. 기기는 원래 Azure IoT DPS에 등록한 뒤 IoT Hub에 MQTT(TLS 8883)로 붙습니다. 서버 인증서를 검증하지 않으므로
   자체서명 인증서로 띄운 에뮬레이터가 DPS와 IoT Hub 역할을 모두 합니다.
2. 프로비저닝 때 `dpsEndpoint`를 내 호스트 이름으로 넣으면, 기기는 그 뒤로 내 서버에만 연결합니다.
3. 에뮬레이터는 기기가 보내는 모든 메시지를 `data/raw.log`에 남기고, `data/cmd.json`이 생기면 desired twin
   PATCH로 기기에 전달합니다.
4. 브리지는 `raw.log`에서 최신 값을 읽어 SmartThings 이벤트로 올리고(10초), SmartThings 상태를 1초마다 폴링해
   사용자가 바꾼 값을 `cmd.json`에 씁니다.

## 디렉터리

| 경로 | 내용 |
|------|------|
| `emulator/dps_hub_emulator.py` | DPS + IoT Hub MQTT 에뮬레이터 |
| `emulator/gen-cert.sh` | 자체서명 인증서 생성 |
| `bridge/bridge.py` | 에뮬레이터 ↔ SmartThings 브리지 |
| `provisioning/provision.py` | 기기 AP 모드에서 Wi-Fi·DPS 설정 주입 |
| `smartthings/` | 디바이스 프로파일·device-config, 생성 스크립트 |
| `docs/protocol.md` | 기기 프로토콜 정리 |
| `docs/smartthings-notes.md` | SmartThings 가상 디바이스 시행착오 |

## 요구사항

- 기기가 해석할 수 있는 호스트 이름 1개 (공인 DNS 또는 공유기 로컬 DNS). 이 이름이 에뮬레이터를 돌리는 서버를
  가리켜야 하고, 기기에서 그 서버의 **8883/tcp**에 닿아야 합니다. 기기는 IP가 아닌 호스트 이름을 받습니다.
- Docker + docker compose (또는 Python 3.10+ 직접 실행)
- 프로비저닝용으로 Wi-Fi가 되는 PC/노트북 1대 (기기 AP에 붙어야 함)
- SmartThings 계정, [SmartThings CLI](https://github.com/SmartThingsCommunity/smartthings-cli) 로그인
- 기기용 2.4 GHz Wi-Fi

## 설치 순서

### 1. 설정과 인증서

```bash
cp .env.example .env          # EMU_HUB_HOST 를 내 호스트 이름으로
set -a; . ./.env; set +a
./emulator/gen-cert.sh        # data/cert.pem, data/key.pem 생성 (CN=EMU_HUB_HOST, 10년)
```

### 2. 에뮬레이터 기동

`ST_DEVICE_ID`는 5단계에서 채우므로, 지금은 에뮬레이터만 올립니다.

```bash
docker compose up -d emulator
docker compose logs -f emulator
```

### 3. 프로비저닝

1. 기기를 페어링 모드로 둡니다(제조사 설명서의 Wi-Fi 등록 절차). 기기가 `lxhsv_xxxxxx` AP를 엽니다.
2. PC를 그 AP에 연결합니다. 비밀번호는 SSID의 `_` 뒤 문자열을 두 번 이어 붙인 값입니다
   (예: `lxhsv_ab12cd` → `ab12cdab12cd`). 앱 동작에서 확인한 규칙이라 펌웨어에 따라 다를 수 있습니다.
3. 먼저 `--dry-run`으로 보낼 내용을 확인한 뒤 실제로 보냅니다.

```bash
export WIFI_PASSWORD='집 Wi-Fi 비밀번호'
python3 provisioning/provision.py --ssid '집Wi-Fi' --dps-endpoint "$EMU_HUB_HOST" --device-id lx-vent-1 --dry-run
python3 provisioning/provision.py --ssid '집Wi-Fi' --dps-endpoint "$EMU_HUB_HOST" --device-id lx-vent-1
```

기기가 집 Wi-Fi에 붙고 수십 초 안에 에뮬레이터 로그에 `[DPS] CONNECT` → `[HUB] CONNECT` → `TELEMETRY`가
보이면 성공입니다.

### 4. SmartThings 가상 디바이스 만들기

```bash
smartthings locations                       # locationId 확인
smartthings locations:rooms <locationId>    # roomId 확인 (선택)
./smartthings/setup.sh --location <locationId> --room <roomId> --name "환기"
```

마지막에 출력되는 디바이스 id를 `.env`의 `ST_DEVICE_ID`에 넣습니다.

### 5. 토큰 넣기와 브리지 기동

SmartThings의 신규 개인 액세스 토큰(PAT)은 24시간 뒤 만료되어 상시 운용에 맞지 않습니다. 브리지는 OAuth
access/refresh 토큰을 파일로 받고, 401이 나면 refresh 토큰으로 갱신합니다.

1. `smartthings login`으로 로그인한 뒤 CLI 자격 증명 파일(macOS/Linux: `~/.config/@smartthings/cli/credentials.json`)의
   `accessToken`을 `data/st_token.txt`, `refreshToken`을 `data/st_refresh.txt`에 각각 한 줄로 저장합니다.
2. `.env`의 `ST_CLIENT_ID`에 그 토큰을 발급한 OAuth client id를 넣습니다(CLI 로그인이면 CLI가 쓰는 client id).
3. 브리지를 올립니다.

```bash
chmod 600 data/st_*.txt
docker compose up -d
docker compose logs -f bridge
```

refresh 토큰은 한 번 쓰면 새 것으로 바뀝니다. 브리지가 쓰는 토큰을 CLI에서 동시에 쓰면 갱신 체인이 갈라져
둘 중 하나가 로그아웃됩니다. 브리지용 토큰을 넣은 뒤에는 CLI를 다시 로그인해 별도 세션으로 쓰세요.

## SmartThings 화면 매핑

| 기기 | SmartThings 능력 | 화면 라벨 |
|------|------------------|-----------|
| 전원 | `switch` | 전원 |
| 모드 2/3/4 | `mode` (auto/turbo/sleep) | 자동/터보/취침 |
| 풍량 1~3 | `fanSpeed` | 풍량 |
| 조명 | `rapidCooling` | 조명 |
| PM2.5 | `dustSensor.fineDustLevel` | 초미세먼지 |
| TVOC 지수 | `tvocMeasurement` | 유해가스 (TVOC) |
| CO2, 온도, 습도 | 각 표준 능력 | 기본 |
| 필터 남은수명 % | `filterState` | 필터 교체 · 남은수명 |
| 열교환기 남은수명 % | `ultravioletIndex` | 소자 교체(열교환기) · 남은수명 |

이 기종에서 보이지 않는 강제배기(1), 급기(6), 풍량 수동 모드(5)는 SmartThings 모드 선택지에 넣지 않았습니다.
기기가 그 모드에 있으면 SmartThings에는 직전 모드가 그대로 보입니다.

## 한계

- 연결 1대 기준으로 만들었습니다(가상 디바이스 1개). 여러 대는 컨테이너를 나눠 띄우는 것을 권합니다.
- 에뮬레이터는 이 기기에 필요한 만큼만 MQTT를 구현했습니다(QoS 0 전달, retained 없음, 인증 없음).
  **8883 포트를 인터넷에 열면 누구나 붙을 수 있습니다.** 가능하면 내부망에만 여세요.
- 명령 전달은 SmartThings 폴링(기본 1초)과 에뮬레이터의 0.5초 확인을 거치므로 1~2초 지연이 있습니다.
- 에뮬레이터를 재시작하면 기기가 다시 붙기까지 약 40초 걸립니다.
- 타이머·펌웨어 업데이트·제조사 앱의 이력/통계 기능은 없습니다.
- SmartThings 가상 디바이스는 생성 직후 무작위 초기값이 기록에 한 건 남고 지울 수 없습니다.

## 원래대로 되돌리기

제조사 앱에서 기기를 다시 등록하면 앱이 제조사 서버의 DPS 정보로 프로비저닝을 다시 하므로 원래 상태로
돌아갑니다. 이 저장소는 기기 펌웨어를 바꾸지 않습니다.

## 면책

- 이 프로젝트는 비공식이며 LX하우시스, 삼성전자(SmartThings), Microsoft(Azure)와 아무 관계가 없고 그들의
  승인이나 지원을 받지 않았습니다. 언급한 상표는 각 소유자의 것입니다.
- 본인이 소유한 기기에만 사용하세요. 사용으로 인한 기기 이상, 보증 문제, 데이터 손실 등은 사용자 책임입니다.
- 프로토콜 정보는 상호운용 목적으로 기기와 앱의 통신을 관찰해 정리한 것이며, 제조사 코드나 펌웨어를 포함하지
  않습니다.
- 소프트웨어는 MIT 라이선스에 따라 "있는 그대로" 제공되며 어떤 보증도 하지 않습니다.

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

## 라이선스

[MIT](LICENSE)
