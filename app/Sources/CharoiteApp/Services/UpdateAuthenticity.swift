import CryptoKit
import Foundation
import Security

#if os(macOS)

/// Два якоря подлинности обновления, независимые от GitHub (карточка №24).
///
/// Контрольная сумма из релиза защищает от битой загрузки, но не от подмены:
/// она лежит РЯДОМ с архивом, и кто дотянулся до релиза (утёкший токен CI,
/// компрометация аккаунта), тот подписал бы и её. Поэтому перед подменой
/// бандла проверяются две вещи, которые из GitHub подделать нельзя:
///
/// 1. Подпись манифеста ключом владельца (ed25519). Приватная половина живёт
///    только на машине владельца — её нет ни в репозитории, ни в GitHub
///    Secrets; манифест подписывается локально при каждом релизе
///    (`scripts/sign_release_manifest.py`, шаг в docs/RELEASING.md).
/// 2. Подпись Apple у скачанного бандла: Developer ID нашей команды. Даже
///    с полным доступом к CI собрать бандл с этой подписью нельзя без
///    сертификата — а его выдаёт Apple, не GitHub.
enum UpdateAuthenticity {
    /// Публичная половина ключа подписи манифеста (raw ed25519, base64).
    static let manifestKeyBase64 = "1jNGKyTIsYecGpq9eCp7PrT5wLMY2QNSQSUjccgx79s="

    /// Требование к подписи бандла: цепочка Apple и лист нашей команды.
    static let teamRequirement =
        "anchor apple generic and certificate leaf[subject.OU] = \"AR7PDJQNR4\""

    /// Подписан ли манифест (сырые байты .sha256-файла) ключом владельца.
    static func manifestSignatureValid(
        manifest: Data, signature: Data,
        publicKeyBase64: String = manifestKeyBase64
    ) -> Bool {
        guard let raw = Data(base64Encoded: publicKeyBase64),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: raw)
        else { return false }
        return key.isValidSignature(signature, for: manifest)
    }

    /// `.sig` — base64 (возможно, с переводом строки) либо сырые 64 байта.
    static func decodeSignature(_ data: Data) -> Data? {
        if data.count == 64 { return data }
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        return Data(base64Encoded: text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// Стоит ли на бандле живая подпись, удовлетворяющая требованию.
    static func bundleSignatureValid(
        at url: URL, requirement: String = teamRequirement
    ) -> Bool {
        var staticCode: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &staticCode) == errSecSuccess,
              let code = staticCode else { return false }
        var req: SecRequirement?
        guard SecRequirementCreateWithString(requirement as CFString, [], &req) == errSecSuccess,
              let requirementRef = req else { return false }
        let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSCheckNestedCode)
        return SecStaticCodeCheckValidity(code, flags, requirementRef) == errSecSuccess
    }
}

#endif
