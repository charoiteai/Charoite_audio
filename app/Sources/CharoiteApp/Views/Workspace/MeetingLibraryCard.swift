import SwiftUI

#if os(macOS)

/// Карточка ленты библиотеки (макет MOBILE_2026-08, macOS-экран 3).
/// Вынесена из MeetingLibraryView: там полоса недели, поиск и лента дня,
/// и вместе с карточкой файл уходил за 750 строк.
extension MeetingLibraryView {

    func recordCard(_ record: MeetingRecord, bucket: LibraryScreenPolicy.Bucket) -> some View {
        let isSelected = navigation.selectedMeetingID == record.id
        let shape = RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
        return Button {
            navigation.selectedMeetingID = record.id
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 7) {
                    stateDot(record.state)
                    Text(record.title).font(.callout.weight(.medium)).lineLimit(1)
                    Spacer(minLength: 4)
                    Text(when(record.startedAt, bucket: bucket))
                        .font(.caption2.monospacedDigit()).foregroundStyle(.tertiary)
                }
                cardBody(record)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(shape.fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(shape.strokeBorder(isSelected ? Theme.accent.opacity(0.45) : Color.primary.opacity(0.06),
                                        lineWidth: 1))
            .contentShape(shape)
            .opacity(record.state == .empty ? 0.75 : 1)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    /// Тело карточки по состоянию: готовой — числа с источником, суть и
    /// глубины; собирающейся — стадия конвейера; упавшей — ошибка словами и
    /// повтор на месте; без речи — результат, не ошибка.
    @ViewBuilder
    func cardBody(_ record: MeetingRecord) -> some View {
        switch record.state {
        case .ready:
            let meta = LibraryScreenPolicy.meta(duration: record.card.durationText,
                                                participants: record.card.participants.count,
                                                tasks: record.card.tasks.count)
            if !meta.isEmpty {
                Text(meta.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
            }
            if let gist = record.card.gist {
                Text(gist).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            if record.depths.count > 1 {
                depthChips(record)
            }
        case .processing:
            Text(MeetingProcessingPolicy.stageText(for: record.snapshot))
                .font(.caption).foregroundStyle(Theme.accent)
        case .error:
            Text(errorText(record))
                .font(.caption).foregroundStyle(Theme.warning)
                .fixedSize(horizontal: false, vertical: true)
            if processing.canRetry(record.snapshot) {
                HStack {
                    Spacer(minLength: 0)
                    Button(L.t("Повторить обработку", "Retry processing", "重试处理")) {
                        processing.retry(record.snapshot)
                    }
                    .charoite(.link, .s)
                }
            }
        case .empty, .unknown:
            Text(stateText(record.state)).font(.caption).foregroundStyle(.secondary)
        }
    }

    /// «Ошибка — исходник сохранён: <что случилось>» — словами, а не точкой.
    func errorText(_ record: MeetingRecord) -> String {
        let head = stateText(.error)
        guard let reason = record.snapshot.error?.trimmingCharacters(in: .whitespacesAndNewlines),
              !reason.isEmpty else { return head }
        return head + ": " + reason
    }

    /// Мини-сегмент глубин: есть — индиго, нет — пунктир без клика. Клик
    /// выбирает встречу и открывает карточку сразу на этой глубине.
    func depthChips(_ record: MeetingRecord) -> some View {
        HStack(spacing: 3) {
            ForEach(MeetingCardDepth.allCases) { depth in
                let has = record.depths.contains(depth)
                Button {
                    depthRaw = depth.rawValue
                    navigation.selectedMeetingID = record.id
                } label: {
                    Text(depth.title)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(has ? Theme.accent : Color.secondary.opacity(0.6))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(has ? Theme.accent.opacity(0.13) : Color.clear))
                        .overlay {
                            if !has {
                                Capsule().strokeBorder(style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                                    .foregroundStyle(Color.primary.opacity(0.18))
                            }
                        }
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(!has)
                .help(has
                      ? L.t("Открыть: \(depth.title)", "Open: \(depth.title)", "打开：\(depth.title)")
                      : L.t("У этой встречи нет: \(depth.title)", "This meeting has no \(depth.title)", "该会议没有：\(depth.title)"))
                .accessibilityLabel(Text(depth.title))
                .accessibilityValue(Text(has ? L.t("есть", "available", "有") : L.t("нет", "missing", "无")))
            }
        }
        .padding(.top, 1)
    }

    /// Точка состояния; «собирается» пульсирует — пока идёт обработка и
    /// пока человек не попросил систему убавить движение.
    @ViewBuilder
    func stateDot(_ state: MeetingProcessingSnapshot.State) -> some View {
        if state == .processing && !reduceMotion {
            PulsingDot(color: stateColor(state))
        } else {
            Circle().fill(stateColor(state)).frame(width: 7, height: 7)
        }
    }

    /// Сегодня — время; на этой неделе — день недели и число; раньше — дата.
    func when(_ date: Date, bucket: LibraryScreenPolicy.Bucket) -> String {
        switch bucket {
        case .today: return Self.timeFormatter.string(from: date)
        case .week: return Self.weekDayDateFormatter.string(from: date)
        case .earlier: return Self.shortDateFormatter.string(from: date)
        }
    }

    static let weekDayDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = L.locale
        f.setLocalizedDateFormatFromTemplate("EE d.MM")
        return f
    }()

    static let shortDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = L.locale
        f.setLocalizedDateFormatFromTemplate("d.MM.yy")
        return f
    }()

}

/// Пульс точки «собирается»: одна анимация непрозрачности на 7-пиксельном
/// круге, только у обрабатываемых карточек и только пока они на экране.
private struct PulsingDot: View {
    let color: Color
    @State private var dimmed = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 7, height: 7)
            .opacity(dimmed ? 0.35 : 1)
            .scaleEffect(dimmed ? 0.8 : 1)
            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: dimmed)
            .onAppear { dimmed = true }
            .onDisappear { dimmed = false }
    }
}

#endif
