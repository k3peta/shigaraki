import Foundation

public struct PromptExample: Equatable, Sendable {
    public let input: String
    public let output: String
}

public struct PromptCard: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var purpose: WritingPurpose
    public var instructions: String
    public var inputExample: String
    public var outputExample: String
    public var isStandard: Bool { ["standard.email", "standard.chat", "standard.document"].contains(id) }
    public init(id: String = UUID().uuidString, name: String, purpose: WritingPurpose = .document, instructions: String,
                inputExample: String = "", outputExample: String = "") {
        self.id = id; self.name = name; self.purpose = purpose; self.instructions = instructions
        self.inputExample = inputExample; self.outputExample = outputExample
    }
    private enum CodingKeys: String, CodingKey { case id, name, purpose, instructions, inputExample, outputExample }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? UUID().uuidString
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "新しいカード"
        purpose = WritingPurpose(rawValue: try values.decodeIfPresent(String.self, forKey: .purpose) ?? "") ?? .document
        instructions = try values.decodeIfPresent(String.self, forKey: .instructions) ?? ""
        inputExample = try values.decodeIfPresent(String.self, forKey: .inputExample) ?? ""
        outputExample = try values.decodeIfPresent(String.self, forKey: .outputExample) ?? ""
    }
    public func modelInstructions(fallback: String = "") -> String {
        let rule = String(instructions.trimmingCharacters(in: .whitespacesAndNewlines).prefix(1200))
        return rule.isEmpty ? fallback : rule
    }
    /// Keep examples separate from instructions; the engine supplies a bounded input/response pair.
    public var example: PromptExample? {
        let input = String(inputExample.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
        let output = String(outputExample.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
        guard !input.isEmpty && !output.isEmpty else { return nil }
        return PromptExample(input: input, output: output)
    }
    public static var standards: [PromptCard] { [
        PromptCard(id: "standard.email", name: "メール", purpose: .email,
                   instructions: "・丁寧な「です・ます調」に整えます。\n・句読点を補い、話題が変わるところで段落を分けます。\n・意味のない言いよどみを除き、依頼や約束の強さを保ちます。\n・本文にない宛名、挨拶、件名、署名は追加しません。",
                   inputExample: "えーと 資料を確認しました 修正はありません 明日の15時までに送っていただけますか",
                   outputExample: "資料を確認しました。修正はありません。\n\n明日の15時までに送っていただけますか。"),
        PromptCard(id: "standard.chat", name: "チャット", purpose: .chat,
                   instructions: "・話し手の口調と敬語の程度を保ちます。\n・句読点を補い、話題が変わるところで改行します。\n・意味のない言いよどみを除き、短いメッセージに整えます。内容の省略や要約はしません。\n・本文にない挨拶、署名、絵文字は追加しません。",
                   inputExample: "えーと 資料を確認したよ 修正はない 明日の15時までに送ってもらえるかな",
                   outputExample: "資料を確認したよ。修正はない。\n明日の15時までに送ってもらえるかな。"),
        PromptCard(id: "standard.document", name: "文書", purpose: .document,
                   instructions: "・本文で主に使われている文体に統一します。判断できない場合は元の文体を保ちます。\n・句読点を補い、話題が変わるところで段落を分けます。\n・意味のない言いよどみを除き、話した内容を省略せずに整えます。\n・本文にない見出し、結論、事実は追加しません。",
                   inputExample: "えーと 調査は完了した 対象は32件だった 修正は必要ない 次の調査は11月6日に行う予定だ",
                   outputExample: "調査は完了した。対象は32件だった。修正は必要ない。\n\n次の調査は11月6日に行う予定だ。")
    ] }
    // Upgrade only exact factory defaults. Edited cards and their stable IDs remain intact.
    private static var previousStandards: [PromptCard] { [
        PromptCard(id: "standard.email", name: "メール", purpose: .email, instructions: "丁寧なです・ます調にし、読みやすい段落に分けてください。本文にない宛名、挨拶、件名、署名は追加しないでください。"),
        PromptCard(id: "standard.chat", name: "チャット", purpose: .chat, instructions: "話し手の口調を保ち、句読点を整えて簡潔な文章にしてください。絵文字は追加しないでください。"),
        PromptCard(id: "standard.document", name: "文書", purpose: .document, instructions: "文体を統一し、句読点と段落を整えてください。話した内容を省略せず、読みやすい文章にしてください。")
    ] }
    public static func restoringStandards(in stored: [PromptCard]) -> [PromptCard] {
        var ids = Set<String>()
        var cards = stored.filter { ids.insert($0.id).inserted }
        for standard in standards {
            if let index = cards.firstIndex(where: { $0.id == standard.id }) {
                if previousStandards.contains(cards[index]) { cards[index] = standard }
            } else { cards.append(standard) }
        }
        return cards
    }
}

public enum PromptCardRouter {
    public static func apply(_ plan: WritingPlan, cards: [PromptCard], cardID: String) -> WritingPlan {
        var result = plan
        // Use canonical standard IDs so malformed/duplicate purpose values cannot crash routing.
        for standard in PromptCard.standards {
            let card = cards.first { $0.id == standard.id } ?? standard
            result.standardInstructions[standard.purpose] = card.modelInstructions(fallback: standard.instructions)
        }
        guard plan.purpose != .plain, plan.mode != .verbatim else { return result }
        if let standard = PromptCard.standards.first(where: { $0.purpose == plan.purpose }) {
            result.cardExample = (cards.first { $0.id == standard.id } ?? standard).example
        }
        if let card = cards.first(where: { $0.id == cardID }) {
            result.purpose = card.purpose
            result.cardName = card.name
            let standard = PromptCard.standards.first { $0.purpose == card.purpose }
            let savedRule = cards.first { $0.id == standard?.id }?.instructions.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let fallback = savedRule.isEmpty ? (standard?.instructions ?? "") : String(savedRule.prefix(1200))
            result.cardInstructions = card.modelInstructions(fallback: fallback)
            result.cardExample = card.example
        }
        return result
    }
}
