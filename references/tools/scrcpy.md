# scrcpy — Android 화면 미러링·제어

- Repo: https://github.com/Genymobile/scrcpy (C) · v4.1 · **Apache-2.0**
- 공식 저장소는 위 하나뿐. 이름에 `scrcpy`가 든 앱스토어 앱은 무관/사칭.

## 언제 (툴박스에서의 위치)

데이터가 **웹/HTTP/공개 API에 없고 Android 앱 안에만** 있을 때. scrcpy로 기기 화면을 데스크톱에
미러링·제어하고, 필요하면 세션을 녹화해 근거를 남긴다. 웹 크롤러가 닿지 못하는 "모바일 전용" 표면을
사람/자동화가 조작하는 통로다. (웹이 있으면 항상 웹 크롤러가 우선 — scrcpy는 최후의 네이티브 수단.)

> 이 도구는 **웹 크롤러가 아니다.** 순수 미러/제어(입력·화면). 앱 내 데이터의 프로그램적 추출은
> 화면 캡처/OCR(→ markitdown 이미지) 또는 접근성/자동화 도구와 결합해야 한다.

## 준비 / 설치

```bash
# 설치
sudo apt install scrcpy      # Debian/Ubuntu
brew install scrcpy          # macOS
choco install scrcpy         # Windows (또는 scoop)

# 기기: 개발자 옵션 → USB 디버깅 활성화, adb 로 인식되면 OK
adb devices
```

## 기본 사용

```bash
scrcpy                                   # USB 연결 기기 미러링 + 제어
scrcpy -m 1024                           # 해상도 상한(성능)
scrcpy --video-codec=h265 --max-fps=60 --no-audio -K   # 고효율, 오디오 끔, UHID 키보드
scrcpy --record session.mp4 --no-audio   # 세션 녹화 (수집 근거/재현)
scrcpy --tcpip                           # 무선(Wi-Fi) 연결
scrcpy --new-display=1920x1080 --start-app=org.videolan.vlc   # 가상 디스플레이에 앱 실행
```

- **OTG 모드**(`--otg`): USB 디버깅 없이 키보드/마우스만 전달(입력 전용, 미러 없음). `doc/otg.md`.
- 녹화·오디오·가상 디스플레이 등 세부 옵션은 upstream `doc/`(recording.md, otg.md 등).

## 함정 / 가드레일

- **본인 소유이거나 명시적 동의된 기기만** 미러/제어/녹화한다.
- USB 디버깅은 보안에 민감 — 신뢰된 호스트에서만, 작업 후 해제 권장.
- scrcpy는 기기에 아무것도 설치하지 않고 root도 불필요하지만, 제어는 실제 입력과 동일하므로
  부작용(구매·삭제 등) 있는 조작은 승인 범위 안에서만.
