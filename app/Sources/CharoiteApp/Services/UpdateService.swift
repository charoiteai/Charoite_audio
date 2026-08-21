import AppKit
import Combine
import CryptoKit
import Foundation
import Security

#if os(macOS)

/// Обновление приложения без терминала.
///
/// Раньше обновление выглядело так: заметить, что вышла новая версия, зайти
/// на GitHub, скачать zip, распаковать, перетащить поверх старого, снять
/// карантин. Пять шагов, каждый из которых легко отложить «на потом» —
/// и версия отстаёт на месяцы, а с ней и ошибки, давно исправленные.
///
/// Здесь — одна кнопка. Скачиваем zip выпуска (не dmg: распаковка не требует
/// монтирования тома и не оставляет за собой висящий диск), проверяем
/// контрольную сумму, подменяем бандл и перезапускаемся.
enum UpdateStage: Equatable {
    case idle
    case downloading(percent: Int)
    case verifying
    case installing
    /// Обновление невозможно прямо сейчас — с причиной, понятной человеку.
    case refused(reason: String)
    case failed(reason: String)
}

@MainActor
final class UpdateService: ObservableObject {
    static let shared = UpdateService()

    @Published private(set) var stage: UpdateStage = .idle

    private init() {
        // Прошлый запуск мог кончиться молчаливым exit 75 helper'а
        // (приложение не завершилось — подмена отменена): маркер делает
        // отказ видимым при следующем старте, а не «версия почему-то та же»
        let marker = Bundle.main.bundleURL.path + ".update-refused"
        if let text = try? String(contentsOfFile: marker, encoding: .utf8) {
            try? FileManager.default.removeItem(atPath: marker)
            stage = .refused(reason: L.t(
                "Прошлое обновление не установилось: \(text.trimmingCharacters(in: .whitespacesAndNewlines)). Повторите обновление.",
                "The previous update was not installed: \(text.trimmingCharacters(in: .whitespacesAndNewlines)). Try updating again.",
                "上次更新未安装：\(text.trimmingCharacters(in: .whitespacesAndNewlines))。请重试更新。"))
        }
    }

    var isBusy: Bool {
        switch stage {
        case .downloading, .verifying, .installing: return true
        case .idle, .refused, .failed: return false
        }
    }

    /// Причина, по которой обновляться сейчас нельзя.
    ///
    /// Идущая запись — единственная, но неоспоримая: обновление заканчивается
    /// перезапуском, а перезапуск во время встречи обрывает её. Так уже
    /// терялись сорок минут разговора, и никакая свежесть версии этого не
    /// стоит. Приложение из образа (том смонтирован только на чтение) тоже
    /// не обновляем: заменять нужно копию в «Программах», а не содержимое
    /// установщика.
    nonisolated static func refusalReason(recording: Bool, bundlePath: String,
                                          parentWritable: Bool = true) -> String? {
        if recording {
            return L.t("идёт запись встречи — обновим после остановки",
                       "a meeting is being recorded — we'll update after you stop",
                       "正在录制会议 —— 停止后再更新")
        }
        if bundlePath.hasPrefix("/Volumes/") {
            return L.t("приложение запущено из образа: перетащите его в «Программы»",
                       "the app is running from a disk image: drag it to Applications first",
                       "应用正从磁盘映像运行：请先将其拖入「应用程序」")
        }
        // Helper переименовывает бандл в его же папке; без права писать в
        // неё `mv` падает под `set -e` уже ПОСЛЕ выхода приложения — ни
        // маркера, ни перезапуска, человек видит просто закрывшееся окно
        // (аудит DeepSeek 16.08). Проверяем до того, как завершиться.
        if !parentWritable {
            return L.t("папка приложения недоступна для записи — обновите вручную, скопировав новую версию в «Программы»",
                       "the app's folder is not writable — update by hand: copy the new version into Applications",
                       "应用所在文件夹不可写 —— 请手动更新：将新版本复制到「应用程序」")
        }
        return nil
    }

    /// Папка, в которой лежит бандл, доступна для записи? Именно там helper
    /// переименовывает и подменяет приложение.
    nonisolated static func parentIsWritable(bundlePath: String) -> Bool {
        let parent = (bundlePath as NSString).deletingLastPathComponent
        return FileManager.default.isWritableFile(atPath: parent)
    }

