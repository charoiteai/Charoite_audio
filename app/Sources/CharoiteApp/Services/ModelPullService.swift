import Foundation
import SwiftUI

#if os(macOS)

/// Два файла набора голосов — в одном месте. Эмбеддинги без сегментации
/// оставляют живую разметку в упрощённом режиме и не дают пересборки после встречи.
enum DiarizationModels {
    static func embeddingURL(_ root: URL) -> URL {
        root.appendingPathComponent("models/diar/embedding.onnx")
    }

    static func segmentationURL(_ root: URL) -> URL {
        root.appendingPathComponent("models/diar/segmentation.onnx")
    }

    /// Есть эмбеддинги / есть сегментация для корня данных.
    static func presence(root: URL) -> (embeddings: Bool, segmentation: Bool) {
        let files = FileManager.default
        return (
            files.fileExists(atPath: embeddingURL(root).path),
            files.fileExists(atPath: segmentationURL(root).path))
    }
}

/// Скачивание модели Ollama из приложения — без терминала.
///
/// Проверка готовности честно говорила «модель не найдена: ollama pull …», но
/// исполнять рецепт отправляла в терминал. Для первого запуска это разрыв
/// ровно посередине пути: человек уже видит, чего не хватает, и уже согласен —
/// не хватает только кнопки. Здесь она: тот же pull через локальный API Ollama,
/// с процентами из его же стрима.
@MainActor
final class ModelPullService: ObservableObject {
    static let shared = ModelPullService()

    /// model → строка прогресса («34 %», «распаковка…»).
    @Published private(set) var progress: [String: String] = [:]
    /// model → текст ошибки последней попытки.
    @Published private(set) var failed: [String: String] = [:]

    func isPulling(_ model: String) -> Bool { progress[model] != nil }

    func pull(_ model: String) {
        guard progress[model] == nil else { return }
        progress[model] = L.t("качаю…", "pulling…", "拉取中…")
        failed[model] = nil
        Task {
            do {
                try await stream(model)
                progress[model] = nil
                SetupReadinessService.shared.refresh(force: true)
            } catch {
                progress[model] = nil
                failed[model] = error.localizedDescription
            }
        }
    }

    /// Ключ прогресса для модели диаризации — она качается не Ollama, а
    /// нашим скриптом, но человеку это различие не нужно.
    static let diarizationKey = "diarization"

    /// Оба файла набора голосов уже стоят?
    static var diarizationInstalled: Bool {
        let got = DiarizationModels.presence(root: AppSettings.charoiteRoot)
        return got.embeddings && got.segmentation
    }

    /// Поставить набор разделения голосов: эмбеддинги, затем сегментацию.
    ///
    /// Инструкция просила выполнить `scripts/get_models.py --diar` в
    /// терминале — единственный шаг установки, ради которого приходилось
    /// открывать консоль после того, как приложение уже работает. Скрипт
    /// тот же: он печатает адрес перед соединением, проверяет, что пришёл
    /// настоящий ONNX, и кладёт оба файла туда, где их ищет демон. Успех —
    /// только когда на месте оба: последняя строка вывода при нехватке
    /// сегментации и есть текст ошибки.
    func pullDiarization() {
        let key = Self.diarizationKey
        guard progress[key] == nil else { return }
        progress[key] = L.t("качаю модель голосов…", "pulling voice model…", "正在拉取声纹模型…")
        failed[key] = nil
        let root = AppSettings.charoiteRoot
        Task.detached {
            let task = Process()
            task.arguments = ["scripts/get_models.py", "--diar"]
            AppSettings.preparePython(task, root: root)
            let pipe = Pipe()
            task.standardOutput = pipe
            task.standardError = pipe
            do {
                try task.run()
                task.waitUntilExit()
            } catch {
                let message = error.localizedDescription
                await MainActor.run {
                    let service = ModelPullService.shared
                    service.progress[key] = nil
                    service.failed[key] = message
                }
                return
            }
            let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                             encoding: .utf8) ?? ""
            let ok = task.terminationStatus == 0
            await MainActor.run {
                let service = ModelPullService.shared
                service.progress[key] = nil
                if ok, ModelPullService.diarizationInstalled {
                    SetupReadinessService.shared.refresh(force: true)
                } else {
                    // Последняя строка вывода — то, на чём скрипт остановился;
                    // молчаливый отказ здесь читается как «кнопка не работает».
                    let tail = out.split(separator: "\n").last.map(String.init) ?? ""
                    service.failed[key] = tail.isEmpty
                        ? L.t("не удалось поставить модель", "could not install the model", "无法安装模型")
                        : tail
                }
            }
        }
    }

    /// Ollama живёт на этой машине — системный прокси для него не судья:
    /// корп-прокси 13.08 душил загрузку весов до 53 КБ/с, а грабля чинилась
    /// точечно в трёх сервисах и здесь была пропущена (аудит 14.08).
    private static let localSession: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        return URLSession(configuration: cfg)
    }()

    private func stream(_ model: String) async throws {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/pull") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.httpBody = try JSONSerialization.data(withJSONObject: ["name": model])
        // Модель качается минутами — обычный таймаут запроса здесь не судья.
        req.timeoutInterval = 3600
        let (bytes, response) = try await Self.localSession.bytes(for: req)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        for try await line in bytes.lines {
            guard let data = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { continue }
            if let err = obj["error"] as? String {
                throw NSError(domain: "ollama", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: err])
            }
            progress[model] = Self.progressText(
                status: obj["status"] as? String,
                completed: (obj["completed"] as? NSNumber)?.int64Value,
                total: (obj["total"] as? NSNumber)?.int64Value)
        }
    }

    /// Строка прогресса из события стрима: проценты, когда они есть.
    nonisolated static func progressText(status: String?, completed: Int64?, total: Int64?) -> String {
        if let completed, let total, total > 0 {
            return "\(Int(Double(completed) / Double(total) * 100)) %"
        }
        return status ?? "…"
    }
}

/// Кнопка «Поставить разметку голосов». Ход и ошибка — из `ModelPullService`,
/// лист мастера не открывается: там Enter начал бы запись.
struct DiarizationInstallButton: View {
    var alignment: HorizontalAlignment = .leading
    @ObservedObject private var pulls = ModelPullService.shared

    var body: some View {
        VStack(alignment: alignment, spacing: 4) {
            control
            if let err = pulls.failed[ModelPullService.diarizationKey] {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
        }
    }

    @ViewBuilder
    private var control: some View {
        if let status = pulls.progress[ModelPullService.diarizationKey] {
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } else {
            Button(Self.title) { pulls.pullDiarization() }
                .charoite(.regular, .s)
        }
    }

    private static var title: String {
        L.t("Поставить разметку голосов",
           "Install voice labelling",
           "安装声纹标注")
    }
}
#endif
