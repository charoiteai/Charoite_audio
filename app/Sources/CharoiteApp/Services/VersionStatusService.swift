import Combine
import Foundation

#if os(macOS)

/// Та ли версия работает, которую человек считает установленной.
///
/// В установке из репозитория живут три разные вещи, и расходятся они молча:
/// само приложение (`.app` в `~/Applications`), код в рабочей папке (демон,
/// ночной цикл, скрипты графа) и последний выпуск на GitHub. Приложение
/// 0.46.0 при уже выпущенной 0.47.0 выглядит совершенно нормально; папка,
/// отставшая на десяток коммитов, — тоже. Понимаешь это, когда чинишь
/// ошибку, которой в свежем коде нет.
///
/// Сеть трогаем только ради номера последнего выпуска: обычный GET к
/// публичному API GitHub, без токена и без единого байта о пользователе.
/// Раз в сутки, молча при любой ошибке — отсутствие связи не новость,
/// достойная строки на экране.
enum VersionState: Equatable {
    /// Приложение, код и выпуск сходятся.
    case current(app: String)
    /// Вышла версия новее установленной.
    case updateAvailable(app: String, latest: String)
    /// Код в рабочей папке не той версии, что приложение.
    case codeMismatch(app: String, code: String)
}

struct VersionStatus: Equatable {
    let state: VersionState

    /// Сравнение версий целиком, без обращения к диску и сети.
    ///
    /// Расхождение с папкой важнее нового выпуска: обновиться человек
    /// успеет, а вот работа на чужом коде объясняет странности, которые
    /// иначе будешь искать в своей голове.
    static func compare(app: String, code: String?, latest: String?) -> VersionStatus {
        if let code, !code.isEmpty, normalize(code) != normalize(app) {
            return VersionStatus(state: .codeMismatch(app: app, code: code))
        }
        if let latest, !latest.isEmpty, isNewer(latest, than: app) {
            return VersionStatus(state: .updateAvailable(app: app, latest: normalize(latest)))
        }
        return VersionStatus(state: .current(app: app))
    }

    /// `v0.47.0`, `0.47.0` и `0.47.0-dirty` — одна и та же версия.
    static func normalize(_ v: String) -> String {
        var s = v.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("v") { s.removeFirst() }
        if let dash = s.firstIndex(of: "-") { s = String(s[s.startIndex..<dash]) }
        return s
    }

    /// Сравнение по числам, а не по строкам: `0.9.0` строкой больше
    /// `0.47.0`, и наивное сравнение предложило бы «обновиться» назад.
    static func isNewer(_ candidate: String, than current: String) -> Bool {
        let a = parts(normalize(candidate)), b = parts(normalize(current))
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0
            let y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    private static func parts(_ v: String) -> [Int] {
        v.split(separator: ".").map { Int($0) ?? 0 }
    }
}

@MainActor
final class VersionStatusService: ObservableObject {
    static let shared = VersionStatusService()

    @Published private(set) var status: VersionStatus

    /// Номер последнего выпуска и время проверки переживают перезапуск:
    /// иначе каждый старт приложения — новый запрос к GitHub.
    private let latestKey = "charoite.latestRelease"
    private let checkedKey = "charoite.latestReleaseChecked"

    private init() {
        status = VersionStatus(state: .current(app: Self.appVersion))
        refresh()
    }

    static var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    }

    /// Версия кода в рабочей папке — по git-тегу.
    ///
    /// Только для установки из репозитория: когда код вложен в бандл, он
    /// физически не может разойтись с приложением, и проверять нечего.
    static func codeVersion(root: URL) -> String? {
        guard !AppSettings.codeIsEmbedded,
              FileManager.default.fileExists(atPath: root.appendingPathComponent(".git").path)
        else { return nil }

        let git = Process()
        git.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        git.arguments = ["-C", root.path, "describe", "--tags", "--abbrev=0"]
        let pipe = Pipe()
        git.standardOutput = pipe
        git.standardError = FileHandle.nullDevice
        guard (try? git.run()) != nil else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        git.waitUntilExit()
        guard git.terminationStatus == 0 else { return nil }
        let out = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (out?.isEmpty ?? true) ? nil : out
    }

    func refresh() {
        let code = Self.codeVersion(root: AppSettings.charoiteRoot)
        let latest = UserDefaults.standard.string(forKey: latestKey)
        status = VersionStatus.compare(app: Self.appVersion, code: code, latest: latest)
        fetchLatestIfDue()
    }

    /// Раз в сутки, и только если человек не запретил.
    private func fetchLatestIfDue() {
        guard AppSettings.checkUpdates else { return }
        let last = UserDefaults.standard.object(forKey: checkedKey) as? Date
        if let last, Date().timeIntervalSince(last) < 24 * 3600 { return }

        let url = URL(string: "https://api.github.com/repos/charoiteai/Charoite_audio/releases/latest")!
        var req = URLRequest(url: url, timeoutInterval: 8)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        // Ключи забираем до запроса: внутри ответа `self` уже не обязан быть
        // жив, а писать номер выпуска в ключ с пустым именем — тихая порча
        // настроек вместо честного «проверить не вышло».
        let (latestKey, checkedKey) = (self.latestKey, self.checkedKey)
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            guard let data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = json["tag_name"] as? String
            else { return }   // нет связи — не повод для строки на экране
            Task { @MainActor in
                UserDefaults.standard.set(tag, forKey: latestKey)
                UserDefaults.standard.set(Date(), forKey: checkedKey)
                self?.status = VersionStatus.compare(
                    app: Self.appVersion,
                    code: Self.codeVersion(root: AppSettings.charoiteRoot),
                    latest: tag)
            }
        }.resume()
    }

    // MARK: - Как это выглядит на «Сегодня»

    var title: String {
        switch status.state {
        case .current(let app):
            return L.t("Версия \(app)", "Version \(app)", "版本 \(app)")
        case .updateAvailable(_, let latest):
            return L.t("Вышла версия \(latest)", "Version \(latest) is out", "\(latest) 版已发布")
        case .codeMismatch:
            return L.t("Приложение и код разошлись",
                       "App and code are out of sync",
                       "应用与代码不一致")
        }
    }

    var detail: String {
        switch status.state {
        case .current:
            return L.t("Приложение и код в рабочей папке — одной версии",
                       "The app and the code in your folder are the same version",
                       "应用与工作目录中的代码版本一致")
        case .updateAvailable(let app, _):
            return L.t("Установлена \(app). Обновление — на GitHub, в разделе Releases",
                       "You have \(app). Get the update from GitHub Releases",
                       "当前为 \(app)。更新请见 GitHub Releases")
        case .codeMismatch(let app, let code):
            return L.t("Приложение \(app), код в рабочей папке \(code) — демон и ночной цикл работают на нём",
                       "App is \(app), the code in your folder is \(code) — the daemon and the nightly pass run on it",
                       "应用为 \(app)，工作目录代码为 \(code) —— 守护进程与夜间处理使用后者")
        }
    }

    var needsAttention: Bool {
        if case .current = status.state { return false }
        return true
    }

    var icon: String {
        switch status.state {
        case .current: return "checkmark.seal"
        case .updateAvailable: return "arrow.down.circle"
        case .codeMismatch: return "exclamationmark.triangle.fill"
        }
    }
}

#endif