    /// Идентификатор команды в подписи бандла; `nil` — подпись ad-hoc (или
    /// её нет). У ad-hoc подписи designated requirement — хеш конкретного
    /// бинаря, поэтому после подмены бандла macOS считает приложение другим
    /// и выданные доступы к микрофону и записи экрана пропадают
    /// (make_app.sh описывает то же для локальных пересборок; CI-сборки без
    /// Developer ID — такие же). Обновление от этого не отказывается —
    /// человек должен знать заранее, что доступы придётся выдать заново
    /// (аудит DeepSeek 16.08).
    nonisolated static func signingTeamIdentifier(of url: URL) -> String? {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &code) == errSecSuccess,
              let code else { return nil }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation),
                                            &info) == errSecSuccess,
              let dict = info as? [String: Any] else { return nil }
        let team = dict[kSecCodeInfoTeamIdentifier as String] as? String
        return (team?.isEmpty ?? true) ? nil : team
    }

    nonisolated static let bundleIsAdHocSigned: Bool =
        signingTeamIdentifier(of: Bundle.main.bundleURL) == nil

    /// Предупреждение к кнопке «Обновить» для сборки без Developer ID.
    nonisolated static var adHocSignatureNote: String? {
        guard bundleIsAdHocSigned else { return nil }
        return L.t("сборка без Developer ID: после обновления macOS попросит заново разрешить микрофон и запись экрана",
                   "build without Developer ID: after the update macOS will ask you to allow the microphone and screen recording again",
                   "未使用 Developer ID 签名的构建：更新后 macOS 会要求重新授权麦克风和屏幕录制")
    }

    /// Совпадает ли скачанное с тем, что опубликовано.
    ///
    /// Файл приезжает по сети и через секунду станет тем приложением, которое
    /// слушает встречи. Проверка суммы — единственное, что отделяет это от
    /// «запустим что дали». Нет опубликованной суммы — не ставим: молча
    /// пропустить проверку хуже, чем не обновиться.
    nonisolated static func checksumMatches(expected: String?, actual: String) -> Bool {
        guard let expected = expected?
            .trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              expected.count == 64 else { return false }
        return expected == actual.lowercased()
    }

    /// Скрипт подмены: приложение не может заменить само себя изнутри.
    ///
    /// Пока процесс жив, бандл занят; после `terminate` некому выполнять
    /// подмену. Поэтому её делает отдельный процесс: ждёт выхода по PID,
    /// подменяет через `ditto` (сохраняет подпись и права, в отличие от `cp`),
    /// снимает карантин со скачанного и запускает приложение обратно.
    nonisolated static func replacementScript() -> String {
        """
        #!/bin/bash
        set -euo pipefail
        pid=$1
        new_app=$2
        target=$3
        for _ in $(seq 1 100); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.1
        done
        # Таймаут не означает, что приложение завершилось. Если PID всё ещё
        # жив, подмена бандла запрещена: она может разорвать текущую запись.
        # Отказ оставляет маркер: helper отвязан и его exit 75 никто не
        # видит — раньше обновление молча не устанавливалось (аудит 14.08),
        # человек узнавал только по не изменившейся версии.
        if kill -0 "$pid" 2>/dev/null; then
          echo "приложение не завершилось за 10 секунд — подмена бандла отменена" \
            > "${target}.update-refused" 2>/dev/null || true
          exit 75
        fi
        rm -f "${target}.update-refused" 2>/dev/null || true
        # Любой сбой ниже — приложение уже завершено, и без этого человек
        # остался бы перед закрывшимся окном: возвращаем то, что есть на
        # диске, и оставляем маркер (аудит DeepSeek 16.08).
        trap 'echo "подмена бандла сорвалась на шаге: $BASH_COMMAND" > "${target}.update-refused" 2>/dev/null || true; open "$target" 2>/dev/null || true' ERR
        # Папка приложения должна существовать и быть записываемой: иначе
        # mv упадёт уже после выхода приложения. Preflight в приложении это
        # проверяет до завершения; здесь — страховка.
        if [ ! -e "$target" ] || [ ! -w "$(dirname "$target")" ]; then
          echo "папка приложения недоступна для записи — подмена бандла отменена" \
            > "${target}.update-refused" 2>/dev/null || true
          open "$target" 2>/dev/null || true
          exit 75
        fi
        # Старую копию не удаляем до успеха: если ditto оборвётся на середине,
        # у человека должно остаться рабочее приложение, а не половина.
        rm -rf "${target}.old"
        mv "$target" "${target}.old"
        if ditto "$new_app" "$target"; then
          xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
          rm -rf "${target}.old"
        else
          rm -rf "$target"
          mv "${target}.old" "$target"
        fi
        open "$target"
        """
    }

    /// Значения идут в argv, а не вставляются в shell-код. Пробелы, кавычки,
    /// `$()` и переводы строк в пути остаются данными и не исполняются bash.
    nonisolated static func replacementArguments(
        script: String,
        pid: Int32,
        newApp: String,
        target: String
    ) -> [String] {
        [script, String(pid), newApp, target]
    }

    // MARK: - Сама установка

    private let repo = "charoiteai/Charoite_audio"

    /// Скачать выпуск и подменить себя. В конце приложение перезапускается.
    func install(tag: String) async {
        guard !isBusy else { return }
        let bundle = Bundle.main.bundleURL
        if let reason = Self.refusalReason(recording: SuflerService.shared.hasActiveLifecycle,
                                           bundlePath: bundle.path,
                                           parentWritable: Self.parentIsWritable(bundlePath: bundle.path)) {
            stage = .refused(reason: reason)
            return
        }

        do {
            stage = .downloading(percent: 0)
            let assets = try await releaseAssets(tag: tag)
            guard let zip = assets["Charoite.app.zip"] else {
                throw Fail(L.t("в выпуске нет Charoite.app.zip",
                               "the release has no Charoite.app.zip",
                               "该发布中没有 Charoite.app.zip"))
            }

            // Якорь №1 (карточка №24) — ДО скачивания архива: манифест
            // «<версия>  <хеш>» подписан ключом владельца, которого нет ни
            // в репозитории, ни в секретах CI. Сумма из релиза ловит битую
            // загрузку, но не подмену — кто дотянулся до релиза, подписал бы
            // и её. Байты манифеста — сырые: подпись стоит на файле как есть.
            guard let manifest = try? await raw(assets["Charoite.app.zip.manifest"]),
                  let sigRaw = try? await raw(assets["Charoite.app.zip.manifest.sig"]),
                  let sig = UpdateAuthenticity.decodeSignature(sigRaw),
                  UpdateAuthenticity.manifestSignatureValid(manifest: manifest,
                                                            signature: sig) else {
                throw Fail(L.t(
                    "у выпуска нет верной подписи манифеста — установку отменил",
                    "the release has no valid manifest signature — installation cancelled",
                    "该发布缺少有效的清单签名 —— 已取消安装"))
            }
            // Связка версий — против даунгрейда: реплей старой честной
            // тройки под новым тегом (круг по PR #366, GLM + DeepSeek).
            let manifestVersion = UpdateAuthenticity.manifestVersion(manifest)
            guard UpdateAuthenticity.versionBindingOK(
                manifestVersion: manifestVersion,
                tag: tag, current: VersionStatusService.appVersion) else {
                throw Fail(L.t(
                    "версия в подписанном манифесте не сходится с выпуском — установку отменил",
                    "signed manifest version does not match the release — installation cancelled",
                    "签名清单中的版本与该发布不符 —— 已取消安装"))
            }

            let work = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("charoite-update-\(tag)")
            try? FileManager.default.removeItem(at: work)
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
            let file = work.appendingPathComponent("Charoite.app.zip")

            let sum = try await download(zip, to: file) { [weak self] percent in
                Task { @MainActor in self?.stage = .downloading(percent: percent) }
            }

            stage = .verifying
            guard Self.checksumMatches(
                expected: UpdateAuthenticity.manifestChecksum(manifest),
                actual: sum) else {
                throw Fail(L.t("контрольная сумма не совпала — установку отменил",
                               "checksum mismatch — installation cancelled",
                               "校验和不匹配 —— 已取消安装"))
            }

            // Загрузка могла идти минуты, и за это время могла начаться
            // встреча. Это последний await перед синхронным запуском helper,
            // поэтому повторный preflight закрывает окно check/use.
            if let reason = Self.refusalReason(recording: SuflerService.shared.hasActiveLifecycle,
                                               bundlePath: bundle.path,
                                               parentWritable: Self.parentIsWritable(bundlePath: bundle.path)) {
                stage = .refused(reason: reason)
                return
            }

            stage = .installing
            try run("/usr/bin/ditto", ["-x", "-k", file.path, work.path])
            let newApp = work.appendingPathComponent("Charoite.app")
            guard FileManager.default.fileExists(atPath: newApp.path) else {
                throw Fail(L.t("в архиве нет приложения",
                               "no app inside the archive",
                               "压缩包内没有应用"))
            }
            // Версия распакованного бандла обязана совпасть с подписанной:
            // ловит и ошибку подписанта, и архив, подложенный до подписи
            // (круг по PR #366, DeepSeek — скрипт теперь тоже сверяет, это
            // второй пояс того же ремня).
            let shipped = (NSDictionary(contentsOf: newApp
                .appendingPathComponent("Contents/Info.plist"))?
                .object(forKey: "CFBundleShortVersionString") as? String)
                .map(VersionStatus.normalize)
            guard let shipped, shipped == manifestVersion else {
                throw Fail(L.t(
                    "версия приложения в архиве не совпала с подписанной — установку отменил",
                    "the app version inside the archive does not match the signed one — installation cancelled",
                    "压缩包内的应用版本与签名版本不符 —— 已取消安装"))
            }
            // Якорь №2: подпись Apple нашей командой. Собрать бандл с ней
            // нельзя даже с полным доступом к CI — сертификат выдаёт Apple.
            guard UpdateAuthenticity.bundleSignatureValid(at: newApp) else {
                throw Fail(L.t(
                    "подпись скачанного приложения не наша — установку отменил",
                    "the downloaded app is not signed by us — installation cancelled",
                    "下载的应用签名不符 —— 已取消安装"))
            }

            let script = work.appendingPathComponent("replace.sh")
            try Self.replacementScript()
                .write(to: script, atomically: true, encoding: .utf8)
            try run("/bin/chmod", ["+x", script.path])

            // Запускаем отвязанно: подмена должна пережить наш собственный
            // выход, иначе процесс-родитель утащит её за собой.
            let replace = Process()
            replace.executableURL = URL(fileURLWithPath: "/usr/bin/nohup")
            replace.arguments = Self.replacementArguments(
                script: script.path,
                pid: ProcessInfo.processInfo.processIdentifier,
                newApp: newApp.path,
                target: bundle.path
            )
            replace.standardOutput = FileHandle.nullDevice
            replace.standardError = FileHandle.nullDevice
            try replace.run()

            NSApplication.shared.terminate(nil)
        } catch {
            stage = .failed(reason: (error as? Fail)?.text ?? error.localizedDescription)
        }
    }

    private struct Fail: Error { let text: String; init(_ t: String) { text = t } }

    private func releaseAssets(tag: String) async throws -> [String: URL] {
        let url = URL(string: "https://api.github.com/repos/\(repo)/releases/tags/\(tag)")!
        let (data, _) = try await URLSession.shared.data(from: url)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let list = (json?["assets"] as? [[String: Any]]) ?? []
        var out: [String: URL] = [:]
        for a in list {
            if let name = a["name"] as? String,
               let link = a["browser_download_url"] as? String,
               let url = URL(string: link) { out[name] = url }
        }
        return out
    }

    private func raw(_ url: URL?) async throws -> Data? {
        guard let url else { return nil }
        let (data, _) = try await URLSession.shared.data(from: url)
        return data
    }

    /// Скачивание с процентом и подсчёт суммы отдельным проходом.
    ///
    /// Побайтовая итерация по `URLSession.bytes` на трёхсотмегабайтном архиве
    /// — это сотни миллионов асинхронных шагов и минуты ожидания на ровном
    /// месте. Системная закачка кладёт файл сама, а хеш потом читается с
    /// диска мегабайтными кусками за пару секунд.
    private func download(_ url: URL, to file: URL,
                          progress: @escaping (Int) -> Void) async throws -> String {
        let watcher = DownloadProgress(onPercent: progress)
        let session = URLSession(configuration: .default, delegate: watcher, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }

        // Делегат передаём В ВЫЗОВ, а не только в сессию: асинхронный
        // `download(from:)` ведёт задачу сам и делегата сессии не спрашивает,
        // поэтому проценты не считал никто. 13.08 архив в 91 МБ тянулся через
        // прокси полчаса, и всё это время на экране висело «Скачиваю… 0%» —
        // неотличимо от зависшей загрузки.
        let (temp, _) = try await session.download(from: url, delegate: watcher)
        try? FileManager.default.removeItem(at: file)
        try FileManager.default.moveItem(at: temp, to: file)
        return try Self.sha256(of: file)
    }

    /// Сколько процентов показать — или `nil`, если показывать нечего.
    ///
    /// Размер приходит из заголовка ответа; сервер вправе его не прислать
    /// (`-1`), и тогда честнее не показывать ничего, чем рисовать ноль:
    /// ноль на экране читается как «загрузка встала».
    nonisolated static func percent(written: Int64, total: Int64) -> Int? {
        guard total > 0, written >= 0 else { return nil }
        return min(100, Int(Double(written) / Double(total) * 100))
    }

    nonisolated static func sha256(of file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func run(_ tool: String, _ args: [String]) throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else {
            throw Fail("\(tool) → \(p.terminationStatus)")
        }
    }
}

/// Проценты закачки: у async-варианта `download(from:)` их нет, а ждать
/// триста мегабайт под неподвижной надписью «скачиваю» — то же самое, что
/// ждать в тишине.
private final class DownloadProgress: NSObject, URLSessionDownloadDelegate {
    private let onPercent: (Int) -> Void
    private var lastShown = -1

    init(onPercent: @escaping (Int) -> Void) { self.onPercent = onPercent }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64,
                    totalBytesExpectedToWrite total: Int64) {
        guard let percent = UpdateService.percent(written: totalBytesWritten, total: total),
              percent != lastShown else { return }
        lastShown = percent
        onPercent(percent)
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        // Файл забирает async-обёртка; делегат нужен только ради процентов.
    }
}

#endif
