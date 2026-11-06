# SmartThings 가상 디바이스 메모

가상 디바이스로 환기창 화면을 만들며 겪은 일을 정리했습니다. SmartThings 정책은 바뀔 수 있습니다.

## 표준 능력만 쓴다

- 사용자 정의(custom) 능력은 만들면 `proposed` 상태로 남습니다. 이 상태에서는 이벤트를 보내면 422가 나고,
  앱이 화면을 그리지 못합니다. 그래서 표준 능력만 쓰고 라벨을 device-config로 바꿨습니다.
- 매핑
  - 조명 → `rapidCooling` (on/off 토글, 라벨을 "조명"으로)
  - 열교환기 남은수명 % → `ultravioletIndex` (detailView를 state 표시 `{{value}} %`로 patch)
  - 모드 → `mode` (alternatives를 auto/turbo/sleep = 자동/터보/취침으로 patch)
  - 필터 남은수명 → `filterState.filterLifeRemaining`
- 먼지 센서 계열은 고정 카드라 라벨을 바꿀 수 없습니다. `battery`를 넣으면 헤더의 배터리 아이콘으로 빠집니다.
- `colorControl`을 넣거나, 존재하지 않는 문자열 vid를 쓰면 앱에서 "연결할 수 없음"으로 보입니다.

## device-config

- `detailView`와 `automation`(conditions/actions)을 **둘 다** patch해야 상세 화면과 자동화 화면의 라벨·모드
  선택지가 맞습니다.
- `dashboard.states`의 patch는 서버가 무시합니다.
- `smartthings/device-config.json`에는 `presentationId`, `manufacturerName`, `vid` 키를 넣지 않습니다.
  생성할 때 서버가 새로 발급합니다.

## vid 반영

- 새 vid는 `metadata.vid`를 넣어 **새 프로파일을 create**할 때만 반영됩니다. 기존 프로파일을 update하고 디바이스를
  만들어도 예전 presentation이 캐시되어 나옵니다. `setup.sh`가 매번 이름에 타임스탬프를 붙여 새 프로파일을
  만드는 이유입니다.
- 가상 디바이스는 프로파일 교체 API가 406을 돌려줍니다. 화면 구성을 바꾸려면 디바이스를 새로 만들어야 하고,
  그러면 디바이스 id가 바뀌므로 `.env`의 `ST_DEVICE_ID`와 자동화 설정도 다시 맞춰야 합니다.

## 생성 직후 초기값

- 생성하자마자 모든 속성이 무작위 값으로 채워져 기록에 한 건 남습니다. 지울 수 없습니다.
- `temperatureRange`도 무작위 °F 범위로 채워져 앱의 온도 단위가 이상해집니다. 브리지가 매 푸시마다
  `{"minimum":-20,"maximum":50,"step":1}` °C로 덮어씁니다(값이 같으면 다시 보내지 않음).
- `mode.supportedModes`도 브리지가 `["auto","turbo","sleep"]`으로 채웁니다.

## 명령 감지

- 가상 디바이스에 명령을 보내면 SmartThings가 그 값을 바로 상태로 반영합니다. 브리지는 상태를 1초마다
  폴링해 직전 값과 달라지면 사용자 조작으로 봅니다.
- 브리지가 텔레메트리로 직접 올린 값은 기억해 두었다가, 그 값으로 바뀐 것은 명령으로 보지 않습니다(에코 방지).
- 첫 폴링은 기준값만 저장하고 명령을 내지 않습니다.

## 토큰

- 새로 발급하는 개인 액세스 토큰(PAT)은 24시간 뒤 만료되어 상시 운용에 쓸 수 없습니다.
- CLI 로그인으로 얻은 OAuth access/refresh 토큰을 파일에 두고, 401이 나면 refresh 토큰으로 갱신합니다.
- refresh 토큰은 1회용이고 갱신할 때마다 새 것으로 바뀝니다. 같은 refresh 토큰을 CLI와 브리지가 함께 쓰면
  먼저 쓴 쪽만 살아남습니다. 브리지에 넣은 뒤에는 CLI를 따로 다시 로그인하세요.
- 토큰 파일 권한은 600으로 두고 저장소에 올리지 마세요(`.gitignore`에 포함).
