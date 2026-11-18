# LSV Origin Auto

LSV/Nyquist `.mpt` 파일을 Origin 프로젝트(`.opju`)로 변환하는 Windows GUI 도구입니다.

## 사용자 실행

- Windows 64-bit용 `LSV-Origin-Auto.exe`를 실행합니다.
- Origin이 설치되어 있고 사용할 수 있는 상태여야 합니다. 이 프로그램은 Origin을 대신 설치하거나 라이선스를 제공하지 않습니다.
- GUI에서 `.mpt` 파일이 들어 있는 폴더를 선택하고 저장 위치를 지정합니다.

## EXE 빌드

Windows 64-bit와 Python 3.14 환경에서 빌드합니다.

```powershell
.\build_exe.cmd
```

스크립트가 `C:\LSV-Origin-Auto-Build-Env`에 빌드용 가상환경을 만들고 필요한 패키지를 설치합니다. 해당 위치에 쓸 수 없으면 ASCII 문자만 포함된 쓰기 가능한 경로를 두 번째 인수로 지정하세요. 예: `.\build_exe.cmd "C:\path\to\python.exe" "D:\build-env"`. Qt 도구가 비ASCII 사용자 경로를 처리하지 못할 수 있습니다.

완성된 실행 파일은 `dist\LSV-Origin-Auto.exe`에 생성됩니다. 빌드 산출물에는 Python 런타임, GUI 및 Origin 자동화용 Python 모듈이 포함됩니다. Origin 프로그램과 라이선스는 포함되지 않으며, 실행 및 변환은 설치된 Origin에 연결해야 합니다.

공개 배포 전에는 PyQt5 라이선스 조건과 포함된 이미지의 재배포 권한을 확인하세요.
