import XCTest
@testable import CharoiteApp

/// Готовность знала про микрофон и BlackHole и молчала про «Запись экрана».
///
/// Без этого права ScreenCaptureKit не поднимается, и вторая сторона звонка
/// в запись не попадает. Человек узнавал об этом после встречи — по пустому
/// каналу собеседника в стенограмме.
///
/// Отдельная ловушка, из-за которой проблему принимали за неисправность:
/// macOS применяет выданное право только к НОВОМУ запуску процесса. Человек
/// нажимает «Разрешить», видит галочку в системных настройках и уверен, что
/// готово, — а захват падает до конца сессии. Ни SETUP, ни интерфейс об этом
/// не говорили (аудит 0.46.0, P0-10).
final class ScreenCaptureReadinessTests: XCTestCase {

    typealias Access = SetupReadinessPolicy.ScreenCaptureAccess

    func testGrantedBeforeLaunchIsReady() {
        XCTAssertEqual(
            SetupReadinessPolicy.screenCaptureAccess(preflight: true,
                                                     grantedInThisSession: false),
            Access.granted,
            "право было до старта — захват работает, вопросов нет")
    }

    func testDeniedIsReported() {
        XCTAssertEqual(
            SetupReadinessPolicy.screenCaptureAccess(preflight: false,
                                                     grantedInThisSession: false),
            Access.denied,
            "права нет — человек должен узнать до встречи, а не по пустому каналу")
    }

    /// Главный случай: выдали прямо сейчас — значит нужен перезапуск.
    func testGrantedInThisSessionRequiresRestart() {
        XCTAssertEqual(
            SetupReadinessPolicy.screenCaptureAccess(preflight: false,
                                                     grantedInThisSession: true),
            Access.grantedNeedsRestart,
            "выданное на ходу право не действует до перезапуска — молчать об "
          + "этом значит оставить человека с галочкой и без собеседника в записи")
    }

    /// Даже если система уже отвечает «да», выдача в этой сессии остаётся
    /// поводом сказать про перезапуск: до него захват не поднимется.
    func testSessionGrantWinsOverPreflight() {
        XCTAssertEqual(
            SetupReadinessPolicy.screenCaptureAccess(preflight: true,
                                                     grantedInThisSession: true),
            Access.grantedNeedsRestart)
    }

    /// Сторож проводки: проверка обязана попасть в снимок готовности.
    ///
    /// Политику легко починить и забыть подключить — тогда тесты выше
    /// останутся зелёными, а человек по-прежнему ничего не увидит.
    func testCheckIsWiredIntoSnapshot() throws {
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteApp/Services/SetupReadinessService.swift")
        let text = try String(contentsOf: source, encoding: .utf8)

        XCTAssertTrue(text.contains("checks.append(screenCaptureCheck())"),
                      "проверка написана, но не добавлена в снимок готовности")
        XCTAssertTrue(text.contains("id: \"screen-capture\""),
                      "у проверки должен быть свой идентификатор")
    }
}
