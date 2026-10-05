import XCTest
@testable import CharoiteApp

final class SetupReadinessTests: XCTestCase {
    func testModelTagsMatchExplicitAndLatestNames() {
        XCTAssertTrue(SetupReadinessPolicy.modelAvailable(
            "bge-m3:latest", in: ["bge-m3:latest"]))
        XCTAssertFalse(SetupReadinessPolicy.modelAvailable(
            "qwen3.5:4b", in: ["qwen3.5:latest"]))
        XCTAssertTrue(SetupReadinessPolicy.modelAvailable(
            "qwen3.5", in: ["qwen3.5:4b"]))
        XCTAssertFalse(SetupReadinessPolicy.modelAvailable(
            "qwen3.5:4b", in: ["gemma4:latest"]))
    }

    func testMissingModelsAreDeduplicated() {
        XCTAssertEqual(
            SetupReadinessPolicy.missingModels(
                ["qwen3.5:4b", "qwen3.5:4b", "gemma4:latest"],
                installed: ["gemma4:latest"]),
            ["qwen3.5:4b"])
    }

    func testSystemAudioRequiresActualBlackHoleInput() {
        XCTAssertTrue(SetupReadinessPolicy.hasSystemAudioInput(
            ["MacBook Microphone", "BlackHole 2ch"]))
        XCTAssertFalse(SetupReadinessPolicy.hasSystemAudioInput(
            ["MacBook Microphone", "External USB Mic"]))
        XCTAssertTrue(SetupReadinessPolicy.hasMicrophoneInput(
            ["MacBook Microphone", "BlackHole 2ch"]))
        XCTAssertFalse(SetupReadinessPolicy.hasMicrophoneInput(
            ["BlackHole 2ch"]))
    }

    func testWarningsDoNotBlockAFirstMeeting() {
        let snapshot = SetupReadinessSnapshot(checks: [
            SetupCheck(id: "audio", state: .warning, title: "audio", detail: "mic only"),
            SetupCheck(id: "graph", state: .warning, title: "graph", detail: "off"),
        ])
        XCTAssertTrue(snapshot.canStart)
        XCTAssertEqual(snapshot.warnings, 2)
        XCTAssertEqual(snapshot.problems, 0)
    }

    func testRefusedModelAddressBlocksStartBeforeTheDaemon() {
        XCTAssertNil(SetupReadinessPolicy.refusedModelAddressCheck(nil),
                     "нет текста — нет строки: путать с отказом нельзя")
        XCTAssertNil(SetupReadinessPolicy.refusedModelAddressCheck("   "),
                     "пробелы — не отказ")
        let text = "llm.base_url = http://[fe80::1%en0]:11434: authority вне белой грамматики"
        let check = SetupReadinessPolicy.refusedModelAddressCheck(text)
        XCTAssertEqual(check?.id, "model-address")
        XCTAssertEqual(check?.state, .blocked)
        XCTAssertEqual(check?.detail, text, "рецепт демона показывается как есть")
        let snapshot = SetupReadinessSnapshot(checks: [check!])
        XCTAssertFalse(snapshot.canStart, "отказ адреса блокирует старт до правки конфига")
        XCTAssertEqual(snapshot.problems, 1)
    }

    func testAnyBlockingProblemDisablesStart() {
        let snapshot = SetupReadinessSnapshot(checks: [
            SetupCheck(id: "python", state: .blocked, title: "python", detail: "missing"),
            SetupCheck(id: "audio", state: .ready, title: "audio", detail: "ready"),
        ])
        XCTAssertFalse(snapshot.canStart)
        XCTAssertEqual(snapshot.problems, 1)
    }
}

/// Первый запуск без терминала: рецепт из detail исполняется кнопкой.
final class ReadinessFixActionsTests: XCTestCase {
    func testPullCommandsBecomeButtons() {
        let detail = "ollama pull qwen3.6:35b-a3b  ·  ollama pull bge-m3"
        XCTAssertEqual(SetupReadinessPolicy.pullableModels(in: detail),
                       ["qwen3.6:35b-a3b", "bge-m3"])
    }

    func testDetailWithoutRecipeGivesNoButtons() {
        XCTAssertTrue(SetupReadinessPolicy.pullableModels(in: "Ollama не отвечает").isEmpty)
        XCTAssertNil(SetupReadinessPolicy.copyableCommand(in: "Ollama не отвечает"))
    }

    func testPipRecipeIsCopyableNotPullable() {
        let detail = ".venv/bin/pip install .  ·  затем перезапуск"
        XCTAssertTrue(SetupReadinessPolicy.pullableModels(in: detail).isEmpty)
        XCTAssertEqual(SetupReadinessPolicy.copyableCommand(in: detail),
                       ".venv/bin/pip install .")
    }

