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

    /// Ручная сверка с таблицей RAM в README 08.08: в «Лёгком» стояла
    /// gemma4:latest на 9.6 ГБ — для 8-гигабайтной машины это своп, а не
    /// «полегче». Набор для 8 ГБ обязан помещаться в 8 ГБ.
    func testЛёгкийНаборРеальноЛёгкий() {
        let light = ModelPresetPolicy.recommended(forGB: 8)
        XCTAssertEqual(light.model, "qwen3.5:4b",
                       "основная модель для 8 ГБ не должна быть тяжелее ~4 ГБ")
        XCTAssertEqual(light.smallModel, "qwen3.5:2b")
        XCTAssertFalse(light.models.contains("gemma4:latest"),
                       "gemma4:latest просит 9.6 ГБ — в 8 ГБ это своп")
    }

    func testНаборыСогласованыСТаблицейRAM() {
        // README, раздел «Какие модели под вашу RAM» — единственный источник
        // правды для этих пар; расхождение здесь человек увидит как тормоза.
        let expected = [
            (32, "qwen3.6:35b-a3b", "qwen3.5:4b"),
            (16, "gemma4:latest", "qwen3.5:2b"),
            (8, "qwen3.5:4b", "qwen3.5:2b"),
        ]
        for (memory, model, small) in expected {
            let preset = ModelPresetPolicy.recommended(forGB: memory)
            XCTAssertEqual(preset.model, model, "\(memory) ГБ: основная модель")
            XCTAssertEqual(preset.smallModel, small, "\(memory) ГБ: лёгкая модель")
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
