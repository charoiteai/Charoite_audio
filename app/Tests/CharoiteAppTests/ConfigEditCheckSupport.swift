import Foundation
import XCTest
@testable import CharoiteApp

/// Проверка записи конфига в тестах — боевой путь (`ConfigEditCheck`, процесс, stdin,
/// срок) со скриптом этого репозитория и интерпретатором, в котором есть PyYAML.
/// Интерпретатор приложения тесту недоступен: корни данных временные.
extension ConfigEditCheck {
    static let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()

    /// `CHAROITE_TEST_PYTHON`, `.venv` репозитория, затем `python3` из PATH — первый с PyYAML.
    static let testPython: URL? = {
        var candidates: [String] = []
        if let env = ProcessInfo.processInfo.environment["CHAROITE_TEST_PYTHON"], !env.isEmpty { candidates.append(env) }
        candidates.append(repo.appendingPathComponent(".venv/bin/python").path)
        let path = ProcessInfo.processInfo.environment["PATH"] ?? ""
        candidates += path.split(separator: ":").map { "\($0)/python3" }
        candidates.append("/usr/bin/python3")
        return candidates.lazy.filter { FileManager.default.isExecutableFile(atPath: $0) }
            .first(where: hasYAML).map { URL(fileURLWithPath: $0) }
    }()

    private static func hasYAML(_ python: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-c", "import yaml"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return false }
        p.waitUntilExit()
        return p.terminationStatus == 0
    }

    static var forTests: ConfigEditCheck? {
        testPython.map { ConfigEditCheck(python: $0, script: repo.appendingPathComponent("scripts/install_engine.py").path) }
    }

    /// Подставить проверку на время теста; без интерпретатора с PyYAML — падение с причиной.
    @MainActor
    static func installForTest(_ test: XCTestCase) throws {
        guard let check = forTests else {
            // Не пропуск: без проверки тесты записи молча перестали бы проверять запись.
            throw NSError(domain: "ConfigEditCheck", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "нет python с PyYAML: задайте CHAROITE_TEST_PYTHON"])
        }
        current = check
        test.addTeardownBlock { @MainActor in ConfigEditCheck.current = .live }
    }
}
