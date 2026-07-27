import SwiftUI

/// Главный экран v1: одна большая кнопка. Всё остальное делает Mac.
struct RecordView: View {
    @StateObject private var rec = Recorder()
    @State private var kind: Recorder.Kind = .meeting

    var body: some View {
        VStack(spacing: 24) {
            Picker("Тип записи", selection: $kind) {
                ForEach(Recorder.Kind.allCases) { k in
                    Text(k.rawValue).tag(k)
                }
            }
            .pickerStyle(.segmented)
            .disabled(rec.isRecording)
            .padding(.horizontal)

            Spacer()

            Button {
                rec.isRecording ? rec.stop() : rec.start(kind: kind)
            } label: {
                ZStack {
                    Circle()
                        .fill(Theme.record)
                        .frame(width: 132, height: 132)
                        .shadow(color: Theme.accent.opacity(0.45), radius: 18, y: 8)
                    RoundedRectangle(cornerRadius: rec.isRecording ? 10 : 66)
                        .fill(.white)
                        .frame(width: rec.isRecording ? 40 : 44,
                               height: rec.isRecording ? 40 : 44)
                        .animation(.spring(response: 0.3), value: rec.isRecording)
                }
            }
            .accessibilityLabel(rec.isRecording ? "Остановить запись" : "Начать запись")

            Text(timeString(rec.elapsed))
                .font(.system(size: 34, weight: .thin, design: .default))
                .monospacedDigit()
                .opacity(rec.isRecording ? 1 : 0.35)

            LevelWave(level: rec.level)
                .frame(height: 28)
                .opacity(rec.isRecording ? 1 : 0.2)

            Spacer()

            Text(rec.lastResult ?? "Стоп — и запись уедет на Mac через iCloud.\nДальше он сам: стенограмма, минутки, граф.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 28)
                .padding(.bottom, 12)
        }
        .padding(.vertical)
        .navigationTitle("Запись")
    }

    private func timeString(_ t: TimeInterval) -> String {
        let s = Int(t)
        return String(format: "%02d:%02d", s / 60, s % 60)
    }
}

/// Простая живая волна уровня — без буфера истории, честные текущие 12 столбиков.
struct LevelWave: View {
    var level: Float
    var body: some View {
        HStack(alignment: .center, spacing: 4) {
            ForEach(0..<12, id: \.self) { i in
                Capsule()
                    .fill(Theme.accent.opacity(0.8))
                    .frame(width: 4,
                           height: 6 + CGFloat(level) * CGFloat(6 + (i * 7) % 22))
            }
        }
        .animation(.linear(duration: 0.18), value: level)
    }
}

#Preview { NavigationStack { RecordView() } }
