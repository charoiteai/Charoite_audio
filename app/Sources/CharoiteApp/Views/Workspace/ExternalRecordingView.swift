import SwiftUI
import UniformTypeIdentifiers

#if os(macOS)

/// Вкладка «Внешняя запись»: положить файл — увидеть очередь — знать, когда
/// копия исчезнет.
///
/// Диктофон телефона, чужая запись звонка, экспорт Zoom — всё идёт тем же
/// конвейером, что живая встреча (STT, диаризация, минутки, граф). Файл
/// копируется в папку импорта (оригинал не трогаем), обработанная копия
/// живёт `import_keep_days` и удаляется вместе с аудио-исходником в архиве;
/// сбойный файл остаётся на виду и не удаляется, повтор — кнопкой.
struct ExternalRecordingView: View {
    @ObservedObject private var importer = ImportService.shared
    @AppStorage("charoite.importDir") private var importDir = ""
    @AppStorage("charoite.importWatch") private var importWatch = false
    @State private var targeted = false
    @State private var showLog = false

    private var dir: String { importDir.isEmpty ? ImportService.defaultDir : importDir }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            dropZone
            list
            footer
        }
        .padding(20)
        .onAppear {
            let d = importer.ensureFolder()
            if importDir.isEmpty { importDir = d }
            importer.refresh(dir: d)
            importer.startRetention(dir: d)
        }
        .onChange(of: importDir) { _, d in importer.refresh(dir: d) }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Image(systemName: "folder")
                    .foregroundStyle(.secondary)
                Text(dir)
                    .font(.callout)
                    .textSelection(.enabled)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button(L.t("Папка…", "Folder…", "文件夹…")) { chooseFolder() }
                    .charoite(.regular, .s)
                Button(L.t("Обработать сейчас", "Process now", "立即处理")) {
                    importer.scanNow(dir: dir)
                }
                .charoite(.prominent, .s, busy: importer.isScanning)
                .disabled(importer.isScanning)
            }
            HStack(spacing: 12) {
                Toggle(L.t("Следить за папкой", "Watch the folder", "监视文件夹"), isOn: $importWatch)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .onChange(of: importWatch) { _, on in
                        on ? importer.enable(dir: dir) : importer.disable()
                    }
                if !importer.status.isEmpty {
                    Text(importer.status).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if !importer.lastLog.isEmpty {
                    Button(showLog ? L.t("Скрыть журнал", "Hide log", "隐藏日志")
                                   : L.t("Журнал", "Log", "日志")) { showLog.toggle() }
                        .buttonStyle(.link)
                        .font(.caption)
                }
            }
            if showLog {
                ScrollView {
                    Text(importer.lastLog)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(maxHeight: 120)
                .padding(8)
                .background(RoundedRectangle(cornerRadius: Theme.radius).fill(Color.primary.opacity(0.05)))
            }
        }
    }

    private var dropZone: some View {
        VStack(spacing: 8) {
            Image(systemName: "tray.and.arrow.down")
                .font(.title2)
                .foregroundStyle(targeted ? Theme.accent : Color.secondary)
            Text(L.t("Перетащите запись сюда", "Drop a recording here", "将录音拖到此处"))
                .font(.callout)
            HStack(spacing: 6) {
                Text(L.t("или", "or", "或")).foregroundStyle(.secondary)
                Button(L.t("выберите файл…", "choose a file…", "选择文件…")) { chooseFiles() }
                    .buttonStyle(.link)
            }
            .font(.callout)
            Text(L.t("m4a · wav · mp3 · caf · txt · md · vtt · srt",
                     "m4a · wav · mp3 · caf · txt · md · vtt · srt",
                     "m4a · wav · mp3 · caf · txt · md · vtt · srt"))
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 22)
        .background(
            RoundedRectangle(cornerRadius: Theme.radiusCard)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
                .foregroundStyle(targeted ? Theme.accent : Color.secondary.opacity(0.4))
                .background(RoundedRectangle(cornerRadius: Theme.radiusCard)
                    .fill(targeted ? Theme.accent.opacity(0.08) : Color.clear))
        )
        .onDrop(of: [UTType.fileURL], isTargeted: $targeted) { providers in
            handleDrop(providers)
        }
        .accessibilityLabel(Text(L.t("Зона перетаскивания записи", "Recording drop zone", "录音拖放区")))
    }

    @ViewBuilder
    private var list: some View {
        if importer.items.isEmpty {
            Text(L.t("Пока пусто. Первый файл — и здесь появится очередь: что ждёт, что собралось, когда копия удалится.",
                     "Nothing yet. Drop the first file and the queue appears here: what waits, what is built, when the copy goes away.",
                     "暂无内容。放入第一个文件后，这里会显示队列：等待中、已生成、副本何时删除。"))
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        } else {
            List(importer.items) { item in
                row(item)
                    .listRowSeparator(.visible)
            }
            .listStyle(.inset)
            .frame(maxHeight: .infinity)
        }
    }

    private func row(_ item: ImportItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon(for: item.phase))
                .foregroundStyle(color(for: item.phase))
                .frame(width: 18)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name).font(.body)
                Text(ExternalRecordingPolicy.statusText(item.phase))
                    .font(.caption)
                    .foregroundStyle(color(for: item.phase))
                Text("\(Int64(item.bytes).formatted(.byteCount(style: .file).locale(L.locale))) · "
                     + item.recorded.formatted(date: .numeric, time: .shortened))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            Spacer()
            HStack(spacing: 6) {
                if case .failed = item.phase {
                    Button(L.t("Повторить", "Retry", "重试")) { importer.retry(item, dir: dir) }
                        .charoite(.regular, .s)
                }
                if case .done(let imported) = item.phase, imported.transcript != nil {
                    Button(L.t("Стенограмма", "Transcript", "逐字稿")) { importer.openTranscript(item) }
                        .charoite(.regular, .s)
                }
                Button(L.t("В Finder", "In Finder", "在访达中显示")) { importer.reveal(item) }
                    .charoite(.quiet, .s)
            }
        }
        .padding(.vertical, 4)
    }

    private var footer: some View {
        Text(L.t("Файл копируется в папку импорта — оригинал остаётся у вас. Обработанная копия живёт столько дней, сколько задано в audio.import_keep_days (по умолчанию как у записей встреч, 2), и удаляется вместе с аудио-исходником в архиве встречи; дата — у каждого файла. При ошибке ничего не удаляется. Всё локально.",
                 "The file is copied into the import folder — your original stays put. A processed copy lives as many days as audio.import_keep_days says (default: same as meeting recordings, 2) and is deleted together with the audio source in the meeting archive; the date is shown per file. On failure nothing is deleted. Everything stays local.",
                 "文件会复制到导入文件夹，你的原件保持不动。已处理的副本按 audio.import_keep_days 设定的天数保留（默认与会议录音相同，2 天），随后与会议归档中的音频源一起删除；每个文件旁显示日期。处理失败时不删除任何内容。全部本地完成。"))
            .font(.caption)
            .foregroundStyle(.secondary)
    }

    private func icon(for phase: ImportItem.Phase) -> String {
        switch phase {
        case .waiting: return "clock"
        case .failed: return "exclamationmark.triangle.fill"
        case .done(let imported): return imported.noSpeech ? "speaker.slash" : "checkmark.circle.fill"
        case .legacy: return "checkmark.circle"
        }
    }

    private func color(for phase: ImportItem.Phase) -> Color {
        switch phase {
        case .waiting: return .secondary
        case .failed: return Theme.warning
        case .done, .legacy: return Theme.ok
        }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        let candidates = providers.filter { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }
        guard !candidates.isEmpty else { return false }
        let dir = self.dir
        for p in candidates {
            p.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                guard let data, let url = URL(dataRepresentation: data, relativeTo: nil) else { return }
                Task { @MainActor in ImportService.shared.add(urls: [url], dir: dir) }
            }
        }
        return true
    }

    private func chooseFiles() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.message = L.t("Запись встречи: m4a, wav, mp3, caf — или текст/субтитры",
                            "Meeting recording: m4a, wav, mp3, caf — or text/subtitles",
                            "会议录音：m4a、wav、mp3、caf，或文本/字幕")
        let dir = self.dir
        panel.begin { response in
            guard response == .OK else { return }
            let urls = panel.urls
            Task { @MainActor in ImportService.shared.add(urls: urls, dir: dir) }
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = ImportService.folderURL(dir)
        panel.message = L.t("Папка импорта: сюда кладутся записи, здесь живут копии до удаления",
                            "Import folder: recordings land here, copies live here until deleted",
                            "导入文件夹：录音放在这里，副本在此保留直到删除")
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in
                importDir = url.path
                importer.refresh(dir: url.path)
                if importWatch { importer.enable(dir: url.path) }
            }
        }
    }
}

#endif
