# SmartThings Edge LAN 연동

기존 DPS/IoT Hub MQTT 에뮬레이터를 유지하면서, SmartThings 가상 기기/OAuth 중계를 **허브에서 실행되는 Edge 드라이버 + LAN 브리지**로 대체합니다. SmartThings 토큰은 설치·배포용 CLI에만 필요하고, LAN 브리지와 알림 프로세스는 ST OAuth를 사용하지 않습니다.

```text
SmartThings 앱 ↔ SmartThings 허브 [Edge Lua]
                         ↕ HTTP / LAN 키
                  홈랩 [LAN 브리지]
                         ↕ raw.log / cmd.json
                  기존 MQTT 에뮬레이터 ↔ LX 환기창
```

**드라이버만 설치해서 출고 상태의 환기창에 직접 연결하는 방식은 아닙니다.** 호환 SmartThings 허브, 같은 LAN의 상시 서버, 이 저장소의 에뮬레이터 및 환기창 프로비저닝이 필요합니다. 홈랩 제거는 지원하지 않습니다. Linux/NAS의 Docker host networking을 기준으로 합니다.

운영 기반 코드는 SmartThings Station과 실제 환기창에서 전원·모드·풍량·조명·재시작 복구를 검증했습니다. 이 공개용 버전은 집 전용 설정을 제거한 별도 패키지이며 로컬 회귀 테스트 및 패키지 빌드로 검증합니다. 타 모델·타 펌웨어 및 다른 집에서의 설치는 아직 검증하지 않았습니다.

## 지원 범위

- 전원, 자동/터보/취침/풍량(수동) 모드, 풍량 1~3단, 조명. 풍량 0 명령은 전원 off.
- 온도·습도·CO2·PM2.5·TVOC·필터 남은 수명. PM10은 지원하지 않으며 0을 만들어 넣지 않습니다.
- 열교환 소자 수명은 브리지/선택적 알림에서 사용하고 앱에는 숨깁니다.
- 모드와 풍량은 별도 명령으로 전달합니다. 전원 on은 명령함 소비 후 4초 대기, 수동 모드 보고를 확인한 뒤 풍량을 보냅니다.
- 명령 응답 202는 접수입니다. 앱 상태는 환기창의 reported 값을 확인한 뒤 갱신합니다.
- 기본은 읽기 전용입니다. 서버와 앱에서 각각 명령을 허용해야 제어됩니다.
- 허브/드라이버당 환기창 한 대, 수동 IPv4 설정을 지원합니다. mDNS/SSDP 자동 탐색과 다중 기기 등록은 미구현입니다.

## 1. 서버 준비

