import Foundation

/// Данные встреч — только владельцу учётной записи.
///
/// Запись разговора и стенограмма и есть то чувствительное, ради чего
/// продукт держат локально. При этом файлы создавались с правами по
/// умолчанию (0644), а каталоги — 0755: на Mac с несколькими учётками
/// любой второй пользователь читал чужие переговоры целиком, не запросив
/// ни одного разрешения (аудит 16.08). Питон-сторона закрывается общей
/// маской (`charoite_paths.harden_umask`), здесь — те же права для файлов,
/// которые создаёт приложение.
enum PrivateFiles {
    /// Файл с данными человека: чтение и запись только владельцу.
    static let fileMode: NSNumber = 0o600
    /// Каталог с данными человека: заходить внутрь может только владелец.
    static let dirMode: NSNumber = 0o700
}

extension FileManager {
    /// Создать каталог данных закрытым (0700).
    ///
    /// Права выставляются и для уже существующего каталога: у всех, кто
    /// поставил Чароит до этой правки, каталоги остались открытыми, а
    /// `createDirectory(attributes:)` молча ничего не делает, если каталог
    /// уже есть.
    @discardableResult
    func createPrivateDirectory(at url: URL) -> Bool {
        try? createDirectory(at: url, withIntermediateDirectories: true,
                             attributes: [.posixPermissions: PrivateFiles.dirMode])
        guard fileExists(atPath: url.path) else { return false }
        try? setAttributes([.posixPermissions: PrivateFiles.dirMode], ofItemAtPath: url.path)
        return true
    }

    /// Создать пустой файл данных закрытым (0600).
    @discardableResult
    func createPrivateFile(atPath path: String) -> Bool {
        createFile(atPath: path, contents: nil,
                   attributes: [.posixPermissions: PrivateFiles.fileMode])
    }

    /// Закрыть уже существующий файл, если приложение его не создавало
    /// (например, стенограмму записал питон-конвейер до этой правки).
    func makePrivate(atPath path: String) {
        try? setAttributes([.posixPermissions: PrivateFiles.fileMode], ofItemAtPath: path)
    }
}

/// Потолок для бессрочных append-логов демона (аудит 16.08, п.7).
///
/// `daemon.err.log` дописывается при каждом старте и никогда не
/// пересоздаётся — так трейсбек после крэша не теряется, но у
/// долгоживущей установки файл с кусками стенограмм растёт без предела.
/// При старте, если лог перерос `maxBytes`, остаётся только хвост
/// `keepBytes` (по границе строки): именно хвост нужен для диагноза.
/// Зеркало `charoite_paths.trim_log` на стороне Python (mlx_server.log).
enum LogTrim {
    static let maxBytes = 20 * 1024 * 1024
    static let keepBytes = 2 * 1024 * 1024

    /// Возвращает true, если усекали. Ошибки глотаются: лог важнее
    /// отсутствия лога, но запуск записи важнее лога.
    @discardableResult
    static func trim(_ url: URL, maxBytes: Int = maxBytes, keepBytes: Int = keepBytes) -> Bool {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = (attrs[.size] as? NSNumber)?.intValue, size > maxBytes,
              let fh = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? fh.close() }
        guard (try? fh.seek(toOffset: UInt64(max(0, size - keepBytes)))) != nil,
              var tail = try? fh.readToEnd() else { return false }
        if let nl = tail.firstIndex(of: 0x0A), nl < tail.endIndex - 1 {
            tail = tail[tail.index(after: nl)...]
        }
        var out = Data("[лог усечён при старте: было \(size) байт]\n".utf8)
        out.append(tail)
        // Запись на месте: права файла (0600) и владелец не меняются.
        guard let w = try? FileHandle(forWritingTo: url) else { return false }
        defer { try? w.close() }
        guard (try? w.truncate(atOffset: 0)) != nil else { return false }
        w.write(out)
        return true
    }
}

