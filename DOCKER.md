# Docker 웹 서비스

[원본 저장소](https://github.com/CjangCjengh/NovelpiaDownloader)의 C# 프로그램을 수정하지 않고, 로그인·설정·다운로드·대기열을 웹 UI로 조작할 수 있게 만든 서비스입니다. PC와 모바일 브라우저에서 폼과 버튼을 사용하며 실행 로그, 진행 상황, 저장 파일을 확인합니다.

기준 원본 커밋: `badfd7d454a9a6a7682c691f54d66e4e62c941b3`.

## 원본 유지 방식

원본 C# 파일, 프로젝트, 리소스, 설정 파일과 README는 수정하지 않았습니다. `.gitignore`에 서비스 실행 파일 제외 규칙만 추가했습니다. 빌드 시 `docker/upstream.sha256`으로 원본 파일의 SHA-256을 검증하며 Microsoft Roslyn 4.12.0으로 원본 프로젝트를 컴파일합니다.

`docker/WebHost.cs`가 원본 어셈블리를 불러와 `MainWin`의 컨트롤에 웹 입력을 전달하고 **기존 이벤트 핸들러를 그대로 호출**합니다. 로그인 요청, 회차/BONUS 필터, 스레드·간격·재시도, 큐, 텍스트 처리와 EPUB 생성은 원본 코드가 수행합니다. 로그는 원본 `ConsoleBox`의 텍스트이며, 진행 건수와 큐도 원본 상태에서 읽습니다. 원본 로그의 마지막 진행 줄이 갱신되는 동작까지 반영합니다.

원본 다운로드 처리에 Windows Forms가 결합되어 있어 서버 내부에는 Mono와 Xvfb 및 UI 메시지 루프가 필요합니다. 원본 창은 숨겨서 실행합니다. 브라우저 화면은 HTML 폼이며 VNC 서버, 원격 화면 또는 캔버스를 사용하지 않습니다. 웹에서 저장 경로는 `/data/downloads`로 고정합니다. 변경한 설정은 원본의 설정 저장 핸들러로 저장합니다.

이미지 CDN이 TLS 1.3 연결을 요구하면 Mono의 HTTPS 구현은 연결에 실패합니다. `docker/ImageTransport.cs`가 `images.novelpia.com` 요청만 컨테이너 내부 Nginx(`127.0.0.1:8091`)로 전달하며, Nginx는 원래 CDN에 HTTPS로 접속하고 인증서와 호스트 이름을 검증합니다. 표지·삽화 다운로드, 재시도, 이미지 형식 판별과 EPUB 포함 처리는 계속 원본 메서드가 수행합니다. 이미지 전송 포트는 외부로 노출하지 않습니다.

## 실행

Docker Compose와 Python 3가 필요합니다.

```sh
git clone https://github.com/sidetool/NovelpiaDownloader.git
cd NovelpiaDownloader
sh docker/setup.sh
docker compose up -d --build
```

`http://서버주소:8797`에 접속합니다. 웹 접속 아이디와 비밀번호는 `.env`의 `WEB_USERNAME`, `WEB_PASSWORD`입니다. `setup.sh`는 긴 무작위 비밀번호를 만들며 기존 `.env`는 유지합니다. 웹 접속 인증과 노벨피아 로그인은 별개입니다.

## 사용

1. **계정 연결**에서 이메일/비밀번호 또는 SNS 계정의 LOGINKEY를 입력합니다. LOGINKEY는 원본과 동일하게 적용하며 실제 접근 권한은 다운로드 시 확인됩니다.
2. 소설 URL/번호와 EPUB/TXT 형식을 선택합니다. 회차 범위를 선택하지 않으면 전체 회차를 받습니다.
3. **다운로드 옵션**에서 BONUS 처리, 공지, 빈 줄, HTML, 이미지, 압축, 파일명, 글꼴, 병렬 수, 간격과 재시도를 설정합니다.
4. **다운로드**로 즉시 실행하거나 **목록에 추가** 후 **대기열**에서 일괄 실행합니다. 추가 시점의 설정이 작품별로 적용되는 원본 동작을 유지합니다.
5. 실시간 **실행 로그**와 **진행 상황**을 확인합니다. **다운로드 중단**은 원본 중단 핸들러를 호출합니다.
6. 완료 로그를 확인한 뒤 **저장 파일** 탭에서 EPUB/TXT를 기기에 받습니다.
7. 각 파일의 **삭제** 또는 **전체 삭제**로 저장 파일을 정리합니다. 확인창에서 승인하면 서버의 EPUB/TXT가 삭제되며 복구할 수 없습니다. 다운로드 또는 대기열 실행 중에는 삭제할 수 없습니다. 로그인 정보, 설정 및 작업용 임시 폴더는 유지합니다.

**이미지 최적화 (고화질)**를 선택하면 원본 EPUB 생성이 끝난 뒤 서버에서 이미지를 최적화하고 `작품명 [최적화].epub`을 별도로 저장합니다. 기본값은 꺼짐입니다. 대기열은 추가할 때 선택한 최적화 옵션을 작품별로 기억하며, 대기열 다운로드가 끝나면 선택한 작품을 처리합니다. TXT에는 적용하지 않습니다. 기존 EPUB도 **저장 파일 → 최적화**로 처리할 수 있습니다. 원본은 유지하며, 최적화본이 이미 있으면 번호를 붙여 새로 저장합니다. 원본을 삭제하기 전 최적화본을 확인하세요.

해상도를 줄이지 않고 불투명 PNG를 JPEG 품질 95, 색상 샘플링 4:4:4로 변환합니다. 변환 후 원본과 비교한 PSNR이 42 dB 미만이거나 용량 감소가 작으면 해당 이미지는 원본을 유지합니다. 투명 PNG, APNG/GIF, 16비트 이미지와 기존 JPEG/WebP는 재압축하지 않으며 색상 프로필을 보존합니다. 본문·목차·읽기 순서는 유지하고 EPUB 내부 이미지 경로 및 MIME 형식만 함께 갱신합니다. JPEG 변환은 손실 압축이므로 픽셀까지 원본과 같지는 않습니다. 실제 감소율은 이미지 구성에 따라 다릅니다.

최적화는 저장된 파일만 처리하며 추가 네트워크 요청을 보내지 않습니다. 진행 상황과 전후 용량은 화면과 실행 로그에 표시됩니다. 최적화 중에는 새 다운로드·설정 변경·파일 삭제를 막습니다. 원본의 **EPUB 압축** 옵션은 기존 ZIP 압축 동작을 그대로 유지합니다.

상태와 로그는 1초마다, 파일 목록은 10초마다 갱신됩니다. 브라우저를 닫아도 다운로드는 계속됩니다. 여러 접속자는 같은 계정·다운로드 작업·대기열을 공유합니다. 큐는 원본처럼 메모리에만 존재하며 프로그램 재시작 후 복원되지 않습니다. 원본의 문자 치환 컨트롤은 비활성 상태이므로 웹에서도 새 편집 기능을 추가하지 않았습니다. 기존 설정 파일에 지정한 매핑 파일은 원본이 읽습니다.

## 데이터와 운영

| 호스트 경로 | 컨테이너 경로 | 내용 |
| --- | --- | --- |
| `data/downloads/` | `/data/downloads` | EPUB/TXT 및 원본 임시 작업 파일 |
| `data/state/config.json` | `/data/state/config.json` | 원본 프로그램 설정 |
| `data/state/web-settings.json` | `/data/state/web-settings.json` | 웹 이미지 최적화 옵션 |

원본은 로그인 정보도 `config.json`에 저장합니다. 이 동작을 유지하며 설정 디렉터리는 프로그램 사용자만 읽을 수 있습니다. 설정 파일을 웹으로 제공하지 않고 상태 API에서도 비밀번호와 LOGINKEY를 반환하지 않습니다. 정상 종료 시 원본 폼을 닫아 저장 이벤트를 실행합니다. 강제 종료 시 마지막 변경이 저장되지 않을 수 있습니다.

```sh
docker compose logs --tail=100
docker compose stop
docker compose up -d
```

`.env`에서 웹 비밀번호를 변경하고 컨테이너를 재생성하면 적용됩니다. Docker 관리자는 프로세스 환경에 접근할 수 있습니다.

## Nginx Proxy Manager / HTTPS

`.env`의 `BIND_ADDRESS=127.0.0.1`로 직접 HTTP 접근을 호스트 안으로 제한합니다. `proxy-nw`라는 기존 네트워크에 연결하려면 다음 오버레이를 사용합니다.

```sh
docker compose -f compose.yaml -f compose.npm.yaml up -d --build
```

운영 `.env`에 `COMPOSE_FILE=compose.yaml:compose.npm.yaml`을 추가하면 이후에는 `docker compose up -d`만으로 같은 네트워크 구성을 유지합니다.

| NPM 설정 | 값 |
| --- | --- |
| Domain Names | 사용할 도메인 |
| Scheme | `http` |
| Forward Hostname | `novelpia-downloader` |
| Forward Port | `8080` |
| SSL Certificate | 해당 도메인을 포함하는 인증서 |
| Force SSL | 켜기 |

외부 HTTPS 포트가 `18443`이어도 같은 출처의 API와 파일 다운로드를 사용합니다. API 브리지는 컨테이너 loopback에서만 듣습니다. 웹·API·파일에 접속 인증을 적용하고, 변경 요청은 CORS 허용 없이 전용 요청 헤더를 요구합니다.

## 검증

```sh
sha256sum --check docker/upstream.sha256
python3 tests/smoke.py
python3 tests/smoke.py --base-url https://사용할도메인:18443 --connect-address 127.0.0.1
```

원본 보존, 인증, 설정 파일 접근 차단, API 상태·유효성 검사, 파일 목록·한글 파일명·다운로드를 검증합니다. `--exercise`를 추가하면 테스트용 LOGINKEY와 소설 번호 0으로 원본 설정 저장·큐 추가·중복 처리·삭제를 확인합니다. **이 옵션은 운영 로그인과 설정을 바꾸므로 사용자 데이터가 없는 테스트 인스턴스에서만 실행합니다.** 소설 제목 조회에는 외부 네트워크를 사용하지만 실제 회차를 다운로드하지 않습니다.

Playwright가 설치되어 있으면 `node tests/browser.cjs`로 웹 폼, 옵션 저장, 대기열·파일 메뉴, 모바일 너비를 검증할 수 있습니다. `BASE_URL`, `CONNECT_ADDRESS`, `BROWSER_EXECUTABLE`, `PLAYWRIGHT_MODULE` 환경 변수로 테스트 대상을 지정합니다. 실제 노벨피아 계정으로 소설 다운로드가 되는지는 해당 계정으로 확인해야 합니다.

`python3 tests/files.py --browser`는 임시 데이터와 무작위 포트를 가진 별도 컨테이너를 자동으로 생성해 삭제 API와 브라우저 확인창·취소·개별/전체 삭제·모바일 표시를 검증합니다. 운영 파일은 사용하지 않습니다. 경로 이탈과 심볼릭 링크 차단, 다운로드/대기열 실행 중 삭제 거절, 설정과 임시 파일 보존도 확인합니다. 브라우저 검증을 생략하려면 `--browser` 없이 실행합니다. 아래 이미지 검증과 같은 컴파일러 이미지가 필요합니다.

이미지 최적화 검증은 Pillow가 설치된 Python 3에서 실행합니다. 생성한 테스트 이미지와 EPUB만 사용합니다.

```sh
python3 tests/optimization.py
python3 tests/optimization_service.py --browser
```

EPUB 이미지 참조·MIME·표지·읽기 순서·본문·해상도·색상 프로필, 투명/애니메이션/이미 압축된 이미지 보존, 화질 하한과 원본 보존을 검증합니다. 서비스 검증은 별도 컨테이너에서 비동기 처리와 진행 표시, 인증·경로 검사, 실행 중 변경 거절, 옵션 저장·재시작, 기존 파일 버튼과 모바일 UI를 확인합니다. 원본 큐 이벤트와 완료 후 자동 최적화는 로컬 제목 응답 서버를 사용하므로 실제 노벨피아 요청이 발생하지 않습니다.

이미지 전송 검증은 실제 CDN 이미지 주소로 실행합니다. 원본의 표지 다운로드, 프로토콜 상대 주소를 사용한 본문 삽화 다운로드, EPUB 이미지 참조와 파일 형식 판별을 확인하며 CDN 응답과 저장 이미지의 SHA-256이 같아야 통과합니다. 계정에 접근하거나 운영 설정을 변경하지 않습니다.

```sh
docker build --target build -t novelpia-downloader-build:local .
python3 tests/images.py --image-url 'https://images.novelpia.com/imagebox/cover/검증할이미지.file'
```
