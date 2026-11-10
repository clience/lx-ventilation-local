# lx-ventilation-local

![LX 지인(Z:IN) 자동환기 환기창 스마트싱스 로컬 연동](docs/images/banner.png)

**LX하우시스 지인(Z:IN) 스마트 환기창 · 스마트싱스(SmartThings) 연동 · 제조사 앱/클라우드 없이 집 안에서 로컬 제어**

**English summary.** Control an LX Z:IN window ventilator through a self-hosted MQTT emulator,
a LAN bridge and a SmartThings Edge driver running on your hub. Runtime control does not require
SmartThings OAuth tokens. A compatible hub and an always-on LAN server are required.

LX Z:IN 자동환기 환기창(모델 `LGH_SMARTVENTIL2021M_G1N`)을 제조사 클라우드 없이 연결합니다.
**신규 설치는 Edge 방식을 권장합니다.** 기존 OAuth 가상 기기 중계 코드는 이전 설치 참고용으로 보존합니다.

- 전원·자동/터보/취침/풍량 모드·풍량·조명 제어
- 온습도·CO2·PM2.5·TVOC·필터 수명 표시
- 선택적 CO2·필터·열교환 소자 Telegram 알림
- [Edge 설치·배포·전환·롤백 안내](edge/README.md)

## 동작 구조

```text
환기창 ↔ MQTT/TLS 에뮬레이터 ↔ raw.log / cmd.json ↔ LAN 브리지
                                                    ↕ HTTP + 전용 LAN 키
                                            SmartThings 허브 [Edge]
                                                    ↕
                                              SmartThings 앱
```

기기는 프로비저닝한 DPS 호스트에 연결하고, 에뮬레이터가 DPS와 IoT Hub 역할을 합니다.
Edge 드라이버는 허브에서 LAN 브리지에 상태를 조회하고 명령을 전달합니다.
전원을 켠 직후 모드 변경은 최소 4초를 기다리며, 앱은 실제 보고값을 표시합니다.

| 경로 | 내용 |
|---|---|
| `edge/` | LAN 브리지, Edge 드라이버, 선택적 알림, 테스트, 설치 안내 |
| `docker-compose.edge.yml` | Edge 서버 실행 구성 |
| `emulator/` | DPS + IoT Hub MQTT 에뮬레이터 및 인증서 생성 |
| `provisioning/` | 기기 Wi-Fi·DPS 설정 |
| `bridge/`, `smartthings/`, `docker-compose.yml` | 이전 OAuth 가상 기기 방식 (보존용) |
| `docs/protocol.md` | 기기 프로토콜 |
| `docs/smartthings-notes.md` | 이전 가상 기기 구현 기록 |

## 준비물

- 호환 SmartThings 허브와 계정, 배포용 SmartThings CLI
- Linux/NAS 상시 서버와 Docker Compose, 서버와 허브 간 LAN 연결
- 환기창용 2.4 GHz Wi-Fi, 초기 설정용 Wi-Fi PC
- 서버 내부 IP로 해석되는 호스트 이름