    func testProgressPrefersPercents() {
        XCTAssertEqual(ModelPullService.progressText(status: "downloading",
                                                     completed: 340, total: 1000), "34 %")
        XCTAssertEqual(ModelPullService.progressText(status: "verifying",
                                                     completed: nil, total: nil), "verifying")
    }
}

/// Набор голосов: предупреждение только когда эмбеддинги есть, а сегментации нет.
final class DiarizationReadinessTests: XCTestCase {
    func testDiarizationCheckCoversFourCombinations() {
        XCTAssertNil(SetupReadinessPolicy.diarizationCheck(embeddings: false, segmentation: false),
                     "без эмбеддингов отдельной строки нет")
        XCTAssertNil(SetupReadinessPolicy.diarizationCheck(embeddings: false, segmentation: true),
                     "одна сегментация — не этот случай")
        XCTAssertNil(SetupReadinessPolicy.diarizationCheck(embeddings: true, segmentation: true),
                     "оба файла на месте — строки нет")

        let check = SetupReadinessPolicy.diarizationCheck(embeddings: true, segmentation: false)
        XCTAssertEqual(check?.id, "diarization")
        XCTAssertEqual(check?.state, .warning)
        let titles = [
            "Голоса после встречи не размечаются заново",
            "Voices are not labelled again after the meeting",
            "会后不会重新标注说话人",
        ]
        let details = [
            "На границах реплик голоса путаются; не хватает модели сегментации, около 7 МБ",
            "Voices get mixed up at utterance boundaries; the segmentation model is missing, about 7 MB",
            "在发言边界上声音会混淆；缺少分段模型，约 7 MB",
        ]
        XCTAssertTrue(titles.contains(check?.title ?? ""))
        XCTAssertTrue(details.contains(check?.detail ?? ""))
        let detail = check?.detail ?? ""
        XCTAssertTrue(SetupReadinessPolicy.pullableModels(in: detail).isEmpty,
                      "терминальный рецепт в тексте стал бы кнопкой pull")
        XCTAssertNil(SetupReadinessPolicy.copyableCommand(in: detail),
                     "терминальный рецепт в тексте стал бы командой для копирования")
        let snapshot = SetupReadinessSnapshot(checks: [check!])
        XCTAssertTrue(snapshot.canStart, "предупреждение не блокирует старт")
        XCTAssertEqual(snapshot.warnings, 1)
        XCTAssertEqual(snapshot.problems, 0)
    }

    /// Кнопка постановки (мастер и «Сегодня») — по составу набора на диске:
    /// видна при любом из трёх неполных сочетаний, в том числе без эмбеддингов,
    /// когда проверки «diarization» в снимке нет (№625).
    func testVoiceSetIsCompleteOnlyWithBothFiles() throws {
        let files = FileManager.default
        for (embeddings, segmentation) in [(false, false), (true, false), (false, true), (true, true)] {
            let root = files.temporaryDirectory
                .appendingPathComponent("voice-set-\(UUID().uuidString)", isDirectory: true)
            defer { try? files.removeItem(at: root) }
            try files.createDirectory(at: root.appendingPathComponent("models/diar"),
                                      withIntermediateDirectories: true)
            if embeddings {
                XCTAssertTrue(files.createFile(atPath: DiarizationModels.embeddingURL(root).path,
                                               contents: Data([8])))
            }
            if segmentation {
                XCTAssertTrue(files.createFile(atPath: DiarizationModels.segmentationURL(root).path,
                                               contents: Data([8])))
            }
            XCTAssertEqual(DiarizationModels.isComplete(root: root), embeddings && segmentation,
                           "эмбеддинги \(embeddings), сегментация \(segmentation)")
            XCTAssertEqual(SetupReadinessPolicy.diarizationCheck(
                embeddings: embeddings, segmentation: segmentation) != nil,
                           embeddings && !segmentation,
                           "проверка — только при «эмбеддинги есть, сегментации нет»")
        }
    }

    func testWizardCaptionNamesWhatIsMissing() {
        let both = SetupReadinessPolicy.diarizationInstallCaption(embeddings: false, segmentation: false)
        let seg = SetupReadinessPolicy.diarizationInstallCaption(embeddings: true, segmentation: false)
        let emb = SetupReadinessPolicy.diarizationInstallCaption(embeddings: false, segmentation: true)
        XCTAssertTrue(both.contains("50"))
        XCTAssertFalse(both.contains("7"))
        XCTAssertTrue(seg.contains("7"))
        XCTAssertFalse(seg.contains("50"))
        XCTAssertTrue(emb.contains("40"))
        XCTAssertFalse(emb.contains("7"))
    }
}
