import CryptoKit
import XCTest
@testable import CharoiteApp

/// Два якоря подлинности обновления (карточка №24). Ключи в тестах —
/// одноразовые: боевой публичный ключ здесь только сверяется по форме.
final class UpdateAuthenticityTests: XCTestCase {

    func testManifestSignatureRoundTrip() throws {
        let key = Curve25519.Signing.PrivateKey()
        let manifest = Data("abc123  Charoite.app.zip\n".utf8)
        let sig = try key.signature(for: manifest)
        let pub = key.publicKey.rawRepresentation.base64EncodedString()

        XCTAssertTrue(UpdateAuthenticity.manifestSignatureValid(
            manifest: manifest, signature: sig, publicKeyBase64: pub))
        // чужой ключ — отказ
        let stranger = Curve25519.Signing.PrivateKey().publicKey
            .rawRepresentation.base64EncodedString()
        XCTAssertFalse(UpdateAuthenticity.manifestSignatureValid(
            manifest: manifest, signature: sig, publicKeyBase64: stranger))
        // подменённый манифест — отказ
        XCTAssertFalse(UpdateAuthenticity.manifestSignatureValid(
            manifest: Data("evil".utf8), signature: sig, publicKeyBase64: pub))
    }

    func testSignatureDecodingAcceptsBase64AndRawOnly() {
        let raw = Data((0..<64).map { UInt8($0) })
        XCTAssertEqual(UpdateAuthenticity.decodeSignature(raw), raw)
        let b64 = Data((raw.base64EncodedString() + "\n").utf8)
        XCTAssertEqual(UpdateAuthenticity.decodeSignature(b64), raw)
        XCTAssertNil(UpdateAuthenticity.decodeSignature(Data("мусор".utf8)))
    }

    func testBuiltInPublicKeyIsARealEd25519Key() {
        // Битый или укороченный ключ в константе молча выключил бы якорь:
        // manifestSignatureValid отвечала бы false на ЛЮБУЮ подпись, и
        // обновления просто перестали бы ставиться. Форму держим тестом.
        let raw = Data(base64Encoded: UpdateAuthenticity.manifestKeyBase64)
        XCTAssertEqual(raw?.count, 32, "raw ed25519 — ровно 32 байта")
        XCTAssertNotNil(try? Curve25519.Signing.PublicKey(
            rawRepresentation: raw ?? Data()))
    }

    func testTeamRequirementPinsAppleChainAndOurTeam() {
        XCTAssertTrue(UpdateAuthenticity.teamRequirement.contains("anchor apple generic"))
        XCTAssertTrue(UpdateAuthenticity.teamRequirement.contains("AR7PDJQNR4"))
    }

    func testBundleSignatureJudgesRealSignatures() throws {
        // Живой негатив и позитив без сети: Калькулятор подписан Apple —
        // требованию «наша команда» он обязан НЕ соответствовать, а мягкому
        // «anchor apple» — соответствовать. Если функция сломается в
        // «всегда true» или «всегда false», одна из веток покраснеет.
        let calc = URL(fileURLWithPath: "/System/Applications/Calculator.app")
        try XCTSkipUnless(FileManager.default.fileExists(atPath: calc.path),
                          "нет системного Калькулятора")
        XCTAssertFalse(UpdateAuthenticity.bundleSignatureValid(at: calc),
                       "чужая подпись прошла требование нашей команды")
        XCTAssertTrue(UpdateAuthenticity.bundleSignatureValid(
            at: calc, requirement: "anchor apple"),
            "живая подпись Apple не прошла мягкое требование")
    }

    func testManifestCarriesVersionAndChecksum() {
        let good = Data("0.57.0  ".utf8) + Data(String(repeating: "a", count: 64).utf8)
        XCTAssertEqual(UpdateAuthenticity.manifestVersion(good), "0.57.0")
        XCTAssertEqual(UpdateAuthenticity.manifestChecksum(good),
                       String(repeating: "a", count: 64))
        // прежний формат — голый хеш — больше не принимается: он не привязан
        // к версии и позволял реплей старого релиза под новым тегом
        let bare = Data(String(repeating: "b", count: 64).utf8)
        XCTAssertNil(UpdateAuthenticity.manifestVersion(bare))
        XCTAssertNil(UpdateAuthenticity.manifestChecksum(bare))
        XCTAssertNil(UpdateAuthenticity.manifestVersion(Data("мусор".utf8)))
    }

    func testVersionBindingStopsDowngradeReplay() {
        // реплей: старая честная тройка (манифест говорит 0.56.0) под тегом v0.99.0
        XCTAssertFalse(UpdateAuthenticity.versionBindingOK(
            manifestVersion: "0.56.0", tag: "v0.99.0", current: "0.57.0"))
        // даунгрейд под честным тегом: версия манифеста не новее текущей
        XCTAssertFalse(UpdateAuthenticity.versionBindingOK(
            manifestVersion: "0.56.0", tag: "v0.56.0", current: "0.57.0"))
        // манифест без версии (старый формат) — отказ
        XCTAssertFalse(UpdateAuthenticity.versionBindingOK(
            manifestVersion: nil, tag: "v0.58.0", current: "0.57.0"))
        // честное обновление проходит
        XCTAssertTrue(UpdateAuthenticity.versionBindingOK(
            manifestVersion: "0.58.0", tag: "v0.58.0", current: "0.57.0"))
    }
}
