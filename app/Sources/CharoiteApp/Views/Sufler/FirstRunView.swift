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
                     "Слушает встречу",
                     "Берёт звук из микрофона и из динамиков — обе стороны разговора. "
                     + "Никаких ботов в звонке: собеседники ничего не увидят.")
                step("lock.laptopcomputer",
                     "Всё остаётся на этом Mac",
                     "Распознавание речи и разбор идут локально. Записи, стенограммы и "
                     + "тезисы никуда не отправляются.")
                step("list.bullet.rectangle",
                     "После встречи — сам напишет",
                     "Стенограмма, тезисы, минутки и решения складываются в папку встреч. "
                     + "Потом можно спросить: «что обсуждали вчера?»")
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
            let icloud = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Mobile Documents/iCloud~md~obsidian/Documents")
            _ = try? FileManager.default.contentsOfDirectory(atPath: icloud.path)
        }
    }

    private var header: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(LinearGradient(colors: [Color(hex: "#6366F1"), Color(hex: "#8B5CF6")],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                    .frame(width: 46, height: 46)
                Text("Ч")
                    .font(.system(.title2, design: .rounded, weight: .bold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("Чароит").font(.system(.title2, weight: .bold))
                Text("Суфлёр рабочих встреч").foregroundStyle(.secondary)
            }
        }
    }

    private func step(_ icon: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 17))
                .foregroundStyle(Color(hex: "#6366F1"))
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
            Label("При первом запуске macOS спросит доступ к микрофону — без него слушать нечего.",
                  systemImage: "info.circle")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            // новичку без встреч есть что пощупать: демо-граф в комплекте
            Label("Нет встреч? В комплекте демо-граф (папка demo/) — наведи на него graph_dir и спроси «что решили по платёжному провайдеру?»",
                  systemImage: "sparkles")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)

            HStack {
                Button("Осмотрюсь сам") {
                    seen = true
                    dismiss()
                }
                Spacer()
                Button {
                    seen = true
                    dismiss()
                    onStart()
                } label: {
                    Text("Начать слушать встречу")
                        .fontWeight(.medium)
                        .padding(.horizontal, 6)
                }
                .keyboardShortcut(.defaultAction)
            }
        }
    }
}

#endif
