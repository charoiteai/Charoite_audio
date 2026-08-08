import Foundation

/// Настройки приложения — всё локальное, персист в UserDefaults.
///
/// Никаких удалённых серверов по умолчанию: Ollama на этой машине, демон
/// суфлёра — из папки установки Charoite_audio, граф — из config.yaml суфлёра.
enum AppSettings {
    /// Папка установки Charoite_audio (там .venv, src/daemon.py, config/).
    ///
    /// Порядок: путь, выбранный человеком в Настройках → склонированный
    /// репозиторий в домашней папке → рабочая папка приложения рядом с
    /// вложенным кодом. Последний случай — установка «из коробки»: код
    /// лежит в бандле, а данные человеку писать всё равно куда-то надо.
    static var charoiteRoot: URL {
        if let s = UserDefaults.standard.string(forKey: "charoite.root"), !s.isEmpty {
            return URL(fileURLWithPath: (s as NSString).expandingTildeInPath)
        }
        let cloned = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Charoite_audio")
        if FileManager.default.fileExists(atPath: cloned.appendingPathComponent("src/daemon.py").path) {
            return cloned
        }
        return codeIsEmbedded ? workspaceRoot : cloned
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
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    /// Откуда запускать код: вложенный в бандл, если рядом с данными его нет.
    static func codeRoot(dataRoot: URL? = nil) -> URL {
        let data = dataRoot ?? charoiteRoot
        let localCode = data.appendingPathComponent("src/daemon.py").path
        if FileManager.default.fileExists(atPath: localCode) { return data }
        return codeIsEmbedded ? embeddedCodeRoot : data
    }

    static var codeRoot: URL { codeRoot(dataRoot: nil) }

    /// Подготовить процесс python: интерпретатор, рабочий каталог кода и
    /// корень данных в окружении.
    ///
    /// Собрано в одну функцию намеренно: запусков python шесть, и раньше
    /// каждый сам склеивал путь — они разъезжались при первой же правке.
    /// С разведёнными корнями цена расхождения выше: процесс, запущенный из
    /// бандла без CHAROITE_ROOT, попытается писать в подписанную папку.
    static func preparePython(_ process: Process, root: URL? = nil) {
        let dataRoot = root ?? charoiteRoot
        process.executableURL = pythonExecutable(root: dataRoot)
        process.currentDirectoryURL = codeRoot(dataRoot: dataRoot)
        var env = ProcessInfo.processInfo.environment
        env["CHAROITE_ROOT"] = dataRoot.path
        process.environment = env
    }

    /// Путь к скрипту поставки — он лежит рядом с кодом, а не с данными.
    static func scriptPath(_ relative: String, root: URL? = nil) -> String {
        codeRoot(dataRoot: root).appendingPathComponent(relative).path
    }

    static var ollamaURL: String {
        let s = UserDefaults.standard.string(forKey: "charoite.ollama") ?? ""
        if let u = URL(string: s), u.host != nil { return s }
        return "http://localhost:11434"
    }

    /// Папка графа Obsidian — читается из config/config.yaml суфлёра
    /// (sufler.graph_dir), чтобы не настраивать одно и то же дважды.
    /// CHAROITE_GRAPH_DIR перекрывает конфиг (скрины/тесты на демо-графе).
    static var graphDir: URL? {
        if let env = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
           !env.isEmpty {
            return URL(fileURLWithPath: (env as NSString).expandingTildeInPath)
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
    @discardableResult
    static func setConfigValue(_ key: String, _ value: String) -> Bool {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        let fm = FileManager.default
        if !fm.fileExists(atPath: cfg.path) {
            // Первый запуск: конфига ещё нет — берём образец из поставки.
            let example = charoiteRoot.appendingPathComponent("config/config.example.yaml")
            guard (try? fm.copyItem(at: example, to: cfg)) != nil else { return false }
        }
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return false }
        let updated = replacing(key, with: value, in: text)
        return (try? updated.write(to: cfg, atomically: true, encoding: .utf8)) != nil
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
        ["true", "yes", "on"].contains((configValue(key) ?? "").lowercased())
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
        guard done else { return false }
        do {
            try out.joined(separator: "\n").write(to: cfg, atomically: true, encoding: .utf8)
            return true
        } catch {
            return false
        }
    }
}
