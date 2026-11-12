import Foundation

struct TypedFeedBlock: Identifiable, Equatable, Decodable {
    let id: String
    let index: Int
    let kind: String
    let text: String
    let questionID: String?
    let answerTo: String?
    let manualID: String?
    let topicID: String?
    let topicLabel: String?
    enum CodingKeys: String, CodingKey {
        case id, index, kind, text
        case questionID = "question_id", answerTo = "answer_to", manualID = "manual_id"
        case topicID = "topic_id", topicLabel = "topic_label"
    }
    private static func validTopicText(_ value: String, maxBytes: Int, maxScalars: Int) -> Bool {
        value.utf8.count <= maxBytes && value.unicodeScalars.count <= maxScalars
            && !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !value.unicodeScalars.contains {
                CharacterSet.controlCharacters.contains($0) || $0.value == 0x2028 || $0.value == 0x2029
            }
    }
    static func decode(_ value: Any) -> [TypedFeedBlock]? {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value),
              let rows = try? JSONDecoder().decode([TypedFeedBlock].self, from: data) else { return nil }
        var ids = Set<String>()
        var questions: [String: TypedFeedBlock] = [:]
        var topicLabels: [String: String] = [:]
        for (expectedIndex, row) in rows.enumerated() {
            guard row.index == expectedIndex, !row.id.isEmpty, ids.insert(row.id).inserted,
                  ["paragraph", "question", "answer", "system"].contains(row.kind) else { return nil }
            // Legacy rows omit both fields (or send null); the producer also uses
            // paired empty strings for topicless system/manual rows. Other pairs
            // are atomic protocol errors, never a reason to infer a topic from text.
            switch (row.topicID, row.topicLabel) {
            case (nil, nil), (""?, ""?): break
            case let (id?, label?):
                guard row.kind != "system",
                      validTopicText(id, maxBytes: 384, maxScalars: 96),
                      validTopicText(label, maxBytes: 1024, maxScalars: 256),
                      topicLabels[id].map({ $0 == label }) ?? true else { return nil }
                topicLabels[id] = label
            default: return nil
            }
            if row.kind == "question" {
                guard let q = row.questionID, !q.isEmpty, questions[q] == nil else { return nil }
                questions[q] = row
            }
            if row.kind == "answer" {
                guard let q = row.answerTo, let question = questions[q],
                      (row.topicID ?? "") == (question.topicID ?? ""),
                      (row.topicLabel ?? "") == (question.topicLabel ?? "") else { return nil }
            }
        }
        return rows
    }
}

/// Presentation-only framing over the producer's ordered Q/A projection.
/// Associated ID cases cannot collide, even with arbitrary protocol block IDs.
enum TypedFeedRow: Identifiable, Equatable {
    enum ID: Hashable {
        case topic(beforeBlockID: String)
        case block(String)
    }
    case topic(topicID: String, label: String, beforeBlockID: String)
    case block(TypedFeedBlock)

    var id: ID {
        switch self {
        case .topic(_, _, let blockID): return .topic(beforeBlockID: blockID)
        case .block(let block): return .block(block.id)
        }
    }

    static func project(_ blocks: [TypedFeedBlock]) -> [Self] {
        var rows: [Self] = []
        var shownTopic: String?
        for block in blocks {
            if block.kind != "system", let topic = block.topicID, !topic.isEmpty,
               let label = block.topicLabel, topic != shownTopic {
                rows.append(.topic(topicID: topic, label: label, beforeBlockID: block.id))
                shownTopic = topic
            }
            rows.append(.block(block))
        }
        return rows
    }
}

#if os(macOS)
extension SuflerService {
    var correlatedManualQuestionID: String? {
        guard !isHinting, !isAutoHinting, !statusIsError, let manual = hintManualID,
              let rows = feedBlocks, !feedProtocolFailed else { return nil }
        for answer in rows where answer.kind == "answer" && answer.manualID == manual {
            if let question = rows.first(where: {
                $0.kind == "question" && $0.manualID == manual && $0.questionID == answer.answerTo
            }) { return question.id }
        }
        return nil
    }
    var hintIsInThread: Bool { correlatedManualQuestionID != nil }
}
#endif

/// A single observer chooses one scroll target for an atomic UI update.
struct TypedFeedScrollUpdate: Equatable {
    let text: String
    let blocks: [TypedFeedBlock]?
    let manualQuestionID: String?

    func target(after previous: Self, follows: Bool) -> String? {
        if let question = manualQuestionID, question != previous.manualQuestionID { return question }
        return follows ? "hintBottom" : nil
    }
}
