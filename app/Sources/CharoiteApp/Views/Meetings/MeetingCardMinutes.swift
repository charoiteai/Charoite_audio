import SwiftUI

#if os(macOS)

/// Подробный протокол в карточке встречи: переключатель и разделы Минуток.
/// Вынесено из MeetingCardView — там и без того больше четырёхсот строк.
extension MeetingCardView {
    /// Переключатель показывается, только когда есть что показывать подробно:
    /// у встреч без Минуток он был бы кнопкой в никуда.
    @ViewBuilder
    var detailPicker: some View {
        if let minutes = card.minutes, !minutes.isEmpty {
            HStack(spacing: 8) {
                Picker("", selection: $detailed) {
                    Text(L.t("Подробно", "Detailed", "详细")).tag(true)
                    Text(L.t("Коротко", "Brief", "简要")).tag(false)
                }
                .pickerStyle(.segmented).labelsHidden().frame(width: 190)
                .accessibilityLabel(L.t("Подробность протокола",
                                        "Level of detail", "记录详细程度"))
                if minutes.isDraft && detailed {
                    Text(L.t("черновик: встреча ещё шла",
                             "draft: the meeting was still running",
                             "草稿：会议仍在进行"))
                        .font(.caption).foregroundStyle(Theme.warning)
                }
                Spacer()
            }
        }
    }

    @ViewBuilder
    func minutesSections(_ minutes: MeetingMinutes) -> some View {
        minutesSection(L.t("Темы", "Topics", "议题"), mark: "•", items: minutes.topics)
        minutesSection(L.t("Решили", "Decided", "决定"), mark: "⚑", items: minutes.decisions)
        // Поручения остаются интерактивными: чекбоксы пишут прямо в markdown,
        // и терять их в подробном виде было бы шагом назад.
        taskSection
        minutesSection(L.t("Открытые вопросы", "Open questions", "待解决问题"),
                       mark: "?", items: minutes.openQuestions)
        minutesSection(L.t("Риски", "Risks", "风险"), mark: "!", items: minutes.risks)
    }

    @ViewBuilder
    func minutesSection(_ title: String, mark: String,
                        items: [MeetingMinutes.Item]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.subheadline.weight(.semibold))
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(item.level == 0 ? mark : "–")
                            .foregroundStyle(item.level == 0 ? Theme.accent : Color.secondary)
                        Text(item.text)
                            .foregroundStyle(item.level == 0 ? .primary : .secondary)
                    }
                    .font(item.level == 0 ? .callout : .caption)
                    .padding(.leading, CGFloat(item.level) * 14)
                }
            }
        }
    }

}

#endif
