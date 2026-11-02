import AppKit
import SwiftUI
import KoeFlowCore

struct DraftEditor: NSViewRepresentable {
    @Binding var text: String
    @Binding var selection: NSRange
    var editable: Bool
    var rules: [WordRule] = []
    var onBeginEditing: () -> Void = {}
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.drawsBackground = false
        let editor = SuggestionTextView(); editor.isRichText = false; editor.allowsUndo = true
        editor.drawsBackground = false; editor.font = .systemFont(ofSize: 21)
        editor.textColor = .labelColor; editor.textContainerInset = NSSize(width: 16, height: 14)
        editor.isVerticallyResizable = true; editor.isHorizontallyResizable = false
        editor.autoresizingMask = [.width]; editor.textContainer?.widthTracksTextView = true
        editor.setAccessibilityLabel("編集する本文")
        editor.setAccessibilityIdentifier("shigaraki-draft-editor")
        editor.setAccessibilityHelp("下線の語をクリックして候補を選べます。選択した語の候補はOptionと下矢印でも開けます。")
        editor.delegate = context.coordinator; scroll.documentView = editor
        return scroll
    }
    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let editor = scroll.documentView as? SuggestionTextView else { return }
        context.coordinator.parent = self; context.coordinator.updating = true
        defer { context.coordinator.updating = false }
        editor.isEditable = editable; editor.isSelectable = !(!editable && editor.string.isEmpty)
        if editor.string != text { editor.string = text; editor.undoManager?.removeAllActions() }
        if Range(selection, in: text) != nil && editor.selectedRange() != selection { editor.setSelectedRange(selection) }
        editor.updateSuggestions(rules: rules)
    }
    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: DraftEditor
        var updating = false
        init(_ parent: DraftEditor) { self.parent = parent }
        func textView(_ textView: NSTextView, shouldChangeTextIn affectedCharRange: NSRange, replacementString: String?) -> Bool {
            guard parent.editable, !updating else { return false }
            parent.onBeginEditing(); return true
        }
        func textDidChange(_ notification: Notification) {
            guard !updating, let editor = notification.object as? NSTextView else { return }
            parent.text = editor.string; parent.selection = editor.selectedRange()
        }
        func textViewDidChangeSelection(_ notification: Notification) {
            guard !updating, let editor = notification.object as? NSTextView else { return }
            parent.selection = editor.selectedRange()
        }
    }
}

/// Native editing/undo owns the replacement; decorating candidates never mutates text.
final class SuggestionTextView: NSTextView {
    private var matches: [WordSuggestionMatch] = []
    private var dismissed = Set<String>()
    private var decoratedText = ""
    private var decoratedRules: [WordRule] = []
    private var decoratedEditable = false
    func updateSuggestions(rules: [WordRule], force: Bool = false) {
        if string != decoratedText || rules != decoratedRules { dismissed.removeAll() }
        guard force || string != decoratedText || rules != decoratedRules || isEditable != decoratedEditable else { return }
        decoratedText = string; decoratedRules = rules; decoratedEditable = isEditable
        let full = NSRange(location: 0, length: (string as NSString).length)
        layoutManager?.removeTemporaryAttribute(.underlineStyle, forCharacterRange: full)
        layoutManager?.removeTemporaryAttribute(.underlineColor, forCharacterRange: full)
        matches = isEditable ? WordSuggestions.matches(in: string, rules: rules).filter { !dismissed.contains($0.key) } : []
        for match in matches {
            layoutManager?.addTemporaryAttributes([
                .underlineStyle: NSUnderlineStyle.single.rawValue | NSUnderlineStyle.patternDot.rawValue,
                .underlineColor: NSColor.systemBlue.withAlphaComponent(0.6)
            ], forCharacterRange: match.range)
        }
    }
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 1, isEditable, let match = match(at: event) {
            showMenu(for: match, at: convert(event.locationInWindow, from: nil)); return
        }
        super.mouseDown(with: event)
    }
    override func menu(for event: NSEvent) -> NSMenu? {
        if isEditable, let match = match(at: event) { return candidateMenu(for: match) }
        return super.menu(for: event)
    }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 125, event.modifierFlags.intersection([.command, .control, .option, .shift]) == .option,
           isEditable, let match = matches.first(where: { match in
               let selection = selectedRange()
               return selection.length == 0
                   ? selection.location >= match.range.location && selection.location < NSMaxRange(match.range)
                   : NSIntersectionRange(selection, match.range).length > 0
           }), let manager = layoutManager, let container = textContainer {
            let glyphs = manager.glyphRange(forCharacterRange: match.range, actualCharacterRange: nil)
            let rect = manager.boundingRect(forGlyphRange: glyphs, in: container)
            showMenu(for: match, at: NSPoint(x: rect.minX + textContainerOrigin.x, y: rect.maxY + textContainerOrigin.y)); return
        }
        super.keyDown(with: event)
    }
    private func match(at event: NSEvent) -> WordSuggestionMatch? {
        guard let manager = layoutManager, let container = textContainer, !string.isEmpty else { return nil }
        let point = convert(event.locationInWindow, from: nil)
        let position = NSPoint(x: point.x - textContainerOrigin.x, y: point.y - textContainerOrigin.y)
        return matches.first { match in
            let glyphs = manager.glyphRange(forCharacterRange: match.range, actualCharacterRange: nil)
            // Check each line separately: the union rectangle can include empty space.
            var hit = false
            manager.enumerateEnclosingRects(forGlyphRange: glyphs, withinSelectedGlyphRange: NSRange(location: NSNotFound, length: 0), in: container) { rect, stop in
                if rect.contains(position) { hit = true; stop.pointee = true }
            }
            return hit
        }
    }
    private final class Choice: NSObject {
        let match: WordSuggestionMatch
        let replacement: String?
        init(_ match: WordSuggestionMatch, _ replacement: String?) { self.match = match; self.replacement = replacement }
    }
    private func candidateMenu(for match: WordSuggestionMatch) -> NSMenu {
        let menu = NSMenu()
        let original = NSMenuItem(title: "原文のまま（\(match.source)）", action: #selector(choose(_:)), keyEquivalent: "")
        original.target = self; original.representedObject = Choice(match, nil); menu.addItem(original)
        menu.addItem(.separator())
        for replacement in match.replacements {
            let item = NSMenuItem(title: String(replacement.prefix(80)), action: #selector(choose(_:)), keyEquivalent: "")
            item.toolTip = replacement; item.target = self; item.representedObject = Choice(match, replacement); menu.addItem(item)
        }
        return menu
    }
    private func showMenu(for match: WordSuggestionMatch, at point: NSPoint) {
        window?.makeFirstResponder(self); setSelectedRange(match.range)
        candidateMenu(for: match).popUp(positioning: nil, at: point, in: self)
    }
    @objc private func choose(_ item: NSMenuItem) {
        guard isEditable, let choice = item.representedObject as? Choice, string == choice.match.snapshot else { return }
        guard let replacement = choice.replacement else {
            dismissed.insert(choice.match.key); updateSuggestions(rules: decoratedRules, force: true); return
        }
        guard choice.match.applying(replacement, to: string) != nil else { return }
        // insertText participates in NSTextView's native undo and delegate transaction.
        breakUndoCoalescing(); insertText(replacement, replacementRange: choice.match.range); breakUndoCoalescing()
        undoManager?.setActionName("候補の置換")
        updateSuggestions(rules: decoratedRules, force: true)
    }
}
