import SwiftUI

#if os(macOS)

/// Экран «Календарь»: события дня рядом с тем, что реально записано.
///
/// Ценность экрана — отметка «записана»: по неделе видно, какие встречи
/// попали в архив, какие прошли мимо, и что записалось вне календаря.
/// Работает и без доступа к календарю — тогда остаются только записи,
/// а подключение предлагается карточкой, не пустым экраном.
struct CalendarDayView: View {
    @ObservedObject private var calendar = CalendarService.shared
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var tasks = TasksService.shared
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false

    @State private var selectedDay = Calendar.current.startOfDay(for: Date())
    @State private var events: [CalendarService.DayEvent] = []

    var body: some View {
        VStack(spacing: 0) {
            weekStrip
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if calendar.accessGranted != true { accessCard }
                    dayFeed
                }
                .padding(18)
                .frame(maxWidth: 760, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .onAppear { reloadEvents() }
        .onChange(of: selectedDay) { _, _ in reloadEvents() }
        .onChange(of: calendar.accessGranted) { _, _ in reloadEvents() }
    }

    private func reloadEvents() {
        events = calendar.events(on: selectedDay)
    }

    // MARK: - Неделя

    private var weekDays: [Date] {
        let cal = Calendar.current
        let start = cal.dateInterval(of: .weekOfYear, for: selectedDay)?.start ?? selectedDay
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    private var recordedDays: Set<Date> {
        Set(repository.records.map { Calendar.current.startOfDay(for: $0.startedAt) })
    }

    private var weekStrip: some View {
        HStack(spacing: 6) {
            Button { shiftWeek(-1) } label: { Image(systemName: "chevron.left") }
                .buttonStyle(.borderless)
                .accessibilityLabel(Text(L.t("Прошлая неделя", "Previous week", "上一周")))
            ForEach(weekDays, id: \.self) { day in
                dayCell(day)
            }
            Button { shiftWeek(1) } label: { Image(systemName: "chevron.right") }
                .buttonStyle(.borderless)
                .accessibilityLabel(Text(L.t("Следующая неделя", "Next week", "下一周")))
            Spacer()
            if !Calendar.current.isDate(selectedDay, inSameDayAs: Date()) {
                Button(L.t("Сегодня", "Today", "今天")) {
                    selectedDay = Calendar.current.startOfDay(for: Date())
                }
                .buttonStyle(.link)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
    }

    private func dayCell(_ day: Date) -> some View {
        let cal = Calendar.current
        let isSelected = cal.isDate(day, inSameDayAs: selectedDay)
        let isToday = cal.isDate(day, inSameDayAs: Date())
        let hasRecords = recordedDays.contains(cal.startOfDay(for: day))
        return Button {
            selectedDay = cal.startOfDay(for: day)
        } label: {
            VStack(spacing: 3) {
                Text(Self.weekdayFormatter.string(from: day))
                    .font(.caption2)
                    .foregroundStyle(isSelected ? Color.white.opacity(0.85) : .secondary)
                Text(Self.dayNumberFormatter.string(from: day))
                    .font(.callout.weight(isToday ? .bold : .regular).monospacedDigit())
                    .foregroundStyle(isSelected ? .white : (isToday ? Theme.accent : .primary))
                Circle()
                    .fill(hasRecords ? (isSelected ? Color.white : Theme.accent) : Color.clear)
                    .frame(width: 5, height: 5)
            }
            .frame(width: 44)
            .padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 8)
                .fill(isSelected ? Theme.accent : Color.clear))
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(Self.fullDayFormatter.string(from: day)))
    }

    private func shiftWeek(_ direction: Int) {
        if let day = Calendar.current.date(byAdding: .day, value: direction * 7, to: selectedDay) {
            selectedDay = Calendar.current.startOfDay(for: day)
        }
    }

    // MARK: - Лента дня

    private var dayRecords: [MeetingRecord] {
        repository.records
            .filter { Calendar.current.isDate($0.startedAt, inSameDayAs: selectedDay) }
            .sorted { $0.startedAt < $1.startedAt }
    }

    private var board: CalendarDayMatch.DayBoard {
        CalendarDayMatch.board(
            events: events,
            recordStarts: dayRecords.map { ($0.id, $0.startedAt) })
    }

    @ViewBuilder
    private var dayFeed: some View {
        let board = board
        if board.slots.isEmpty && board.looseRecordIDs.isEmpty {
            CharoiteEmptyState(
                title: L.t("Тихий день", "A quiet day", "安静的一天"),
                explanation: L.t(
                    "Ни событий в календаре, ни записей за этот день.",
                    "No calendar events and no recordings on this day.",
                    "这一天没有日程，也没有录音。"))
        } else {
            ForEach(board.slots) { slot in
                eventCard(slot)
            }
            if !board.looseRecordIDs.isEmpty {
                Text(L.t("Записи вне календаря", "Recordings outside the calendar", "日历之外的录音"))
                    .font(.headline)
                    .padding(.top, board.slots.isEmpty ? 0 : 8)
                ForEach(board.looseRecordIDs, id: \.self) { id in
                    if let record = repository.record(id: id) {
                        recordRow(record)
                    }
                }
            }
        }
    }

    private func eventCard(_ slot: CalendarDayMatch.Slot) -> some View {
        let event = slot.event
        let now = Date()
        let end = event.end ?? event.start.addingTimeInterval(30 * 60)
        let isPast = end < now
        let isLive = event.start <= now && now <= end
        let missed = isPast && slot.recordIDs.isEmpty
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .trailing, spacing: 1) {
                    Text(Self.timeFormatter.string(from: event.start))
                        .font(.callout.weight(.medium).monospacedDigit())
                    if let end = event.end {
                        Text(Self.timeFormatter.string(from: end))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(width: 44, alignment: .trailing)
                VStack(alignment: .leading, spacing: 2) {
                    Text(event.title)
                        .font(.callout.weight(.medium))
                        .lineLimit(2)
                    if event.attendees > 0 {
                        Label(L.t("\(event.attendees) участников", "\(event.attendees) attendees", "\(event.attendees) 位参与者"),
                              systemImage: "person.2")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if isLive {
                    Text(L.t("идёт", "now", "进行中"))
                        .font(.caption.weight(.medium))
                        .foregroundStyle(Theme.accent)
                } else if missed {
                    Text(L.t("без записи", "not recorded", "未录音"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            ForEach(slot.recordIDs, id: \.self) { id in
                if let record = repository.record(id: id) {
                    recordRow(record)
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: Theme.radiusCard)
            .fill(isLive ? Theme.accent.opacity(0.055) : Color.primary.opacity(0.03)))
        .opacity(missed ? 0.62 : 1)
    }

    /// Строка записанной встречи: галка, длительность, поручения — и переход
    /// к карточке. Одна и та же для события и для записи вне календаря.
    private func recordRow(_ record: MeetingRecord) -> some View {
        let open = tasks.items(for: record.id, includeDone: false).count
        return Button {
            navigation.open(.meetings, meetingID: record.id)
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Theme.ok)
                VStack(alignment: .leading, spacing: 1) {
                    Text(record.title)
                        .font(.callout)
                        .lineLimit(1)
                    Text(recordDetail(record, openTasks: open))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 7).fill(Theme.surfaceMemory))
            .contentShape(RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(L.t("Записана: \(record.title)", "Recorded: \(record.title)", "已录音：\(record.title)")))
    }

    private func recordDetail(_ record: MeetingRecord, openTasks: Int) -> String {
        var parts = [L.t("записана в ", "recorded at ", "录音于 ")
            + Self.timeFormatter.string(from: record.startedAt)]
        if let duration = MeetingDurationCache.durationText(for: record.snapshot) {
            parts.append(duration)
        }
        if openTasks > 0 {
            parts.append(L.t("\(openTasks) открытых поручений",
                             "\(openTasks) open action items",
                             "\(openTasks) 项未完成任务"))
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Доступ

    private var accessCard: some View {
        CharoiteEmptyState(
            title: L.t("Календарь не подключён", "Calendar is not connected", "未连接日历"),
            explanation: L.t(
                "Charoite читает только названия и время событий — локально, из системного календаря. Подключите, чтобы видеть встречи дня рядом с их записями. Ниже — то, что уже записано.",
                "Charoite reads only event titles and times — locally, from the system calendar. Connect it to see the day's meetings next to their recordings. Below is what has been recorded already.",
                "Charoite 仅在本地读取系统日历中事件的标题与时间。连接后可将当天的会议与其录音并排查看。下方是已有的录音。")) {
            Button(L.t("Подключить календарь", "Connect calendar", "连接日历")) {
                calendarBriefs = true
                calendar.enable(askForNotifications: true)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.accent)
        }
        .padding(.bottom, 6)
    }

    // MARK: - Форматтеры

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let weekdayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("EE")
        return f
    }()

    private static let dayNumberFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.dateFormat = "d"
        return f
    }()

    private static let fullDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("EEEE d MMMM")
        return f
    }()
}

#endif
