# Пресеты конфигурации

*[English](README.md) · [**Русский**] · [中文](README.zh.md)*

Скопируйте один пресет в `config/config.yaml`, затем впишите `user_name` и `graph_dir`.

- `config.example.yaml` — русский дефолт: GigaAM STT (русский SOTA), документы по-русски.
- `config.example.en.yaml` — английский: Parakeet STT, документы и роль суфлёра по-английски.
- `config.example.zh.yaml` — китайский: Whisper STT, документы по-китайски; Qwen (LLM по умолчанию) в китайском нативен.

Каждый ключ задокументирован прямо в файле. Сам `config.yaml` в git-игноре — ваши настройки не покидают машину.
