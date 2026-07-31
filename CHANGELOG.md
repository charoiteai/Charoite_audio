# Changelog

All notable changes to Charoite are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.42.0](https://github.com/charoiteai/Charoite_audio/compare/v0.41.0...v0.42.0) (2026-07-31)


### Features

* **macOS:** show post-meeting processing status ([#192](https://github.com/charoiteai/Charoite_audio/issues/192)) ([0c480db](https://github.com/charoiteai/Charoite_audio/commit/0c480db1fe576c05ec8174ae38fe8036b4af964f))


### Bug Fixes

* **mcp:** сервер поднимается и на mcp 2.x ([#190](https://github.com/charoiteai/Charoite_audio/issues/190)) ([1ec2e9d](https://github.com/charoiteai/Charoite_audio/commit/1ec2e9d4cd86003facc00e1e0f0c89e66c92c0b7))

## [0.41.0](https://github.com/charoiteai/Charoite_audio/compare/v0.40.0...v0.41.0) (2026-07-31)


### Features

* **android:** компаньон для Android — запись, доставка, граф ([#178](https://github.com/charoiteai/Charoite_audio/issues/178)) ([8688f93](https://github.com/charoiteai/Charoite_audio/commit/8688f93735517b4330f409c3b47304f3b756c095))
* **macOS:** напоминать о записи вне окна приложения ([#188](https://github.com/charoiteai/Charoite_audio/issues/188)) ([4ed2dea](https://github.com/charoiteai/Charoite_audio/commit/4ed2dead31aa8f8caee268999bb15d39e8ea932a))


### Bug Fixes

* **android:** make companion checks and imports reliable ([#180](https://github.com/charoiteai/Charoite_audio/issues/180)) ([9e9bd03](https://github.com/charoiteai/Charoite_audio/commit/9e9bd033dfe30006eb3e7432bb0299be1f7afc18))
* **android:** protect recordings during recovery and delivery ([#187](https://github.com/charoiteai/Charoite_audio/issues/187)) ([90fc3ca](https://github.com/charoiteai/Charoite_audio/commit/90fc3ca0f001631345fd17ee776b30d28087fd7d))

## [0.40.0](https://github.com/charoiteai/Charoite_audio/compare/v0.39.0...v0.40.0) (2026-07-30)


### Features

* **bench:** DER-бенч диаризации — «путает говорящих» стало числом ([#172](https://github.com/charoiteai/Charoite_audio/issues/172)) ([0ce2b37](https://github.com/charoiteai/Charoite_audio/commit/0ce2b3704db83fc737f94802482f24e81bb68bc5))
* **cloud:** воркер разбора — таймаут, проверка ответа и границы правок графа ([#177](https://github.com/charoiteai/Charoite_audio/issues/177)) ([58acd09](https://github.com/charoiteai/Charoite_audio/commit/58acd097a82694a8231f072157723dfeb7ca45a1))
* **diarize:** живая диаризация по кускам речи — DER 0.725 → 0.246 ([#173](https://github.com/charoiteai/Charoite_audio/issues/173)) ([5cb4cff](https://github.com/charoiteai/Charoite_audio/commit/5cb4cff4d54825acea8c9ef078593bd3a8834e90))
* **voice:** гейт по голосу — басовитого собеседника больше не назовут Анной ([#170](https://github.com/charoiteai/Charoite_audio/issues/170)) ([f7abf96](https://github.com/charoiteai/Charoite_audio/commit/f7abf965a3adb5075d4a55a7a049d7c29c7ec522))


### Bug Fixes

* **privacy:** облако читает подготовленный набор, а не весь репозиторий ([#175](https://github.com/charoiteai/Charoite_audio/issues/175)) ([a3f96c2](https://github.com/charoiteai/Charoite_audio/commit/a3f96c22397ad9b9586e472878858fba1f12403f))
* **privacy:** право писать даёт cloud_edit_graph, а не согласие на разбор ([#174](https://github.com/charoiteai/Charoite_audio/issues/174)) ([8ea9f14](https://github.com/charoiteai/Charoite_audio/commit/8ea9f1407f1bfdf9e04534e3800ed81c2a0dce1e))

## [0.39.0](https://github.com/charoiteai/Charoite_audio/compare/v0.38.1...v0.39.0) (2026-07-30)


### Features

* **cue:** полоса «встреча началась — начать запись?» ([#168](https://github.com/charoiteai/Charoite_audio/issues/168)) ([96a7313](https://github.com/charoiteai/Charoite_audio/commit/96a731388c77df4ce8a5ba52d22fe6d9e69926b4))
* **forget:** забыть встречу целиком — scripts/forget_meeting.py ([#163](https://github.com/charoiteai/Charoite_audio/issues/163)) ([91bad9d](https://github.com/charoiteai/Charoite_audio/commit/91bad9d62d5b898cf9b1d6f5be962cb426ebc0f7))
* **models:** модель диаризации ставится одной командой ([#164](https://github.com/charoiteai/Charoite_audio/issues/164)) ([506406c](https://github.com/charoiteai/Charoite_audio/commit/506406cd147e7d772f567fde6144b0edaeebd418))
* **protocol:** протокол встречи одной командой — в буфер, в файл, для письма ([#166](https://github.com/charoiteai/Charoite_audio/issues/166)) ([d0daec9](https://github.com/charoiteai/Charoite_audio/commit/d0daec97f487439476eaad40e15879b1659e4754))

## [0.38.1](https://github.com/charoiteai/Charoite_audio/compare/v0.38.0...v0.38.1) (2026-07-30)


### Bug Fixes

* **docs:** команды README запускаются через .venv, а не тем питоном, где нет пакетов ([#157](https://github.com/charoiteai/Charoite_audio/issues/157)) ([c4ed6d4](https://github.com/charoiteai/Charoite_audio/commit/c4ed6d423c921d6674f247989806d5e5e60cc662))
* **markers:** страж обезличивания проверяет всё дерево, а не только диф ([#159](https://github.com/charoiteai/Charoite_audio/issues/159)) ([1a04da6](https://github.com/charoiteai/Charoite_audio/commit/1a04da673e78564f5f0e62f8f64cd216abf12bfc))
* **names:** опознанное имя проверяется одинаково с моделью голосов и без неё ([#155](https://github.com/charoiteai/Charoite_audio/issues/155)) ([3ef05e5](https://github.com/charoiteai/Charoite_audio/commit/3ef05e56d716caceb6a14394d91280e3541b3530))
* **privacy:** все четыре тумблера облака решаются в src/privacy.py ([#156](https://github.com/charoiteai/Charoite_audio/issues/156)) ([7fb826f](https://github.com/charoiteai/Charoite_audio/commit/7fb826f2b997f445c384f60356a38f18372c4291))

## [0.38.0](https://github.com/charoiteai/Charoite_audio/compare/v0.37.0...v0.38.0) (2026-07-30)


### Features

* **graph:** досье по темам — сводки поверх ядер с индексом для поиска, инкрементальная пересборка по отпечатку источников, опциональная ревизия облаком ([#150](https://github.com/charoiteai/Charoite_audio/issues/150)) ([fcb7899](https://github.com/charoiteai/Charoite_audio/commit/fcb7899))
* **app:** тумблер «Разрешить облаку править досье» в Настройках ([#152](https://github.com/charoiteai/Charoite_audio/issues/152)) ([06d4782](https://github.com/charoiteai/Charoite_audio/commit/06d4782e4789c3d7019cad322c0ccf2ee3a1e674))
* **tasks:** поручения из минуток доходят до окна задач ([#149](https://github.com/charoiteai/Charoite_audio/issues/149)) ([1143b1b](https://github.com/charoiteai/Charoite_audio/commit/1143b1bf426da020db8a2340b8c79bc4fa99eb64))

## [0.37.0](https://github.com/charoiteai/Charoite_audio/compare/v0.36.0...v0.37.0) (2026-07-29)


### Features

* **graph:** дедупликация копий файлов — в поиске и в ночном цикле ([#145](https://github.com/charoiteai/Charoite_audio/issues/145)) ([770206b](https://github.com/charoiteai/Charoite_audio/commit/770206b8f9db52dbe0b190b8af81efefe7095b42))
* **search:** чанкинг семантики, бюджет контекста, ускорение вчетверо ([#146](https://github.com/charoiteai/Charoite_audio/issues/146)) ([cfdf80d](https://github.com/charoiteai/Charoite_audio/commit/cfdf80d11c33ec84b4ab2d372f4a6929ad5fb0f9))


### Bug Fixes

* kill-switch, потеря встречи при рестарте, нерабочие пресеты en/zh ([#141](https://github.com/charoiteai/Charoite_audio/issues/141)) ([912fbe0](https://github.com/charoiteai/Charoite_audio/commit/912fbe0598d750f0c34c3f0182d0c8890e822ffb))
* аварийное восстановление встречи, заморозки UI, доделанная локализация ([#144](https://github.com/charoiteai/Charoite_audio/issues/144)) ([940877d](https://github.com/charoiteai/Charoite_audio/commit/940877db8451a11da25ffc69714d1655c8587d5a))

## [0.36.0](https://github.com/charoiteai/Charoite_audio/compare/v0.35.0...v0.36.0) (2026-07-28)


### Features

* **chat:** 32K context with matching budgets ([#138](https://github.com/charoiteai/Charoite_audio/issues/138)) ([63f189e](https://github.com/charoiteai/Charoite_audio/commit/63f189ea5fd885efdc4972d7b71c288348be19c9))
* smarter local chat + UI in three languages + English screenshots ([#135](https://github.com/charoiteai/Charoite_audio/issues/135)) ([8bb7978](https://github.com/charoiteai/Charoite_audio/commit/8bb79780e014bfc54c5416ade081314314c3df6c))


### Bug Fixes

* **privacy:** CHAROITE_NO_CLOUD kill-switch alias ([#139](https://github.com/charoiteai/Charoite_audio/issues/139)) ([889c34e](https://github.com/charoiteai/Charoite_audio/commit/889c34e68388fcd870315a4e123bf05cfb15dd63))

## [0.35.0](https://github.com/charoiteai/Charoite_audio/compare/v0.34.0...v0.35.0) (2026-07-28)


### Features

* **i18n:** Chinese face — engine prompts, config preset, README and docs ([#132](https://github.com/charoiteai/Charoite_audio/issues/132)) ([df8db39](https://github.com/charoiteai/Charoite_audio/commit/df8db398a7e2b11ef71ae02d37bbe0e561c2b642))
* **i18n:** every folder documented in three languages + the owl icon returns ([#134](https://github.com/charoiteai/Charoite_audio/issues/134)) ([854cd3c](https://github.com/charoiteai/Charoite_audio/commit/854cd3cfc172e5f7d9eafac054d6fb1977881cb3))

## [0.34.0](https://github.com/charoiteai/Charoite_audio/compare/v0.33.0...v0.34.0) (2026-07-28)


### Features

* brand crystal icons for macOS and iOS + English config preset ([#130](https://github.com/charoiteai/Charoite_audio/issues/130)) ([9e24691](https://github.com/charoiteai/Charoite_audio/commit/9e246917fe51377f1c7bd25dc084ad38979cf6b1))

## [0.33.0](https://github.com/charoiteai/Charoite_audio/compare/v0.32.0...v0.33.0) (2026-07-28)


### Features

* **app:** cloud Claude feed lives inside the hint pane ([#128](https://github.com/charoiteai/Charoite_audio/issues/128)) ([96259e4](https://github.com/charoiteai/Charoite_audio/commit/96259e46449fe5e7cbdeebe7a04d06e5517057c2))

## [0.32.0](https://github.com/charoiteai/Charoite_audio/compare/v0.31.1...v0.32.0) (2026-07-28)


### Features

* **ios:** meetings feed, graph tasks and a Live Activity recording timer ([#126](https://github.com/charoiteai/Charoite_audio/issues/126)) ([2b90f03](https://github.com/charoiteai/Charoite_audio/commit/2b90f030dc99169d13e72bec22676e963f139eb4))

## [0.31.1](https://github.com/charoiteai/Charoite_audio/compare/v0.31.0...v0.31.1) (2026-07-27)


### Bug Fixes

* voice-notes index first letter + cloud guard covers scripts/ ([#115](https://github.com/charoiteai/Charoite_audio/issues/115)) ([ea7e77b](https://github.com/charoiteai/Charoite_audio/commit/ea7e77bd463028827f77a2285cf36ee2d58d2387))

## [0.31.0](https://github.com/charoiteai/Charoite_audio/compare/v0.30.0...v0.31.0) (2026-07-27)


### Features

* iOS folder-picker delivery + outbox queue; nightly Opus review in brief ([#111](https://github.com/charoiteai/Charoite_audio/issues/111)) ([7c0dde1](https://github.com/charoiteai/Charoite_audio/commit/7c0dde11c63427d6d3fe21a02d8d91c99281ade5))

## [0.30.0](https://github.com/charoiteai/Charoite_audio/compare/v0.29.0...v0.30.0) (2026-07-27)


### Features

* iPhone companion v1 skeleton + voice-note routing in import ([#108](https://github.com/charoiteai/Charoite_audio/issues/108)) ([e3dd182](https://github.com/charoiteai/Charoite_audio/commit/e3dd182d301b6f762f9d42fd1cdd08b61d0621bb))

## [0.29.0](https://github.com/charoiteai/Charoite_audio/compare/v0.28.0...v0.29.0) (2026-07-27)


### Features

* merge same-voice diarization shards (no biometrics stored) ([#106](https://github.com/charoiteai/Charoite_audio/issues/106)) ([c588393](https://github.com/charoiteai/Charoite_audio/commit/c588393d9da78b2f616a6e133669c333cbe8d1ef))

## [0.28.0](https://github.com/charoiteai/Charoite_audio/compare/v0.27.0...v0.28.0) (2026-07-27)


### Features

* copy buttons on panes + speaker-name canon from graph + faster first context ([#103](https://github.com/charoiteai/Charoite_audio/issues/103)) ([002d5ef](https://github.com/charoiteai/Charoite_audio/commit/002d5efd33e8101cb5fc06d844f79be06cad686a))

## [0.27.0](https://github.com/charoiteai/Charoite_audio/compare/v0.26.0...v0.27.0) (2026-07-27)


### Features

* **archive:** meeting time in archive folder name («date HH-MM — topic») ([#101](https://github.com/charoiteai/Charoite_audio/issues/101)) ([2587564](https://github.com/charoiteai/Charoite_audio/commit/2587564e1752a62931518d1a438327a04c9dfddd))

## [0.26.0](https://github.com/charoiteai/Charoite_audio/compare/v0.25.0...v0.26.0) (2026-07-27)


### Features

* cloud refinement into the hint card itself + opening archive brief ([#99](https://github.com/charoiteai/Charoite_audio/issues/99)) ([ecfd877](https://github.com/charoiteai/Charoite_audio/commit/ecfd8770a77dda4f6373abf9f51cbc1b7cdb5f84))

## [0.25.0](https://github.com/charoiteai/Charoite_audio/compare/v0.24.0...v0.25.0) (2026-07-27)


### Features

* live meeting context from archive + cloud-refined hints ([#97](https://github.com/charoiteai/Charoite_audio/issues/97)) ([c497d4c](https://github.com/charoiteai/Charoite_audio/commit/c497d4c80251c5ed8c8ee24cafd11e4a006ce5bf))

## [0.24.0](https://github.com/charoiteai/Charoite_audio/compare/v0.23.0...v0.24.0) (2026-07-27)


### Features

* import folder, replacement dictionary, post-meeting hook ([#95](https://github.com/charoiteai/Charoite_audio/issues/95)) ([cfd9d92](https://github.com/charoiteai/Charoite_audio/commit/cfd9d9275c8d4b85558968e3a22d105f3b2909aa))

## [0.23.0](https://github.com/charoiteai/Charoite_audio/compare/v0.22.1...v0.23.0) (2026-07-27)


### Features

* voice diary (⌥⌘J) + one-command meeting import ([#93](https://github.com/charoiteai/Charoite_audio/issues/93)) ([908eb92](https://github.com/charoiteai/Charoite_audio/commit/908eb92b06c43fabe4f66c4c59fffb31092e34f6))

## [0.22.1](https://github.com/charoiteai/Charoite_audio/compare/v0.22.0...v0.22.1) (2026-07-26)


### Bug Fixes

* **app:** canonical rel paths — /var vs /private/var symlink broke index keys ([#90](https://github.com/charoiteai/Charoite_audio/issues/90)) ([440c0e9](https://github.com/charoiteai/Charoite_audio/commit/440c0e96864b1f7131a52217d4367531b669153f))
* **privacy:** облако выключено по умолчанию во всех трёх местах чтения конфига ([#87](https://github.com/charoiteai/Charoite_audio/issues/87)) ([58982a9](https://github.com/charoiteai/Charoite_audio/commit/58982a92d3cfc64a66fe6236b26cbb0f9a092a10))
* **privacy:** облако выключено по умолчанию во всех трёх местах чтения конфига ([#89](https://github.com/charoiteai/Charoite_audio/issues/89)) ([630185a](https://github.com/charoiteai/Charoite_audio/commit/630185ace9efadd8ff24d412458a5554a3f085f5))
* **release:** Charoite.app.zip собирается из кода своего тега, а не из вершины main ([630185a](https://github.com/charoiteai/Charoite_audio/commit/630185ace9efadd8ff24d412458a5554a3f085f5))
* **release:** Charoite.app.zip собирается из кода своего тега, а не из вершины main ([58982a9](https://github.com/charoiteai/Charoite_audio/commit/58982a92d3cfc64a66fe6236b26cbb0f9a092a10))
* **search:** семантика по всему графу, инвалидация индекса, латинский стеммер ([630185a](https://github.com/charoiteai/Charoite_audio/commit/630185ace9efadd8ff24d412458a5554a3f085f5))
* **search:** семантика по всему графу, инвалидация индекса, латинский стеммер ([58982a9](https://github.com/charoiteai/Charoite_audio/commit/58982a92d3cfc64a66fe6236b26cbb0f9a092a10))
* **tier3:** ревизия помечает, но не сливает без явного разрешения — днём и ночью ([630185a](https://github.com/charoiteai/Charoite_audio/commit/630185ace9efadd8ff24d412458a5554a3f085f5))
* **tier3:** ревизия помечает, но не сливает без явного разрешения — днём и ночью ([58982a9](https://github.com/charoiteai/Charoite_audio/commit/58982a92d3cfc64a66fe6236b26cbb0f9a092a10))
* **ui:** подсказка кнопки Claude ведёт к существующему тумблеру ([630185a](https://github.com/charoiteai/Charoite_audio/commit/630185ace9efadd8ff24d412458a5554a3f085f5))
* **ui:** подсказка кнопки Claude ведёт к существующему тумблеру ([58982a9](https://github.com/charoiteai/Charoite_audio/commit/58982a92d3cfc64a66fe6236b26cbb0f9a092a10))

## [0.22.0](https://github.com/charoiteai/Charoite_audio/compare/v0.21.0...v0.22.0) (2026-07-25)


### Features

* **bench:** --demo-en — the English demo loop check (3/3 live) ([#80](https://github.com/charoiteai/Charoite_audio/issues/80)) ([26c7259](https://github.com/charoiteai/Charoite_audio/commit/26c72595abb9fbec2f14dd93376e51a719cf7a70))

## [0.21.0](https://github.com/charoiteai/Charoite_audio/compare/v0.20.0...v0.21.0) (2026-07-25)


### Features

* **app:** tasks grouped by source file ([#78](https://github.com/charoiteai/Charoite_audio/issues/78)) ([b130b7a](https://github.com/charoiteai/Charoite_audio/commit/b130b7ab2d1005ebb129954859faf711b3139d1e))

## [0.20.0](https://github.com/charoiteai/Charoite_audio/compare/v0.19.1...v0.20.0) (2026-07-25)


### Features

* **bench:** --demo mode — verify the RAG loop before any meeting ([#75](https://github.com/charoiteai/Charoite_audio/issues/75)) ([626358d](https://github.com/charoiteai/Charoite_audio/commit/626358dbe7aaa51353279207796c46ff7c755c65))

## [0.19.1](https://github.com/charoiteai/Charoite_audio/compare/v0.19.0...v0.19.1) (2026-07-25)


### Bug Fixes

* **ci:** release-app fires after release-please via workflow_run ([#72](https://github.com/charoiteai/Charoite_audio/issues/72)) ([86a8015](https://github.com/charoiteai/Charoite_audio/commit/86a80153cbe5789a8790b035281c7a63e0445143))

## [0.19.0](https://github.com/charoiteai/Charoite_audio/compare/v0.18.0...v0.19.0) (2026-07-25)


### Features

* **app:** semantic index size in the Settings check ([#70](https://github.com/charoiteai/Charoite_audio/issues/70)) ([798400a](https://github.com/charoiteai/Charoite_audio/commit/798400a7ff7f7956ecc002346d0c707a5dff23f2))

## [0.18.0](https://github.com/charoiteai/Charoite_audio/compare/v0.17.0...v0.18.0) (2026-07-25)


### Features

* English search stemming + English demo graph with e2e tests ([#67](https://github.com/charoiteai/Charoite_audio/issues/67)) ([bc6212c](https://github.com/charoiteai/Charoite_audio/commit/bc6212c0177d51574d37c4f77096612be67af09b))

## [0.17.0](https://github.com/charoiteai/Charoite_audio/compare/v0.16.0...v0.17.0) (2026-07-25)


### Features

* **app:** archive answer history survives restarts ([#65](https://github.com/charoiteai/Charoite_audio/issues/65)) ([6a3be7d](https://github.com/charoiteai/Charoite_audio/commit/6a3be7dfe80d5997f57d8a17f6721079919f8c94))

## [0.16.0](https://github.com/charoiteai/Charoite_audio/compare/v0.15.0...v0.16.0) (2026-07-25)


### Features

* English graph node content (phase 2) — values only, stable contract ([#63](https://github.com/charoiteai/Charoite_audio/issues/63)) ([048fcc0](https://github.com/charoiteai/Charoite_audio/commit/048fcc064446ed090cc55f5d0e3a3c91acb90341))

## [0.15.0](https://github.com/charoiteai/Charoite_audio/compare/v0.14.1...v0.15.0) (2026-07-25)


### Features

* English meeting documents (phase 1) — sufler.language: en ([#61](https://github.com/charoiteai/Charoite_audio/issues/61)) ([bc2170b](https://github.com/charoiteai/Charoite_audio/commit/bc2170bd3a32ac098bbc7df9fa58c131cafb18af))

## [0.14.1](https://github.com/charoiteai/Charoite_audio/compare/v0.14.0...v0.14.1) (2026-07-25)


### Bug Fixes

* **app:** dictation auto-stop after 10 minutes ([#57](https://github.com/charoiteai/Charoite_audio/issues/57)) ([ef903fa](https://github.com/charoiteai/Charoite_audio/commit/ef903fa41f8b8985f2fd5b7e0f8a6e897c389657))

## [0.14.0](https://github.com/charoiteai/Charoite_audio/compare/v0.13.1...v0.14.0) (2026-07-25)


### Features

* **app:** night cycle toggle in Settings ([#54](https://github.com/charoiteai/Charoite_audio/issues/54)) ([7a8bf16](https://github.com/charoiteai/Charoite_audio/commit/7a8bf16a9e939de96f670b75e817cdef6bf45561))

## [0.13.1](https://github.com/charoiteai/Charoite_audio/compare/v0.13.0...v0.13.1) (2026-07-25)


### Bug Fixes

* **app:** bundle version from the latest git tag ([#51](https://github.com/charoiteai/Charoite_audio/issues/51)) ([cb129f0](https://github.com/charoiteai/Charoite_audio/commit/cb129f080188e1b32f93123dcfd57b240e322679))

## [0.13.0](https://github.com/charoiteai/Charoite_audio/compare/v0.12.0...v0.13.0) (2026-07-25)


### Features

* **scripts:** doctor.py — one-command install diagnosis ([#47](https://github.com/charoiteai/Charoite_audio/issues/47)) ([97e184a](https://github.com/charoiteai/Charoite_audio/commit/97e184af585a40f56edbe0ff758d354579c63cf2))

## [0.12.0](https://github.com/charoiteai/Charoite_audio/compare/v0.11.0...v0.12.0) (2026-07-25)


### Features

* **app:** bge-m3 check in Settings + first package tests ([#45](https://github.com/charoiteai/Charoite_audio/issues/45)) ([3861f8f](https://github.com/charoiteai/Charoite_audio/commit/3861f8f7cfd5ebd6f681f7c72516a8d7d763a85a))

## [0.11.0](https://github.com/charoiteai/Charoite_audio/compare/v0.10.0...v0.11.0) (2026-07-25)


### Features

* **app:** calendar brief — one-click prep for the next meeting (opt-in) ([#43](https://github.com/charoiteai/Charoite_audio/issues/43)) ([19d5cb6](https://github.com/charoiteai/Charoite_audio/commit/19d5cb6cb344fadfbbd42433e4f68827acef958c))

## [0.10.0](https://github.com/charoiteai/Charoite_audio/compare/v0.9.0...v0.10.0) (2026-07-25)


### Features

* custom minutes template + copy-all-open-tasks + bge-m3 RAM note ([#41](https://github.com/charoiteai/Charoite_audio/issues/41)) ([04aac8f](https://github.com/charoiteai/Charoite_audio/commit/04aac8fe9d9581066e52f6f9b37c47d7f2732704))

## [0.9.0](https://github.com/charoiteai/Charoite_audio/compare/v0.8.0...v0.9.0) (2026-07-25)


### Features

* **app:** streaming archive answers + live model list ([#39](https://github.com/charoiteai/Charoite_audio/issues/39)) ([8023572](https://github.com/charoiteai/Charoite_audio/commit/8023572d3d75d00c8b01e628f2a0179a7744227a))

## [0.8.0](https://github.com/charoiteai/Charoite_audio/compare/v0.7.0...v0.8.0) (2026-07-25)


### Features

* **app:** semantic layer in the built-in search (bge-m3 + RRF) ([#37](https://github.com/charoiteai/Charoite_audio/issues/37)) ([7cd0e47](https://github.com/charoiteai/Charoite_audio/commit/7cd0e47b52795b74b2f309b9c33f86817a4e2bc9))

## [0.7.0](https://github.com/charoiteai/Charoite_audio/compare/v0.6.0...v0.7.0) (2026-07-25)


### Features

* improvement cycle — 12 UX/bug/docs/feature upgrades ([#35](https://github.com/charoiteai/Charoite_audio/issues/35)) ([7c6d75d](https://github.com/charoiteai/Charoite_audio/commit/7c6d75daddc75e216fdbad180dbd5c41cec20e49))

## [0.6.0](https://github.com/charoiteai/Charoite_audio/compare/v0.5.0...v0.6.0) (2026-07-24)


### Features

* **search:** archive search v2 — ranking, honesty gate, clickable sources ([#33](https://github.com/charoiteai/Charoite_audio/issues/33)) ([92d2084](https://github.com/charoiteai/Charoite_audio/commit/92d20844912b185bce988b28fb720cf261c0a4db))

## [0.5.0](https://github.com/charoiteai/Charoite_audio/compare/v0.4.0...v0.5.0) (2026-07-24)


### Features

* **app:** native macOS app — SwiftUI shell over the daemon ([#31](https://github.com/charoiteai/Charoite_audio/issues/31)) ([59c38c4](https://github.com/charoiteai/Charoite_audio/commit/59c38c4250bf9ca49cd218e4841e3f61b713fb5c))

## [0.4.0](https://github.com/charoiteai/Charoite_audio/compare/v0.3.0...v0.4.0) (2026-07-24)


### Features

* **sleep:** morning brief + memory bench + unified nightly loop ([#29](https://github.com/charoiteai/Charoite_audio/issues/29)) ([58e9c07](https://github.com/charoiteai/Charoite_audio/commit/58e9c07d3b8888dfcb31855e98b55b6e76b494ab))

## [0.3.0](https://github.com/charoiteai/Charoite_audio/compare/v0.2.0...v0.3.0) (2026-07-24)


### Features

* **brief:** auto-brief from the archive at meeting start ([#27](https://github.com/charoiteai/Charoite_audio/issues/27)) ([7154e0e](https://github.com/charoiteai/Charoite_audio/commit/7154e0e00a1d995c783669f38e6b76d4bc762f26))

## [0.2.0](https://github.com/charoiteai/Charoite_audio/compare/v0.1.3...v0.2.0) (2026-07-24)


### Features

* **nli:** semantic thesis dedup via local NLI + honest instant prompt ([#23](https://github.com/charoiteai/Charoite_audio/issues/23)) ([559416e](https://github.com/charoiteai/Charoite_audio/commit/559416eca1842e9d6c118d08a80f5db222400540))
* **tier3:** automatic core revision everywhere, cautious mode ([#25](https://github.com/charoiteai/Charoite_audio/issues/25)) ([57f1eb6](https://github.com/charoiteai/Charoite_audio/commit/57f1eb6a94a817ae7e4456803ef45d44524ecd7d))
* **tier3:** core revision tool — duplicates and nestings via bge-m3 + NLI ([#24](https://github.com/charoiteai/Charoite_audio/issues/24)) ([5131e29](https://github.com/charoiteai/Charoite_audio/commit/5131e29386a5e17fab210ccc6e0ae0f2b0b4a87b))


### Bug Fixes

* **cloud:** cloud panel no longer invents agenda from prompt hardcode ([#20](https://github.com/charoiteai/Charoite_audio/issues/20)) ([1ddbeb2](https://github.com/charoiteai/Charoite_audio/commit/1ddbeb236c8a2170601fffdf1b2ceb54325883b4))
* **graph:** core provenance survives paraphrasing — fuzzy anchor to transcript ([#22](https://github.com/charoiteai/Charoite_audio/issues/22)) ([27e4fad](https://github.com/charoiteai/Charoite_audio/commit/27e4fadb7a4dd02474b70f24c59b3d3e15558e3c))

## [0.1.3](https://github.com/charoiteai/Charoite_audio/compare/v0.1.2...v0.1.3) (2026-07-23)


### Bug Fixes

* граф не обновлялся — модель уходила думать и обрывала JSON ([#10](https://github.com/charoiteai/Charoite_audio/issues/10)) ([e47af9f](https://github.com/charoiteai/Charoite_audio/commit/e47af9fff7a0931097b0f4e3ab1807e4274980b4))
* детекция вопросов — ловим «?» в любом месте, спорное решает модель ([#11](https://github.com/charoiteai/Charoite_audio/issues/11)) ([f147b85](https://github.com/charoiteai/Charoite_audio/commit/f147b855b1b3f83e29f5a81cbb0b44cb5de057b4))
* имя проекта из содержания, неоднозначные имена, пометка NOISE ([#12](https://github.com/charoiteai/Charoite_audio/issues/12)) ([0b06c24](https://github.com/charoiteai/Charoite_audio/commit/0b06c24ff340590023d30d6609b9ddedcd8ab06e))

## [0.1.2] - 2026-07-22

### Added

- **Speaker names survive the post-meeting rebuild.** The daemon now hands the
  rebuild what it learned during the meeting (a sidecar with the live speaker
  count and recognised names); the rebuild maps those names onto its own
  clusters **by time**, because the two clusterings are independent and matching
  them by label would attach a name to the wrong person.
- **Fewer phantom speakers.** The live speaker count is passed to the offline
  clustering as a hint instead of letting it decide freely — in a real meeting
  auto mode produced 14 "people" where the live pass had heard 8.
- **Provenance in the knowledge graph.** Each chronicle entry now records who
  said it, when, and the exact quote. The quote is verified against the
  transcript before writing: models readily invent plausible wording, and an
  invented quote in a graph is worse than none.
- **Déjà vu matches by meaning.** Recurring topics are now found via embeddings
  instead of comparing word stems, which could not connect "we cut the GPU
  funding" to the topic "GPU budget". The threshold is relative to the median,
  since bi-encoder scores sit in a narrow band.

### Fixed

- **Minutes and summaries no longer sprawl.** They were running 2-3× longer than
  a document meant to be read in a minute. Length is now enforced in code rather
  than asked for in the prompt — models do not count their own output reliably.
- **Prompts follow current guidance**: data is delimited from instructions, rules
  are phrased positively ("write it this way") instead of stacked negations, and
  the task format carries one worked example.
- Embedding calls time out in 20s instead of 120s, so a busy backend cannot stall
  the déjà vu loop.
- The auto-hint loop no longer dies silently on an unexpected error.

## [0.1.1] - 2026-07-22

### Fixed

- **Explicit context window (`num_ctx`) for every LLM call.** Graph extraction,
  the post-meeting debrief and the MCP minutes tool were calling Ollama without
  it, so the model loaded with the (very large) context from its Modelfile,
  bloating the KV cache and swapping on 16–32 GB machines.
- **Minutes no longer pull a second heavy model.** The MCP minutes tool had a
  hardcoded 17 GB model that could not run alongside the resident one on 16 GB;
  it now uses the model from the config.
- **Speaker naming: "the name is a vocative, not the speaker".** The guard that
  prevents *"Sam, what do you think?"* from labelling the **current** speaker as
  Sam compared against a line format that never matches the transcript tail, so
  it never fired.
- **Name parsing no longer drops every name.** A greedy `{...}` match glued two
  JSON objects together and failed to parse; the last flat object is used now.
- **Summary history takes the newest events.** The per-topic chronicle is written
  newest-first, so taking the last three entries fed the summary the *oldest*
  context instead of what happened most recently.

## [0.1.0] - 2026-07-21

Initial public release. A fully local AI meeting assistant for macOS on Apple
Silicon — audio, transcription, diarization and LLM summaries all run on your
machine. Nothing leaves the Mac by default.

### Added

- **Fully local pipeline.** Speech-to-text (GigaAM via ONNX), diarization
  (ERes2Net embeddings) and summaries/graph (Qwen via Ollama) run on-device.
  No cloud, no telemetry, no accounts.
- **Speaker diarization that ships.** Live `Speaker 1/2/…` labels during the
  meeting, plus an offline re-pass over the full recording afterwards for clean
  per-speaker paragraphs. Names are filled in only when someone introduces
  themselves — never guessed.
- **Self-updating knowledge graph.** Meetings become episodes; people, systems
  and decisions become nodes; recurring topics become "Cores" with status and
  history. During a meeting Charoite surfaces past context: *"⏮ this was
  discussed on <date>, status was …"*.
- **Layered output per meeting.** One-minute Summary (with links to what changed
  since previous meetings) → Minutes → Debrief → full Transcript. Read as deep
  as you need.
- **Real-time assistance.** Instant local answer when the other side asks you a
  question (⚡), auto-theses, live draft minutes, voice notes and dictation.
- **Optional cloud layer.** A deeper post-meeting analysis via an external
  provider exists in the code but is **off by default** and clearly documented.
  Leave it off and the product stays 100% offline.
- **Privacy by architecture.** All network calls go to `localhost` only; voice
  embeddings used to tell speakers apart live in RAM for the duration of the
  meeting and are never written to disk. Verifiable with Wireshark or LuLu.
- **Explicit model context sizing** (`num_ctx`) across LLM calls to keep the
  local KV-cache small and inference fast on 16–32 GB machines.

### Requirements

- macOS 14+ on Apple Silicon (M1 or newer), 16 GB+ unified memory (32 GB ideal),
  Ollama with the documented models pulled.

### Known limitations

- Terminal / command-line workflow for now; a one-click macOS app is planned.
- Prompts are Russian-first; English prompts for a wider audience are on the
  roadmap.
- Cross-meeting voice recognition (binding a voice to a person node
  automatically) is not implemented yet.

[0.1.0]: https://github.com/charoiteai/Charoite_audio/releases/tag/v0.1.0
