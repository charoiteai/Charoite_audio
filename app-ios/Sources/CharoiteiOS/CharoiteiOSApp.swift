import ActivityKit
import SwiftUI

@main
struct CharoiteiOSApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
                .task {
                    // Приложение только что стартовало — значит, никакая запись
                    // сейчас идти не может. Всё, что осталось от прошлого
                    // запуска, — следы аварии, и с ними надо разобраться до
                    // того, как человек нажмёт «Запись».

                    // 1. Плашка на локскрине, пережившая смерть приложения,
                    //    показывала бегущий таймер несуществующей записи —
                    //    интерфейс, который активно врёт, хуже отсутствующего.
                    for a in Activity<RecordActivityAttributes>.activities {
                        await a.end(nil, dismissalPolicy: .immediate)
                    }
                    // 2. Файл недописанной записи: переносим в очередь, чтобы
                    //    записанное до сбоя всё-таки уехало на Mac.
                    Inbox.rescueOrphans()
                }
        }
    }
}
