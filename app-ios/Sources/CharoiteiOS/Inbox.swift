import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Доставка записи на Mac.
///
/// Основной путь — папка, которую пользователь один раз выбрал в iCloud
/// Drive (security-scoped bookmark): тот же «Charoite Inbox», куда смотрит
/// папка импорта macOS-приложения. Обычный iCloud Drive синкается надёжно —
/// в отличие от контейнера приложения, который может неделями не
/// материализоваться на Mac (проверено 27.07).
///
/// Недоставленное не пропадает: очередь в Documents/Outbox (tmp система
/// чистит, Documents — нет), досылка при каждом запуске и каждом стопе.
enum Inbox {
    private static let bookmarkKey = "inbox.bookmark"

    static var outbox: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Outbox", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// Куда пишется ИДУЩАЯ запись. Documents, а не tmp: систему не волнует,
    /// что мы посреди встречи, — tmp она чистит когда захочет, и вместе с ним
    /// исчезал единственный экземпляр часового разговора.
    static var inProgress: URL {
        let d = outbox.appendingPathComponent("current", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// Уже уехавшие записи, которые ещё держим на телефоне.
    ///
    /// «iCloud принял» не значит «Mac получил»: 27.07 контейнер не
    /// материализовался на маке неделями. Пока копия лежит здесь, встречу
    /// можно отдать руками — кнопкой «Поделиться» или через «Файлы».
    static var sent: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Sent", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// Сколько отправленных записей держим. Часовая встреча — десятки
    /// мегабайт; пять последних это страховка, а не второй архив.
    static let keepSent = 5

    /// Расширения, которые считаются записью. CAF — основной формат: он
    /// переживает обрыв (в отличие от M4A без атома `moov`), M4A оставлен
    /// для файлов, записанных прежними версиями.
    private static let audioExts: Set<String> = ["caf", "m4a"]

    /// Манифест записи (№200): `<файл>.json` рядом с аудио — причина и момент
    /// остановки. Единица очереди — ПАРА: аудио уезжает, спасается, уходит в
    /// Sent и удаляется вместе с манифестом, иначе причина остаётся на телефоне
    /// ровно в тех сценариях, ради которых её пишут (Critical DS входного круга).
    static func sidecar(for audio: URL) -> URL {
        audio.appendingPathExtension("json")
    }

    /// Перенести аудио и его манифест одним движением; манифест — если есть.
    private static func movePair(_ audio: URL, to dest: URL) throws {
        let fm = FileManager.default
        try fm.moveItem(at: audio, to: dest)
        let sc = sidecar(for: audio)
        if fm.fileExists(atPath: sc.path) {
            try? fm.removeItem(at: sidecar(for: dest))
            try? fm.moveItem(at: sc, to: sidecar(for: dest))
        }
    }

    private static func removePair(_ audio: URL) {
        let fm = FileManager.default
        try? fm.removeItem(at: audio)
        try? fm.removeItem(at: sidecar(for: audio))
    }

    static var queuedCount: Int {
        queued.count
    }

    /// Записи, ждущие отправки, — новые первыми.
    static var queued: [URL] {
        recordings(in: outbox)
    }

    /// Одна запись в очереди — всё, что нужно показать человеку.
    ///
    /// Одной серой строкой «в очереди: 6» отделаться нельзя: за ней могут
    /// стоять шесть свежих файлов, которые уедут через минуту, а могут —
    /// получасовая встреча недельной давности, про которую человек уверен,
    /// что она давно на Mac.
    struct Item: Identifiable, Equatable {
        let url: URL
        let recorded: Date
        let bytes: Int

        var id: URL { url }
        var name: String { Self.humanName(url) }
        var size: String { sizeText(url) }

        /// Сколько запись ждёт доставки.
        func waiting(since now: Date = Date()) -> TimeInterval {
            max(0, now.timeIntervalSince(recorded))
        }

        /// Ждёт дольше суток — знак, что доставка не работает, а не «сейчас уедет».
        func isStuck(since now: Date = Date()) -> Bool {
            waiting(since: now) > 24 * 3600
        }

        /// «Встреча», «Заметка», «Дневник» — по префиксу, который читает Mac.
        static func humanName(_ url: URL) -> String {
            let file = url.lastPathComponent
            if file.hasPrefix("note_") { return L.t("Заметка", "Note", "笔记") }
            if file.hasPrefix("diary_") { return L.t("Дневник", "Diary", "日记") }
            return L.t("Встреча", "Meeting", "会议")
        }
    }

    /// Очередь как список: что именно лежит, когда записано и сколько весит.
    static var queuedItems: [Item] {
        recordings(in: outbox).map { url in
            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey,
                                                           .fileSizeKey])
            return Item(url: url,
                        recorded: values?.contentModificationDate ?? .distantPast,
                        bytes: values?.fileSize ?? 0)
        }
    }

