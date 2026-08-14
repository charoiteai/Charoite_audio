import XCTest
@testable import CharoiteApp

/// Человек не должен уходить в терминал за движком моделей.
///
/// Инструкция начиналась с `brew install ollama && brew services start ollama`,
/// и обе команды легко выполнить неправильно: первая не поднимает сервер, а
/// поставить brew-версию поверх Ollama.app — значит получить два экземпляра,
/// дерущихся за порт 11434. Ровно это вскрылось 13.08 на живой машине.
///
/// Тесты держат решение о том, что показать и чем чинить, — оно принимается
/// по фактам (отвечает ли порт, какие пути существуют), а не по догадкам.
final class OllamaRuntimeTests: XCTestCase {
    func testRespondingPortMeansReady() {
        let s = OllamaRuntimeService.decide(responding: true, brewBinary: nil,
                                            appBundleExists: false, brewAvailable: false)
        XCTAssertEqual(s, .running)
        XCTAssertEqual(OllamaRuntimeService.actionTitle(for: s), "",
                       "у готового состояния кнопки быть не должно")
    }

    func testBrewInstallationIsStartedAsService() {
        let s = OllamaRuntimeService.decide(responding: false,
                                            brewBinary: "/opt/homebrew/bin/ollama",
                                            appBundleExists: false, brewAvailable: true)
        XCTAssertEqual(s, .installedNotRunning(launcher: .brewService))
    }

    func testBrewWinsOverAppBundle() {
        // Стоят обе — поднимаем brew-сервис, а не второй экземпляр из
        // приложения: иначе они снова подерутся за порт.
        let s = OllamaRuntimeService.decide(responding: false,
                                            brewBinary: "/opt/homebrew/bin/ollama",
                                            appBundleExists: true, brewAvailable: true)
        XCTAssertEqual(s, .installedNotRunning(launcher: .brewService))
    }

    func testAppBundleAloneIsLaunchedAsApp() {
        let s = OllamaRuntimeService.decide(responding: false, brewBinary: nil,
                                            appBundleExists: true, brewAvailable: false)
        XCTAssertEqual(s, .installedNotRunning(launcher: .appBundle(path: "/Applications/Ollama.app")))
    }

    func testNothingInstalledOffersInstall() {
        let withBrew = OllamaRuntimeService.decide(responding: false, brewBinary: nil,
                                                   appBundleExists: false, brewAvailable: true)
        XCTAssertEqual(withBrew, .notInstalled(canUseBrew: true))
        XCTAssertFalse(OllamaRuntimeService.actionTitle(for: withBrew).isEmpty,
                       "без движка человеку нужна кнопка, а не совет из терминала")
    }

    func testExplanationDiffersWithAndWithoutBrew() {
        // Без Homebrew мы не ставим молча — открываем страницу загрузки, и
        // человек должен понимать это ДО нажатия.
        let a = OllamaRuntimeService.explanation(for: .notInstalled(canUseBrew: true))
        let b = OllamaRuntimeService.explanation(for: .notInstalled(canUseBrew: false))
        XCTAssertNotEqual(a, b)
        XCTAssertFalse(a.isEmpty)
        XCTAssertFalse(b.isEmpty)
    }

    func testEveryStateExplainsItself() {
        for state: OllamaRuntime in [.running,
                                     .installedNotRunning(launcher: .brewService),
                                     .installedNotRunning(launcher: .appBundle(path: "/Applications/Ollama.app")),
                                     .notInstalled(canUseBrew: true),
                                     .notInstalled(canUseBrew: false)] {
            XCTAssertFalse(OllamaRuntimeService.explanation(for: state).isEmpty,
                           "состояние без объяснения — это молчаливый тупик: \(state)")
        }
    }
}