신규 설치는 [저장소 README의 에뮬레이터·프로비저닝 단계](../README.md#설치)를 먼저 완료하세요. 기존 설치에서는 에뮬레이터와 `raw.log`를 그대로 사용하며 재프로비저닝하지 않습니다.

저장소 루트에서:

```sh
cp .env.edge.example .env.edge
mkdir -p .edge-secrets edge-state
chmod 700 .edge-secrets edge-state
python3 - <<'PY'
import os, secrets
p = '.edge-secrets/lan-key.txt'
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(secrets.token_hex(32) + '\n')
PY
```

`.env.edge`에서 `EMU_HUB_HOST`, 파일 소유자의 UID/GID(`id -u`, `id -g`)를 설정합니다. 기존 설치의 데이터 경로가 다르면 compose의 `./data`를 실제 경로로 바꾸세요. 전용 LAN 키는 ST 토큰과 무관합니다.

이미 실행 중인 에뮬레이터를 재시작하지 않고 읽기 전용 브리지만 추가:

```sh
docker compose --env-file .env.edge -f docker-compose.edge.yml up -d --no-deps lan-bridge
```

신규 설치라면 같은 compose 파일로 먼저 `emulator`를 기동하세요. 기존 OAuth `bridge`와 읽기 전용 LAN 브리지는 함께 관찰할 수 있습니다. **`cmd.json`을 쓰는 프로세스는 반드시 하나만 활성화**해야 합니다.

HTTP 포트는 신뢰하는 LAN에만 노출하세요. 인터넷 포트 포워딩은 하지 않습니다. 데이터 폴더에서 기존 ST 토큰을 새 브리지에 복사하거나, 새 CLI에서 기존 refresh 토큰을 재사용하지 않습니다.

## 2. Edge 드라이버 배포

SmartThings CLI는 별도로 로그인합니다. 기존 OAuth 브리지가 소유한 토큰을 가져오지 않습니다. 자신의 채널에 배포하거나, 관리자가 공유한 채널 초대 링크로 설치할 수 있습니다. **이 저장소에는 공개 채널 초대 링크가 아직 없습니다.** GitHub 소스 공개와 SmartThings 채널 공유는 별개입니다.

```sh
smartthings edge:channels:create
smartthings edge:channels:enroll <HUB_ID> --channel <CHANNEL_ID>
smartthings edge:drivers:package edge/driver \
  --channel <CHANNEL_ID> --hub <HUB_ID>
```

CLI 3.x에서는 `--hub`가 설치를 포함하므로 `--install`을 함께 쓰지 않습니다. 오프라인 빌드는:

```sh
mkdir -p edge/dist
smartthings edge:drivers:package edge/driver --build-only edge/dist/lxvent-lan.zip
```

현재 프로파일은 이 프로젝트의 공개 화면 메타데이터(`vid`/`mnmn`)를 참조합니다. 계정·허브·실제 기기 ID나 키는 포함하지 않습니다. 자신의 화면을 만들려면:

```sh
smartthings presentation:device-config:create -i edge/presentation/device-config.json
```

반환된 `vid`를 `edge/driver/profiles/lxvent-lan.yaml`에 설정하고 `mnmn: SmartThingsCommunity`를 유지하세요. 별도 제품으로 포크한다면 `edge/driver/config.yaml`의 `packageKey`도 자신의 값으로 바꿉니다.

## 3. 앱 연결 및 관찰

SmartThings 앱의 기기 추가 → 주변 검색으로 **LX 환기창**을 추가합니다. 처음에는 키가 없어서 오프라인입니다. 기기 설정에 다음을 입력합니다.

| 설정 | 값 |
|---|---|
| LAN 브리지 IPv4 | 실제 서버의 LAN IP |
| 포트 | `.env.edge`의 `LXVENT_PORT` (기본 18765) |
| 전용 LAN 키 | `.edge-secrets/lan-key.txt` 내용 |
| LAN 브리지 기기 ID | `LXVENT_DEVICE_ID`와 동일 (기본 `lxvent-main`) |
| 명령 허용 | 처음에는 끔 |

약 10초 뒤 센서값을 확인합니다. 이 공개 버전은 다른 기기에서 설정을 복사하지 않으므로 반드시 입력해야 합니다. 이 기기 ID는 LAN 응답 식별자이며 MQTT 프로비저닝의 device ID를 변경하지 않습니다.

프로파일 구조를 바꾼 뒤 기존 기기의 상세 화면이 열리지 않는 문제가 실제로 재현됐습니다. **실행 중인 기기의 프로파일을 자동 교체하지 않습니다.** 화면 구조를 바꿀 때는 별도 테스트 기기로 검증하고, 새 기기를 등록한 뒤 루틴을 이관하세요. 기존 기기를 삭제하면 루틴 연결도 영향을 받습니다.

## 4. 제어권 전환

1. 기존 컨테이너 설정과 알림 발송 상태를 백업합니다. ST 인증 파일은 현재 위치에 보존합니다.
2. 기존 OAuth 브리지만 중지합니다. 에뮬레이터는 계속 실행합니다.
3. 남아 있는 `cmd.json`이 소비됐는지 확인합니다. 남은 명령을 임의로 삭제/재생하지 않습니다.
4. `.env.edge`의 `LXVENT_ENABLE_COMMANDS=true`로 바꾸고 `lan-bridge`만 재생성합니다.
5. 앱에서 새 기기의 명령 허용을 켭니다.
6. 전원, 모드, 풍량, 조명, on 직후 모드 요청, off의 대기 명령 취소를 검증합니다.
7. 기존 가상 기기를 쓰던 앱 루틴/장면을 새 Edge 기기로 옮깁니다. ID는 자동 승계되지 않습니다.

롤백은 새 `lan-bridge`의 명령을 차단하거나 중지한 뒤 기존 OAuth 브리지를 시작합니다. 두 writer를 동시에 실행하지 마세요. 회전한 ST 토큰을 과거 백업으로 되돌리지 않습니다.

## 선택 사항: 기존 알림 유지

`alerts`는 CO2·필터·열교환 소자 규칙만 허용합니다. 온습도 및 다른 ST 기기 조회는 지원하지 않습니다. Telegram을 사용하지 않으면 이 서비스를 켜지 않아도 됩니다.

- `edge/alert-rules.example.json`을 `edge/alert-rules.json`으로 복사하거나, 기존 운영 규칙을 그대로 가져옵니다.
- `.edge-secrets/telegram-token.txt`에 자신의 기존 봇 키를 저장하고 권한을 600으로 설정합니다.
- `.env.edge`의 `TG_CHAT_ID`를 설정합니다. 송신은 `LXVENT_ALERT_SEND=true`일 때만 켜집니다.
- 기존 알림 담당 프로세스를 중지한 뒤, 기존 `alerts.json`을 `edge-state/alerts.json`으로 옮겨 이미 발송한 알림 상태를 이어갑니다. UID/GID가 위 compose 실행 사용자와 일치해야 합니다.

```sh
docker compose --env-file .env.edge -f docker-compose.edge.yml --profile alerts up -d alerts
```

이 예시 규칙은 CO2 1000ppm에서 알림/800ppm에서 재무장, 소모품 2%에서 알림/12%에서 재무장입니다. 파일을 자신의 설정에 맞게 확인하세요. 송신 실패는 발송 완료로 기록하지 않습니다. `edge-state/health.json`에서 마지막 확인 시각과 원본 데이터 상태를 볼 수 있습니다.

롤백 시 새 alerts를 먼저 중지하고 최신 `edge-state/alerts.json`을 기존 알림 담당 프로세스에 인계한 뒤 재시작합니다. 두 알림 담당자를 동시에 켜면 중복 알림이 발생할 수 있습니다.

## 검증과 한계

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s edge/tests -v
lua edge/tests/test_driver.lua
luac -p edge/driver/src/init.lua
```

- Python 테스트는 인증·읽기 전용·명령 순서·취소·큐 만료·센서 파싱·알림 재무장을 검증합니다. Lua 테스트는 모의 프레임워크로 실행하므로 실제 허브 검증을 대체하지 않습니다.
- 한 번에 `raw.log`의 마지막 1 MiB를 읽습니다. 오래된 드문 속성은 다음 보고까지 없을 수 있습니다. 파일 수정 시각은 개별 센서의 측정 시각이 아닙니다.
- 600초 동안 로그가 갱신되지 않으면 오프라인 처리합니다. 실제 펌웨어 보고 주기를 확인하세요.
- 명령함 삭제는 물리 ACK가 아닙니다. 큐는 재시작하면 폐기되며, 이미 전달된 명령은 취소를 보장하지 않습니다.
- 파일 마운트인 alerts의 `raw.log`를 rename 방식으로 회전했다면 alerts 컨테이너도 재시작해야 합니다.
- 공유 채널 초대에는 해당 채널의 드라이버 설치 권한이 포함됩니다. 가정용 개발 채널과 공개 배포 채널을 분리하는 편이 관리하기 쉽습니다.

공식 문서: [LAN 드라이버](https://developer.smartthings.com/docs/devices/hub-connected/lan), [채널 및 초대](https://developer.smartthings.com/docs/devices/hub-connected/driver-channels).
