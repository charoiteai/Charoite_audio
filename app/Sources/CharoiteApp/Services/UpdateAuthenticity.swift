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

    /// Версия, которую удостоверяет подписанный манифест: первая строка
    /// формата "<version>  <sha256>". nil — формат не тот (старый голый хеш
    /// больше не принимается: он не привязан к версии и позволял реплей
    /// старого релиза под новым тегом — круг по PR #366, GLM + DeepSeek).
    static func manifestVersion(_ manifest: Data) -> String? {
        guard let text = String(data: manifest, encoding: .utf8) else { return nil }
        let line = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        let cols = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
        guard cols.count == 2, cols[1].count == 64 else { return nil }
        return String(cols[0])
    }

    /// Хеш, который удостоверяет манифест: второй столбец первой строки.
    static func manifestChecksum(_ manifest: Data) -> String? {
        guard let text = String(data: manifest, encoding: .utf8) else { return nil }
        let line = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        let cols = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
        guard cols.count == 2, cols[1].count == 64 else { return nil }
        return cols[1].lowercased()
    }

    /// Держит ли связка версий против даунгрейда и реплея.
    ///
    /// Круг по PR #366 (GLM + DeepSeek независимо): подпись над голым хешом
    /// позволяла реплей — атакующий с правом записи в релизы кладёт СТАРУЮ
    /// честную тройку (zip + манифест + подпись) под НОВЫЙ тег, isNewer
    /// смотрит на тег, и клиент откатывается на версию без якорей. Теперь
    /// подписанный манифест несёт версию, и она обязана совпасть с тегом
    /// и быть новее установленной.
    static func versionBindingOK(manifestVersion: String?,
                                 tag: String, current: String) -> Bool {
        guard let manifestVersion else { return false }
        return manifestVersion == VersionStatus.normalize(tag)
            && VersionStatus.isNewer(manifestVersion, than: current)
    }

    /// Подписан ли манифест (сырые байты .manifest-файла) ключом владельца.
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
        // kSecCSStrictValidate обязателен: без него сверяются только Mach-O
        // (все архитектуры, вложенные бинарники), а конверт ресурсов — нет.
        // Демон — это .py в Contents/Resources/charoite/, не Mach-O: подмена
        // daemon.py в подписанном бандле иначе проходит проверку (круг по
        // PR #366, DeepSeek). Совпадает со строгостью самой сборки
        // (make_app.sh: codesign --verify --deep --strict).
        let flags = SecCSFlags(rawValue: kSecCSStrictValidate
                               | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode)
        return SecStaticCodeCheckValidity(code, flags, requirementRef) == errSecSuccess
    }
}

#endif
