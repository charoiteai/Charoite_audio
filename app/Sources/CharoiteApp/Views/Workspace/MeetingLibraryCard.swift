import SwiftUI

#if os(macOS)

/// Карточка ленты библиотеки (макет MOBILE_2026-08, macOS-экран 3).
/// Вынесена из MeetingLibraryView: там полоса недели, поиск и лента дня,
/// и вместе с карточкой файл уходил за 750 строк.
extension MeetingLibraryView {

    /// Карточка — VStack с рамкой; кнопка выбора оборачивает заголовок и
    /// тело, а чипы глубин и «Повторить обработку» стоят рядом, не внутри её
    /// label: кнопка в label другой кнопки — неопределённый случай SwiftUI,
    /// и клик по чипу мог бы уходить во внешнюю (DeepSeek, круг-1).
    func recordCard(_ record: MeetingRecord, bucket: LibraryScreenPolicy.Bucket) -> some View {
        let isSelected = navigation.selectedMeetingID == record.id
        let shape = RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
        return VStack(alignment: .leading, spacing: 5) {
            Button {
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
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(isSelected ? .isSelected : [])
            cardActions(record)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(shape.fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(shape.strokeBorder(isSelected ? Theme.accent.opacity(0.45) : Color.primary.opacity(0.06),
                                    lineWidth: 1))
        // Вся рамка — зона выбора: поля и пустое место в строке действий
        // тоже выбирают встречу (DeepSeek, круг-2). Кнопки внутри стоят
        // глубже и забирают свои клики сами; вложенных кнопок нет.
        .contentShape(shape)
        .onTapGesture { navigation.selectedMeetingID = record.id }
        .opacity(record.state == .empty ? 0.75 : 1)
    }

    /// Тело карточки по состоянию: готовой — числа с источником и суть;
    /// собирающейся — стадия конвейера; упавшей — ошибка словами и причина;
    /// без речи — результат, не ошибка.
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
            let gist = record.card.gist?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !gist.isEmpty {
                Text(gist).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            } else if meta.isEmpty {
                // Карточка без единого слова — одна точка: состояние обязано
                // быть и словом, для глаз и для VoiceOver.
                Text(stateText(.ready)).font(.caption).foregroundStyle(.secondary)
            }
        case .processing:
            Text(MeetingProcessingPolicy.stageText(for: record.snapshot))
                .font(.caption).foregroundStyle(Theme.accent)
        case .error:
            Text(stateText(.error)).font(.caption).foregroundStyle(Theme.warning)
            // Причина — как её записал конвейер, отдельной строкой: она
            // приходит на языке конвейера, а не интерфейса.
            if let reason = record.snapshot.error?.trimmingCharacters(in: .whitespacesAndNewlines),
               !reason.isEmpty {
                Text(reason).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case .empty, .unknown:
            Text(stateText(record.state)).font(.caption).foregroundStyle(.secondary)
        }
    }

    /// Действия под телом: мини-сегмент глубин у готовой встречи, повтор —
    /// у упавшей. Глубины считаются здесь, по живому диску: заметку мог
    /// убрать forget_meeting.py при неизменном статусе, а кэш врал бы.
    @ViewBuilder
    func cardActions(_ record: MeetingRecord) -> some View {
        switch record.state {
        case .ready:
            let depths = MeetingCardDepth.available(card: record.card, meeting: record.snapshot)
            if depths.count > 1 {
                depthChips(record, available: depths)
            }
        case .error:
            if processing.canRetry(record.snapshot) {
                HStack {
                    Spacer(minLength: 0)
                    // Тихая, не ссылка: повтор запускает конвейер, а ссылка по
                    // шкале кнопок ничего не меняет.
                    Button(L.t("Повторить обработку", "Retry processing", "重试处理")) {
                        // Повтор и выбирает встречу: человек смотрит на то,
                        // что перезапустил, а не на прежнюю карточку.
                        navigation.selectedMeetingID = record.id
                        processing.retry(record.snapshot)
                    }
                    .charoite(.quiet, .s)
                }
            }
        case .processing, .empty, .unknown:
            EmptyView()
        }
    }

    /// Мини-сегмент глубин: есть — индиго, нет — пунктир без клика. Клик
    /// выбирает встречу и открывает карточку сразу на этой глубине.
    func depthChips(_ record: MeetingRecord, available: [MeetingCardDepth]) -> some View {
        HStack(spacing: 3) {
            ForEach(MeetingCardDepth.allCases) { depth in
                let has = available.contains(depth)
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

    /// Сегодня — время; на этой неделе — день недели и число; раньше и впереди — дата.
    func when(_ date: Date, bucket: LibraryScreenPolicy.Bucket) -> String {
        switch bucket {
        case .today: return Self.timeFormatter.string(from: date)
        case .week: return Self.weekDayDateFormatter.string(from: date)
        case .earlier, .upcoming: return Self.shortDateFormatter.string(from: date)
        }
    }

    /// Локаль продукта, как у всех дат в приложении (L10n): под русским
    /// заголовком системный форматтер писал бы «Sat 17.08».
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
