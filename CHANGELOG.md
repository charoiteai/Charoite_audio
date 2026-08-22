# Changelog

All notable changes to Charoite are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.58.0](https://github.com/charoiteai/Charoite_audio/compare/v0.57.0...v0.58.0) (2026-08-22)


### Features

* **design:** карточка встречи — четыре глубины чтения на месте, одна дверь в редактор ([#371](https://github.com/charoiteai/Charoite_audio/issues/371)) ([220ab81](https://github.com/charoiteai/Charoite_audio/commit/220ab81cd13b0a85359b196a21c9649c646ee56d))
* **design:** пустые состояния говорят, что нажать; «Сегодня» без календаря ([#373](https://github.com/charoiteai/Charoite_audio/issues/373)) ([c570b05](https://github.com/charoiteai/Charoite_audio/commit/c570b05066380aa9544477426c4c894f8f021831))
* **design:** честность о сети, поверхности происхождения и дисциплина токенов ([#369](https://github.com/charoiteai/Charoite_audio/issues/369)) ([8272874](https://github.com/charoiteai/Charoite_audio/commit/82728740c17ee6e76ea950c89e15d9f73b64d203))

## [0.57.0](https://github.com/charoiteai/Charoite_audio/compare/v0.56.0...v0.57.0) (2026-08-22)


### Features

* **ios:** TestFlight-доставка компаньона — ExportOptions и ключ шифрования ([#365](https://github.com/charoiteai/Charoite_audio/issues/365)) ([3b11c72](https://github.com/charoiteai/Charoite_audio/commit/3b11c72c65547182d0af13ff5812bd355f539c88))
* **tasks:** сводка «просрочено · открыто · сделано» и режим «По сроку» ([#367](https://github.com/charoiteai/Charoite_audio/issues/367)) ([273498c](https://github.com/charoiteai/Charoite_audio/commit/273498ca3878d682e87e326152e39f9057e99c6c))
* **update:** два якоря подлинности обновления, независимые от GitHub ([#366](https://github.com/charoiteai/Charoite_audio/issues/366)) ([014b45f](https://github.com/charoiteai/Charoite_audio/commit/014b45f060cde6a84723501ae168c5e697d2315a))


### Bug Fixes

* keep live STT ahead of audio ([#362](https://github.com/charoiteai/Charoite_audio/issues/362)) ([1c304de](https://github.com/charoiteai/Charoite_audio/commit/1c304deddc811d12dc8734adc5e652ef67bcc230))
* **tests:** мутатор судил мутанта по байткоду соседа — запрет .pyc в дереве ([#368](https://github.com/charoiteai/Charoite_audio/issues/368)) ([aff65c8](https://github.com/charoiteai/Charoite_audio/commit/aff65c8afa15461988297b21d6e01f0149276f92))
* снимки графов вне iCloud (клоны APFS) + потолок ночного прогона ([#363](https://github.com/charoiteai/Charoite_audio/issues/363)) ([3424e41](https://github.com/charoiteai/Charoite_audio/commit/3424e41364033a217d83231fd7f3c45abfae7f89))

## [0.56.0](https://github.com/charoiteai/Charoite_audio/compare/v0.55.2...v0.56.0) (2026-08-20)


### Features

* **tests:** мутатор — ломает изменённые строки и требует, чтобы тесты упали ([#360](https://github.com/charoiteai/Charoite_audio/issues/360)) ([b9d9082](https://github.com/charoiteai/Charoite_audio/commit/b9d9082ed414154dd1f5bcfc84e07382688f7a5d))

## [0.55.2](https://github.com/charoiteai/Charoite_audio/compare/v0.55.1...v0.55.2) (2026-08-20)


### Bug Fixes

* **diarize:** в звонке владелец — весь микрофон, а не доминирующий голос ([#356](https://github.com/charoiteai/Charoite_audio/issues/356)) ([04c4396](https://github.com/charoiteai/Charoite_audio/commit/04c43964bd710e8cacf6286150c27ffe89638dd5))
* три Critical конвейера и потеря живого звука ([#355](https://github.com/charoiteai/Charoite_audio/issues/355)) ([3cfe6a3](https://github.com/charoiteai/Charoite_audio/commit/3cfe6a3eebdc8931916bfa56a15ff524ff8dba86))

## [0.55.1](https://github.com/charoiteai/Charoite_audio/compare/v0.55.0...v0.55.1) (2026-08-20)


### Bug Fixes

* **search:** китайский запрос не находил ничего — ни в бенче, ни в приложении ([#352](https://github.com/charoiteai/Charoite_audio/issues/352)) ([7c53007](https://github.com/charoiteai/Charoite_audio/commit/7c53007ac41b28b5e873010316aec744ae01a630))
* **stt:** поток умирал на первой реплике — имя heard затенило словарь автостопа ([#354](https://github.com/charoiteai/Charoite_audio/issues/354)) ([5dbd1bc](https://github.com/charoiteai/Charoite_audio/commit/5dbd1bc160bd95c2431f52bcb77eec655ac6a845))

## [0.55.0](https://github.com/charoiteai/Charoite_audio/compare/v0.54.0...v0.55.0) (2026-08-19)


### Features

* **diarize:** имя владельца по каналу захвата, без голосовой биометрии ([#349](https://github.com/charoiteai/Charoite_audio/issues/349)) ([b870c2d](https://github.com/charoiteai/Charoite_audio/commit/b870c2dc914f56f8fbd04db7095f4638a4908c96))
* **lifecycle:** подмашина остановки — у застревания появился выход ([#351](https://github.com/charoiteai/Charoite_audio/issues/351)) ([047cf43](https://github.com/charoiteai/Charoite_audio/commit/047cf43c64dab0d2f55f6be2aa5ea48626dd002b))

## [0.54.0](https://github.com/charoiteai/Charoite_audio/compare/v0.53.0...v0.54.0) (2026-08-19)


### Features

* **install:** профили под 8/16/32/64 ГБ по замерам, а не по правилу «30B» ([#345](https://github.com/charoiteai/Charoite_audio/issues/345)) ([ab988a7](https://github.com/charoiteai/Charoite_audio/commit/ab988a76146673b2e36166fc7cd6a867ac00477b))

## [0.53.0](https://github.com/charoiteai/Charoite_audio/compare/v0.52.2...v0.53.0) (2026-08-18)


### Features

* **record:** забытая запись останавливается сама — тишина и потолок длительности ([#342](https://github.com/charoiteai/Charoite_audio/issues/342)) ([98989ff](https://github.com/charoiteai/Charoite_audio/commit/98989ff092f0210c018feaddc4a1b5ccc699ff91))

## [0.52.2](https://github.com/charoiteai/Charoite_audio/compare/v0.52.1...v0.52.2) (2026-08-18)


### Bug Fixes

* **live:** подсказки переживают занятую модель — ретраи, гейт фона, статусы вместо ошибок в панели ([#339](https://github.com/charoiteai/Charoite_audio/issues/339)) ([6256807](https://github.com/charoiteai/Charoite_audio/commit/6256807c95b9041d88735ed93bdfacfb0ebc1f07))

## [0.52.1](https://github.com/charoiteai/Charoite_audio/compare/v0.52.0...v0.52.1) (2026-08-18)


### Bug Fixes

* **app:** байткод вложенного python — в кэш пользователя, не в подписанный бандл ([#338](https://github.com/charoiteai/Charoite_audio/issues/338)) ([ebac0dd](https://github.com/charoiteai/Charoite_audio/commit/ebac0dd9b0a43f88c36dda46cbd8da3fb532f2e6))
* **release:** имя владельца сертификата — маска в логах CI ([#336](https://github.com/charoiteai/Charoite_audio/issues/336)) ([04e29aa](https://github.com/charoiteai/Charoite_audio/commit/04e29aa23bb6eed6aee0e1555b92682a9724c990))

## [0.52.0](https://github.com/charoiteai/Charoite_audio/compare/v0.51.0...v0.52.0) (2026-08-18)


### Features

* **release:** Developer ID подпись, hardened runtime и нотаризация в CI ([#335](https://github.com/charoiteai/Charoite_audio/issues/335)) ([4de5d8b](https://github.com/charoiteai/Charoite_audio/commit/4de5d8b4a15575fdd1ea6e39fa3a0709f304240e))


### Bug Fixes

* **nightly:** партия A по аудиту ночного конвейера — граф без модели, tier3, досье, Opus-шаги, бриф, доставка ревизии ([#333](https://github.com/charoiteai/Charoite_audio/issues/333)) ([7e2fbd7](https://github.com/charoiteai/Charoite_audio/commit/7e2fbd7dfaf6a499eec0facae383f31fdd0ba409))

## [0.51.0](https://github.com/charoiteai/Charoite_audio/compare/v0.50.0...v0.51.0) (2026-08-17)


### Features

* **app:** record capsule with readiness on the Today screen ([#320](https://github.com/charoiteai/Charoite_audio/issues/320)) ([c67b01a](https://github.com/charoiteai/Charoite_audio/commit/c67b01a729fdee0d34c6693bd80eea3aa9728b83))
* **diarize:** positional live diarization — per-piece STT windows ([#317](https://github.com/charoiteai/Charoite_audio/issues/317)) ([0575471](https://github.com/charoiteai/Charoite_audio/commit/05754714cd881a53ec9fc204e570dc33d546c8be))
* **graph:** live cross-check of the meeting against graph nodes ([#318](https://github.com/charoiteai/Charoite_audio/issues/318)) ([a853715](https://github.com/charoiteai/Charoite_audio/commit/a853715e5b3810a54435c8d9936fca32aceff163))
* **llm:** второй движок mlx-server — кэш префикса для живой нити ([#314](https://github.com/charoiteai/Charoite_audio/issues/314)) ([8031cf5](https://github.com/charoiteai/Charoite_audio/commit/8031cf57019228e2a0c1a0908415355070dabb32))


### Bug Fixes

* **app:** audit 14.08 hotfixes — silent exit 75, name collisions, proxy, unwraps, l10n ([#321](https://github.com/charoiteai/Charoite_audio/issues/321)) ([aada229](https://github.com/charoiteai/Charoite_audio/commit/aada229076a7dafee53343b8100140628cfdbdb3))
* **app:** fail closed before replacing updater bundle ([#306](https://github.com/charoiteai/Charoite_audio/issues/306)) ([76130a8](https://github.com/charoiteai/Charoite_audio/commit/76130a8c7a07d78ef67a68c69ad2d4dc9c13102d))
* **app:** панель суфлёра стопкой вместо или-или, вычистка архивного контура ([#322](https://github.com/charoiteai/Charoite_audio/issues/322)) ([855c09b](https://github.com/charoiteai/Charoite_audio/commit/855c09b5840317ef1357a8940af7d1f8932f6583))
* **audit:** партия по аудиту DeepSeek 16.08 — граница штампа, права, allow_remote, апдейтер, пины CI ([#331](https://github.com/charoiteai/Charoite_audio/issues/331)) ([7a74573](https://github.com/charoiteai/Charoite_audio/commit/7a745739afaf64fed32d4f5ed804257eb197b5cb))
* **cloud:** изоляция headless-вызовов claude от инъекций из стенограмм ([#308](https://github.com/charoiteai/Charoite_audio/issues/308)) ([16338df](https://github.com/charoiteai/Charoite_audio/commit/16338dfefea593c84b2ee5206063edbe2a80074e))
* **cloud:** инструменты разбора встречи ограничены рабочим графом ([#332](https://github.com/charoiteai/Charoite_audio/issues/332)) ([6ba6a63](https://github.com/charoiteai/Charoite_audio/commit/6ba6a635e1f4d5f58f5ccfa30bc9c3b19632c260))
* **daemon:** ⚡ живёт после опознания имени, сироты пересобираются по одной ([#313](https://github.com/charoiteai/Charoite_audio/issues/313)) ([14bffe4](https://github.com/charoiteai/Charoite_audio/commit/14bffe408c223026a6b85bef1d992e5883b85530))
* **privacy:** данные встреч закрыты от других учёток, сырые потоки под ретеншн ([#324](https://github.com/charoiteai/Charoite_audio/issues/324)) ([d10686f](https://github.com/charoiteai/Charoite_audio/commit/d10686f4fb52230ac761d741a62078ad87254343))
* **privacy:** демон только из бандла, забытая встреча уходит из индекса ([#328](https://github.com/charoiteai/Charoite_audio/issues/328)) ([c114389](https://github.com/charoiteai/Charoite_audio/commit/c1143898013008577c2a2864bb6fb1ae9c6e0e9b))
* **privacy:** папка данных не подхватывается из записываемого клона — вторая дверь TCC ([#329](https://github.com/charoiteai/Charoite_audio/issues/329)) ([e83ade7](https://github.com/charoiteai/Charoite_audio/commit/e83ade775c8c1da965b6e327fa79fc9c3d116d99))
* **privacy:** хвосты второго мнения по [#324](https://github.com/charoiteai/Charoite_audio/issues/324)–[#328](https://github.com/charoiteai/Charoite_audio/issues/328) — рубильник, pip мимо lock, забывание, пустая сессия ([#330](https://github.com/charoiteai/Charoite_audio/issues/330)) ([b626d00](https://github.com/charoiteai/Charoite_audio/commit/b626d0078e8d8ff4406d96d56bf7a8994681f19b))
* **release:** sha256 у встроенного CPython, dependabot pip, честный swiftlint-хук ([#310](https://github.com/charoiteai/Charoite_audio/issues/310)) ([0193a9f](https://github.com/charoiteai/Charoite_audio/commit/0193a9f63e112647763375873a7cf4ef0ed4564e))
* **security:** право правки живёт только с бэкапом, документы говорят правду ([#316](https://github.com/charoiteai/Charoite_audio/issues/316)) ([283d2f1](https://github.com/charoiteai/Charoite_audio/commit/283d2f1383e62fbe38c0ae88d4f1de7a6d8cdbc4))
* **supply:** lock с хешами для бандла, суммы моделей, SHA-пины релизных workflow ([#325](https://github.com/charoiteai/Charoite_audio/issues/325)) ([4f095bf](https://github.com/charoiteai/Charoite_audio/commit/4f095bf51647eda102ae2f7a149f6349ab688d34))

## [0.50.0](https://github.com/charoiteai/Charoite_audio/compare/v0.49.0...v0.50.0) (2026-08-14)


### Features

* **app:** движок моделей ставится кнопкой, а не двумя командами в терминале ([#300](https://github.com/charoiteai/Charoite_audio/issues/300)) ([a1ac2e5](https://github.com/charoiteai/Charoite_audio/commit/a1ac2e5c5137c1be9da92d6816815ee709df2d7f))
* **bench:** сравнение моделей на разборе встречи, а не на скорости ([#296](https://github.com/charoiteai/Charoite_audio/issues/296)) ([2f54c75](https://github.com/charoiteai/Charoite_audio/commit/2f54c75e33eb612e5e7598ffd828c716e1bc0b6f))


### Bug Fixes

* **app:** keep stuck daemon lifecycle active ([#304](https://github.com/charoiteai/Charoite_audio/issues/304)) ([ad32348](https://github.com/charoiteai/Charoite_audio/commit/ad323487ee576d2b2c78a93abe6c642be7372c3d))
* **app:** serialize recording lifecycle and isolate capture files ([#301](https://github.com/charoiteai/Charoite_audio/issues/301)) ([4d0c1df](https://github.com/charoiteai/Charoite_audio/commit/4d0c1df08e84714cf9c17be74ab786fc7a0b8bb5))
* **bench:** ненастроенный бенч памяти — не провал, а подсказка ([#299](https://github.com/charoiteai/Charoite_audio/issues/299)) ([9d3193d](https://github.com/charoiteai/Charoite_audio/commit/9d3193d16eed4100b332534d974dad1de5b90fde))
* **ci:** ночные iOS-тесты падали не по делу — из-за языка раннера ([#294](https://github.com/charoiteai/Charoite_audio/issues/294)) ([5c8f7cb](https://github.com/charoiteai/Charoite_audio/commit/5c8f7cbc8d31db6be622d466a178165df1ed5af5))
* **diar:** короткие «да» и «угу» — один голос, а не восемь собеседников ([#303](https://github.com/charoiteai/Charoite_audio/issues/303)) ([3844cd5](https://github.com/charoiteai/Charoite_audio/commit/3844cd5fb414683a109a00dc68450b5f499d9f70))
* **diar:** порог склейки голосов измерен, а участники больше не дробятся ([#302](https://github.com/charoiteai/Charoite_audio/issues/302)) ([fcb7ec3](https://github.com/charoiteai/Charoite_audio/commit/fcb7ec3da0de25368c45721d0a2d728cdc19e504))
* **graph:** цитата ядра — непрерывный отрезок, а не склейка через многоточие ([#297](https://github.com/charoiteai/Charoite_audio/issues/297)) ([bea6f3b](https://github.com/charoiteai/Charoite_audio/commit/bea6f3b76f9d4af11a200aa0ead0c76512b7501c))

## [0.49.0](https://github.com/charoiteai/Charoite_audio/compare/v0.48.1...v0.49.0) (2026-08-13)


### Features

* **app:** встреча с неразобранными именами не выглядит готовой до конца ([#293](https://github.com/charoiteai/Charoite_audio/issues/293)) ([748d840](https://github.com/charoiteai/Charoite_audio/commit/748d8406b418f96b7bf883f8bf47058b8dd952bc))


### Bug Fixes

* **nightly:** бриф к утру, а ревизия ядер — по свежим ядрам ([#290](https://github.com/charoiteai/Charoite_audio/issues/290)) ([f9046ca](https://github.com/charoiteai/Charoite_audio/commit/f9046ca03a4c93e6f117967b8c17a51a34998302))
* **rebuild:** молчание модели на именах — не успешный прогон ([#292](https://github.com/charoiteai/Charoite_audio/issues/292)) ([d2736aa](https://github.com/charoiteai/Charoite_audio/commit/d2736aad9b46629a157c1c14ba3fcdf364a3a0c7))

## [0.48.1](https://github.com/charoiteai/Charoite_audio/compare/v0.48.0...v0.48.1) (2026-08-12)


### Bug Fixes

* **app:** ночь с молчащей моделью больше не считается успешной ([#282](https://github.com/charoiteai/Charoite_audio/issues/282)) ([b3c70fa](https://github.com/charoiteai/Charoite_audio/commit/b3c70fa0c6707cec3bf67da1123afb9414e9bd58))
* **nightly:** цикл ждёт разбор встречи и работает на одной модели ([#284](https://github.com/charoiteai/Charoite_audio/issues/284)) ([0defaf9](https://github.com/charoiteai/Charoite_audio/commit/0defaf9b27c5356e68f0969f26f72370f1b5e523))
* **rebuild:** пересборка встречи не запускается второй раз поверх идущей ([#285](https://github.com/charoiteai/Charoite_audio/issues/285)) ([42fe950](https://github.com/charoiteai/Charoite_audio/commit/42fe9504c8fbe356654d08e98621bd574303c592))

## [0.48.0](https://github.com/charoiteai/Charoite_audio/compare/v0.47.0...v0.48.0) (2026-08-12)


### Features

* **ci:** сторож conventional-заголовков PR ([#281](https://github.com/charoiteai/Charoite_audio/issues/281)) ([e894c6e](https://github.com/charoiteai/Charoite_audio/commit/e894c6eb6cd5e8e742e93d63df5d557646f3cf2d))


### Bug Fixes

* **app:** адрес LLM в приложении подчиняется privacy — P0-6 закрыт ([#273](https://github.com/charoiteai/Charoite_audio/issues/273)) ([5ad7074](https://github.com/charoiteai/Charoite_audio/commit/5ad70749806f97d26cca52c4749995a1a9fad3dc))
* **app:** готовность знает про «Запись экрана» и требует перезапуска — P0-10 ([#276](https://github.com/charoiteai/Charoite_audio/issues/276)) ([d108605](https://github.com/charoiteai/Charoite_audio/commit/d108605084f6ee11530d87688850dcb52a16753a))
* **app:** мастер первого запуска создаёт config.yaml и не врёт «Сохранено» — P0-7 ([#275](https://github.com/charoiteai/Charoite_audio/issues/275)) ([4c90e31](https://github.com/charoiteai/Charoite_audio/commit/4c90e317ee050e5226f9394644a106b43cc00620))
* **app:** остановка захвата не стирает файлы следующей встречи — P0-5 ([#277](https://github.com/charoiteai/Charoite_audio/issues/277)) ([e236f58](https://github.com/charoiteai/Charoite_audio/commit/e236f58a08fd8e9c550c7231cde165720b0d54a5))
* **ios:** прерывание без `.ended` больше не вечное — P0-9 ([#278](https://github.com/charoiteai/Charoite_audio/issues/278)) ([3be0234](https://github.com/charoiteai/Charoite_audio/commit/3be02346ffb88dfc8e6c2d9538bcbfa967c3c9a8))

## [0.47.0](https://github.com/charoiteai/Charoite_audio/compare/v0.46.0...v0.47.0) (2026-08-11)


### Features

* **app:** native macOS integration + import pipeline fixes ([#251](https://github.com/charoiteai/Charoite_audio/issues/251)) ([c82c4c4](https://github.com/charoiteai/Charoite_audio/commit/c82c4c430fa495bac68b8792e619975bafffdd06))
* **app:** python-контур внутри бандла — установка без терминала ([#261](https://github.com/charoiteai/Charoite_audio/issues/261)) ([4d829a9](https://github.com/charoiteai/Charoite_audio/commit/4d829a963ba7758ded5d09505ab59a02a4d7b2ed))
* **app:** код демона в бандле + документация приведена к реальности ([#264](https://github.com/charoiteai/Charoite_audio/issues/264)) ([95a0b2b](https://github.com/charoiteai/Charoite_audio/commit/95a0b2bd55d5e00574dcd4c16a6a917355ccb302))
* **app:** наборы моделей под память машины — выбор в мастере ([#263](https://github.com/charoiteai/Charoite_audio/issues/263)) ([9cbbf18](https://github.com/charoiteai/Charoite_audio/commit/9cbbf18a128c87801ae7d90376a9382bed296c30))
* **app:** подробный протокол в карточке встречи и задачи по дате встречи ([#247](https://github.com/charoiteai/Charoite_audio/issues/247)) ([6380b3a](https://github.com/charoiteai/Charoite_audio/commit/6380b3a1b7b0bc85d177c17ad30061878541c0b1))
* **app:** срок поручения чипом на экране задач ([#266](https://github.com/charoiteai/Charoite_audio/issues/266)) ([99a5d17](https://github.com/charoiteai/Charoite_audio/commit/99a5d17ded07b31f2f40410f5e0aeed50415e265))
* **app:** экран «Календарь» — события дня с отметками записанных встреч ([#240](https://github.com/charoiteai/Charoite_audio/issues/240)) ([3c94bf2](https://github.com/charoiteai/Charoite_audio/commit/3c94bf266c4189feebb5682de79d9ae5fe6b61ab))
* **audio:** Core Audio tap infrastructure (disabled), orphan cleanup, config ladder ([#248](https://github.com/charoiteai/Charoite_audio/issues/248)) ([06061d6](https://github.com/charoiteai/Charoite_audio/commit/06061d6ad3c83b3a880071f1cbf1f6d2ae98b92a))
* **audio:** системный звук из коробки через ScreenCaptureKit ([#260](https://github.com/charoiteai/Charoite_audio/issues/260)) ([a853b12](https://github.com/charoiteai/Charoite_audio/commit/a853b12616c280e489464d35aab717f783679c31))
* **stt:** SenseVoice — китайское распознавание без Whisper ([#268](https://github.com/charoiteai/Charoite_audio/issues/268)) ([4d94bbf](https://github.com/charoiteai/Charoite_audio/commit/4d94bbf80611d3fdc26a49028acda1b5e792938c))
* **sufler:** имя спикера при смене голоса; облако правит нить, а не комментирует ([#244](https://github.com/charoiteai/Charoite_audio/issues/244)) ([55c78d5](https://github.com/charoiteai/Charoite_audio/commit/55c78d58a9503329e69bd1fefad9d8726ee8428a))
* саммари, карточка и телефон говорят на всех трёх языках ([#267](https://github.com/charoiteai/Charoite_audio/issues/267)) ([c24533c](https://github.com/charoiteai/Charoite_audio/commit/c24533c6d38ffec03f608eea60d9d9a6f800133e))


### Bug Fixes

* **app:** meeting thread stays on screen after Stop ([#255](https://github.com/charoiteai/Charoite_audio/issues/255)) ([9868df4](https://github.com/charoiteai/Charoite_audio/commit/9868df4a3659541f65a5293c6c5745a2366d2c5c))
* **app:** второе окно рисовалось криво — ширина колонки сайдбара не задана ([#262](https://github.com/charoiteai/Charoite_audio/issues/262)) ([1768f82](https://github.com/charoiteai/Charoite_audio/commit/1768f82b8fd32bc25078866cd88381dc6821f4ef))
* **audio:** disable tap by default — its lifecycle wedges CoreAudio on 26.5 ([#256](https://github.com/charoiteai/Charoite_audio/issues/256)) ([6fcabbe](https://github.com/charoiteai/Charoite_audio/commit/6fcabbe201679c457fdd884fdd71799a2d97ca98))
* **audio:** tap stream survives odd reads and resumes after restart ([#252](https://github.com/charoiteai/Charoite_audio/issues/252)) ([9d632e7](https://github.com/charoiteai/Charoite_audio/commit/9d632e749f464f1eab96d87d1953fda954064419))
* **audio:** конвейер переживает мёртвый канал — не встаёт вместе с ним ([#249](https://github.com/charoiteai/Charoite_audio/issues/249)) ([f918a2a](https://github.com/charoiteai/Charoite_audio/commit/f918a2a6d09e248783ebf79d1c6ae0f6b0554639))
* **design:** buttons never shrink; meeting toolbar scrolls instead of hiding ([#258](https://github.com/charoiteai/Charoite_audio/issues/258)) ([6a56a8f](https://github.com/charoiteai/Charoite_audio/commit/6a56a8ffecf70fe3525446aa46b34a6a4c0b62c0))
* **ios:** a call is a pause, not a failure — keep the file, resume after ([#253](https://github.com/charoiteai/Charoite_audio/issues/253)) ([745c756](https://github.com/charoiteai/Charoite_audio/commit/745c75619c65fc2ebf3356d4cfb819aba7eafb0f))
* **ios:** reject non-iCloud delivery folder, forget local bookmarks ([#250](https://github.com/charoiteai/Charoite_audio/issues/250)) ([d9adf45](https://github.com/charoiteai/Charoite_audio/commit/d9adf457b67b3fb2b6e52dfc042953e24ba53866))
* **sufler:** подсказка — имя при передаче слова, без отчётных глаголов ([#245](https://github.com/charoiteai/Charoite_audio/issues/245)) ([2d42788](https://github.com/charoiteai/Charoite_audio/commit/2d427886d7406d54a4fe9434d2eddf81f0768e58))
* аудит 0.46.0 — встреча переживает аварию, ретеншн мирится с восстановлением ([#269](https://github.com/charoiteai/Charoite_audio/issues/269)) ([a28ac5d](https://github.com/charoiteai/Charoite_audio/commit/a28ac5d23d2e741e11364e030cf3b5874de1eb36))
* целостность пересборки, privacy и календаря ([#246](https://github.com/charoiteai/Charoite_audio/issues/246)) ([580625d](https://github.com/charoiteai/Charoite_audio/commit/580625d83343c1fbef4934be64bff6a1c1057523))

## [0.46.0](https://github.com/charoiteai/Charoite_audio/compare/v0.45.0...v0.46.0) (2026-08-04)


### Features

* **macOS:** связать поручения с их встречами ([#236](https://github.com/charoiteai/Charoite_audio/issues/236)) ([4aef81b](https://github.com/charoiteai/Charoite_audio/commit/4aef81bce32b268b3dd268066a2eabb1e99e8d38))


### Bug Fixes

* аудит 0.45.0 — ключ API, privacy-обходы, договор об имени встречи ([#237](https://github.com/charoiteai/Charoite_audio/issues/237)) ([1435e2e](https://github.com/charoiteai/Charoite_audio/commit/1435e2ebb8c0cbee6664abcfb3d40b6cfcbd3e45))
* рабочий стол после живого прогона — сайдбар, карточка, протокол ([#234](https://github.com/charoiteai/Charoite_audio/issues/234)) ([a0c7d32](https://github.com/charoiteai/Charoite_audio/commit/a0c7d329687d1defaeae39d6635386a50b423763))

## [0.45.0](https://github.com/charoiteai/Charoite_audio/compare/v0.44.0...v0.45.0) (2026-08-04)


### Features

* **sufler:** одно полотно вместо трёх панелей — нить, и в ней всё ([#232](https://github.com/charoiteai/Charoite_audio/issues/232)) ([33b352e](https://github.com/charoiteai/Charoite_audio/commit/33b352e50be80c4c203218112c0682571358f86b))
* единый рабочий стол Charoite и переносимые карточки встреч ([#230](https://github.com/charoiteai/Charoite_audio/issues/230)) ([71d7989](https://github.com/charoiteai/Charoite_audio/commit/71d79893e9049e12b2e965f2135e26698223b3bd))

## [0.44.0](https://github.com/charoiteai/Charoite_audio/compare/v0.43.0...v0.44.0) (2026-08-03)


### Features

* **app:** кнопка «Повторить обработку» — ошибка встречи больше не тупик ([#201](https://github.com/charoiteai/Charoite_audio/issues/201)) ([a6fb8a6](https://github.com/charoiteai/Charoite_audio/commit/a6fb8a6236b9742643375af991d5322a74ef05cd))
* **app:** список последних встреч — состояние, результат и повтор по каждой ([#204](https://github.com/charoiteai/Charoite_audio/issues/204)) ([89285d8](https://github.com/charoiteai/Charoite_audio/commit/89285d83fcec7cbd6fcb3a720c75dfb49565ebad))
* **app:** таймер записи и состояния встречи в меню-баре ([#203](https://github.com/charoiteai/Charoite_audio/issues/203)) ([379ab50](https://github.com/charoiteai/Charoite_audio/commit/379ab505fdedd8f1db0e3e89c7cff307515e6edb))
* **ios:** очередь как экран; архив — одна встреча, одна папка ([#213](https://github.com/charoiteai/Charoite_audio/issues/213)) ([dd4c118](https://github.com/charoiteai/Charoite_audio/commit/dd4c118ae6469979bbc3a4b5e6c65ce74ec0b14c))
* **macOS:** карточка встречи — результат в приложении, не в файле ([#220](https://github.com/charoiteai/Charoite_audio/issues/220)) ([fedf4cc](https://github.com/charoiteai/Charoite_audio/commit/fedf4cc8a7480dccaeec8057aeb4543e414e0149))
* **sufler:** нить встречи — растёт, а не пересобирается ([#218](https://github.com/charoiteai/Charoite_audio/issues/218)) ([d27a4ee](https://github.com/charoiteai/Charoite_audio/commit/d27a4eeb2f787df258baf6fd29cc78fc7e6c66e4))
* **sufler:** подсказка ведёт нить разговора, а не отвечает за вас ([#216](https://github.com/charoiteai/Charoite_audio/issues/216)) ([2cbcf84](https://github.com/charoiteai/Charoite_audio/commit/2cbcf84cf74a0738fef37834cf4eb2fd72475431))
* честное время обработки, прогресс по частям и doctor про работу ([#212](https://github.com/charoiteai/Charoite_audio/issues/212)) ([a00a281](https://github.com/charoiteai/Charoite_audio/commit/a00a28192fed96aec98be78caebbea6c3c70b975))


### Bug Fixes

* **app:** «Повторить обработку» — три гонки, из-за которых мог стартовать второй конвейер ([#202](https://github.com/charoiteai/Charoite_audio/issues/202)) ([0fac660](https://github.com/charoiteai/Charoite_audio/commit/0fac660c20741954d09a0d6b2331c8be9fe24a1e))
* **archive:** решения в саммари берём из минуток, а не ищем заново ([#215](https://github.com/charoiteai/Charoite_audio/issues/215)) ([a52eeda](https://github.com/charoiteai/Charoite_audio/commit/a52eedaa4dfcda849d9474e5b5013bc8c1bb6c72))
* **archive:** саммари теряло решения встречи, а потом и поручения ([#214](https://github.com/charoiteai/Charoite_audio/issues/214)) ([93724cb](https://github.com/charoiteai/Charoite_audio/commit/93724cb52d9e83d25191524d0fa46effc3d7d222))
* **graph:** модель выбирает проект из существующих, а не придумывает ([#209](https://github.com/charoiteai/Charoite_audio/issues/209)) ([d62206e](https://github.com/charoiteai/Charoite_audio/commit/d62206edc24e79f8363ce21adeae2436fc912fe8))
* **graph:** разбор переживает вставшую модель и догоняет упавшие встречи ([#208](https://github.com/charoiteai/Charoite_audio/issues/208)) ([4e2729b](https://github.com/charoiteai/Charoite_audio/commit/4e2729b35edc9c8499b343c35dd15749fe85ffe4))
* **ios:** «Поделиться записью» переживает доставку ([#207](https://github.com/charoiteai/Charoite_audio/issues/207)) ([a8d0602](https://github.com/charoiteai/Charoite_audio/commit/a8d0602a0777390e4ee68d85fff5483ba5475956))
* **ios:** сторож вставшей записи, кнопка «Поделиться» и файловый доступ ([#205](https://github.com/charoiteai/Charoite_audio/issues/205)) ([23168a7](https://github.com/charoiteai/Charoite_audio/commit/23168a7016aba949c7adf78ec2adbf1b903ff3d5))
* **ios:** файловый доступ к очереди записей — ключи в project.yml ([#206](https://github.com/charoiteai/Charoite_audio/issues/206)) ([d1d4228](https://github.com/charoiteai/Charoite_audio/commit/d1d4228fa4d447014c22eed6984fa5ad84d76282))
* **llm:** перезапускаем того, кто держит порт, а не что установлено ([#210](https://github.com/charoiteai/Charoite_audio/issues/210)) ([a5438ff](https://github.com/charoiteai/Charoite_audio/commit/a5438ffe9e0b9c0a48567ef7eceed12372fb5fff))
* **macOS:** полировка списка встреч по разбору ([#219](https://github.com/charoiteai/Charoite_audio/issues/219)) ([ce4f380](https://github.com/charoiteai/Charoite_audio/commit/ce4f380eadc01908ec6948732773b4dfc517d4c9))
* **privacy:** llm.base_url через privacy, полные границы правки графа, забывание доходит до .cloud_backup ([#199](https://github.com/charoiteai/Charoite_audio/issues/199)) ([56e08ff](https://github.com/charoiteai/Charoite_audio/commit/56e08ff4d96aa9d727ba28451b6353bf4bb9ba11))
* **rename:** главный файл без темы получает её при переименовании ([#221](https://github.com/charoiteai/Charoite_audio/issues/221)) ([fedd433](https://github.com/charoiteai/Charoite_audio/commit/fedd433ea61802db8f7193b5329310844669e2c1))
* **ui:** находки живого прохода по приложениям ([#211](https://github.com/charoiteai/Charoite_audio/issues/211)) ([04a1b26](https://github.com/charoiteai/Charoite_audio/commit/04a1b2653fb486e7b83777e45bbd18ef3760a507))

## [0.43.0](https://github.com/charoiteai/Charoite_audio/compare/v0.42.0...v0.43.0) (2026-07-31)


### Features

* **macOS:** проверить готовность до первой встречи ([#197](https://github.com/charoiteai/Charoite_audio/issues/197)) ([162c4a5](https://github.com/charoiteai/Charoite_audio/commit/162c4a554d522bc999a67cad876c500da15e76ff))

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