    /// Последняя запись — из очереди или из уже отправленных.
    ///
    /// Пока файл заперт внутри приложения, у человека нет ни одного способа
    /// достать его руками, если iCloud молчит. Отсюда кнопка «Поделиться» на
    /// экране и `UIFileSharingEnabled` в `project.yml` (сам Info.plist
    /// генерируется xcodegen и в репозитории не хранится — правка прямо в нём
    /// стирается следующей генерацией).
    ///
    /// Смотрит в обе папки намеренно: искать только в очереди значило бы, что
    /// кнопка есть ровно тогда, когда доставка сломалась, — то есть исчезает
    /// в тот момент, когда всё прошло штатно, и человек её просто не находит.
    static var lastRecording: URL? {
        recordings(in: outbox, sent).first
    }

    private static func recordings(in dirs: URL...) -> [URL] {
        let fm = FileManager.default
        return dirs
            .flatMap { (try? fm.contentsOfDirectory(
                at: $0, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [] }
            .filter { audioExts.contains($0.pathExtension) }
            .sorted { modified($0) > modified($1) }
    }

    private static func modified(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate ?? .distantPast
    }

    /// Человеческий размер файла для подписи под кнопкой.
    static func sizeText(_ url: URL) -> String {
        let bytes = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
        // Единицы — на языке приложения: ByteCountFormatter берёт локаль
        // устройства, и в русском интерфейсе выходило «106 KB» рядом с
        // подписью «Поделиться записью».
        return Int64(bytes).formatted(.byteCount(style: .file).locale(L.locale))
    }

    /// Записи, пережившие смерть приложения: их никто не закрыл и не поставил
    /// в очередь. Зовётся на старте — раньше такие файлы просто пропадали.
    static func rescueOrphans(from current: URL? = nil, to queue: URL? = nil) {
        let fm = FileManager.default
        let current = current ?? inProgress
        let queue = queue ?? outbox
        let left = (try? fm.contentsOfDirectory(at: current, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        for f in left where audioExts.contains(f.pathExtension) {
            // Файл, который init рекордера создал, а record() не начал
            // (убийство процесса посреди пробы взвода), — пустышка, не
            // запись: на Mac она уезжала «встречей» нулевой длины (GLM r2).
            // Секундный огрызок настоящей записи весит больше порога.
            let bytes = (try? f.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
            if bytes < orphanMinBytes {
                removePair(f)
                continue
            }
            // Файл никто не закрыл (процесс убит, суспенд без `.ended`, краш): факт —
            // «стопа не было», время — mtime последнего кадра. Манифест, который
            // stop() успел написать до гибели, не перезаписывается (Important GLM)
            if Recorder.StopRecord.read(nextTo: f) == nil {
                let at = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? Date()
                try? Recorder.StopRecord(kind: .stop, reason: .noStop, at: at, seconds: nil, series: nil)
                    .write(nextTo: f)
            }
            try? movePair(f, to: uniqueName(in: queue, like: f))
        }
    }

    /// Ниже этого размера в `current/` — не запись, а заготовка контейнера.
    static let orphanMinBytes = 1024

    /// Свободное имя рядом с занятым. Затирать чужой файл нельзя нигде:
    /// ни в своей очереди, ни в папке импорта на Mac.
    private static func uniqueName(in dir: URL, like file: URL) -> URL {
        let fm = FileManager.default
        var candidate = dir.appendingPathComponent(file.lastPathComponent)
        let base = file.deletingPathExtension().lastPathComponent
        let ext = file.pathExtension
        var n = 1
        while fm.fileExists(atPath: candidate.path) {
            candidate = dir.appendingPathComponent("\(base)-\(n).\(ext)")
            n += 1
        }
        return candidate
    }

    static var folderChosen: Bool {
        destinationFolder() != nil
    }

    /// Папка выбрана не в iCloud Drive. Локальное хранилище телефона
    /// («На iPhone») выглядит в «Файлах» точно так же, но записи из него
    /// никуда не синкаются: 06.08 закладка указывала на «На iPhone →
    /// Загрузки» — телефон честно копировал встречи туда, помечал их
    /// отправленными, а Mac неделями ждал их в пустой папке iCloud.
    struct NotUbiquitousError: LocalizedError {
        var errorDescription: String? {
            L.t("Эта папка не в iCloud Drive — записи не доедут до Mac. Выберите iCloud Drive → Charoite Inbox.",
                "This folder is not in iCloud Drive — recordings will never reach the Mac. Pick iCloud Drive → Charoite Inbox.",
                "该文件夹不在 iCloud Drive 中——录音无法送达 Mac。请选择 iCloud Drive → Charoite Inbox。")
        }
    }

    /// Пользователь выбрал папку в «Файлах» — запоминаем закладку навсегда.
    static func saveFolder(_ url: URL) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard FileManager.default.isUbiquitousItem(at: url) else {
            throw NotUbiquitousError()
        }
        let bm = try url.bookmarkData()
        UserDefaults.standard.set(bm, forKey: bookmarkKey)
    }

    private static func destinationFolder() -> URL? {
        guard let bm = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: bm, bookmarkDataIsStale: &stale) else {
            // Закладка не разрешается (восстановление из бэкапа, папку удалили).
            // Забываем её, чтобы UI перестал рисовать «папка настроена» и позвал
            // выбрать заново: раньше folderChosen смотрел на наличие байтов, и
            // человек месяцами копил очередь, не понимая, почему ничего не едет.
            UserDefaults.standard.removeObject(forKey: bookmarkKey)
            return nil
        }
        if stale {
            // Обновлять закладку можно только внутри security scope — раньше
            // bookmarkData() вызывался снаружи, всегда падал в try? и закладка
            // оставалась протухшей навсегда.
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if let fresh = try? url.bookmarkData() {
                UserDefaults.standard.set(fresh, forKey: bookmarkKey)
            }
        }
        // Закладки, сохранённые до проверки на iCloud, могли указывать на
        // локальную папку телефона (баг 06.08) — «доставка» в неё выглядит
        // успешной, но никуда не ведёт. Такую забываем: пусть UI позовёт
        // выбрать папку заново, чем очередь молча едет в никуда.
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard FileManager.default.isUbiquitousItem(at: url) else {
            UserDefaults.standard.removeObject(forKey: bookmarkKey)
            return nil
        }
        return url
    }

    /// Файл — в очередь, затем попытка доставки всей очереди.
    /// `seconds` — длительность по рекордеру: секунда живого звука в очередь идёт
    /// при любом битрейте, порог байтов — только для заготовки контейнера.
    static func deliver(_ file: URL, seconds: TimeInterval? = nil,
                        status: @MainActor @escaping (String) -> Void) async {
        let fm = FileManager.default
        // огрызок ротации при живом сбое кодека (меньше порога заготовки контейнера и
        // короче секунды) на Mac уезжал бы «встречей» (GLM M6 r1 по #565). Не удаляем:
        // остаётся в current/, rescueOrphans уберёт его на следующем старте, а до тех
        // пор файл можно отдать руками (критика DS r2); размер только измеренный (GLM M2)
        if let bytes = (try? file.resourceValues(forKeys: [.fileSizeKey]))?.fileSize,
           bytes < orphanMinBytes, (seconds ?? 0) < 1 {
            await status(L.t("Пустая запись (\(bytes) Б) — не отправляю; уберётся при следующем запуске",
                             "Empty recording (\(bytes) B) — not sending; removed on next launch",
                             "空录音（\(bytes) B）— 不发送；下次启动时清理"))
            await flush(status: status)      // очередь всё равно досылаем (GLM M1 r2)
            return
        }
        do {
            try movePair(file, to: uniqueName(in: outbox, like: file))
        } catch {
            // Раньше ошибка глушилась `try?`, и файл оставался в tmp — то есть
            // терялся при первой же уборке системы. Молчать здесь нельзя.
            await status(L.t("Не удалось поставить запись в очередь: \(error.localizedDescription)",
                             "Could not queue the recording: \(error.localizedDescription)",
                             "无法将录音加入队列：\(error.localizedDescription)"))
            return
        }
        await flush(status: status)
    }

    /// Дослать всё из очереди. Вызывается на старте и после каждого стопа.
    ///
    /// Публикация атомарная: копируем под `.part` и переименовываем. Сканер на
    /// Mac отбирает файлы по расширению и не проверяет, дописан ли файл, —
    /// попадание его таймера в окно копирования 43-мегабайтной встречи давало
    /// расшифровку половины разговора, после чего файл уезжал в done/ и
    /// повторный импорт становился невозможен.
    static func flush(status: @MainActor @escaping (String) -> Void) async {
        // Один проход за раз: `.task` вкладки и стоп приходят вместе, а check-then-set
        // на статике из разных потоков пропускал оба — два копирования одного файла
        // в один `.part` (аудит 13.09, DS I3 / GLM M4). Замок — актор.
        await gate.run {
            // Стоп с локскрина гасит аудиосессию, и копия 40-мегабайтной встречи
            // замирала в `.part` до следующего запуска (GLM M6): просим у системы
            // время на доставку; истёк бюджет — дописываем текущий файл и выходим,
            // остальное подберёт следующий проход (DS I4 r1 по #565).
            expired = false
            let bg = await MainActor.run {
                UIApplication.shared.beginBackgroundTask(withName: "charoite.inbox-flush") {
                    Inbox.expired = true
                }
            }
            await flushLocked(status: status)
            await MainActor.run {
                if bg != .invalid { UIApplication.shared.endBackgroundTask(bg) }
            }
        }
    }

    /// Фоновый бюджет истёк: цикл доставки должен остановиться после текущего файла.
    private nonisolated(unsafe) static var expired = false

    private static func flushLocked(status: @MainActor @escaping (String) -> Void) async {
        let fm = FileManager.default
        let files = (try? fm.contentsOfDirectory(at: outbox, includingPropertiesForKeys: nil))?
            .filter { audioExts.contains($0.pathExtension) } ?? []
        guard !files.isEmpty else { return }
        guard let dir = destinationFolder() else {
            await status(L.t("Выберите папку iCloud (кнопка вверху) — записей в очереди: \(files.count)",
                             "Choose the iCloud folder (button above) — queued: \(files.count)",
                             "请选择 iCloud 文件夹（上方按钮）— 队列中：\(files.count)"))
            return
        }
        let scoped = dir.startAccessingSecurityScopedResource()
        defer { if scoped { dir.stopAccessingSecurityScopedResource() } }

        var delivered = 0
        var waiting = 0
        var processed = 0
        var stuck: [String] = []
        for f in files {
            // фоновый бюджет вышел — остальное следующим проходом; первый файл доводим всегда:
            // отказ системы во времени иначе давал пустой проход на каждом стопе с локскрина (DS I3 r2)
            if expired, processed > 0 { break }
            processed += 1
            // Скопирован прошлым проходом и ждёт выгрузки: не копировать второй раз
            // (иначе на Mac уехал бы дубль с суффиксом «-1»), а сверить состояние.
            // Копию при ошибке НЕ удаляем: ошибка бывает остаточной или временной, а
            // перекопирование 40 МБ по кругу и дубль на Mac хуже висящей записи в
            // очереди с честным статусом (DS I3, критика GLM r1 по #565).
            if let pending = pendingDest(for: f) {
                let state = await awaitUpload(of: pending, upTo: uploadGrace)
                switch pendingVerdict(state) {
                case .retire:
                    retire(f)
                    delivered += 1
                case .wait:
                    waiting += 1
                case .keep:
                    stuck.append(f.lastPathComponent)
                    if case .failed(let why) = state {
                        await status(L.t("iCloud не принял \(f.lastPathComponent): \(why)",
                                         "iCloud rejected \(f.lastPathComponent): \(why)",
                                         "iCloud 拒绝了 \(f.lastPathComponent)：\(why)"))
                    }
                }
                continue
            }
            let dest = uniqueName(in: dir, like: f)
            let part = dest.appendingPathExtension("part")
            do {
                // Манифест — первым и под тем же именем, что уедет аудио (dest считается один
                // раз): сканер на Mac стартует по аудио и к этому моменту уже видит причину;
                // второй uniqueName для манифеста приклеил бы его к чужой старой встрече
                // (Important GLM входного круга по №200)
                let sc = sidecar(for: f)
                if fm.fileExists(atPath: sc.path) {
                    let scDest = sidecar(for: dest)
                    let scPart = scDest.appendingPathExtension("part")
                    try? fm.removeItem(at: scPart)
                    try? fm.removeItem(at: scDest)
                    try fm.copyItem(at: sc, to: scPart)
                    try fm.moveItem(at: scPart, to: scDest)
                }
                try? fm.removeItem(at: part)
                try fm.copyItem(at: f, to: part)
                // Из очереди убираем, только когда iCloud ВЫГРУЗИЛ файл. Проверка
                // «ошибки выгрузки сразу после moveItem» была мёртвой: выгрузка к
                // тому моменту ещё не начиналась, ключ всегда пуст, и «Уехало на
                // Mac» печаталось по факту локального копирования; при переполненной
                // квоте копия потом вытеснялась из Sent (аудит 13.09, DS I1 / GLM I1).
                // Метка рядом с файлом — ДО публикации: убийство между публикацией и
                // меткой давало второй экземпляр в iCloud (DS I2 r1 по #565); при
                // падении после метки следующий проход увидит «копии нет», снимет
                // метку и скопирует заново — дубля нет.
                try markPending(f, dest: dest)
                try fm.moveItem(at: part, to: dest)     // публикация одним шагом
                let state = await awaitUpload(of: dest, upTo: uploadGrace)
                switch pendingVerdict(state) {
                case .retire:
                    retire(f)
                    delivered += 1
                case .wait:
                    waiting += 1                                   // подтвердим следующим проходом
                case .keep:
                    stuck.append(f.lastPathComponent)
                    if case .failed(let why) = state {
                        await status(L.t("iCloud не принял \(f.lastPathComponent): \(why)",
                                         "iCloud rejected \(f.lastPathComponent): \(why)",
                                         "iCloud 拒绝了 \(f.lastPathComponent)：\(why)"))
                    }
                }
            } catch {
                // continue, а не return: один сбойный файл не должен запирать
                // всю очередь, включая сегодняшнюю встречу.
                try? fm.removeItem(at: part)
                // метка — безусловно: при занятом dest иначе усыновлялся чужой файл (GLM I2 r2)
                try? fm.removeItem(at: pendingMark(for: f))
                stuck.append(f.lastPathComponent)
                await status(L.t("Не отправилось (\(f.lastPathComponent)): \(error.localizedDescription)",
                                 "Failed (\(f.lastPathComponent)): \(error.localizedDescription)",
                                 "发送失败（\(f.lastPathComponent)）：\(error.localizedDescription)"))
            }
        }
        let left = queuedCount
        if delivered > 0 || left == 0 {
            rechecks = 0     // прогресс есть — бюджет перепроверок полный; висящий файл его не съедает (DS M2 r2)
        }
        if waiting > 0 {
            // копии в iCloud, выгрузка идёт: «Уехало» ещё нельзя, но и не сбой —
            // досверим сами через полминуты, пока приложение открыто
            let failed = stuck.isEmpty ? ("", "", "") : (", не отправилось: \(stuck.count)", ", failed: \(stuck.count)", "，失败：\(stuck.count)")
            await status(L.t("Скопировано в iCloud, ждёт выгрузки: \(waiting)" + (delivered > 0 ? ", уехало: \(delivered)" : "") + failed.0,
                             "Copied to iCloud, uploading: \(waiting)" + (delivered > 0 ? ", delivered: \(delivered)" : "") + failed.1,
                             "已复制到 iCloud，正在上传：\(waiting)" + (delivered > 0 ? "，已发送：\(delivered)" : "") + failed.2))
            scheduleRecheck(status: status)
        } else if left == 0 {
            await status(L.t("Уехало на Mac: \(delivered) файл(а)",
                             "Delivered to Mac: \(delivered)",
                             "已发送到 Mac：\(delivered)"))
        } else {
            await status(L.t("Отправлено \(delivered), в очереди \(left)" + (stuck.isEmpty ? "" : ", не отправилось: \(stuck.count)"),
                             "Sent \(delivered), queued \(left)" + (stuck.isEmpty ? "" : ", failed: \(stuck.count)"),
                             "已发送 \(delivered)，队列中 \(left)" + (stuck.isEmpty ? "" : "，失败：\(stuck.count)")))
        }
    }

    /// Состояние выгрузки опубликованной копии в iCloud.
    enum UploadState: Equatable {
        case uploaded, uploading, unknown
        case failed(String)
    }

    /// Что делать с файлом очереди по состоянию его копии: убрать из очереди,
    /// ждать выгрузку или держать с отказом. Чистая политика — таблица в тесте
    /// (DS M3 r2 по #565). «Неизвестно» = принято: для папки вне контейнера
    /// ключей часто нет, судить нечем.
    enum PendingVerdict: Equatable { case retire, wait, keep }

    static func pendingVerdict(_ state: UploadState) -> PendingVerdict {
        switch state {
        case .uploaded, .unknown: return .retire
        case .uploading: return .wait
        case .failed: return .keep
        }
    }

    /// Сколько ждём выгрузку в том же проходе: маленькие заметки успевают,
    /// часовая встреча — нет, её подтвердит следующий проход или перепроверка.
    static let uploadGrace: TimeInterval = 10

    static func uploadState(of dest: URL) -> UploadState {
        guard let v = try? dest.resourceValues(forKeys: [
            .isUbiquitousItemKey, .ubiquitousItemIsUploadedKey,
            .ubiquitousItemIsUploadingKey, .ubiquitousItemUploadingErrorKey,
        ]) else { return .unknown }
        if v.isUbiquitousItem == false { return .uploaded }     // папка не в iCloud — доставка локальная
        // «выгружено» сильнее остаточной ошибки прошлой попытки: иначе доехавший файл
        // читался как отвергнутый (DS I3 / GLM I3 r1 по #565)
        if v.ubiquitousItemIsUploaded == true { return .uploaded }
        if let err = v.ubiquitousItemUploadingError { return .failed(err.localizedDescription) }
        if v.ubiquitousItemIsUploading == true { return .uploading }
        // Для папки вне контейнера (security-scoped bookmark) ключи часто пусты:
        // судить нечем — считаем принятым, как считалось всегда.
        return .unknown
    }

    private static func awaitUpload(of dest: URL, upTo limit: TimeInterval) async -> UploadState {
        let deadline = Date().addingTimeInterval(limit)
        var state = uploadState(of: dest)
        var polls = 0
        // «неизвестно» на первом опросе — ключи могли не успеть появиться: перечитываем
        // несколько раз, прежде чем считать принятым (GLM I2 r1 по #565). Отмена
        // задачи и истёкший фоновый бюджет прерывают ожидание сразу — иначе
        // проглоченная CancellationError крутила цикл вхолостую 10 с (DS I5).
        while (state == .uploading || (state == .unknown && polls < 3)), Date() < deadline, !expired {
            do {
                try await Task.sleep(nanoseconds: 1_000_000_000)
            } catch {
                break
            }
            polls += 1
            state = uploadState(of: dest)
        }
        return state
    }

    /// Метка «скопирован в iCloud, ждёт подтверждения»: рядом с файлом очереди,
    /// внутри — путь копии. Расширение `.sent` в список записей не входит.
    static func pendingMark(for file: URL) -> URL {
        file.appendingPathExtension("sent")
    }

    static func markPending(_ file: URL, dest: URL) throws {
        try dest.path.write(to: pendingMark(for: file), atomically: true, encoding: .utf8)
    }

    /// Куда файл уже скопирован прошлым проходом; nil — не копировался или копия
    /// исчезла (тогда метка снимается и файл поедет заново).
    static func pendingDest(for file: URL) -> URL? {
        let mark = pendingMark(for: file)
        guard let path = try? String(contentsOf: mark, encoding: .utf8) else { return nil }
        let dest = URL(fileURLWithPath: path.trimmingCharacters(in: .whitespacesAndNewlines))
        guard FileManager.default.fileExists(atPath: dest.path) else {
            try? FileManager.default.removeItem(at: mark)
            return nil
        }
        return dest
    }

    /// Досверить выгрузку, пока приложение открыто: не больше `maxRechecks` раз
    /// подряд, чтобы не крутиться вечно при мёртвом iCloud.
    private static let maxRechecks = 6
    private nonisolated(unsafe) static var rechecks = 0     // пишется только под замком прохода
    private static func scheduleRecheck(status: @MainActor @escaping (String) -> Void) {
        guard rechecks < maxRechecks else { return }
        rechecks += 1
        Task {
            try? await Task.sleep(nanoseconds: 30_000_000_000)
            await flush(status: status)
        }
    }

    /// Доставленный файл — из очереди в отправленные, лишнее удаляем.
    /// Очередь остаётся списком долгов, а копия последних встреч живёт на
    /// телефоне независимо от того, доехали ли они до Mac.
    private static func retire(_ file: URL) {
        let fm = FileManager.default
        // сначала файл, потом метка: падение между ними иначе оставляло файл в очереди
        // без метки, и подтверждённая копия ехала второй раз (DS I2 r1 по #565)
        defer { try? fm.removeItem(at: pendingMark(for: file)) }
        guard (try? movePair(file, to: uniqueName(in: sent, like: file))) != nil else {
            // Переложить не вышло — из очереди файл убрать всё равно надо,
            // иначе он поедет в iCloud на каждом flush по кругу.
            removePair(file)
            return
        }
        let old = recordings(in: sent).dropFirst(keepSent)
        for f in old { removePair(f) }
    }

    /// Замок одного прохода flush — актор вместо гонки на статике.
    private actor FlushGate {
        private var busy = false
        /// Критическая секция внутри актора: замок отпускается при любом выходе
        /// из тела, включая будущие ранние return (DS I6 r1 по #565).
        func run(_ body: () async -> Void) async {
            guard !busy else { return }
            busy = true
            defer { busy = false }
            await body()
        }
    }
    private static let gate = FlushGate()
}

/// Системный выбор папки (UIKit-мост). Пикер общий, папки разные: вкладка
/// записи выбирает им папку ДОСТАВКИ («Charoite Inbox»), вкладки встреч и
/// задач — папку ГРАФА (Obsidian-vault); какая нужна — объясняет вызывающий
/// экран своей подписью.
struct FolderPicker: UIViewControllerRepresentable {
    var onPick: (URL) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let p = UIDocumentPickerViewController(forOpeningContentTypes: [.folder])
        p.delegate = context.coordinator
        // Открываемся сразу в iCloud Drive. По умолчанию пикер показывает
        // «Недавние», а у нового пользователя там пусто: первый экран настройки
        // выглядит как пустой список, и до нужной папки надо ещё догадаться
        // дойти через «Обзор».
        p.directoryURL = FileManager.default
            .url(forUbiquityContainerIdentifier: nil)?
            .deletingLastPathComponent()
        return p
    }

    func updateUIViewController(_ vc: UIDocumentPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL) -> Void
        init(onPick: @escaping (URL) -> Void) { self.onPick = onPick }
        func documentPicker(_ controller: UIDocumentPickerViewController,
                            didPickDocumentsAt urls: [URL]) {
            if let u = urls.first { onPick(u) }
        }
    }
}
