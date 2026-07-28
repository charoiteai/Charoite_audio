import SwiftUI

#if os(macOS)

/// Экран первого запуска.
///
/// Раньше человек, открыв программу впервые, видел пустой суфлёр: две серые
/// панели и кнопка «Слушать встречу». Ни что это, ни зачем ему микрофон, ни
/// куда уедет запись — нигде не сказано. Для программы, которая просит доступ
/// к звуку рабочих совещаний, молчание в этом месте дороже любой другой
/// экономии: человек либо не нажмёт, либо нажмёт, не понимая, на что согласился.
struct FirstRunView: View {
    /// Показываем один раз; отметка переживает перезапуск.
    @AppStorage("charoit.firstRunSeen") private var seen = false
    @Environment(\.dismiss) private var dismiss
    let onStart: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            header
            Divider()
            VStack(alignment: .leading, spacing: 16) {
                step("waveform.circle.fill",
                     L.t("Слушает встречу", "Listens to the meeting", "旁听会议"),
                     L.t("Берёт звук из микрофона и из динамиков — обе стороны разговора. Никаких ботов в звонке: собеседники ничего не увидят.",
                         "Takes audio from the microphone and the speakers — both sides of the conversation. No bots joining the call: the others see nothing.",
                         "同时采集麦克风与扬声器的声音——对话双方都在内。不会有机器人加入通话：对方看不到任何东西。"))
                step("lock.laptopcomputer",
                     L.t("Всё остаётся на этом Mac", "Everything stays on this Mac", "一切都留在这台 Mac 上"),
                     L.t("Распознавание речи и разбор идут локально. Записи, стенограммы и тезисы никуда не отправляются.",
                         "Speech recognition and analysis run locally. Recordings, transcripts and theses are never sent anywhere.",
                         "语音识别与分析均在本地进行。录音、逐字稿与要点不会发送到任何地方。"))
                step("list.bullet.rectangle",
                     L.t("После встречи — сам напишет", "Writes it up afterwards", "会后自动整理"),
                     L.t("Стенограмма, тезисы, минутки и решения складываются в папку встреч. Потом можно спросить: «что обсуждали вчера?»",
                         "Transcript, theses, minutes and decisions land in the meetings folder. Later you can ask: \"what did we discuss yesterday?\"",
                         "逐字稿、要点、纪要与决定都会存入会议文件夹。之后你可以问：「昨天讨论了什么？」"))
            }
            Spacer(minLength: 0)
            footer
        }
        .padding(28)
        .frame(width: 520, height: 460)
        .onAppear(perform: warmUpFolderAccess)
    }

    /// Спрашиваем доступ к папке заранее, пока человек читает этот экран.
    ///
    /// Граф встреч живёт в iCloud, и первое обращение к нему поднимает
    /// системный запрос «приложение хочет получить доступ к файлам iCloud
    /// Drive». Раньше он прилетал в момент, когда демон дописывал граф, — то
    /// есть посреди встречи, поверх разговора. Безобидное чтение каталога
    /// здесь переносит вопрос в спокойную минуту.
    private func warmUpFolderAccess() {
        DispatchQueue.global(qos: .utility).async {
            // Греем ФАКТИЧЕСКУЮ папку графа, а не жёсткий путь Obsidian:
            // у пользователя с локальным vault прогрев по чужому пути ничего
            // не открывал, и системный запрос всё равно прилетал посреди
            // встречи — ровно тогда, когда его тут и пытаются предотвратить.
            let target = AppSettings.graphDir ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Mobile Documents/iCloud~md~obsidian/Documents")
            _ = try? FileManager.default.contentsOfDirectory(atPath: target.path)
        }
    }

    private var header: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(Theme.brand)
                    .frame(width: 46, height: 46)
                Text(L.t("Ч", "C", "C"))   // литера логотипа
                    .font(.system(.title2, design: .rounded, weight: .bold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(L.t("Чароит", "Charoite", "Charoite")).font(.system(.title2, weight: .bold))
                Text(L.t("Суфлёр рабочих встреч", "Meeting copilot", "会议助手")).foregroundStyle(.secondary)
            }
        }
    }

    private func step(_ icon: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 17))
                .foregroundStyle(Theme.accent)
                .frame(width: 24)
                .accessibilityHidden(true)      // смысл несёт текст рядом
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(text)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Про микрофон предупреждаем ДО нажатия: системный запрос,
            // прилетевший посреди встречи, — худший момент из возможных.
            Label(L.t("При первом запуске macOS спросит доступ к микрофону — без него слушать нечего.",
                      "On first run macOS will ask for microphone access — without it there is nothing to listen to.",
                      "首次运行时 macOS 会请求麦克风权限——没有它就无从旁听。"),
                  systemImage: "info.circle")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            // новичку без встреч есть что пощупать: демо-граф в комплекте
            Label(L.t("Нет встреч? В комплекте демо-граф (папка demo/) — наведи на него graph_dir и спроси «что решили по платёжному провайдеру?»",
                      "No meetings yet? A demo graph ships in demo/ — point graph_dir at it and ask \"what did we decide about the payment provider?\"",
                      "还没有会议？随附示例图谱（demo/ 文件夹）——把 graph_dir 指向它，然后问「支付服务商的事定了什么？」"),
                  systemImage: "sparkles")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)

            HStack {
                Button(L.t("Осмотрюсь сам", "I'll look around", "我自己看看")) {
                    seen = true
                    dismiss()
                }
                Spacer()
                Button {
                    seen = true
                    dismiss()
                    onStart()
                } label: {
                    Text(L.t("Начать слушать встречу", "Start listening", "开始旁听"))
                        .fontWeight(.medium)
                        .padding(.horizontal, 6)
                }
                .keyboardShortcut(.defaultAction)
            }
        }
    }
}

#endif
