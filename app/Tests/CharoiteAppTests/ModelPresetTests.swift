import XCTest
@testable import CharoiteApp

/// Подбор моделей под память машины.
///
/// Пресеты жили комментарием в config.example.yaml, и человек переносил их
/// руками. Ошибка здесь молчаливая: слишком тяжёлая модель не падает, она
/// уходит в своп — подсказка приходит через минуту вместо секунды, и вывод
/// делается «продукт медленный», а не «модель не по машине».
final class ModelPresetTests: XCTestCase {

    func testРекомендацияПоПамяти() {
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 64).id, "full")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 32).id, "full")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 24).id, "balanced")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 16).id, "balanced")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 8).id, "light")
    }

    func testНа16ГбНеПредлагаемСамыйТяжёлый() {
        // Формально 20 ГБ весов в 16 ГБ «влезают» — но вместе с системой,
        // браузером и созвоном это своп посреди встречи.
        XCTAssertNotEqual(ModelPresetPolicy.recommended(forGB: 16).id, "full")
    }

    func testПресетыИдутОтТяжёлогоКЛёгкому() {
        let needs = ModelPresetPolicy.all.map(\.needsGB)
        XCTAssertEqual(needs, needs.sorted(by: >), "порядок в списке — это порядок выбора")
    }

    func testКаждыйПресетНесётДвеМодели() {
        for preset in ModelPresetPolicy.all {
            XCTAssertEqual(preset.models.count, 2, preset.id)
            XCTAssertFalse(preset.model.isEmpty)
            XCTAssertFalse(preset.smallModel.isEmpty)
        }
    }

    func testТекущийПресетУзнаётсяПоОбеимМоделям() {
        let full = ModelPresetPolicy.all[0]
        XCTAssertEqual(ModelPresetPolicy.current(model: full.model,
                                                 smallModel: full.smallModel)?.id, "full")
        // Человек поправил одну строку руками — это уже не наш пресет,
        // и показывать «Полный» было бы неправдой.
        XCTAssertNil(ModelPresetPolicy.current(model: full.model,
                                               smallModel: "qwen3.5:2b"))
        XCTAssertNil(ModelPresetPolicy.current(model: nil, smallModel: nil))
    }

    func testПамятьМашиныОпределяется() {
        XCTAssertGreaterThan(ModelPresetPolicy.machineMemoryGB, 0,
                             "без объёма памяти рекомендация становится гаданием")
    }
}
