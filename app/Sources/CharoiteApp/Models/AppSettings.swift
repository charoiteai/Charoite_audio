import Foundation

/// Настройки приложения — всё локальное, персист в UserDefaults.
///
/// Никаких удалённых серверов по умолчанию: Ollama на этой машине, демон
/// суфлёра — из папки установки Charoite_audio, граф — из config.yaml суфлёра.
enum AppSettings {
    /// Папка данных: config/, transcripts/, recordings/, models/, logs/.
    ///
    /// Порядок: путь, выбранный человеком в Настройках → без вложенного кода
    /// (запуск из клона) — сам клон в домашней папке → при вложенном коде —
    /// рабочая папка приложения в Application Support.
    ///
    /// Клон `~/Charoite_audio` при вложенном коде больше НЕ подхватывается
    /// сам по наличию `src/daemon.py`. Папка данных — это ещё и
    /// `config/config.yaml` с `sufler.post_meeting_hook` (команда, которую
    /// демон запускает после каждой встречи через shell) и `models/`. Пока
    /// корень данных брался по первому попавшемуся клону, любой процесс без
    /// TCC-прав подкладывал пустой `~/Charoite_audio/src/daemon.py` и свой
    /// `config.yaml` — и подписанное приложение выполняло его команду после
    /// первой же встречи со своими правами на микрофон и экран. #328 закрыл
    /// эту дверь для кода, а для данных она оставалась (второе мнение по
    /// #328, 16.08). Тот, у кого данные в клоне, выбирает его явно — см.
    /// `legacyCloneAwaitsChoice` и предложение на экране «Сегодня».
    static var charoiteRoot: URL {
        if let chosen = explicitRoot { return chosen }
        return codeIsEmbedded ? workspaceRoot : legacyCloneRoot
    }

    /// Папка, выбранная человеком в Настройках (`charoite.root`); пусто — не выбрана.
    static var explicitRoot: URL? {
        guard let s = UserDefaults.standard.string(forKey: "charoite.root"), !s.isEmpty else { return nil }
        return URL(fileURLWithPath: (s as NSString).expandingTildeInPath)
    }

