import ActivityKit
import AppIntents
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
                    Text(L.t("запись идёт", "recording", "录音中"))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                timer(context)
                    .font(.title3.weight(.light))
                stopButton
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
                    VStack(spacing: 6) {
                        stopButton
                        Text(L.t("Запись уедет на Mac.",
                                 "The recording travels to the Mac.",
                                 "录音会送往 Mac。"))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
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

    /// Стоп, не трогая телефон: приложение будит система, интерфейс не
    /// поднимается. Раньше здесь стояла надпись «Стоп — в приложении», и
    /// после встречи писались лишние минуты, пока телефон не разблокируют.
    @ViewBuilder
    private var stopButton: some View {
        if #available(iOS 17.0, *) {
            Button(intent: StopRecordingIntent()) {
                Label(L.t("Стоп", "Stop", "停止"), systemImage: "stop.fill")
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
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
