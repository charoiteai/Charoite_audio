import ActivityKit
import SwiftUI
import WidgetKit

@main
struct CharoiteWidgets: WidgetBundle {
    var body: some Widget {
        RecordLiveActivity()
    }
}

/// Таймер записи в Dynamic Island и на локскрине. Телефон на столе
/// экраном вниз — а запись всё равно видна и досягаема.
struct RecordLiveActivity: Widget {
    private let accent = Color(red: 0.39, green: 0.40, blue: 0.95)   // Theme.accent

    var body: some WidgetConfiguration {
        ActivityConfiguration(for: RecordActivityAttributes.self) { context in
            // Локскрин / баннер
            HStack(spacing: 10) {
                Image(systemName: "waveform")
                    .foregroundStyle(accent)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Charoite · \(context.attributes.kind)")
                        .font(.footnote.weight(.semibold))
                    Text("запись идёт")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                timer(context)
                    .font(.title3.weight(.light))
            }
            .padding(14)
            .activityBackgroundTint(Color.black.opacity(0.6))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label(context.attributes.kind, systemImage: "waveform")
                        .font(.footnote)
                        .foregroundStyle(accent)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    timer(context)
                        .font(.title3.weight(.light))
                }
                DynamicIslandExpandedRegion(.bottom) {
                    Text("Стоп — в приложении. Запись уедет на Mac.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            } compactLeading: {
                Image(systemName: "waveform")
                    .foregroundStyle(accent)
            } compactTrailing: {
                timer(context)
                    .font(.caption2)
                    .frame(maxWidth: 44)
            } minimal: {
                Image(systemName: "waveform")
                    .foregroundStyle(accent)
            }
        }
    }

    /// Живой таймер без обновлений из приложения — система считает сама.
    private func timer(_ context: ActivityViewContext<RecordActivityAttributes>) -> some View {
        Text(timerInterval: context.state.startedAt...Date(
            timeInterval: 8 * 3600, since: context.state.startedAt),
             countsDown: false)
            .monospacedDigit()
            .multilineTextAlignment(.trailing)
    }
}