    /// Клон репозитория в домашней папке — установка через терминал.
    static var legacyCloneRoot: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Charoite_audio")
    }

    /// В клоне есть данные или код: раньше приложение брало такую папку само.
    static var legacyCloneLooksUsed: Bool {
        let fm = FileManager.default
        return fm.fileExists(atPath: legacyCloneRoot.appendingPathComponent("config/config.yaml").path)
            || fm.fileExists(atPath: legacyCloneRoot.appendingPathComponent("src/daemon.py").path)
    }

    /// Откуда берётся папка данных — чистой функцией ради тестов и текста в интерфейсе.
    enum DataSource: Equatable {
        case chosenByHuman     // путь из Настроек
        case workspace         // Application Support: код в бандле, данные — человеку
        case legacyClone       // запуск из клона: данные рядом с кодом
    }

    static func dataSource(embedded: Bool, explicitRoot: Bool) -> DataSource {
        if explicitRoot { return .chosenByHuman }
        return embedded ? .workspace : .legacyClone
    }

    /// Клон в домашней папке выглядит рабочим, но при вложенном коде сам не
    /// берётся — решить должен человек, один раз и видимо.
    static func legacyCloneAwaitsChoice(embedded: Bool, explicitRoot: Bool,
                                        cloneLooksUsed: Bool) -> Bool {
        embedded && !explicitRoot && cloneLooksUsed
    }

    static var legacyCloneAwaitsChoice: Bool {
        legacyCloneAwaitsChoice(embedded: codeIsEmbedded, explicitRoot: explicitRoot != nil,
                                cloneLooksUsed: legacyCloneLooksUsed)
    }

    /// Разрешение запускать код демона из явно выбранной папки (разработка).
    ///
    /// Ключ `charoite.codeFromRoot`. Отсутствие ключа — прежний договор
    /// (#328): явный путь с `src/daemon.py` = код оттуда, это видно в
    /// Настройках. Предложение миграции папки данных пишет `false` явно:
    /// человек выбирает, ГДЕ ДАННЫЕ, а не чей код исполнять с правами
    /// приложения — эти два решения не должны склеиваться.
    static var codeFromRootAllowed: Bool {
        let d = UserDefaults.standard
        return d.object(forKey: "charoite.codeFromRoot") == nil ? true : d.bool(forKey: "charoite.codeFromRoot")
    }

    /// Человек согласился взять клон как папку данных: путь пишем явно, а
    /// разрешение на код из него — явно НЕ даём (см. `codeFromRootAllowed`).
    static func adoptLegacyCloneAsDataRoot() {
        let d = UserDefaults.standard
        d.set(false, forKey: "charoite.codeFromRoot")
        d.set("~/Charoite_audio", forKey: "charoite.root")
    }

    /// Код демона, вложенный в бандл: `Resources/charoite/src/daemon.py`.
    ///
    /// Без него вложенный python бесполезен — демон запускается как
    /// `src/daemon.py`, и папку репозитория пришлось бы клонировать, то есть
    /// «установка без терминала» осталась бы обещанием.
    static var embeddedCodeRoot: URL {
        Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/charoite")
    }

    static var codeIsEmbedded: Bool {
        FileManager.default.fileExists(
            atPath: embeddedCodeRoot.appendingPathComponent("src/daemon.py").path)
    }

    /// Куда писать данные, когда код лежит в подписанном бандле.
    ///
    /// Бандл доступен только на чтение, а записи, стенограммы, логи и модели
    /// принадлежат человеку. Application Support — штатное место для такого
    /// на macOS, и оно переживает переустановку приложения.
    static var workspaceRoot: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                            in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support")
        let root = base.appendingPathComponent("Charoite", isDirectory: true)
        FileManager.default.createPrivateDirectory(at: root)
        return root
    }

    /// Откуда запускать код: вложенный в бандл, если рядом с данными его нет.
    ///
    /// Молчаливого предпочтения записываемой копии здесь больше нет.
    /// Приложение подписано и держит выданные ему права на микрофон и запись
    /// экрана; демон наследует их, запускаясь дочерним процессом. Пока код
    /// брался из первой попавшейся папки с `src/daemon.py`, любой процесс без
    /// TCC-прав мог подложить `~/Charoite_audio/src/daemon.py` — и Charoite
    /// выполнил бы его со своими разрешениями (аудит 16.08). Комментарий у
    /// запуска демона всё это время обещал обратное: «бандл подписан и
    /// доступен только на чтение».
    ///
    /// Правило: есть вложенный код — идём из бандла. Локальный код нужен
    /// разработчику, и это остаётся возможным: путь, ЯВНО выбранный
    /// человеком в Настройках (`charoite.root`), уважается — но он виден
    /// в интерфейсе, а не подхватывается сам.
    static func codeRoot(dataRoot: URL? = nil) -> URL {
        let data = dataRoot ?? charoiteRoot
        let localCode = data.appendingPathComponent("src/daemon.py").path
        let source = codeSource(embedded: codeIsEmbedded, explicitRoot: explicitRoot != nil,
                                localCodeExists: FileManager.default.fileExists(atPath: localCode),
                                codeFromRoot: codeFromRootAllowed)
        switch source {
        case .embedded: return embeddedCodeRoot
        case .chosenByHuman, .besideData: return data
        }
    }

    /// Решение о корне кода без обращения к диску и настройкам — для тестов
    /// и для внятного объяснения в интерфейсе.
    enum CodeSource: Equatable {
        case embedded          // подписанный бандл, только на чтение
        case chosenByHuman     // путь из Настроек: разработческая установка
        case besideData        // код лежит рядом с данными (клон репозитория)
    }

    static func codeSource(embedded: Bool, explicitRoot: Bool,
                           localCodeExists: Bool, codeFromRoot: Bool = true) -> CodeSource {
        if embedded && !explicitRoot { return .embedded }
        // явная папка данных ещё не разрешение исполнять её код: тумблер отдельный
        if embedded && !codeFromRoot { return .embedded }
        if localCodeExists { return explicitRoot ? .chosenByHuman : .besideData }
        return embedded ? .embedded : .besideData
    }

    static var codeRoot: URL { codeRoot(dataRoot: nil) }

    /// Подготовить процесс python: интерпретатор, рабочий каталог кода и
    /// корень данных в окружении.
    ///
    /// Собрано в одну функцию намеренно: штатные запуски python раньше
    /// сами склеивали путь — они разъезжались при первой же правке.
    /// С разведёнными корнями цена расхождения выше: процесс, запущенный из
    /// бандла без CHAROITE_ROOT, попытается писать в подписанную папку.
    /// `executable` нужен контурам, где готовая команда оборачивает python
    /// (`/usr/bin/nice`) или была собрана до перехода в detached-задачу.
    static func preparePython(
        _ process: Process,
        root: URL? = nil,
        executable: URL? = nil
    ) {
        let dataRoot = root ?? charoiteRoot
        process.executableURL = executable ?? pythonExecutable(root: dataRoot)
        process.currentDirectoryURL = codeRoot(dataRoot: dataRoot)
        var env = ProcessInfo.processInfo.environment
        env["CHAROITE_ROOT"] = dataRoot.path
        process.environment = env
    }

    /// Путь к скрипту поставки — он лежит рядом с кодом, а не с данными.
    static func scriptPath(_ relative: String, root: URL? = nil) -> String {
        codeRoot(dataRoot: root).appendingPathComponent(relative).path
    }

    static let defaultOllamaURL = "http://localhost:11434"

    /// Решение по адресу LLM-сервера: тот же договор, что у `privacy.py`.
    ///
    /// Приложение — второй путь наружу, которого питоновские правила не
    /// видели. Поле в настройках принимало любой адрес с хостом, и на него
    /// уходили чанки семантического индекса (весь архив встреч) и живая
    /// стенограмма — при том, что демон на этом же конфиге отказывается
    /// работать через `privacy.llm_base_url`. Обещание «ничего не уходит с
    /// этой машины» держал один слой из двух (аудиты 0.45.0 P1-5,
    /// 0.46.0 P0-6).
    ///
    /// Правило повторяет питон дословно: loopback проходит всегда,
    /// остальное — только при явном `llm.allow_remote: true` в
    /// `config/config.yaml`, и никогда под рубильником `CHAROITE_NO_CLOUD`
    /// / `SUFLER_NO_CLOUD`. Молчание конфига — это «нет».
    enum RemoteHostDecision: Equatable {
        /// Адрес разрешён — им и пользуемся.
        case allowed(String)
        /// Адрес отвергнут: работаем локально, но человек должен это увидеть.
        /// В параметрах — сам адрес и причина, чтобы показать их в настройках.
        case rejected(url: String, reason: String)
    }

    static var ollamaURLDecision: RemoteHostDecision {
        let raw = (UserDefaults.standard.string(forKey: "charoite.ollama") ?? "")
            .trimmingCharacters(in: .whitespaces)
        guard let u = URL(string: raw), let host = u.host, !host.isEmpty else {
            return .allowed(defaultOllamaURL)      // поле пустое или мусор — дефолт
        }
        let url = raw.hasSuffix("/") ? String(raw.dropLast()) : raw
        if isLoopbackHost(host) { return .allowed(url) }

        let env = ProcessInfo.processInfo.environment
        for key in ["CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD"] where !(env[key] ?? "").isEmpty {
            return .rejected(
                url: url,
                reason: "адрес указывает не на эту машину, а рубильник \(key) "
                      + "запрещает любой выход наружу")
        }
        if configFlag("allow_remote") {
            return .allowed(url)
        }
        return .rejected(
            url: url,
            reason: "адрес указывает не на эту машину. Чароит локальный по "
                  + "умолчанию: чтобы обращаться к другому хосту, поставьте в "
                  + "config/config.yaml явное llm.allow_remote: true")
    }

    /// Адрес, по которому реально ходим. Отвергнутый адрес не подменяем молча —
    /// причина доступна через `ollamaURLRejection` и показывается в настройках.
    static var ollamaURL: String {
        switch ollamaURLDecision {
        case .allowed(let url): return url
        case .rejected: return defaultOllamaURL
        }
    }

    /// Непустое, если адрес из настроек отвергнут: (адрес, причина).
    static var ollamaURLRejection: (url: String, reason: String)? {
        if case .rejected(let url, let reason) = ollamaURLDecision {
            return (url, reason)
        }
        return nil
    }

    /// Указывает ли хост на эту машину: имена + разбор IP, всё остальное
    /// (имя машины, .local, домен) — не сюда.
    ///
    /// Строже питона ровно в одном месте, и это осознанно (аудит 16.08):
    /// `privacy._is_loopback` отвергает `0.0.0.0`, потому что
    /// `ipaddress` не считает его петлёй, — а как адрес НАЗНАЧЕНИЯ он
    /// ведёт на эту же машину, и запрос никуда не уходит. Здесь он
    /// разрешён; расхождение не про приватность, а про строгость разбора,
    /// и раньше комментарий обещал дословное повторение питона — врал.
    static func isLoopbackHost(_ host: String) -> Bool {
        let h = host.lowercased()
        if ["localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"].contains(h) { return true }
        if h.hasPrefix("127.") {
            let parts = h.split(separator: ".")
            return parts.count == 4 && parts.allSatisfy { UInt8($0) != nil }
        }
        return false
    }

    /// Папка графа Obsidian — читается из config/config.yaml суфлёра
    /// (sufler.graph_dir), чтобы не настраивать одно и то же дважды.
    /// CHAROITE_GRAPH_DIR или SUFLER_GRAPH_DIR перекрывает конфиг (скрины и
    /// тесты на демо-графе). Оба имени — с тем же приоритетом, что у Python
    /// (`src/graphs.py`): демон получает окружение приложения, и одно имя на
    /// одной стороне давало бы UI на одном графе, а запись — на другом.
    static let graphDirEnvNames = ["CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR"]

    static var graphDir: URL? {
        for name in graphDirEnvNames {
            if let env = ProcessInfo.processInfo.environment[name],
               !env.trimmingCharacters(in: .whitespaces).isEmpty {
                return resolvePath(env, relativeTo: charoiteRoot)
            }
        }
        if let v = configValue("graph_dir") {
            return resolvePath(v, relativeTo: charoiteRoot)
        }
        return nil
    }

    /// Относительный путь в config.yaml считается от корня установки — так же,
    /// как его видит Python-демон с `currentDirectoryURL = charoiteRoot`.
    /// Иначе документированный `graph_dir: demo/graph` работал в демоне, но
    /// приложение искало граф относительно случайной текущей папки .app.
    static func resolvePath(_ raw: String, relativeTo root: URL) -> URL {
        let expanded = (raw as NSString).expandingTildeInPath
        if (expanded as NSString).isAbsolutePath {
            return URL(fileURLWithPath: expanded)
        }
        return root.appendingPathComponent(expanded).standardizedFileURL
    }

    /// Язык интерфейса: та же настройка, что у документов встреч
    /// (sufler.language: ru|en|zh) — продукт переключается одним ключом,
    /// а не системной локалью. CHAROITE_UI_LANG перекрывает (скрины/тесты).
    static var uiLanguage: String {
        if let env = ProcessInfo.processInfo.environment["CHAROITE_UI_LANG"],
           ["ru", "en", "zh"].contains(env) { return env }
        if let v = configValue("language"), ["ru", "en", "zh"].contains(v) { return v }
        return "ru"
    }

    /// Лёгкий разбор одной строки config.yaml, без YAML-зависимости.
    /// Ключ ищется по всему файлу (stt.language и sufler.language совпадают
    /// по имени — берём последнее вхождение: sufler-секция ниже stt).
    /// Спрашивать ли GitHub о последнем выпуске.
    ///
    /// Единственный исходящий запрос, который делает приложение само:
    /// публичный GET за номером версии, без токена и без данных о человеке.
    /// Выключается `sufler.check_updates: false` в конфиге и общим рубильником
    /// `CHAROITE_NO_CLOUD` — тем же, что запрещает облачные шаги: кто выключил
    /// облако целиком, не ждёт от приложения похода в сеть за версией.
    static var checkUpdates: Bool {
        if cloudForbiddenByEnvironment { return false }
        return configValue("check_updates")?.lowercased() != "false"
    }

    /// Общий рубильник облака из окружения: экран настроек показывает его
    /// состояние рядом с тумблером проверки версии, а не притворяется, что
    /// тумблер что-то решает, когда решение уже принято снаружи.
    static var cloudForbiddenByEnvironment: Bool {
        // Непустое значение, как у демона (src/privacy.py): `CHAROITE_NO_CLOUD=`
        // там облако не запрещает, и приложение не должно спорить с ним.
        let env = ProcessInfo.processInfo.environment
        return ["CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD"]
            .contains { !(env[$0] ?? "").isEmpty }
    }

    static func configValue(_ key: String) -> String? {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return nil }
        return parseValue(key, in: text)
    }

    /// Интерпретатор, которым запускается python-контур.
    ///
    /// Порядок осознанный:
    /// 1. **Вложенный в бандл** (`Contents/Resources/python`) — с ним
    ///    установка не начинается с терминала: ни git clone, ни venv, ни pip.
    ///    Переносимая сборка CPython, независимая от Homebrew.
    /// 2. **`.venv` рядом с репозиторием** — как было раньше. Разработчик и
    ///    тот, кто ставил руками, не должны ничего замечать.
    ///
    /// Возвращаем путь, а не факт наличия: вызывающих десять штук, и каждый
    /// раньше собирал `.venv/bin/python` сам — расходились бы по одному.
    /// `root` передают те вызовы, которые уже принимают корень параметром —
    /// им подменяют путь тесты. Игнорировать его значило бы сделать их
    /// непроверяемыми: поймано тестами при переходе на вложенный контур.
    static func pythonExecutable(root: URL? = nil) -> URL {
        let embedded = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/python/bin/python3")
        if FileManager.default.isExecutableFile(atPath: embedded.path) { return embedded }
        return (root ?? charoiteRoot).appendingPathComponent(".venv/bin/python")
    }

    static var pythonExecutable: URL { pythonExecutable(root: nil) }

    /// Контур взят из бандла — то есть терминал при установке не понадобился.
    static var pythonIsEmbedded: Bool {
        pythonExecutable.path.contains("Contents/Resources/python")
    }

    /// Записать значение в `config/config.yaml` — без YAML-зависимости.
    ///
    /// До этого приложение конфиг только читало, и два обязательных поля —
    /// имя владельца и папка графа — человек правил в текстовом редакторе.
    /// Для «поставил и работает» это лишний шаг: те же два поля спрашивает
    /// мастер первого запуска.
    ///
    /// Возвращает false, если файла нет и создать его не удалось: молчаливый
    /// отказ здесь означал бы «настроил, а ничего не изменилось».
    ///
    /// Две поправки после аудита 0.46.0 (P0-7), из-за которых на чистой
    /// машине не работало вообще ничего:
    ///
    /// 1. **Образец искали в папке ДАННЫХ.** В бандловой установке он лежит
    ///    в поставке (`Contents/Resources/charoite/config/`), то есть в
    ///    корне КОДА — у нового пользователя в папке данных нет ни его, ни
    ///    самого конфига. Копирование падало, мастер молча ничего не
    ///    сохранял. Теперь смотрим оба корня: сначала данные (ручная
    ///    установка), потом код (бандл).
    /// 2. **Каталог `config/` никто не создавал.** `copyItem` в
    ///    несуществующую папку — ошибка, и на свежей установке она случалась
    ///    всегда, потому что папку данных до первого запуска никто не
    ///    размечает.
    /// Несколько ключей одной записью файла.
    ///
    /// По ключу за раз профиль ложился в конфиг частями: модели записались,
    /// флаги нет — и человек оставался с гибридом старого и нового набора,
    /// который ничем себя не выдаёт (ревью 19.08, DeepSeek). Пишем всё или
    /// ничего.
    @discardableResult
    static func setConfigValues(_ pairs: [(key: String, value: String)]) -> Bool {
        guard !pairs.isEmpty else { return true }
        guard let cfg = ensureConfigExists(),
              var text = try? String(contentsOf: cfg, encoding: .utf8) else { return false }
        for pair in pairs {
            text = replacing(pair.key, with: pair.value, in: text)
        }
        return (try? text.write(to: cfg, atomically: true, encoding: .utf8)) != nil
    }

    /// Путь к config.yaml, создавая его из образца при первом запуске.
    private static func ensureConfigExists() -> URL? {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        let fm = FileManager.default
        if !fm.fileExists(atPath: cfg.path) {
            // Папка данных на первом запуске пуста — размечаем сами.
            guard (try? fm.createDirectory(at: cfg.deletingLastPathComponent(),
                                           withIntermediateDirectories: true)) != nil else {
                return nil
            }
            guard let example = configExampleURL,
                  (try? fm.copyItem(at: example, to: cfg)) != nil else { return nil }
        }
        return cfg
    }

    @discardableResult
    static func setConfigValue(_ key: String, _ value: String) -> Bool {
        setConfigValues([(key, value)])
    }

    /// Образец конфига из поставки: в ручной установке лежит рядом с данными,
    /// в бандловой — внутри приложения. Ищем в обоих корнях, потому что
    /// «код в поставке, данные у человека» — это две разные папки.
    static var configExampleURL: URL? {
        let candidates = [
            charoiteRoot.appendingPathComponent("config/config.example.yaml"),
            codeRoot.appendingPathComponent("config/config.example.yaml"),
        ]
        return candidates.first { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// Замена значения ключа в тексте конфига. Чистая функция — под тестом.
    ///
    /// Правим ПОСЛЕДНЕЕ вхождение по той же причине, по какой его читает
    /// parseValue: одинаковые имена ключей встречаются в разных секциях, и
    /// секция sufler лежит ниже. Значение всегда в кавычках: путь с пробелом
    /// или имя с двоеточием иначе ломают YAML молча.
    static func replacing(_ key: String, with value: String, in text: String) -> String {
        var lines = text.components(separatedBy: "\n")
        let escaped = value.replacingOccurrences(of: "\"", with: "\\\"")
        var target: Int?
        for (i, line) in lines.enumerated()
        where line.trimmingCharacters(in: .whitespaces).hasPrefix(key + ":") {
            target = i
        }
        if let i = target {
            let indent = String(lines[i].prefix { $0 == " " })
            // Комментарий справа сохраняем: он объясняет поле человеку.
            let comment = lines[i].range(of: " #").map { String(lines[i][$0.lowerBound...]) } ?? ""
            lines[i] = "\(indent)\(key): \"\(escaped)\"\(comment)"
            return lines.joined(separator: "\n")
        }
        // Ключа нет вовсе — дописываем в секцию sufler, а не в конец файла:
        // в корне YAML он ничего не настроит.
        if let i = lines.firstIndex(where: { $0.hasPrefix("sufler:") }) {
            lines.insert("  \(key): \"\(escaped)\"", at: i + 1)
            return lines.joined(separator: "\n")
        }
        return text + "\nsufler:\n  \(key): \"\(escaped)\"\n"
    }

    /// Отделено от чтения файла ради тестов: разбор — чистая функция.
    ///
    /// Три ловушки, каждая давала молчаливый отказ.
    /// • CRLF: `.whitespaces` не включает `\r`, и путь графа получал хвостовой
    ///   возврат каретки — каталог «не существовал», архив молчал, а язык
    ///   «en\r» не совпадал ни с одним значением и откатывался на русский.
    ///   Файл с CRLF появляется сам: редактор на Windows, шара, копипаст.
    /// • `#` внутри значения: `graph_dir: "~/Vault #1"` резался до `~/Vault`.
    /// • Блочный скаляр (`key: >`) давал значение «>» — то есть относительный
    ///   путь от рабочего каталога приложения.
    static func parseValue(_ key: String, in text: String) -> String? {
        var found: String?
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let t = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard t.hasPrefix(key + ":") else { continue }
            var v = t.dropFirst(key.count + 1)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard !v.hasPrefix(">"), !v.hasPrefix("|") else { continue }  // блочный скаляр не наш случай
            if let quote = v.first, quote == "\"" || quote == "'" {
                // Значение в кавычках: берём ровно до закрывающей кавычки,
                // всё после неё — комментарий. Раньше комментарий у такой
                // строки не отсекался вовсе, и в поле мастера приезжало
                //   Мария"           # метка вашего микрофона
                // (найдено на живом экране 07.08, когда конфиг стал писаться
                // из приложения — а оно пишет значения в кавычках всегда:
                // путь с пробелом или имя с двоеточием иначе ломают YAML).
                let body = v.dropFirst()
                if let close = body.firstIndex(of: quote) {
                    v = String(body[..<close])
                } else {
                    v = String(body)   // кавычка не закрыта — берём остаток
                }
            } else if let hash = v.range(of: " #") {   // комментарий отделён пробелом
                v = String(v[..<hash.lowerBound])
            }
            v = v.trimmingCharacters(in: .whitespacesAndNewlines)
            v = v.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            v = v.trimmingCharacters(in: .whitespacesAndNewlines)
            if !v.isEmpty { found = v }
        }
        return found
    }

    /// Логический ключ конфига. Отсутствует или не распознан — `false`:
    /// для облачных разрешений безопасный дефолт — «нельзя».
    ///
    /// Список ровно тот, что питон считает разрешением: PyYAML разбирает
    /// true/yes/on в булево True, и `is True` в privacy.py его принимает.
    /// «1» PyYAML читает числом — питон отказывает, значит и здесь нельзя:
    /// тумблер в UI не имеет права показывать «включено» там, где демон
    /// скажет «нет».
    static func configFlag(_ key: String) -> Bool {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return false }
        return parseBool(key, in: text) == true
    }

    /// Ровно те токены, которые PyYAML читает как булево (регистр — как у
    /// PyYAML, без «tRUE»). Всё остальное для питона — строка или число.
    private static let yamlTrue: Set<String> = ["true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"]
    private static let yamlFalse: Set<String> = ["false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"]

    /// Логическое значение ключа так, как его прочитает демон: `true` только
    /// для ГОЛОГО булева токена. `allow_remote: "true"` — в кавычках — для
    /// PyYAML строка, и `privacy.py` (`is True`) отказывает; `parseValue`
    /// кавычки снимает, и приложение по нему разрешало удалённый хост там,
    /// где демон падал на старте (аудит DeepSeek 16.08). Мастер конфига
    /// пишет значения в кавычках всегда — расхождение было штатным.
    /// Отсутствие, кавычки, «1», мусор — `nil`.
    static func parseBool(_ key: String, in text: String) -> Bool? {
        var found: Bool?
        var seen = false
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let t = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard t.hasPrefix(key + ":") else { continue }
            var v = t.dropFirst(key.count + 1).trimmingCharacters(in: .whitespacesAndNewlines)
            if let hash = v.range(of: " #") { v = String(v[..<hash.lowerBound]) }
            if v.hasPrefix("#") { v = "" }
            v = v.trimmingCharacters(in: .whitespacesAndNewlines)
            seen = true
            if yamlTrue.contains(v) { found = true }
            else if yamlFalse.contains(v) { found = false }
            else { found = nil }          // кавычки, число, пусто — не булево
        }
        return seen ? found : nil
    }

    /// Переписать логический ключ в config.yaml суфлёра.
    ///
    /// Приложение не тащит YAML-зависимость ради одного тумблера: правится
    /// ровно та строка, где ключ уже объявлен, остальной файл — включая
    /// комментарии, которыми конфиг и документирован — не трогается.
    /// Ключа в файле нет — не дописываем: значит конфиг не от этой версии,
    /// и молча менять его структуру опаснее, чем отказать.
    @discardableResult
    static func setConfigFlag(_ key: String, _ value: Bool) -> Bool {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return false }

        var out: [String] = []
        var done = false
        for line in text.components(separatedBy: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if !done, t.hasPrefix(key + ":") {
                // отступ сохраняем — ключ живёт внутри секции
                let indent = String(line.prefix(while: { $0 == " " }))
                // хвостовой комментарий сохраняем: он объясняет смысл ключа
                var tail = ""
                if let hash = line.range(of: " #") { tail = String(line[hash.lowerBound...]) }
                out.append("\(indent)\(key): \(value)\(tail)")
                done = true
            } else {
                out.append(line)
            }
        }
        if !done {
            // Ключа в файле нет — так бывает у всех, кто поставил Чароит до
            // появления тумблера: строка не находится, тумблер молча не
            // работает (круг-1 DS по #436). Дописываем в конец секции
            // sufler — она есть в любом рабочем конфиге, потому что без неё
            // демон не стартует.
            guard let start = out.firstIndex(where: {
                $0.trimmingCharacters(in: .whitespaces).hasPrefix("sufler:")
            }) else { return false }
            var end = out.index(after: start)
            while end < out.endIndex {
                let line = out[end]
                let t = line.trimmingCharacters(in: .whitespaces)
                // конец секции — первая непустая строка без отступа
                if !t.isEmpty && !line.hasPrefix(" ") && !line.hasPrefix("\t") { break }
                end = out.index(after: end)
            }
            out.insert("  \(key): \(value)", at: end)
            done = true
        }
        do {
            try out.joined(separator: "\n").write(to: cfg, atomically: true, encoding: .utf8)
            return true
        } catch {
            return false
        }
    }
}