[**Edge 드라이버 설치 (LX Vent LAN)**](https://bestow-regional.api.smartthings.com/invite/WnlLoe6d6K2K)

링크에서 삼성 계정 로그인 → 초대 수락 → 허브 Enroll → LX Vent LAN Install 순서로 설치합니다.
서버 준비와 앱 설정은 [Edge 안내](edge/README.md)를 따르세요. 공개 소스로 자신의 채널에 직접 배포할 수도 있습니다.
드라이버만 설치해서 출고 상태의 환기창에 바로 연결하는 방식은 아닙니다.

## 설치

기존 에뮬레이터가 동작 중이면 재프로비저닝 없이 [Edge 안내](edge/README.md)로 이동하세요.
신규 설치는 아래 0~3단계로 에뮬레이터 연결을 확인한 뒤 Edge 안내를 따릅니다.

### 0. 호스트 이름 준비

기기가 `vent.example.com` 같은 이름을 서버 IP(예: `192.168.0.10`)로 풀 수 있게 만드는 단계입니다. 둘 중 하나를 고르세요.

- **공유기 로컬 DNS**: 공유기 설정에서 "DNS 레코드", "로컬 호스트 이름" 같은 메뉴에 이름과 서버 IP를 등록합니다.
  외부에 아무것도 공개하지 않아 가장 안전합니다. 공유기가 이 기능을 지원해야 합니다.
- **공인 DNS에 내부 IP 등록**: 가진 도메인의 DNS에 A 레코드로 `vent.example.com → 192.168.0.10`(내부 IP)을
  등록합니다. Cloudflare라면 프록시를 끄고 "DNS only"로 둡니다. 내부 IP라 외부에서 접속할 수는 없습니다.

확인:

```bash
nslookup vent.example.com    # 서버의 내부 IP가 나와야 합니다
```

### 1. 저장소 받기, 설정, 인증서

```bash
git clone https://github.com/clience/lx-ventilation-local.git
cd lx-ventilation-local
cp .env.edge.example .env.edge
```

`.env.edge`를 열어 `EMU_HUB_HOST`를 0단계의 호스트 이름으로 바꿉니다. UID/GID 등은 Edge 안내에서 설정합니다.

```bash
set -a; . ./.env.edge; set +a
./emulator/gen-cert.sh        # data/cert.pem, data/key.pem 생성 (CN=EMU_HUB_HOST, 10년)
```

확인: `ls data/` 에 `cert.pem`, `key.pem`이 있습니다.

### 2. 에뮬레이터 기동

지금은 에뮬레이터만 올립니다.

```bash
docker compose --env-file .env.edge -f docker-compose.edge.yml up -d emulator
docker compose --env-file .env.edge -f docker-compose.edge.yml logs -f emulator
```

확인: 로그에 리슨 메시지가 나오고, 다른 PC에서 서버의 8883 포트가 열려 있습니다.

```bash
nc -vz vent.example.com 8883   # succeeded / open 이면 OK
```

### 3. 기기 프로비저닝

기기에 집 Wi-Fi 정보와 "내 서버로 붙어라"는 정보를 넣는 단계입니다. 기기를 바꾸거나 Wi-Fi를 바꿀 때만 다시 합니다.

1. 기기를 Wi-Fi 등록(페어링) 모드로 둡니다. 방법은 제조사 설명서의 Wi-Fi 등록 절차를 따르세요.
   기기가 `lxhsv_xxxxxx` 이름의 Wi-Fi를 엽니다.
2. PC를 그 Wi-Fi에 연결합니다. 비밀번호는 SSID의 `_` 뒤 문자열을 두 번 이어 붙인 값입니다
   (예: `lxhsv_ab12cd` → `ab12cdab12cd`). 앱 동작에서 확인한 규칙이라 펌웨어에 따라 다를 수 있습니다.
3. 먼저 `--dry-run`으로 보낼 내용을 확인한 뒤 실제로 보냅니다. 비밀번호는 출력에서 가려집니다.

```bash
export WIFI_PASSWORD='집 Wi-Fi 비밀번호'
python3 provisioning/provision.py --ssid '집Wi-Fi이름' --dps-endpoint "$EMU_HUB_HOST" --device-id lx-vent-1 --dry-run
python3 provisioning/provision.py --ssid '집Wi-Fi이름' --dps-endpoint "$EMU_HUB_HOST" --device-id lx-vent-1
```

4. PC를 원래 Wi-Fi로 되돌립니다.

확인: 수십 초 안에 에뮬레이터 로그에 아래 순서가 보이면 성공입니다.

```
[DPS] CONNECT ...
[HUB] CONNECT ...
[HUB] *** TELEMETRY *** {"sensor":{..."airCondition":{"co2":...}}}
```

> ⚠️ `provision.py`는 기기·앱 통신을 관찰해 재구성한 것으로, 공개 저장소 버전 자체는 아직 실기기에서 다시
> 검증하지 않았습니다. 실패하면 `--dry-run` 출력과 함께 이슈로 알려주세요.

### 4. Edge 연결 및 검증

[Edge 설치 안내](edge/README.md)에 따라 읽기 전용 LAN 브리지를 추가하고, 자신의 채널에 드라이버를
배포해 테스트 기기를 만듭니다. 센서값을 확인한 뒤 기존 writer를 중지하고 제어권을 전환합니다.
기존 가상 기기의 루틴은 새 Edge 기기로 직접 옮겨야 합니다.

## 운영과 한계

- 실행·업데이트·명령 검증·알림·롤백은 [Edge 안내](edge/README.md)를 따르세요.
- 허브/드라이버당 환기창 한 대를 지원합니다. 홈랩 제거와 다중 기기는 지원하지 않습니다.
- 에뮬레이터는 필요한 MQTT 기능만 구현하고 인증을 제공하지 않습니다. 8883과 LAN HTTP 포트는
  신뢰하는 내부망에만 두고 인터넷에 노출하지 마세요.
- `cmd.json` writer는 한 개만 실행합니다. 수동 파일 쓰기도 활성 브리지와 충돌할 수 있습니다.
- PM10은 제공하지 않습니다. TVOC의 물리 단위는 확인되지 않았으므로 앱의 ppb 표기를 교정된 측정값으로 해석하지 마세요.
- 운영 기반 코드는 실기기에서 확인했으나, 일반화한 공개 패키지는 다른 가정/모델에서 검증 전입니다.
- 제조사 앱으로 다시 등록하면 제조사 서버로 돌아갈 수 있습니다. 펌웨어는 변경하지 않습니다.
  롤백할 때는 새 writer와 알림을 먼저 중지하고, 기존 기기와 최신 인증 파일을 보존하세요.

## 면책

- 이 프로젝트는 비공식이며 LX하우시스, 삼성전자(SmartThings), Microsoft(Azure)와 아무 관계가 없고 그들의
  승인이나 지원을 받지 않았습니다. 언급한 상표는 각 소유자의 것입니다.
- 본인이 소유한 기기에만 사용하세요. 사용으로 인한 기기 이상, 보증 문제, 데이터 손실 등은 사용자 책임입니다.
- 프로토콜 정보는 상호운용 목적으로 기기와 앱의 통신을 관찰해 정리한 것이며, 제조사 코드나 펌웨어를 포함하지
  않습니다.
- 소프트웨어는 MIT 라이선스에 따라 "있는 그대로" 제공되며 어떤 보증도 하지 않습니다.

## 개발

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s edge/tests -v
lua edge/tests/test_driver.lua
```

이슈와 PR 환영합니다. 다른 LX Z:IN 기종에서 동작했다면 모델명과 함께 알려주세요.

## 라이선스

[MIT](LICENSE)
