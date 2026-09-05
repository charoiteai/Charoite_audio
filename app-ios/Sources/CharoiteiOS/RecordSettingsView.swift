import SwiftUI

/// Шестерёнка экрана записи: «писать сразу» и честные границы iOS.
struct RecordSettingsView: View {
    @AppStorage("record.autostart") private var autostart = true
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle(L.t("Писать сразу при открытии", "Record as soon as the app opens", "打开应用即开始录音"),
                           isOn: $autostart)
                } footer: {
                    Text(L.t("Открыли приложение — запись уже идёт, без второго нажатия; тип — последний выбранный; работает после того, как выбрана папка доставки. Если микрофон занят звонком или другим приложением, приложение ждёт и стартует само, когда микрофон отдадут, — пока оно открыто: свёрнутое приложение iOS стартовать не даёт; вернётесь в течение 30 минут — стартует при возврате. Стоп — кнопкой или из плашки на локскрине.",
                             "Open the app and the recording is already running, no second tap; the kind is the last one you picked; works once the delivery folder is chosen. If a call or another app holds the microphone, the app waits and starts by itself when the microphone is released — while the app is open: iOS never lets a backgrounded app start; come back within 30 minutes and it starts on return. Stop with the button or from the lock-screen banner.",
                             "打开应用即开始录音，无需再按一次；类型为上次选择的；在选定投递文件夹后生效。如果麦克风被通话或其他应用占用，应用会等待并在麦克风释放后自动开始——需保持应用打开：iOS 不允许后台应用开始录音；30 分钟内返回则在返回时开始。用按钮或锁屏横幅停止。"))
                }
                Section(L.t("Без рук", "Hands-free", "免提")) {
                    Text(L.t("Siri, Команды и кнопка действия: «Начать запись в Charoite». Из фона iOS запись не запускает — приложение откроется само и начнёт писать.",
                             "Siri, Shortcuts and the Action button: “Start recording in Charoite”. iOS never starts a recording from the background — the app opens itself and starts.",
                             "Siri、快捷指令和操作按钮：“用 Charoite 开始录音”。iOS 不允许从后台开始录音——应用会自行打开并开始。"))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section(L.t("Что iOS не даёт", "What iOS does not allow", "iOS 不允许的")) {
                    Text(L.t("Сам звонок на iPhone не пишет ни одно приложение: микрофон принадлежит звонку. Звонок посреди записи — пауза; после него продолжается тот же файл, а если вход не вернули за три попытки — записанное сохраняется. Звонки пишет Mac.",
                             "No app records the call itself on iPhone: the microphone belongs to the call. A call during a recording is a pause; the same file continues afterwards, and if the input is not returned after three attempts, what was recorded is saved. Calls are recorded by the Mac.",
                             "iPhone 上没有任何应用能录下通话本身：麦克风归通话所有。录音中来电只是暂停；之后同一文件继续，若三次尝试后仍未取回输入，则保存已录内容。通话由 Mac 录制。"))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle(L.t("Запись", "Recording", "录音"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(L.t("Готово", "Done", "完成")) { dismiss() }
                }
            }
        }
    }
}

#Preview { RecordSettingsView() }
