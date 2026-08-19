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
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 128).id, "full")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 64).id, "full")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 48).id, "precise")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 32).id, "precise")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 24).id, "balanced")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 16).id, "balanced")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 8).id, "light")
        XCTAssertEqual(ModelPresetPolicy.recommended(forGB: 4).id, "light")
    }

    func testНа16ГбНеПредлагаемСамыйТяжёлый() {
        // Формально 20 ГБ весов в 16 ГБ «влезают» — но вместе с системой,
        // браузером и созвоном это своп посреди встречи.
        XCTAssertNotEqual(ModelPresetPolicy.recommended(forGB: 16).id, "full")
    }

    /// 35B просит 20.4 ГБ весов, и вместе с STT, эмбеддером и системой это
    /// 27–30 ГБ из 32 — своп на первом же длинном разборе. На 32 ГБ идёт
    /// 27B (16.9 ГБ): по бенчу она точнее по цитатам, а медленный разбор —
    /// фоновая работа, которая теперь ещё и уступает живой встрече.
    func testНа32ГбНеСамаяТяжёлаяМодель() {
        let preset = ModelPresetPolicy.recommended(forGB: 32)
        XCTAssertEqual(preset.id, "precise")
        XCTAssertFalse(preset.models.contains("qwen3.6:35b-mlx"),
                       "35B на 32 ГБ уходит в своп вместе с системой и STT")
    }

    func testПресетыИдутОтТяжёлогоКЛёгкому() {
        let needs = ModelPresetPolicy.all.map(\.needsGB)
        XCTAssertEqual(needs, needs.sorted(by: >), "порядок в списке — это порядок выбора")
    }

    func testСписокСкачиванияБезДублей() {
        for preset in ModelPresetPolicy.all {
            XCTAssertFalse(preset.model.isEmpty)
            XCTAssertFalse(preset.smallModel.isEmpty)
            XCTAssertEqual(preset.models.count, Set(preset.models).count,
                           "\(preset.id): одну и ту же модель нельзя качать дважды")
            XCTAssertEqual(preset.models.count, preset.isSingleModel ? 1 : 2, preset.id)
        }
    }

    /// Ручная сверка с таблицей RAM в README 08.08: в «Лёгком» стояла
    /// gemma4:latest на 9.6 ГБ — для 8-гигабайтной машины это своп, а не
    /// «полегче». Набор для 8 ГБ обязан помещаться в 8 ГБ.
    func testЛёгкийНаборРеальноЛёгкий() {
        let light = ModelPresetPolicy.recommended(forGB: 8)
        XCTAssertEqual(light.model, "qwen3.5:4b",
                       "основная модель для 8 ГБ не должна быть тяжелее ~4 ГБ")
        XCTAssertTrue(light.isSingleModel,
                      "README и MODELS.md обещают на 8 ГБ одну модель на обе роли")
        XCTAssertFalse(light.models.contains("gemma4:latest"),
                       "gemma4:latest просит 8.9 ГБ — в 8 ГБ это своп")
    }

    /// Бенч 19.08 снял правило «меньше 30B ломают JSON-схему»: qwen3.5:4b
    /// разобрала все три встречи (31 решение, 30 ядер, 96% цитат) — больше
    /// находок, чем у 12B. Значит на 8 ГБ граф не выключают, он работает.
    func testНа8ГбГрафРаботает() {
        let light = ModelPresetPolicy.recommended(forGB: 8)
        XCTAssertTrue(light.graph, "4B разбирает граф — выключать его нет причины")
        XCTAssertFalse(light.dejaVu, "а вот bge-m3 рядом с системой в 8 ГБ не живёт")
    }

    func testНаборыСогласованыСТаблицейRAM() {
        // README, раздел «Какие модели под вашу RAM» — единственный источник
        // правды для этих пар; расхождение здесь человек увидит как тормоза.
        let expected = [
            (64, "qwen3.6:35b-mlx", "qwen3.5:4b"),
            (32, "qwen3.8:27b-mlx", "qwen3.5:4b"),
            (16, "gemma4:12b", "qwen3.5:4b"),
            (8, "qwen3.5:4b", "qwen3.5:4b"),   // «Light LLM: same model»
        ]
        for (memory, model, small) in expected {
            let preset = ModelPresetPolicy.recommended(forGB: memory)
            XCTAssertEqual(preset.model, model, "\(memory) ГБ: основная модель")
            XCTAssertEqual(preset.smallModel, small, "\(memory) ГБ: лёгкая модель")
        }
    }

    /// «Граф знаний выключен» в описании лёгкого набора было обещанием без
    /// выключателя: конвейер всё равно звал разбор после каждой встречи.
    /// Теперь профиль пишет флаги в config.yaml, и их читает Python
    /// (install_profile.flag) — что выключено, то выключено.
    func testПрофильПишетСвоиВыключателиВКонфиг() {
        let light = ModelPresetPolicy.recommended(forGB: 8)
        let flags = Dictionary(uniqueKeysWithValues: light.configFlags.map { ($0.key, $0.value) })
        XCTAssertEqual(flags["graph"], "true", "4B разбирает граф — бенч 19.08")
        XCTAssertEqual(flags["deja_vu"], "false", "bge-m3 в 8 ГБ рядом с системой не живёт")

        let full = ModelPresetPolicy.recommended(forGB: 64)
        let fullFlags = Dictionary(uniqueKeysWithValues: full.configFlags.map { ($0.key, $0.value) })
        XCTAssertEqual(fullFlags["graph"], "true")
        XCTAssertEqual(fullFlags["deja_vu"], "true")
    }

    func testТяжёлыеПрофилиНичегоНеОтключают() {
        for preset in ModelPresetPolicy.all where preset.needsGB >= 16 {  // 16, 32, 64
            XCTAssertTrue(preset.graph, "\(preset.id): граф — смысл продукта")
            XCTAssertTrue(preset.dejaVu, "\(preset.id): дежавю укладывается в память")
            XCTAssertEqual(preset.configFlags.count, 2, preset.id)
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
