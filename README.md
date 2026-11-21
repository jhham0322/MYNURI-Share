# MYNURI Share

이 저장소에는 Flutter Android 앱과 Windows PC 명령 실행기가 들어 있습니다. Android 패키지 ID는 `com.mynuri.share`입니다.

## PC 실행기와 Tailscale

PC와 Android에서 Tailscale에 로그인하고 같은 테일넷에 연결합니다. PC에서 MagicDNS와 Tailscale HTTPS가 사용 가능해야 합니다. 먼저 Python 가상환경에 서버를 설치하고, PATH에서 Paseo CLI와 사용할 작업 처리기가 동작하는지 확인합니다.

```powershell
cd myserver
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\tools\Start-MobileExecutor.ps1 -Python .\.venv\Scripts\python.exe
```

두 번째 PowerShell에서 다음 명령을 실행합니다. Android 기기가 한 대면 자동 선택됩니다. 여러 대라면 Tailscale에 표시된 휴대전화 호스트 이름을 `-Device`로 지정합니다. `-Preview`를 붙이면 설정을 적용하기 전에 선택과 접속 주소를 확인할 수 있습니다.

```powershell
.\tools\Connect-MobileTailnet.ps1 -Python .\.venv\Scripts\python.exe
```

스크립트가 출력한 HTTPS 주소를 Android 앱의 **연결 설정**에 저장합니다. PC 실행기와 인증 프록시는 루프백에 바인딩됩니다. Tailscale Serve는 선택한 기기의 Tailscale 계정과 IP가 일치하는 요청을 전달하는 프록시에 연결합니다. 기존 서비스와 포트가 겹치면 설정을 중단합니다.

## Android 빌드와 맞춤 목소리

```powershell
cd mynuri
flutter pub get
flutter build apk --debug
```

릴리스 빌드에는 배포자 본인의 Android 서명 키가 필요합니다. `android/key.properties`에 `storeFile`, `storePassword`, `keyAlias`, `keyPassword`를 설정합니다.

앱에서 **맞춤 목소리 → 모델 다운로드 → MP3·WAV 가져오기**를 선택하고 2~60초 파일을 등록합니다. **미리 듣기**로 확인하고 **음성 출력**을 켭니다. MOSS-TTS-Nano 모델은 첫 사용 시 약 763MB를 내려받으며, 등록한 프로필은 Android 앱 전용 저장소에 보관합니다. 모델과 개인 음성 파일은 별도입니다.
