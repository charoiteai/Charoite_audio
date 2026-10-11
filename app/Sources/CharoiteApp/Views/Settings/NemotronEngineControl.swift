import SwiftUI

#if os(macOS)

/// Кнопка Nemotron (№622): один вид поверх `NemotronEngineService` и
/// `NemotronVisibleActions.make` — в настройках, в мастере и в поповере
/// «Сегодня». Своих правил не держит. Ни одна кнопка не берёт Enter: в мастере
/// он у «Начать слушать встречу».
struct NemotronEngineControl: View {
    @ObservedObject private var service = NemotronEngineService.shared
    @ObservedObject private var activity = EngineInstallActivity.shared
    @ObservedObject private var sufler = SuflerService.shared

    private var visible: NemotronVisibleActions {
        NemotronVisibleActions.make(action: service.action, plan: service.plan,
                                    busy: activity.busy, recording: sufler.hasActiveLifecycle)
    }

    var body: some View {
        let v = visible
        VStack(alignment: .leading, spacing: 6) {
            if let title = v.title {
                Text(title).font(.subheadline.weight(.semibold))
            }
            if let detail = v.detail {
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let outcome = v.outcome {
                Label(outcome.text, systemImage: outcome.success ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(outcome.success ? Color.green : Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let progress = v.progress {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.mini)
                    Text(progress).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            if v.cancel != .hidden {
                Button(L.t("Отмена", "Cancel", "取消")) { service.cancel() }
                    .charoite(.regular, .s)
                    .disabled(v.cancel == .disabled)
            }
            ForEach(v.warnings, id: \.self) { text in
                Label(text, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if v.voiceSetButton {
                DiarizationInstallButton()
            }
            if let consent = v.consent {
                ConsentBlock(consent: consent)
            }
            buttons(v)
            if v.retry {
                Button(L.t("Повторить", "Retry", "重试")) { service.requestPlan() }
                    .charoite(.regular, .s)
            }
        }
        .onAppear { service.requestPlanIfNeeded() }
    }

    @ViewBuilder
    private func buttons(_ v: NemotronVisibleActions) -> some View {
        let rows: [(NemotronVisibleActions.Button, CharoiteRole, () -> Void)] = [
            v.install.map { ($0, .prominent, { service.startInstall() }) },
            v.reinstall.map { ($0, .regular, { service.startInstall() }) },
            v.enable.map { ($0, .prominent, { service.startEnable() }) },
            v.disable.map { ($0, .regular, { service.disable() }) },
        ].compactMap { $0 }
        ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
            VStack(alignment: .leading, spacing: 2) {
                Button(row.0.title, action: row.2)
                    .charoite(row.1, .s)
                    .disabled(!row.0.enabled)
                if let note = row.0.note, !note.isEmpty {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

/// Размеры, лицензия, смена движка и сеть — до нажатия «Поставить и включить».
private struct ConsentBlock: View {
    let consent: NemotronVisibleActions.Consent

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach([consent.sizes, consent.need, consent.voiceSetNote, consent.memoryNote].compactMap { $0 },
                    id: \.self) { line in
                Text(line).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let name = consent.licenseName {
                HStack(spacing: 4) {
                    Text(L.t("лицензия весов:", "weights licence:", "权重许可：")).font(.caption).foregroundStyle(.secondary)
                    if let url = consent.licenseURL {
                        Link(name, destination: url).font(.caption)
                    } else {
                        Text(name).font(.caption)
                    }
                }
            }
            Text(consent.licenseNote).font(.caption).foregroundStyle(.secondary)
            Text(consent.switchNote).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !consent.network.isEmpty {
                DisclosureGroup(L.t("куда пойдёт в сеть", "network access", "将访问的网络")) {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(consent.network, id: \.self) { line in
                            Text(line).font(.caption2).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .font(.caption)
            }
        }
    }
}

/// Компактная кнопка «Nemotron…» на «Сегодня»: видна по действию или беде
/// (`showOnToday`), действие — в поповере. Лист мастера не открывает.
struct NemotronTodayButton: View {
    @ObservedObject private var service = NemotronEngineService.shared
    @ObservedObject private var activity = EngineInstallActivity.shared
    @ObservedObject private var sufler = SuflerService.shared
    @State private var shown = false

    var body: some View {
        let v = NemotronVisibleActions.make(action: service.action, plan: service.plan,
                                            busy: activity.busy, recording: sufler.hasActiveLifecycle)
        Group {
            if v.showOnToday {
                Button(L.t("Nemotron…", "Nemotron…", "Nemotron…")) { shown = true }
                    .charoite(.regular, .s)
                    .popover(isPresented: $shown, arrowEdge: .bottom) {
                        NemotronEngineControl()
                            .padding(14)
                            .frame(width: 360)
                    }
            }
        }
        .onAppear { service.requestPlanIfNeeded() }
    }
}

#endif
