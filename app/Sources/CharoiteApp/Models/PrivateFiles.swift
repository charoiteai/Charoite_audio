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
