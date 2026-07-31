import Foundation
import ServiceManagement
import SwiftUI

#if os(macOS)

/// Штатный login item macOS без собственного launchd-plist.
///
/// Регистрация всегда явная: приложение не добавляет себя в автозапуск без
/// тумблера пользователя. `requiresApproval` считаем выбранным состоянием —
/// человек уже попросил включить запуск, но macOS ждёт подтверждения в
/// «Объектах входа».
@MainActor
final class LaunchAtLoginService: ObservableObject {
    static let shared = LaunchAtLoginService()

    @Published private(set) var isEnabled = false
    @Published private(set) var note = ""

    private let service = SMAppService.mainApp

    private init() {
        refresh()
    }

    func setEnabled(_ enabled: Bool) {
        do {
            if enabled {
                try service.register()
            } else {
                try service.unregister()
            }
            refresh()
        } catch {
            refresh()
            note = L.t(
                "Не удалось изменить автозапуск: \(error.localizedDescription)",
                "Could not change launch at login: \(error.localizedDescription)",
                "无法更改登录时启动：\(error.localizedDescription)")
        }
    }

    func refresh() {
        switch service.status {
        case .enabled:
            isEnabled = true
            note = ""
        case .requiresApproval:
            isEnabled = true
            note = L.t(
                "Разрешите Charoite в Системных настройках › Основные › Объекты входа",
                "Allow Charoite in System Settings › General › Login Items",
                "请在「系统设置 › 通用 › 登录项」中允许 Charoite")
        case .notFound:
            isEnabled = false
            note = L.t(
                "Автозапуск доступен после установки Charoite.app",
                "Launch at login is available after Charoite.app is installed",
                "安装 Charoite.app 后才能启用登录时启动")
        case .notRegistered:
            isEnabled = false
            note = ""
        @unknown default:
            isEnabled = false
            note = ""
        }
    }
}

#endif
