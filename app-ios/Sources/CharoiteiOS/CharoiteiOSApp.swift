import SwiftUI

@main
struct CharoiteiOSApp: App {
    var body: some Scene {
        WindowGroup {
            NavigationStack { RecordView() }
                .tint(Theme.accent)
        }
    }
}
