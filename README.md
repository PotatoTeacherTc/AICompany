# AICompany

AI 기반 자동화와 콘텐츠 제작 시스템을 연구하는 개인 AI 프로젝트 워크스페이스입니다.

## 🎯 Vision

AI 기술을 활용하여 반복 작업을 자동화하고,
콘텐츠 제작과 디지털 프로젝트를 효율적으로 운영하는 시스템 구축을 목표로 합니다.

## 📂 Project Structure
- Archive
  - 이전 자료 보관

- Assets
  - 프로젝트 리소스 관리

- Automation
  - AI 자동화 스크립트

- Images
  - 이미지 자료

- Music
  - AI 음악 프로젝트

- Projects
  - 주요 프로젝트 개발 공간

- Prompt Library
  - AI 프롬프트 관리

- Temp
  - 임시 작업 공간

- Videos
  - 영상 콘텐츠 자료

## 🚀 Projects

### AI Music Factory
AI 기반 음악 제작 및 자동화 시스템

### YouTube Automation
영상 제작 과정 자동화 연구

### AI Dashboard
AI 프로젝트 관리 및 데이터 시각화

### Website Builder
AI 기반 웹 제작 자동화

## 🛠 Tech Stack

- Python
- Node.js
- Git / GitHub
- AI Tools
- Automation Systems

## 📌 Development Log

모든 개발 과정과 실험 기록을 GitHub에 저장합니다.

## 👤 Author

PotatoTeacherTc

## Automated tests

The Automation test suite uses only Python's standard-library `unittest`.
It creates files, music projects, and execution history in temporary
directories, so it does not alter `Automation/TestFiles`, `Automation/Music`,
or production execution history.

Run it from the Automation directory:

```powershell
cd Automation
python -m unittest discover -s tests -v
```

## Offline creative demo

Run the first integrated creative workflow with deterministic Fake providers:

```powershell
cd Automation
python main.py creative-demo
```

The command creates lyrics and a content plan, then runs the existing Fake
music, image, video, and YouTube stages. It prints only safe IDs, stage status,
title, and available usage fields. Local state is written beneath the
git-ignored `Automation/logs/creative-demo` directory.

An Ollama text model can be selected only explicitly:

```powershell
$env:AICOMPANY_TEXT_MODEL = "your-installed-model"
python main.py creative-demo --local-text
```

The endpoint must be loopback and Ollama must already be installed and running.
There is no automatic download, account login, API key, paid-provider fallback,
or external media call. Automated regression tests cover the local adapter
through an injected transport so they remain portable when Ollama is absent.

The loopback workflow has subsequently been verified with Ollama 0.32.5 and
`qwen2.5:1.5b`:

```powershell
$env:AICOMPANY_TEXT_PROVIDER = "ollama"
$env:AICOMPANY_TEXT_MODEL = "qwen2.5:1.5b"
$env:AICOMPANY_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
$env:AICOMPANY_TEXT_PROVIDER_TIMEOUT = "60"
python main.py creative-demo "한국어 발라드와 회복을 주제로 한 영상 콘텐츠를 구성해 주세요." --local-text
```

Only the lyrics and content-plan stages use the local model. Music, image,
video, and YouTube remain deterministic Fake stages. The verified run reported
zero estimated cost. A failed explicit Ollama run returns failure and never
falls back to Fake. Model installation, updates, and downloads remain explicit
user actions.
