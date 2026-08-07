import Foundation

#if os(macOS)

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
                SetupReadinessService.shared.refresh()
            } catch {
                progress[model] = nil
                failed[model] = error.localizedDescription
            }
        }
    }

    /// Ключ прогресса для модели диаризации — она качается не Ollama, а
    /// нашим скриптом, но человеку это различие не нужно.
    static let diarizationKey = "diarization"

    /// Модель разделения голосов уже стоит?
    static var diarizationInstalled: Bool {
        FileManager.default.fileExists(
            atPath: AppSettings.charoiteRoot
                .appendingPathComponent("models/diar/embedding.onnx").path)
    }

    /// Поставить модель разделения голосов.
    ///
    /// Инструкция просила выполнить `scripts/get_models.py --diar` в
    /// терминале — единственный шаг установки, ради которого приходилось
    /// открывать консоль после того, как приложение уже работает. Скрипт
    /// тот же: он печатает адрес перед соединением, проверяет, что пришёл
    /// настоящий ONNX, и кладёт файл туда, где его ищет демон.
    func pullDiarization() {
        let key = Self.diarizationKey
        guard progress[key] == nil else { return }
        progress[key] = L.t("качаю модель голосов…", "pulling voice model…", "正在拉取声纹模型…")
        failed[key] = nil
        let root = AppSettings.charoiteRoot
        Task.detached {
            let task = Process()
            task.executableURL = root.appendingPathComponent(".venv/bin/python")
            task.arguments = ["scripts/get_models.py", "--diar"]
            task.currentDirectoryURL = root
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
                    SetupReadinessService.shared.refresh()
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

    private func stream(_ model: String) async throws {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/pull") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.httpBody = try JSONSerialization.data(withJSONObject: ["name": model])
        // Модель качается минутами — обычный таймаут запроса здесь не судья.
        req.timeoutInterval = 3600
        let (bytes, response) = try await URLSession.shared.bytes(for: req)
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
#endif
